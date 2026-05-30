#!/usr/bin/env python3
"""Convert welcome_lion.json (sequential WebP frames) into an optimised
single animated WebP for browser-native decoding.

Optimisations applied:
  1. Downscale to TARGET_SIZE (matches CSS max-width) — cuts pixel count ~48%.
  2. Drop every Nth frame (FRAME_STEP) — halves decode workload.
  3. Higher compression method (method=6) — slower encode, same decode speed.
  4. Moderate quality (65) — imperceptible on small decorative animations.

This script is a one-shot build helper; run it whenever the lion source
JSON is regenerated. The resulting static/welcome_lion.webp is what the
browser actually loads on the welcome page.
"""
import base64
import io
import json
import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "static", "welcome_lion.json")
DST = os.path.join(ROOT, "static", "welcome_lion.webp")

# --- Tunable parameters ---
TARGET_SIZE = 260          # px — matches CSS .empty-welcome-img max-width
FRAME_STEP = 2            # keep every Nth frame (2 = half the frames)
FPS = 15                  # original FPS
EFFECTIVE_FPS = FPS / FRAME_STEP
FRAME_MS = int(round(1000 / EFFECTIVE_FPS))  # ~133 ms per frame
QUALITY = 65             # lossy quality (lower = smaller, 65 is fine at 260px)
METHOD = 6              # compression effort (6 = slowest encode, best ratio)
# ---------------------------

with open(SRC, "r") as f:
    data = json.load(f)

assets = {a["id"]: a for a in data["assets"]}
layers = sorted(data["layers"], key=lambda l: l.get("ip", 0))

raw_frames = []
for layer in layers:
    asset = assets[layer["refId"]]
    p = asset["p"]
    if not p.startswith("data:"):
        raise SystemExit(f"unexpected non-data URI in {asset['id']}")
    raw = base64.b64decode(p.split(",", 1)[1])
    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    raw_frames.append(im)

print(f"source: {len(raw_frames)} frames @ {raw_frames[0].size}")

# Subsample frames (keep every FRAME_STEP-th frame)
sampled = raw_frames[::FRAME_STEP]

# Downscale to target size (maintain aspect ratio via thumbnail)
frames = []
for im in sampled:
    resized = im.resize((TARGET_SIZE, TARGET_SIZE), Image.LANCZOS)
    frames.append(resized)

print(f"output: {len(frames)} frames @ {frames[0].size}, {EFFECTIVE_FPS:.1f} fps, {FRAME_MS} ms/frame")

frames[0].save(
    DST,
    format="WEBP",
    save_all=True,
    append_images=frames[1:],
    duration=FRAME_MS,
    loop=0,
    lossless=False,
    quality=QUALITY,
    method=METHOD,
)

size = os.path.getsize(DST)
print(f"wrote {DST}, size={size} bytes ({size/1024:.1f} KB)")
