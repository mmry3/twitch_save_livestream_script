#!/bin/env python3
"""Запись стримов Twitch через streamlink с буферизацией записи в ОЗУ.

streamlink отдаёт поток в --stdout, этот скрипт читает пайп и складывает
данные в очередь в ОЗУ; на диск пишет ОТДЕЛЬНЫЙ ПОТОК. Пока в буфере есть
место, залипание файловой системы (fsync-барьер, снапшот, медленный NAS,
уснувший HDD) не тормозит чтение пайпа — streamlink не упирается в
переполненный stdout, продолжает качать сегменты и не теряет HLS-окно.
Back-pressure включается только когда буфер реально забит; каждое такое
залипание считается и пишется в лог вместе с пиком буфера.
"""

import sys
import os
import time
import subprocess
import re
import json
import threading
import collections
from multiprocessing import Process
from random import uniform
from time import gmtime, strftime, time as now_ts
from pathlib import Path

MIN_WAIT = 2
MAX_WAIT = 11

CHUNK = 1 << 20          # сколько читаем из пайпа за раз
MIB = 1 << 20
BUFFER_MB = 512          # буфер в ОЗУ по умолчанию, MiB (на каждого стримера)
DRAIN_TIMEOUT = 120      # сколько ждём, пока диск дожуёт буфер при закрытии

INVALID_CHARS = r'<>:"/\\|?*'
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

def sanitize_filename_windows(name: str, replacement="_") -> str:
    if not name:
        return "no_title"
    name = re.sub(r'[\U00010000-\U0010ffff]', '', name)

    def is_valid_char(c):
        code = ord(c)
        return (
            c == ' ' or
            (31 < code < 127 and c not in INVALID_CHARS) or
            'А' <= c <= 'я' or c in ('Ё', 'ё')
        )
    name = ''.join(c if is_valid_char(c) else replacement for c in name)
    name = name.strip(" .")
    name = name[:100]
    if name.upper() in RESERVED_NAMES:
        name = f"_{name}"
    return name or "stream_title"

def timestamp():
    return strftime("[%Y-%m-%d %H:%M:%S]", gmtime())

def log_error(message: str):
    with open("errors.log", "a", encoding='utf-8') as err_log:
        err_log.write(f"{timestamp()} {message}\n")


# =============================================================================
#  Запись на диск отдельным потоком
# =============================================================================

class BufferedFileWriter:
    """Пишет на диск из отдельного потока. Очередь в ОЗУ гасит залипания ФС.

    Буфер ограничен по БАЙТАМ, а не по числу кусков: чтение из пайпа отдаёт
    куски произвольного размера, и лимит «в штуках» ничего не гарантирует.

    buffering=0 намеренно: буфер у нас свой, лишний слой только маскирует
    задержки, которые мы как раз хотим измерять.
    """

    def __init__(self, path, buffer_mb=BUFFER_MB, mode="wb"):
        self.path = path
        self.limit = max(8, int(buffer_mb)) * MIB
        self.written = 0            # байт реально отдано ядру
        self.queued = 0             # байт принято в буфер
        self.stalls = 0             # сколько раз упёрлись в потолок буфера
        self.stall_seconds = 0.0    # суммарное ожидание диска
        self.max_depth = 0          # пик занятости буфера, байт
        self.error = None
        self._q = collections.deque()
        self._depth = 0
        self._eof = False
        self._cv = threading.Condition()
        self._f = open(path, mode, buffering=0)
        self._t = threading.Thread(target=self._run, name="disk-writer",
                                   daemon=True)
        self._t.start()

    # ---- поток диска --------------------------------------------------------

    def _run(self):
        try:
            while True:
                with self._cv:
                    while not self._q and not self._eof:
                        self._cv.wait()
                    if not self._q:
                        break
                    chunk = self._q.popleft()

                mv = memoryview(chunk)
                while mv:
                    n = self._f.write(mv)
                    if not n:
                        raise OSError(f"write() вернул {n!r} для {self.path}")
                    mv = mv[n:]
                self.written += len(chunk)

                with self._cv:
                    self._depth -= len(chunk)
                    self._cv.notify_all()
        except BaseException as e:          # noqa: BLE001
            self.error = e
            print(f"{timestamp()} ОШИБКА записи на диск "
                  f"{os.path.basename(self.path)}: {e!r}", flush=True)
            log_error(f"disk write error {self.path}: {e!r}")
            # разбудить всех, кто ждёт места: буфер всё равно мёртв
            with self._cv:
                self._q.clear()
                self._depth = 0
                self._eof = True
                self._cv.notify_all()
        finally:
            try:
                self._f.close()
            except Exception:
                pass

    # ---- сторона продьюсера -------------------------------------------------

    @property
    def depth(self):
        return self._depth

    def _append(self, chunk):
        self._q.append(chunk)
        self._depth += len(chunk)
        self.queued += len(chunk)
        if self._depth > self.max_depth:
            self.max_depth = self._depth
        self._cv.notify()

    def feed_nowait(self, chunk) -> bool:
        """True — кусок в буфере, False — буфер полон (ждать придётся)."""
        if self.error:
            raise RuntimeError(f"поток записи мёртв: {self.error!r}")
        with self._cv:
            if self._eof:
                raise RuntimeError("поток записи закрыт")
            # кусок больше лимита всё равно проходит, если буфер пуст
            if self._depth and self._depth + len(chunk) > self.limit:
                self.stalls += 1
                return False
            self._append(chunk)
        return True

    def feed(self, chunk):
        """Блокирующий вариант — back-pressure, когда буфер забит."""
        started = now_ts()
        with self._cv:
            while (not self.error and not self._eof and self._depth
                   and self._depth + len(chunk) > self.limit):
                self._cv.wait(1.0)
            if self.error:
                raise RuntimeError(f"поток записи мёртв: {self.error!r}")
            if self._eof:
                raise RuntimeError("поток записи закрыт")
            self._append(chunk)
        self.stall_seconds += now_ts() - started

    def close(self, timeout=DRAIN_TIMEOUT) -> bool:
        """Дописать буфер и остановить поток. False — не успел за timeout."""
        with self._cv:
            self._eof = True
            self._cv.notify_all()
        self._t.join(timeout)
        return not self._t.is_alive()


# =============================================================================
#  streamlink
# =============================================================================

COMMON_OPTS = [
    "--twitch-low-latency",
    "--twitch-disable-ads",
    "--stream-segment-threads", "3",
    "--hls-live-restart",
    "--stream-segment-timeout", "15",
    "--stream-segment-attempts", "10",
]

def with_proxy(cmd, proxy, twitch_proxy_playlist):
    if proxy:
        cmd.insert(1, f"--http-proxy={proxy}")
    if twitch_proxy_playlist:
        cmd.insert(1, f"--twitch-proxy-playlist={twitch_proxy_playlist}")
    return cmd

def is_stream_live(author_name, quality="best", proxy=None, twitch_proxy_playlist=None):
    cmd = with_proxy([
        "streamlink", "--stream-url",
        "--twitch-disable-ads",
        f"https://www.twitch.tv/{author_name}", quality
    ], proxy, twitch_proxy_playlist)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return False


def pump(cmd, path, buffer_mb, log_file, author_name):
    """Одна запись: streamlink -> пайп -> буфер в ОЗУ -> поток диска.

    Возвращает (rc, writer, длительность в секундах).
    """
    writer = BufferedFileWriter(path, buffer_mb)
    started = now_ts()
    warned = False

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log_file, bufsize=0)
    try:
        while True:
            chunk = p.stdout.read(CHUNK)
            if not chunk:
                break
            try:
                if writer.feed_nowait(chunk):
                    continue
            except RuntimeError:
                # диск умер — тянуть поток дальше бессмысленно
                p.terminate()
                break
            if not warned:
                warned = True
                print(f"{timestamp()} {author_name}: буфер {buffer_mb} MiB "
                      f"заполнен — диск не успевает, ждём запись", flush=True)
            try:
                writer.feed(chunk)      # back-pressure, дальше уже ничем не помочь
            except RuntimeError:
                p.terminate()
                break
    except KeyboardInterrupt:
        print(f"{timestamp()} {author_name}: Ctrl+C — дописываю буфер на диск",
              flush=True)
        p.terminate()
        raise
    finally:
        try:
            p.stdout.close()
        except Exception:
            pass
        rc = p.wait()
        if not writer.close():
            print(f"{timestamp()} {author_name}: диск не дожевал "
                  f"{writer.depth / MIB:.1f} MiB за {DRAIN_TIMEOUT} c", flush=True)

    return rc, writer, now_ts() - started


def download(author_name, quality="best", proxy=None, twitch_proxy_playlist=None,
             buffer_mb=BUFFER_MB, outdir="."):
    uri = f"https://www.twitch.tv/{author_name}"
    log_filename = f"{author_name}_{strftime('%Y%m%d_%H-%M-%S', gmtime())}.log"

    while True:
        if not is_stream_live(author_name, quality, proxy, twitch_proxy_playlist):
            wait_time = int(uniform(MIN_WAIT, MAX_WAIT))
            print(f"{timestamp()} Stream is offline {author_name}. Waiting {wait_time} sec...")
            time.sleep(wait_time)
            continue

        current_time = timestamp()
        try:
            info_cmd = with_proxy(
                ["streamlink", "--json"] + COMMON_OPTS + [uri, quality],
                proxy, twitch_proxy_playlist)

            info_result = subprocess.run(info_cmd, capture_output=True, text=True, timeout=10)
            if info_result.returncode != 0:
                raise subprocess.CalledProcessError(info_result.returncode, info_cmd)

            stream_info = json.loads(info_result.stdout)
            meta = stream_info.get('metadata', {}) or {}
            original_title = meta.get('title', 'no_title')
            clean_title = sanitize_filename_windows(original_title)
            stream_id = str(meta.get('id') or '')

            # имя файла собираем сами: streamlink пишет в --stdout и своих
            # плейсхолдеров ({time}, {id}) больше не подставляет
            stamp = strftime('%Y%m%d %H-%M-%S', gmtime())
            suffix = f"[{stream_id}]" if stream_id else ""
            path = os.path.join(
                outdir,
                f"{stamp} [{author_name}] {clean_title} [{quality}]{suffix}.ts")

            cmd = with_proxy(
                ["streamlink", "--stdout"] + COMMON_OPTS + [uri, quality],
                proxy, twitch_proxy_playlist)

            print(f"{current_time} LIVE {author_name}. Recording: {clean_title} "
                  f"(буфер {buffer_mb} MiB)")

            with open(log_filename, "a", encoding='utf-8') as log_file:
                log_file.write(f"{current_time} Starting recording for {author_name} ({clean_title})\n")
                log_file.write(f"{current_time} -> {path}\n")
                log_file.flush()

                rc, writer, elapsed = pump(cmd, path, buffer_mb, log_file, author_name)

                stats = (f"{writer.written / MIB:.1f} MiB за {elapsed / 60:.1f} мин, "
                         f"пик буфера {writer.max_depth / MIB:.1f} MiB, "
                         f"залипаний диска {writer.stalls} "
                         f"({writer.stall_seconds:.1f} c), streamlink rc={rc}")
                print(f"{timestamp()} {author_name}: готово — {stats}")
                log_file.write(f"{timestamp()} Finished recording: {stats}\n\n")

            if writer.stalls:
                print(f"{timestamp()} {author_name}: диск не справляется — "
                      f"увеличьте --buffer-mb или смените носитель")
            if writer.error:
                log_error(f"{author_name} — disk writer died: {writer.error!r}")

            # пустой огрызок на диске не нужен
            if writer.written == 0 and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

        except json.JSONDecodeError as e:
            log_error(f"{author_name} — ERROR parsing stream info: {str(e)}")
        except subprocess.TimeoutExpired:
            log_error(f"{author_name} — Timeout while checking stream info")
        except subprocess.CalledProcessError as e:
            log_error(f"{author_name} — ERROR streamlink: {str(e)}")
        except KeyboardInterrupt:
            print(f"{timestamp()} Stopped by User {author_name}.")
            return
        except Exception as e:
            log_error(f"{author_name} — Unexpected error: {str(e)}")

        wait_time = int(uniform(MIN_WAIT, MAX_WAIT))
        print(f"{timestamp()} Stream ended or Error {author_name}. Restart after {wait_time} sec...\n")
        try:
            time.sleep(wait_time)
        except KeyboardInterrupt:
            print(f"{timestamp()} Stopped by User {author_name}.")
            return

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 save_livestream_parallel-proxy+TTV.py "
              "[--proxy http://IP:PORT] [--twitch-proxy-playlist=URL] "
              "[--buffer-mb 512] [--outdir DIR] <streamer1> ...")
        sys.exit(1)

    proxy = None
    twitch_proxy_playlist = None
    buffer_mb = BUFFER_MB
    outdir = "."
    streamers = []

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--proxy" and i + 1 < len(args):
            proxy = args[i + 1]
            i += 2
        elif args[i].startswith("--twitch-proxy-playlist="):
            twitch_proxy_playlist = args[i].split("=", 1)[1]
            i += 1
        elif args[i] == "--buffer-mb" and i + 1 < len(args):
            buffer_mb = int(args[i + 1])
            i += 2
        elif args[i].startswith("--buffer-mb="):
            buffer_mb = int(args[i].split("=", 1)[1])
            i += 1
        elif args[i] == "--outdir" and i + 1 < len(args):
            outdir = args[i + 1]
            i += 2
        else:
            streamers.append(args[i])
            i += 1

    if not streamers:
        print("No streamers provided.")
        sys.exit(1)

    os.makedirs(outdir, exist_ok=True)
    print(f"{timestamp()} Буфер {buffer_mb} MiB на стримера | "
          f"Out: {os.path.abspath(outdir)} | Streamers: {', '.join(streamers)}")

    processes = []
    for name in streamers:
        p = Process(target=download,
                    args=(name, "best", proxy, twitch_proxy_playlist,
                          buffer_mb, outdir))
        p.start()
        processes.append(p)

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\nStop all processes...")
        for p in processes:
            p.terminate()

if __name__ == '__main__':
    main()
