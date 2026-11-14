# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A homelab weather-satellite image gallery ("BCN ground station") for images received in Barcelona with an RTL-SDR V3: METEOR-M2 (polar, 137.900 MHz LRPT — most of this file) and ELEKTRO-L3 (geostationary, 1691 MHz LRIT — see the ELEKTRO-L3 paragraph below). Raw signals are decoded twice per pass by the n8n `Tracking` workflow: first a fast pass with SatDump v2.0.0-alpha (`/home/yannklein/SatDump/build`), then it's overwritten by the actual production decode with **SatDump v1.2.2** (`/home/yannklein/SatDump-1.2.2/build`), whose composite config (`/home/yannklein/SatDump-1.2.2/satdump_cfg.json`, instrument block `"msu_mr"`) is what determines every named composite (MSA, MCIR, AVHRR_221/3a21, "MSU-MR 124") you actually see in a decoded directory. The resulting per-pass directories of PNG images are then served via a single-page `index.html`.

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

See `docs/SIGNAL-CHAIN.md` for the full pipeline from satellite transmitter to
this website — pass scheduling/recording (n8n `Tracking` workflow), RF
capture, QPSK demod, Viterbi/Reed-Solomon decoding, and the geolocation math
below. That level of detail isn't needed for day-to-day changes here, but is
useful background not derivable from this repo alone.

## Architecture

**`passes.json`** is the central data file. It is a flat JSON array consumed by `index.html`. Each entry has: `date`, `time`, `satellite`, `folder` (relative path to decoded dir), `imgs` (PNG filenames inside `folder/MSU-MR/`), `maxEl`, `frequency`, `gain`, `direction` (`StoN` or `NtoS`, inferred from pass time), `isDaylight` (bool, read from `meta.json`, defaults to `true` for passes recorded before this field existed).

**`rebuild-passes.sh`** scans all `decoded_*` directories, reads `meta.json` from each (falls back to filesystem timestamps), lists PNGs in `MSU-MR/`, and writes `passes.json`. Run this whenever decoded directories are added, renamed, or removed. A pass is dropped entirely (not written to `passes.json`) if `isDaylight` is `false` and none of the emissive/thermal channels (`MSU-MR-4/5/6.png`) are present — a night pass with only reflective channels (1-3) has no usable imagery at all.

**`decoded_YYYYMMDDHHMMSS/`** — one directory per satellite pass, produced by SatDump. Structure:
- `meta.json` — satellite name, maxEl, frequency, gain, date, time, direction, `isDaylight`
- `MSU-MR/` — PNGs and `product.cbor` (SatDump's CBOR projection metadata with TLE + per-row timestamps)
- `MSU-MR (Filled)/` — gap-filled variant from SatDump
- `meteor_m2-x_lrpt.cadu`, `dataset.json`, `telemetry.json` — SatDump outputs

**`index.html`** (and its `theding-index.html` counterpart, same logic, French copy, filtered to `location === 'theding'` passes) — entirely self-contained, no build step. Fetches `passes.json` at load time and renders the gallery. Key logic:
- `WANTED_IMAGES` — a fixed-order, curated allowlist of exactly which composite filenames are ever shown as thumbnails, each with its display label. A pass shows whichever of these actually exist in `pass.imgs`, in this order, and nothing else (raw channels are listed separately as plain links, not thumbnails).
- `needsFlip(imgName, pass)` — non-projected images from NtoS passes are CSS-flipped (`scale(-1,-1)`) so north is always up.
- A pass card shows a 🌙 badge when `pass.isDaylight === false`; the "next 3 passes" prediction widget shows the same badge for predicted night passes (which are now actually recorded, not skipped).
- Next-pass predictions and recording status are fetched live from `https://n8n.yannklein.dev/webhook/next-passes` and `/satdump-status`.

**ELEKTRO-L3 (geostationary, 14.5°W, LRIT 1691 MHz)** — separate from the METEOR pipeline. `elektro/<SAT>/<YYYY-MM-DD_HH-MM-SS UTC>/` holds SatDump `elektro_lrit` full-disk PNGs (2784px). `bash rebuild-elektro.sh [SATDUMP_OUTPUT_DIR]` copies new slots in from a capture dir (copy, not move — SatDump may still be writing), builds `natural_color.png` (`elektro_natcolor.py`, ch1-3, repairs dropout strips) and `natural_color_borders.png` (`elektro_borders.py`, direct geostationary projection fitted to this disk, Natural Earth lines from `naturalearth/`), `ir_ch4/ir_ch9_enhanced_borders.png` (`elektro_ir_enhanced.py`: inverted IR with a red/yellow-green/blue-cyan scale for the coldest cloud tops, LUT matched to the first hand-made images; lost-data strips detected by ch4/ch9 row correlation + vertical continuity and rebuilt from the other IR channel, rows bad in both left black; `elektro_borders.py` style `ir`) when channel 4/9 exist, makes `preview/` JPGs of those featured images, and writes `elektro.json` (with a per-slot `rev` mtime that `index.html` appends as `?v=` to image URLs — Cloudflare caches images 4h, so regenerated files would otherwise keep showing the old render). Python deps live in `.venv/` (numpy, pillow, scipy). `index.html` renders it as its own section above the METEOR gallery: only those natural-color + enhanced-IR images as thumbnails, raw channels + SatDump false color as links; a slot `meta.json` with `{"quality": "poor"}` collapses it. `elektro-movie.html` is a standalone player (also shown in an iframe popup from the ELEKTRO section title via `?embed=1`) that loops every non-poor slot's `preview/` JPG for a chosen channel — natural color (± borders), the two enhanced IR, or raw channels (`preview/L3_<n>.jpg`, also made by `rebuild-elektro.sh`); `?ch=` / `borders=0` select it. `rebuild-elektro.sh` also writes the public API `api/elektro.json` (served at `https://satellite.yannklein.dev/api/elektro.json`; base overridable with `ELEKTRO_API_BASE`): an object keyed by slot id, each pass holding `quality`, timing/downlink/channel metadata, and one key per channel (`ch<n>`, `natural_color`, `false_color_321`) whose value is the full-res URL, or `{raw, enhanced, border}` (whichever exist) when there's more than the raw image. No CORS headers (stock nginx), so browsers on other origins can't fetch it directly. Capture is still manual (`satdump live elektro_lrit ~/elektro_test`), but processing is automated: the n8n `ELEKTRO processing` workflow runs `rebuild-elektro.sh ~/elektro_test` over SSH every 5 min (incremental; only copies files untouched for 2+ min so nothing half-written is taken; `flock` prevents overlapping runs; `elektro.json` is written atomically). `index.html` re-polls `elektro.json` every 2 min and re-renders the ELEKTRO section when it changes, and hides slots until at least one featured composite exists. The 3D tracker also shows ELEKTRO-L3 (NORAD 44903) at true scale (GEO ring ~6.6 Earth radii out; default camera pulled back to frame it, markers scale with camera distance), plus the Moon at true size and distance (~60 Earth radii, low-precision ephemeris in `sublunar()`, sun-lit so it shows its phase, earthshine floor so a new Moon is still visible, scaled up to a 5 px minimum on-screen radius; not framed by the default camera — rotate to find it), and the Sun as a glow sprite along the real sun direction (placed at 140 Earth radii, between the Moon and the star sphere, since its true distance is past the far plane; no list row or telemetry for it).

**`add_map_overlay.py`** — reads `product.cbor` with `cbor2`, extracts TLE lines and per-row timestamps, propagates the orbit with `sgp4` to compute geographic bounds, then uses Cartopy + Matplotlib to composite coastlines/borders/cities over the PNG.

## Image filtering logic

The gallery shows exactly the composites listed in `WANTED_IMAGES` (`index.html`/`theding-index.html`), in this fixed order, whichever exist for a given pass:
1. `False Color AVHRR 221` — `msu_mr_rgb_AVHRR_221_False_Color_corrected.png`
2. `False Color AVHRR 3a21` — `msu_mr_rgb_AVHRR_3a21_False_Color_corrected.png` (needs channel 3)
3. `False Color 124 (ch 1/2/4)` — `msu_mr_rgb_MSU-MR_124_False_Color_corrected.png` (needs channel 4)
4. `Visible (ch 1)` — `msu_mr_Visible_Ch1_corrected.png`
5. `Thermal (ch 4)` — `msu_mr_Thermal_Ch4_corrected.png` (needs channel 4)
6. `False Color AVHRR 221 — projected` — `rgb_msu_mr_rgb_AVHRR_221_False_Color_projected.png`

MSA and MCIR (SatDump's basemap-underlay composites) are deliberately excluded: each only uses one real channel blended with a static reference image (`nasa_hd.jpg`) and has a known cosmetic registration drift between that basemap and the real data (see below) — not worth surfacing in the gallery. Raw channel PNGs and any other composite SatDump generates are never shown as thumbnails (raw channels are listed separately as plain download links per pass).

`_map`/`_corrected_map` variants and non-`_corrected` base composites are never in `WANTED_IMAGES` and so never shown either. Most `_map` files are produced natively by SatDump itself (the `draw_map_overlay` flag on composites like MSA/MCIR in `satdump_cfg.json`); `add_map_overlay.py` can additionally (over)write a nicer Cartopy-rendered version at the same path, but nothing in this repo's automated pipeline calls it.

### Why MSA/MCIR can show a shifted-looking basemap

Both use the compiled `underlay_with_clouds` C++ function (`SatDump-1.2.2/plugins/standard_cpp_compos_support/underlay_with_clouds.h`): the *real* channel data is placed via the satellite-swath TLE/timestamp projection (same one driving the border overlay), but the *static* `nasa_hd.jpg` backdrop is independently re-projected with a simple flat equirectangular transform. These two projections don't always agree — the swath-based one accumulates real-world error (stale TLE, timing drift, dropped-scanline gaps), so the real data can visibly drift relative to the (always self-consistent) basemap + border overlay. This is a SatDump characteristic, not a bug in this repo, and not fixable via config — it's part of why MSA/MCIR were dropped from the gallery.

### Day passes vs. channel-4 substitution vs. night passes

METEOR-M2 sometimes transmits channel 4 (~3.9µm, emissive/thermal, works day or night) instead of channel 3 (~1.6µm, reflective, day-only) in its LRPT triplet — confirmed to be an operator-driven switch for nighttime IR acquisitions. Channels 1-3 are pure reflectance and are blank at night; channels 4/5/6 are emissive brightness-temperature and work regardless of sunlight (only channel 4 has been observed in practice so far, but nothing here assumes that stays true).

- **Day, channel 3 present**: business as usual — AVHRR 221/3a21 both show.
- **Day, channel 3 missing, channel 4 present**: AVHRR 3a21 (needs ch3) simply doesn't get generated (SatDump silently skips a composite whose channel is missing rather than producing garbage). **"MSU-MR 124 False Color"** (`ch1, ch2, 1-ch4`) and **"Thermal Ch4"** (`1 - ch4`) fill in as the channel-4 equivalents.
- **Night**: only emissive channels have data, so only `Thermal (ch 4)` (and "MSU-MR 124" if ch1/2 also happen to have signal) show. A night pass with no emissive channel at all is dropped by `rebuild-passes.sh` before it ever reaches `passes.json`.

"MSU-MR 124 False Color" is a pre-existing SatDump preset; "Visible Ch1" and "Thermal Ch4" are two small presets added to `satdump_cfg.json` (single-channel `equation`, `geo_correct: true`, no basemap blending) specifically for this gallery. `AVHRR 221 False Color` also got a `project` block added (world-mosaic `_projected` variant) since it's the most reliably-available composite (only needs ch1+ch2). `AVHRR 3a21 False Color`, `MSU-MR 124 False Color`, `MCIR`, `AVHRR 543b/3b45 IR False Color`, and `Night Microphysics` all have `project` blocks too, so if channel 5/6 ever show up in a real pass, whichever composite ends up using them gets a projected variant automatically with no further config changes — `WANTED_IMAGES` would just need those filenames added if they should appear in the gallery.

## Dependencies (Python)

`cbor2`, `numpy`, `Pillow`, `sgp4`, `cartopy`, `matplotlib`
