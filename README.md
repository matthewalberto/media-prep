# media-prep

Get a recording ready to upload. Three commands:

## Why it matters

- **The problem:** a video that's too big gets rejected by the form, and one that's too quiet or clips sounds careless to the panel watching it, usually right on a deadline.
- **What this saves:** three commands with the settings already worked out, and a check that reports the true peak before you press upload.
- **Where it came from:** built after one of my application videos went out with clipped audio.

- `normalize` evens out the loudness so a quiet phone recording plays at a normal level.
- `fit` shrinks a video to fit under a file-size limit, like "max 10 MB" on an application form.
- `check` reports the size, loudness and true peak, so you know it won't clip or bounce before you upload.

It's a thin, careful wrapper around ffmpeg. Your original file is never changed.

## Who it's for

- **Staff who record webinars, course videos or talks** and need them to sound consistent from one to the next.
- **Anyone uploading a video to a form with a size cap**: grant and program applications, job applications, conference submissions, learning platforms.
- **Developers** who want sensible ffmpeg settings for loudness and size targets without working them out again.

## What you need

- Python 3.8 or newer. No Python packages beyond the standard library.
- `ffmpeg` and `ffprobe` on your PATH. On Windows: `winget install ffmpeg` or `choco install ffmpeg`. On macOS: `brew install ffmpeg`. On Debian or Ubuntu: `sudo apt install ffmpeg`.

## Quick start

```
python media_prep.py check     "intro.mp4"
python media_prep.py normalize "intro.mp4"               # writes "intro (normalized).mp4"
python media_prep.py fit       "intro (normalized).mp4" --max-mb 10
                                                          # writes "intro (normalized) (under 10MB).mp4"
python media_prep.py check     "intro (normalized) (under 10MB).mp4" --max-mb 10 --max-tp -1
```

**Order matters: normalize first, then fit.** Normalize works on the full-quality file. Fit keeps the loudness you set.

## The commands

### normalize

Measures the audio, then corrects it in a second pass to -14 LUFS, the usual level for online video, with a true peak ceiling of -2 dBTP. It also cuts rumble below 70 Hz. The video is copied as it is, so the picture doesn't lose any quality.

```
python media_prep.py normalize "webinar.mp4" --lufs -16 --tp -1.5 --out "webinar-final.mp4"
```

Use `--lufs -16` for speech-only podcasts if you prefer. It also works on audio files (`.m4a`, `.wav`); the output is AAC in `.m4a` when the input container can't hold it.

### fit

Re-encodes the video with two-pass H.264 so the file lands just under the limit. It aims at about 93% of the cap, then checks the real size and tries again about 8% smaller if needed, up to four times.

```
python media_prep.py fit "talk.mp4" --max-mb 25 --height 1080
```

- `--max-mb` reads megabytes as decimal: 10 MB means 10,000,000 bytes. That's the stricter reading, so the file passes whichever way the site counts.
- `--height` defaults to 720. At low bitrates 720p looks sharper than 1080p.
- `--preset` defaults to `slower` for better quality per byte. Use `fast` for quick drafts.
- If the video is so long that the bitrate would fall under 150 kbit/s, it stops and tells you to shorten it or lower the height.

### check

```
python media_prep.py check "talk.mp4" --max-mb 10 --max-tp -1
```

Prints the duration, size, streams, integrated loudness, loudness range and true peak. With `--max-mb` or `--max-tp` it exits with code 1 if the file breaks a limit, so you can use it in a script or a checklist. Run it on the final file before you upload, and look at the true peak: anything above about -0.5 dBTP can clip on playback.

## Why these settings

These come from testing talking-head phone videos at low bitrates.

- **Audio stays at 128k AAC.** It's tempting to cut the audio bitrate to give the video more room. Don't. At 64k to 80k, ffmpeg's AAC encoder overshoots peaks, and a limited -2 dBFS signal can come out above 0 dBTP, which clips.
- **Downmix before the limiter, not after.** Mixing to mono after the limiter can add about 3 dB back to the peaks.
- **Output is dual-mono stereo, not mono.** A true mono file can measure about 3 LU quieter on some players and upload sites, so it plays quieter than you meant.
- **Measure true peak with the EBU R128 meter.** loudnorm's own peak figure drifts. `check` and `fit` use `ebur128` for the final number.
- **A low-pass filter doesn't fix peaks.** Cutting high frequencies made peaks worse in testing, not better.

## Tests

```
python -m unittest discover -s tests -v
```

The unit tests need nothing. The end-to-end tests generate a six-second test pattern with a quiet tone using ffmpeg, then normalize, fit and check it. They skip if ffmpeg isn't installed. No real recordings are used.

## License

MIT. See [LICENSE](LICENSE).

---

Built by [Matthew Alberto](https://github.com/matthewalberto). Need a version of this for your team or institution? Get in touch on [LinkedIn](https://www.linkedin.com/in/matthewalberto) or at [matthewalberto.com](https://matthewalberto.com).
