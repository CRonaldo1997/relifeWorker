#!/usr/bin/env python3
"""Make Lottie embedded raster frames transparent by knocking out a flat
background color via 4-corner flood fill.

Many "Lottie" files exported from After Effects with raster compositions are
actually a sequence of opaque WebP/PNG frames embedded as base64. When the
source frames have a flat background (e.g. a baked-in grey rectangle), the
animation renders with that rectangle behind the artwork. This tool fixes
that in-place:

  1. For each frame in `assets[*].p` (data: URLs only):
       a. Decode the embedded image to RGBA.
       b. Sample the background color from the 4 corners (mean RGB).
       c. Build a "candidate background" mask: pixels whose Chebyshev
          distance to the sampled colour is <= --tolerance.
       d. Flood-fill that mask from the 4 corners so only the background
          *connected to the borders* is keyed out. This preserves any
          background-coloured pixels that happen to be inside the artwork.
       e. Feather the resulting alpha edge by --feather pixels.
       f. Re-encode (WebP lossy q=90 by default; PNG also supported) and
          rewrite the base64 payload.
  2. Write the modified JSON back to disk. A `.bak` copy of the original
     is created automatically the first time you run the tool on a file.

Usage:
  python3 lottie_alpha_keyer.py path/to/anim.json
  python3 lottie_alpha_keyer.py path/to/anim.json --tolerance 18 --feather 1.5
  python3 lottie_alpha_keyer.py path/to/anim.json --format png --output out.json

Tips:
  * --tolerance: increase if grey speckles remain after a run (try 20-28).
    Decrease if the artwork's own colours get eaten (try 8-12).
  * --feather:   higher = smoother alpha edge (default 1.5 px Gaussian).
                 set 0 for a hard edge.
  * --bg "r,g,b": override auto-sampled background colour (e.g. "226,226,226").

Requires: Pillow (with WebP support). No numpy needed.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter


DATA_URL_RE = re.compile(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.*)$", re.DOTALL)


def sample_bg(im: Image.Image, inset: int = 2) -> Tuple[int, int, int]:
    """Return the mean RGB of the 4 corner pixels (with a tiny inset to
    avoid sub-pixel sampling artefacts on the very edge)."""
    rgb = im.convert("RGB")
    w, h = rgb.size
    pts = [
        (inset, inset),
        (w - 1 - inset, inset),
        (inset, h - 1 - inset),
        (w - 1 - inset, h - 1 - inset),
    ]
    rs, gs, bs = 0, 0, 0
    for x, y in pts:
        r, g, b = rgb.getpixel((x, y))
        rs += r
        gs += g
        bs += b
    n = len(pts)
    return rs // n, gs // n, bs // n


def keyout(
    im: Image.Image,
    bg: Tuple[int, int, int],
    tolerance: int,
    feather: float,
) -> Image.Image:
    """Return an RGBA copy of `im` where pixels connected to the border and
    within `tolerance` (Chebyshev) of `bg` are made transparent.

    The original alpha channel (if any) is preserved as an upper bound."""
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    w, h = im.size

    # Chebyshev distance to bg, per-channel, reduced to max channel.
    bg_im = Image.new("RGB", (w, h), bg)
    diff = ImageChops.difference(im.convert("RGB"), bg_im)
    r, g, b = diff.split()
    max_diff = ImageChops.lighter(ImageChops.lighter(r, g), b)

    # Candidate background mask: 255 where pixel is close to bg, else 0.
    cand = max_diff.point(lambda v: 255 if v <= tolerance else 0, mode="L")

    # Flood-fill from each corner. We mark the filled area with value 128 so
    # we can extract only the border-connected background and not isolated
    # bg-coloured speckles inside the artwork.
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    for sx, sy in seeds:
        if cand.getpixel((sx, sy)) == 255:
            ImageDraw.floodfill(cand, (sx, sy), 128, thresh=0)
    bg_mask = cand.point(lambda v: 255 if v == 128 else 0, mode="L")

    if feather > 0:
        bg_mask = bg_mask.filter(ImageFilter.GaussianBlur(feather))

    # New alpha = min(original alpha, 255 - bg_mask)
    r2, g2, b2, a = im.split()
    inv = bg_mask.point(lambda v: 255 - v, mode="L")
    new_alpha = ImageChops.darker(a, inv)
    return Image.merge("RGBA", (r2, g2, b2, new_alpha))


def encode(im: Image.Image, fmt: str, quality: int, alpha_quality: int, lossless: bool) -> bytes:
    buf = io.BytesIO()
    fmt = fmt.lower()
    if fmt == "webp":
        if lossless:
            im.save(buf, format="WEBP", lossless=True, method=6)
        else:
            # method=6 = best compression effort; alpha_quality lets us trim
            # the alpha channel separately from the RGB channels.
            im.save(
                buf,
                format="WEBP",
                quality=quality,
                alpha_quality=alpha_quality,
                method=6,
            )
    elif fmt == "png":
        im.save(buf, format="PNG", optimize=True)
    else:
        raise ValueError(f"unsupported output format: {fmt}")
    return buf.getvalue()


def parse_bg_arg(s: Optional[str]) -> Optional[Tuple[int, int, int]]:
    if not s:
        return None
    parts = [int(p.strip()) for p in s.split(",")]
    if len(parts) != 3 or not all(0 <= p <= 255 for p in parts):
        raise argparse.ArgumentTypeError("--bg must be 'r,g,b' with 0..255")
    return tuple(parts)  # type: ignore[return-value]


def process(
    in_path: Path,
    out_path: Path,
    *,
    tolerance: int,
    feather: float,
    fmt: str,
    quality: int,
    alpha_quality: int,
    lossless: bool,
    bg_override: Optional[Tuple[int, int, int]],
    dry_run: bool,
    force_rekey: bool,
) -> None:
    data = json.loads(in_path.read_text(encoding="utf-8"))
    assets = data.get("assets") or []
    targets = []
    for idx, a in enumerate(assets):
        p = a.get("p")
        if isinstance(p, str):
            m = DATA_URL_RE.match(p)
            if m:
                targets.append((idx, a, m.group(1).lower(), m.group(2)))

    if not targets:
        print(f"[!] No embedded image assets (data:image/*;base64,...) found in {in_path}")
        return

    # Safety: detect a file that already has transparency. Re-keying an already
    # keyed file is wasteful and slowly degrades quality on each pass.
    if not force_rekey:
        try:
            _idx0, _a0, _fmt0, _b640 = targets[0]
            _probe = Image.open(io.BytesIO(base64.b64decode(_b640)))
            if _probe.mode in ("RGBA", "LA") or _probe.info.get("transparency") is not None:
                _rgba = _probe.convert("RGBA")
                _alpha = _rgba.split()[-1]
                _lo, _hi = _alpha.getextrema()
                if _lo < 255:
                    print(
                        f"[!] {in_path.name} already contains transparent pixels "
                        f"(frame 0 alpha range {_lo}..{_hi}). Re-keying may degrade quality.\n"
                        f"    Pass --force-rekey to proceed anyway, or restore from .bak first."
                    )
                    return
        except Exception:
            pass

    q_desc = "lossless" if lossless and fmt == "webp" else f"q={quality}/a={alpha_quality}"
    print(
        f"[+] {in_path.name}: {len(targets)} embedded frame(s) "
        f"(tolerance={tolerance}, feather={feather}, format={fmt}, {q_desc})"
    )

    total_before = 0
    total_after = 0
    sampled_bg: Optional[Tuple[int, int, int]] = None
    t0 = time.time()

    for n, (idx, asset, src_fmt, b64) in enumerate(targets, 1):
        raw = base64.b64decode(b64)
        total_before += len(raw)
        im = Image.open(io.BytesIO(raw))
        # Sample the bg once from frame 0 so all frames are keyed with the
        # same colour. Embedded image-sequence Lotties share a background.
        if sampled_bg is None:
            sampled_bg = bg_override or sample_bg(im)
            print(f"    background colour: rgb{sampled_bg}")
        out_im = keyout(im, sampled_bg, tolerance, feather)
        out_bytes = encode(out_im, fmt, quality, alpha_quality, lossless)
        total_after += len(out_bytes)
        asset["p"] = f"data:image/{fmt};base64," + base64.b64encode(out_bytes).decode("ascii")
        if n % 20 == 0 or n == len(targets):
            print(f"    {n}/{len(targets)} frames  ({time.time()-t0:.1f}s elapsed)")

    if dry_run:
        print("[i] --dry-run: not writing output")
        return

    out_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    elapsed = time.time() - t0
    print(
        f"[OK] wrote {out_path}  "
        f"({out_path.stat().st_size/1024:.1f} KB) in {elapsed:.1f}s"
    )
    print(
        f"     embedded image bytes: {total_before/1024:.1f} KB -> "
        f"{total_after/1024:.1f} KB"
    )


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Knock out a flat background color from Lottie embedded frames."
    )
    ap.add_argument("input", type=Path, help="Path to the Lottie .json file")
    ap.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output .json path (default: overwrite input in place)",
    )
    ap.add_argument(
        "--tolerance",
        type=int,
        default=16,
        help="Per-channel max distance from bg colour to count as background (default: 16)",
    )
    ap.add_argument(
        "--feather",
        type=float,
        default=1.5,
        help="Gaussian blur radius applied to alpha edge in px (default: 1.5; 0 = hard edge)",
    )
    ap.add_argument(
        "--format",
        choices=["webp", "png"],
        default="webp",
        help="Re-encode each frame as this format (default: webp)",
    )
    ap.add_argument(
        "--quality",
        type=int,
        default=80,
        help="WebP quality 1..100 (ignored for PNG/--lossless; default: 80)",
    )
    ap.add_argument(
        "--alpha-quality",
        type=int,
        default=80,
        help="WebP alpha channel quality 1..100 (default: 80)",
    )
    ap.add_argument(
        "--lossless",
        action="store_true",
        help="Use lossless WebP (larger; ignores --quality/--alpha-quality)",
    )
    ap.add_argument(
        "--bg",
        type=parse_bg_arg,
        default=None,
        help="Override auto-sampled background colour, e.g. '226,226,226'",
    )
    ap.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip writing <input>.bak before overwriting",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Process frames but do not write the output JSON",
    )
    ap.add_argument(
        "--force-rekey",
        action="store_true",
        help="Process the file even if its frames already have transparent pixels",
    )
    args = ap.parse_args(argv)

    in_path: Path = args.input
    if not in_path.is_file():
        print(f"error: input not found: {in_path}", file=sys.stderr)
        return 2

    out_path: Path = args.output or in_path
    if out_path == in_path and not args.no_backup and not args.dry_run:
        bak = in_path.with_suffix(in_path.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(in_path, bak)
            print(f"[i] backup -> {bak}")
        else:
            print(f"[i] backup already exists ({bak}); leaving as-is")

    process(
        in_path,
        out_path,
        tolerance=args.tolerance,
        feather=args.feather,
        fmt=args.format,
        quality=args.quality,
        alpha_quality=args.alpha_quality,
        lossless=args.lossless,
        bg_override=args.bg,
        dry_run=args.dry_run,
        force_rekey=args.force_rekey,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
