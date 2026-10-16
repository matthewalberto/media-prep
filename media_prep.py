"""Get a recording ready for upload: even out the loudness, fit it under a size limit, check the peaks.

Needs ffmpeg and ffprobe on PATH.

  python media_prep.py check     <in.mp4> [--max-mb 10] [--max-tp -1]
  python media_prep.py normalize <in.mp4> [--out <path>] [--lufs -14] [--tp -2]
  python media_prep.py fit       <in.mp4> --max-mb 10 [--out <path>] [--height 720]

check:     prints duration, size, streams, integrated loudness and true peak. With --max-mb or
           --max-tp it exits 1 if the file breaks either limit, so you can use it in a script.
normalize: two-pass ffmpeg loudnorm (linear mode) with a 70 Hz high-pass to cut rumble. The
           video stream is copied, so picture quality doesn't change. -14 LUFS is the usual
           target for online video.
fit:       two-pass libx264 at the bitrate that lands near 93% of the limit, reading "10 MB"
           as 10,000,000 bytes (the stricter reading). Audio is 128k AAC, dual-mono stereo,
           behind a -2 dB limiter. It re-encodes about 8% smaller until the file fits, then
           re-measures the true peak. Loudness is kept, so run normalize first.

The input file is never changed or deleted.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

AUDIO_CONTAINERS = {".mp4", ".m4a", ".mov", ".mkv"}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"{cmd[0]} failed ({r.returncode}):\n{r.stderr[-2000:]}")
    return r


def probe(path):
    """ffprobe streams and format as a dict."""
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height,bit_rate,channels:format=duration,size",
             "-of", "json", path])
    return json.loads(r.stdout)


def has_stream(info, kind):
    return any(s.get("codec_type") == kind for s in info.get("streams", []))


def duration(path):
    return float(probe(path)["format"]["duration"])


def loudness(path, extra_filter=""):
    """First-pass loudnorm measurement: input_i (LUFS), input_tp, input_lra, input_thresh, target_offset."""
    af = (extra_filter + "," if extra_filter else "") + "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json"
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", af, "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        sys.exit("could not read a loudness measurement from ffmpeg")
    return json.loads(m.group(0))


def true_peak(path):
    """EBU R128 true peak in dBTP. Trust this over loudnorm's own peak figure, which drifts."""
    r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af",
             "ebur128=peak=true:framelog=quiet", "-f", "null", "-"])
    vals = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", r.stderr)
    if not vals:
        sys.exit("could not read a true peak from ffmpeg")
    return float("-inf") if vals[-1] == "-inf" else float(vals[-1])


def default_out(path, suffix, audio_only=False):
    base, ext = os.path.splitext(path)
    if audio_only and ext.lower() not in AUDIO_CONTAINERS:
        ext = ".m4a"
    return f"{base} ({suffix}){ext}"


def video_bitrate_k(cap_bytes, seconds, audio_k=128, target=0.93):
    """Video bitrate (kbit/s) that puts the whole file at `target` of the cap."""
    return int(cap_bytes * target * 8 / seconds / 1000) - audio_k


def normalize(a):
    info = probe(a.input)
    if not has_stream(info, "audio"):
        sys.exit("no audio stream to normalize")
    video = has_stream(info, "video")
    out = a.out or default_out(a.input, "normalized", audio_only=not video)
    m = loudness(a.input, "highpass=f=70")
    print(f"before: {m['input_i']} LUFS, true peak {m['input_tp']} dBTP")
    ln = (f"loudnorm=I={a.lufs}:TP={a.tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
          f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}"
          ":linear=true")
    maps = ["-map", "0:v:0", "-c:v", "copy"] if video else ["-vn"]
    run(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", a.input, *maps, "-map", "0:a:0",
         "-af", f"highpass=f=70,{ln},aresample=48000", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", out])
    after = loudness(out)
    print(f"after:  {after['input_i']} LUFS, true peak {true_peak(out)} dBTP -> {out}")
    return out


def fit(a):
    info = probe(a.input)
    if not has_stream(info, "video"):
        sys.exit("fit is for video files; this input has no video stream")
    audio = has_stream(info, "audio")
    out = a.out or default_out(a.input, f"under {a.max_mb:g}MB")
    cap = int(a.max_mb * 1_000_000)
    dur = float(info["format"]["duration"])
    audio_k = 128 if audio else 0
    video_k = video_bitrate_k(cap, dur, audio_k)
    vf = f"scale=-2:{a.height}:flags=lanczos"
    # Downmix with pan BEFORE the limiter. Downmixing after it (-ac 1) can push peaks back over.
    audio_args = (["-af", "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,alimiter=limit=0.79:level=false",
                   "-c:a", "aac", "-b:a", f"{audio_k}k"] if audio else ["-an"])
    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, "x264")
        for attempt in range(4):
            if video_k < 150:
                sys.exit(f"video bitrate would drop to {video_k}k, which is too low. "
                         "Shorten the video or lower --height.")
            common = ["-vf", vf, "-c:v", "libx264", "-preset", a.preset, "-b:v", f"{video_k}k",
                      "-passlogfile", log]
            run(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", a.input, *common, "-pass", "1", "-an",
                 "-f", "mp4", os.devnull])
            run(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", a.input, *common, "-pass", "2",
                 "-maxrate", f"{int(video_k * 1.6)}k", "-bufsize", f"{int(video_k * 3.2)}k",
                 "-profile:v", "high", "-pix_fmt", "yuv420p", *audio_args, "-movflags", "+faststart", out])
            size = os.path.getsize(out)
            print(f"try {attempt + 1}: {video_k}k video -> {size:,} bytes ({size / cap:.1%} of {cap:,})")
            if size <= cap * 0.96:
                if audio:
                    tp = true_peak(out)
                    warn = ", WARNING: above -0.5 dBTP, may clip" if tp > -0.5 else ""
                    print(f"OK -> {out} (true peak {tp} dBTP{warn})")
                else:
                    print(f"OK -> {out}")
                return out
            video_k = int(video_k * 0.92)
    sys.exit("could not fit after 4 tries")


def check(a):
    info = probe(a.input)
    size = int(info["format"]["size"])
    print(f"duration: {float(info['format']['duration']):.1f} s, size: {size:,} bytes")
    for s in info.get("streams", []):
        print("stream: " + ", ".join(f"{k}={v}" for k, v in s.items()))
    ok = True
    if a.max_mb is not None:
        cap = int(a.max_mb * 1_000_000)
        fits = size <= cap
        ok &= fits
        print(f"size limit {a.max_mb:g} MB ({cap:,} bytes): {'OK' if fits else 'TOO BIG'}")
    if has_stream(info, "audio"):
        m = loudness(a.input)
        tp = true_peak(a.input)
        print(f"loudness: {m['input_i']} LUFS, LRA {m['input_lra']} LU, true peak {tp} dBTP")
        if a.max_tp is not None:
            under = tp <= a.max_tp
            ok &= under
            print(f"true peak limit {a.max_tp:g} dBTP: {'OK' if under else 'OVER'}")
    else:
        print("no audio stream")
    return 0 if ok else 1


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("normalize", help="even out loudness; video is copied untouched")
    n.add_argument("input")
    n.add_argument("--out")
    n.add_argument("--lufs", type=float, default=-14, help="target integrated loudness (default -14)")
    n.add_argument("--tp", type=float, default=-2.0, help="true peak ceiling in dBTP (default -2)")
    f = sub.add_parser("fit", help="re-encode a video to fit under a size limit")
    f.add_argument("input")
    f.add_argument("--out")
    f.add_argument("--max-mb", type=float, required=True, help="limit in decimal MB (10 = 10,000,000 bytes)")
    f.add_argument("--height", type=int, default=720, help="output height in pixels (default 720)")
    f.add_argument("--preset", default="slower", help="x264 preset (default slower; 'fast' for quick tests)")
    c = sub.add_parser("check", help="report size, loudness and true peak")
    c.add_argument("input")
    c.add_argument("--max-mb", type=float, help="exit 1 if the file is bigger than this (decimal MB)")
    c.add_argument("--max-tp", type=float, help="exit 1 if the true peak is above this (dBTP)")
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.cmd == "check":
        return check(a)
    {"normalize": normalize, "fit": fit}[a.cmd](a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
