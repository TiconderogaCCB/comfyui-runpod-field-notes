#!/usr/bin/env python3
"""check-output.py <file.mp4|file.png> [...]

Content gate for ComfyUI generation outputs. Born the night seven
consecutive LTX-2.3 jobs returned status "success" with pitch-black
frames - "job completed" is NOT "job produced content" (PITFALLS #1).

Verdict per file: PASS / BLACK / BAD. Exit code 1 if any non-PASS.

Method (dependency-free, works on pod and laptop wherever ffmpeg exists):
- mp4: ffprobe the container (frames/duration), extract first/middle/last
  frames as PNG, then judge PNG byte-size. A 768x448 solid-color PNG
  compresses to ~1KB; any real photographic frame is >30KB. Threshold
  15KB flags black/flat output.
- png: judged directly by the same size heuristic. (Threshold is
  calibrated for PNG; very small real JPEGs can false-flag - eyeball
  any BLACK verdict on a .jpg.)

This is a tripwire, not a quality metric - it catches the silent-garbage
failure mode only.
"""
import json
import os
import subprocess
import sys
import tempfile

THRESHOLD = 15_000  # bytes per extracted frame PNG


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=nb_frames,width,height", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True)
    return json.loads(out.stdout or "{}")


def check_mp4(path):
    info = probe(path)
    streams = info.get("streams") or [{}]
    nb = int(streams[0].get("nb_frames") or 0)
    dur = float(info.get("format", {}).get("duration") or 0)
    with tempfile.TemporaryDirectory() as td:
        sizes = []
        for tag, ts in [("first", 0.0), ("mid", dur / 2), ("last", max(dur - 0.2, 0))]:
            fp = os.path.join(td, tag + ".png")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", "%.2f" % ts, "-i", path,
                 "-frames:v", "1", fp], capture_output=True)
            sizes.append(os.path.getsize(fp) if os.path.exists(fp) else 0)
    if not any(sizes):
        return "BAD", "no frames decodable (frames=%d dur=%.2fs)" % (nb, dur)
    if max(sizes) < THRESHOLD:
        return "BLACK", "frames=%d dur=%.2fs frame-pngs=%s bytes (all < %d = flat/black)" % (
            nb, dur, sizes, THRESHOLD)
    return "PASS", "frames=%d dur=%.2fs frame-pngs=%s bytes" % (nb, dur, sizes)


def check_png(path):
    size = os.path.getsize(path)
    if size < THRESHOLD:
        return "BLACK", "%d bytes (< %d = flat/black)" % (size, THRESHOLD)
    return "PASS", "%d bytes" % size


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    bad = 0
    for path in sys.argv[1:]:
        if not os.path.exists(path):
            print("MISSING %s" % path)
            bad += 1
            continue
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            verdict, detail = check_png(path)
        else:
            verdict, detail = check_mp4(path)
        print("%-6s %s  (%s)" % (verdict, path, detail))
        if verdict != "PASS":
            bad += 1
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
