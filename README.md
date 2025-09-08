# save_livestream.py

Requires `streamlink` and `python3` to be installed and in your respective PATH environment variable.

Script always waiting for all(multiplie) start live stream. Have options - `proxy settings` and `twitch-proxy-playlist = TTV-LOL-PRO v1`.
For option `TTV-LOL-PRO v1` you need to patch streamlink - https://github.com/2bc4/streamlink-ttvlol 

Current wait time (in seconds):
* MIN_WAIT = 2
* MAX_WAIT = 11

Current streamlink options:
* --twitch-low-latency
* --twitch-disable-ads
* --stream-segment-threads=3
* --hls-live-restart
* --stream-segment-timeout=15
* --stream-segment-attempts=10

You can change it, see doc - https://streamlink.github.io/cli.html 

Example:
```
$ python3 save_livestream_parallel.py [--proxy http://IP:PORT] [--twitch-proxy-playlist=URL] <streamer1> <streamer2> ...

[2025-08-08 13:06:46] Stream is offline <streamer 1>.. Waiting 7 sec...
[2025-08-08 13:06:44] LIVE <streamer 2>. Recording: <NAME>
[2025-08-08 13:06:50] Stream is offline <streamer 3>.. Waiting 9 sec...

[...]
```

