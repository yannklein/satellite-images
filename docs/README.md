# Satellite ground station — the pipeline

This is the technical companion to the article **["The Antenna That Wouldn't Stop Growing"](https://dev.to/)** *(link once published)* — a distributed weather-satellite ground station that receives **METEOR-M2-3/M2-4** LRPT imagery on **137.9 MHz** at two sites (Barcelona, Spain and Théding, France), decodes and projects it, and publishes it to a public gallery.

The article tells the *story* and explains the *why*. This folder is the *how* — the actual (sanitized) automation, scripts, and frontend that run it.

> ⚠️ **Sanitized.** Secrets, API keys, internal host IPs, and the remote SSH alias have been replaced with placeholders (`YOUR_N2YO_API_KEY`, `PASS_SECRET`, `/home/USER`, `n8n.YOUR_DOMAIN`, `theding`). This is a reference to learn from and adapt, **not** a turnkey drop-in — you'll wire it to your own machines.

## How it works (one paragraph)

A **Scheduling** workflow runs nightly, asks [N2YO](https://www.n2yo.com/api/) for upcoming passes of the tracked satellites over each tracked location, optionally filters to daylight, and hands each qualifying pass to a **Tracking** workflow. Tracking waits until just before the satellite rises, then routes by location: **Barcelona** records baseband locally at 2.4 MS/s and decodes on the spot; **Théding** (a weak netbook 1,000 km away) records a lean 250 kS/s file and does *nothing else* — the raw is `rsync`'d back to Barcelona and decoded there. Decoding is [SatDump](https://www.satdump.org/); a second pinned SatDump build handles map projection/geo-correction. A **Status** workflow exposes "next passes" and "is it recording right now" to the gallery frontend, which reads a `passes.json` index (rebuilt after every decode) and renders the images — including a Barcelona-centered timelapse.

```
Scheduling (nightly)                Tracking (per pass)                 Publish
──────────────────                  ──────────────────                  ───────
N2YO passes ─► daylight? ─► POST ─► wait for AOS ─► route by location    rebuild-passes.sh
                                     │                                    ─► passes.json
                                     ├─ Barcelona: rtl_sdr 2.4MS/s ─► decode ─┤
                                     └─ Théding:   rtl_sdr 250kS/s ─► rsync ─► decode ─┤
                                                                          compute_barcelona_px.py
                                                                          add_map_overlay.py
                                                                          gallery/index.html
```

## What's in here

| Path | What it is |
|------|-----------|
| `n8n/scheduling.workflow.json` | Nightly pass prediction → schedule (N2YO + sunrise/sunset gating) |
| `n8n/tracking.workflow.json` | The core capture→decode→publish flow, with the Barcelona vs Théding split |
| `n8n/status.workflow.json` | `/next-passes` and `/satdump-status` webhooks for the frontend |
| `scripts/compute_barcelona_px.py` | Georeferencing: computes Barcelona's pixel (row/col) in each pass from the TLE + scan timestamps, for centering the timelapse |
| `scripts/add_map_overlay.py` | Draws coastlines/borders/city labels onto a decoded image (cartopy) |
| `scripts/rebuild-passes.sh` | Walks `decoded_*/`, reads each `meta.json`, emits `passes.json` |
| `gallery/index.html` | The public gallery frontend (pass grid, lightbox, Barcelona-centered timelapse) |
| `gallery/passes.sample.json` | Two sample entries showing the `passes.json` schema |
| `env.example` | The env vars / secrets you need to supply |

## Hardware (both sites)

- **RTL-SDR Blog V3** dongle (~€30) + its telescopic **dipole kit**
- **Nooelec SAWbird+ NOAA** LNA at the antenna feedpoint, bias-tee powered
- V-dipole: each arm **53.4 cm**, **120°**, mounted **flat/horizontal**
- Barcelona: an old MacBook running Debian (the server). Théding: an old i386 netbook (capture-and-ship only)

## Setup sketch

1. **Build SatDump** from source on the decode machine ([docs](https://www.satdump.org/)). Run it from its own `build/` directory or it won't find its plugins. If you want the projection behavior this pipeline relies on, keep a stable build (e.g. 1.2.2) pinned alongside the alpha; the two builds' intermediate products aren't cross-compatible, so re-decode from raw baseband when switching.
2. **Import the three n8n workflows.** Recreate the two Data Tables (`Tracked Satellites`: `NORAD_ID`, `name`, `frequency`; `Tracked locations`: `name`, `latitude`, `longitude`) and re-point every `dataTableId`. Recreate the SSH credentials.
3. **Set secrets** (see `env.example`): `PASS_SECRET` and your N2YO key (replace `YOUR_N2YO_API_KEY` in the two workflows). **Rotate your N2YO key if it was ever committed anywhere.**
4. **Remote site:** put a Cloudflare tunnel (`cloudflared`) on the remote node so `ssh theding` works with no static IP and no open ports. Confirm `rtl_sdr`, `rtl_biast`, and `jq` exist there.
5. **Python deps** for the scripts: `pip install cbor2 sgp4 numpy pillow cartopy matplotlib`.
6. **Frontend:** set `N8N_BASE` in `gallery/index.html`, serve the folder (any static host — mine is an nginx container), point it at the directory where `rebuild-passes.sh` writes `passes.json` and the `decoded_*` image folders live.

## A few hard-won notes

- **Sample only as fast as the signal is wide.** METEOR LRPT is ~120 kHz; 250 kS/s captures it fully at ~1/10 the file size — decisive when the raw crosses a border. 2.4 MS/s locally where the file never moves.
- **Amplify at the antenna, not the radio.** The LNA must sit before the coax, or cable loss sets your noise floor.
- **Flip is pass-direction, not a bug.** `NtoS` passes are upside-down-and-mirrored vs `StoN`; direction is derived from the rise azimuth (`startAz` 90–270° → `NtoS`), flipped 180° server-side (ImageMagick) for the raw channels and via CSS in the gallery.
- **`bash`, not `zsh`,** for the capture scripts — unmatched globs and array syntax bite otherwise.

## License

MIT (or your choice). The imagery is received from public, unencrypted civilian weather satellites.
