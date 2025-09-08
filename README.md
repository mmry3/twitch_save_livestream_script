# livestream_scripts

Collection of scripts for archiving and organizing livestreams (mainly for r/EDMLivestreams).
These should work on Windows, Linux and macOS.

### save_livestream.py

Takes a base filename and twitch link, which can be either a link to a twitch channel or specific video. Automatically adds timestamps to the filename. Loops endlessly and can be left alone to watch for upcoming streams.

Requires `streamlink` and `python3` to be installed and in your respective PATH environment variable.

Example:
```
$ python3 save_livestream.py "Insomniac" https://twitch.tv/insomniac

Downloading from Twitch!
Downloading stream...
URL: https://www.twitch.tv/insomniac
Filename : insomniac - 2020-11-04 06-38-04.ts
[cli][info] Found matching plugin twitch for URL https://www.twitch.tv/insomniac
[cli][info] Available streams: audio_only, 160p (worst), 360p, 480p, 720p, 1080p (best)
[cli][info] Opening stream: 1080p (hls)
[plugin.twitch][info] Will skip ad segments
[download][.. 2020-11-04 06-38-04.ts] Written 6.1 MB (10s @ 618.9 KB/s)

[...]
```


Usage:
```
$ python3 upload.py timestamps.txt
```

