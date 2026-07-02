# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A homelab satellite image gallery for METEOR-M2 weather satellites received at 137.900 MHz in Barcelona using an RTL-SDR V3. Raw signals are decoded with SatDump v2.0.0-alpha, producing per-pass directories of PNG images that are then served via a single-page `index.html`.

## Key commands

**Rebuild passes.json** (after adding/removing decoded directories):
```bash
bash rebuild-passes.sh
```

**Add map overlay to an image** (coastlines + borders drawn via Cartopy using TLE from product.cbor):
```bash
python3 add_map_overlay.py decoded_YYYYMMDDHHMMSS/MSU-MR/product.cbor \
    decoded_YYYYMMDDHHMMSS/MSU-MR/msu_mr_rgb_MSA_corrected.png \
    decoded_YYYYMMDDHHMMSS/MSU-MR/msu_mr_rgb_MSA_corrected_map.png
```

## Architecture

**`passes.json`** is the central data file. It is a flat JSON array consumed by `index.html`. Each entry has: `date`, `time`, `satellite`, `folder` (relative path to decoded dir), `imgs` (PNG filenames inside `folder/MSU-MR/`), `maxEl`, `frequency`, `gain`, `direction` (`StoN` or `NtoS`, inferred from pass time).

**`rebuild-passes.sh`** scans all `decoded_*` directories, reads `meta.json` from each (falls back to filesystem timestamps), lists PNGs in `MSU-MR/`, and writes `passes.json`. Run this whenever decoded directories are added, renamed, or removed.

**`decoded_YYYYMMDDHHMMSS/`** — one directory per satellite pass, produced by SatDump. Structure:
- `meta.json` — satellite name, maxEl, frequency, gain, date, time, direction
- `MSU-MR/` — PNGs and `product.cbor` (SatDump's CBOR projection metadata with TLE + per-row timestamps)
- `MSU-MR (Filled)/` — gap-filled variant from SatDump
- `meteor_m2-x_lrpt.cadu`, `dataset.json`, `telemetry.json` — SatDump outputs

**`index.html`** — entirely self-contained, no build step. Fetches `passes.json` at load time and renders the gallery. Key logic:
- `shouldShow(imgName)` — filters which images appear: projected composites (`rgb_*`), corrected composites (`*_corrected` without `_map`), and map overlays (`*_map` for `msu_mr_false_color_map.png`). Raw channels and uncalibrated images are hidden.
- `needsFlip(imgName, pass)` — non-projected images from NtoS passes are CSS-flipped (`scale(-1,-1)`) so north is always up.
- `CHANNEL_LABELS` — human-readable names for known composite filenames.
- Next-pass predictions and recording status are fetched live from `https://n8n.yannklein.dev/webhook/next-passes` and `/satdump-status`.

**`add_map_overlay.py`** — reads `product.cbor` with `cbor2`, extracts TLE lines and per-row timestamps, propagates the orbit with `sgp4` to compute geographic bounds, then uses Cartopy + Matplotlib to composite coastlines/borders/cities over the PNG.

## Image filtering logic

The gallery only shows three categories of images (controlled by `shouldShow()` in `index.html`):
1. `rgb_*` — geo-projected composites (always shown, never flipped)
2. `*_corrected.png` (without `_map`) — corrected AVHRR/MSA composites
3. `msu_mr_false_color_map.png` — legacy map overlay (kept for backward compat)

`_map` variants of corrected images (e.g. `msu_mr_rgb_MSA_corrected_map.png`) are produced by `add_map_overlay.py` but are intentionally excluded from the gallery by `shouldShow()`.

## Dependencies (Python)

`cbor2`, `numpy`, `Pillow`, `sgp4`, `cartopy`, `matplotlib`
