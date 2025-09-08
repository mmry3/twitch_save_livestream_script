# save_livestream.py

Requires `streamlink` and `python3` to be installed and in your respective PATH environment variable.

Script always waiting for all(multiplie) start live stream. Have options - `proxy settings` and `twitch-proxy-playlist = TTV-LOL-PRO v1`
For option `TTV-LOL-PRO v1` you need to patch streamlink - https://github.com/2bc4/streamlink-ttvlol

Current wait time:
* MIN_WAIT = 2
* MAX_WAIT = 11
You can change it!

Current streamlink options:
* --twitch-low-latency
* --twitch-disable-ads
* --stream-segment-threads=3
* --hls-live-restart
* --stream-segment-timeout=15
* --stream-segment-attempts=10

You can change, see docs - https://streamlink.github.io/cli.html 

Example:
```
$ python3 save_livestream_parallel.py [--proxy http://IP:PORT] [--twitch-proxy-playlist=URL] <streamer1> <streamer2> ...

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

