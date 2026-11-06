#!/usr/bin/env python3
"""Download a video, extract frames, and transcribe its audio.

Usage:
    process_video.py <url-or-local-file> <out_dir> [--interval SECONDS]
                     [--max-frames N] [--model WHISPER_MODEL] [--language CODE]

Writes into <out_dir>:
    video.<ext>        the downloaded video (or a copy of the local file)
    audio.wav          16 kHz mono audio
    frames/frame_XXXX.jpg   frames, one every --interval seconds
    transcript.txt     plain transcript
    transcript.md      timestamped transcript (with matching frame names)
    metadata.json      title, uploader, duration, url, description
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def need(tool):
    if shutil.which(tool) is None:
        sys.exit(f"error: '{tool}' not found. Install it first (see SKILL.md).")


def run(cmd):
    subprocess.run(cmd, check=True)


def fmt(t):
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


def download(src, out):
    p = Path(src).expanduser()
    if p.exists():
        dest = out / f"video{p.suffix or '.mp4'}"
        shutil.copy(p, dest)
        return dest, {"title": p.stem, "source": str(p)}
    need("yt-dlp")
    run([
        "yt-dlp", "--no-playlist", "--write-info-json",
        "-f", "bv*[height<=720]+ba/b[height<=720]/b",
        "--merge-output-format", "mp4",
        "-o", str(out / "video.%(ext)s"), src,
    ])
    vids = [f for f in out.glob("video.*") if f.suffix not in (".json", ".part")]
    if not vids:
        sys.exit("error: download produced no video file")
    meta = {"source": src}
    info = out / "video.info.json"
    if info.exists():
        d = json.loads(info.read_text())
        for k in ("title", "uploader", "duration", "upload_date", "description", "webpage_url"):
            if d.get(k) is not None:
                meta[k] = d[k]
        info.unlink()
    return vids[0], meta


def duration(video):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("out_dir")
    ap.add_argument("--interval", type=float, default=0,
                    help="seconds between frames (default: auto, ~40 frames total)")
    ap.add_argument("--max-frames", type=int, default=60)
    ap.add_argument("--model", default="base",
                    help="faster-whisper model: tiny, base, small, medium, large-v3")
    ap.add_argument("--language", default=None, help="e.g. en, de (default: auto-detect)")
    a = ap.parse_args()

    need("ffmpeg")
    need("ffprobe")
    out = Path(a.out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    print("==> Downloading", flush=True)
    video, meta = download(a.source, out)
    dur = duration(video)
    meta["duration"] = meta.get("duration") or dur

    interval = a.interval or max(2.0, dur / 40) if dur else (a.interval or 10.0)
    if dur and dur / interval > a.max_frames:
        interval = dur / a.max_frames
    meta["frame_interval_seconds"] = round(interval, 2)

    print(f"==> Extracting frames every {interval:.1f}s", flush=True)
    frames = out / "frames"
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir()
    run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video),
         "-vf", f"fps=1/{interval},scale='min(1280,iw)':-2",
         "-q:v", "3", str(frames / "frame_%04d.jpg")])
    frame_files = sorted(frames.glob("*.jpg"))
    meta["frame_count"] = len(frame_files)

    print("==> Extracting audio", flush=True)
    audio = out / "audio.wav"
    has_audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True).stdout.strip()
    segments = []
    if has_audio:
        run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(video),
             "-vn", "-ac", "1", "-ar", "16000", str(audio)])
        print(f"==> Transcribing with faster-whisper ({a.model})", flush=True)
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            sys.exit("error: faster-whisper missing. Run: pip install faster-whisper")
        try:
            model = WhisperModel(a.model, device="auto", compute_type="auto")
            segs, info = model.transcribe(str(audio), language=a.language, vad_filter=True)
            meta["language"] = info.language
            segments = [(s.start, s.end, s.text.strip()) for s in segs]
        except Exception as exc:  # e.g. model download blocked/offline
            meta["transcription_error"] = f"{type(exc).__name__}: {exc}"
            print(f"   WARNING: transcription failed: {meta['transcription_error']}",
                  file=sys.stderr)
    else:
        print("   (no audio track; skipping transcription)")

    (out / "transcript.txt").write_text(" ".join(t for _, _, t in segments) + "\n")
    lines = [f"# Transcript: {meta.get('title', '')}", ""]
    for s, e, t in segments:
        idx = int(s // interval) + 1
        frame = f"frame_{idx:04d}.jpg" if idx <= len(frame_files) else ""
        lines.append(f"- [{fmt(s)}–{fmt(e)}] ({frame}) {t}")
    (out / "transcript.md").write_text("\n".join(lines) + "\n")
    (out / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    print(f"\nDone. Output in {out}")
    print(f"  frames: {len(frame_files)}  transcript segments: {len(segments)}")


if __name__ == "__main__":
    main()
