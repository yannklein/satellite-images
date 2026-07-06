# Signal chain: from METEOR-M2's transmitter to this gallery

This documents the full pipeline behind this project, from RF signal to a pixel
on the website. Most of it is not derivable from this repo alone — the pass
scheduling/recording step lives in an n8n workflow (`Tracking`,
`https://n8n.yannklein.dev/workflow/36SkBVTjTIGDQ8iu`), and the demodulation/
FEC decoding happens inside SatDump's own DSP pipeline, invoked as a single
opaque command. This file exists so that context isn't lost.

## 1. Pass scheduling → recording trigger

Some external caller (predicts upcoming passes from TLEs — likely the same
source that feeds `index.html`'s `next-passes` widget) issues:

```
GET https://n8n.yannklein.dev/webhook/schedule-pass
    ?token=...&startUTC=...&endUTC=...&satFrequency=...&satName=...&maxEl=...&startAz=...
```

The n8n **Tracking** workflow (id `36SkBVTjTIGDQ8iu`) handles it:

1. **`n8n secret pass check`** — compares `query.token` to `$env.PASS_SECRET`.
   Wrong token → 403. Correct → responds `200 OK` immediately (so the caller
   isn't blocked for ~15 minutes) while the same execution continues async.
2. **`Wait for satellite coming`** — suspends (no polling) until
   `startUTC - 5 minutes`, Europe/Madrid time.
3. **`Start/stop tracking`** (SSH into the home server) runs, roughly:
   ```bash
   pkill dump1090 || true; pkill AIS-catcher || true; pkill rtl_sdr || true
   rtl_biast -b 1                                   # power the inline LNA
   rtl_sdr -f <satFrequency> -s 2400000 -g 20 meteor_<ts>.raw &
   # ... kill rtl_sdr after (endUTC-startUTC)+10min, with a +20min watchdog failsafe ...
   satdump pipeline meteor_m2-x_lrpt baseband <raw> <decoded_dir> \
     --samplerate=2400000 --baseband_format=cu8 --fill_missing
   # copy MSU-MR (Filled)/*.png over MSU-MR/*.png
   # write meta.json (satellite, maxEl, frequency, gain=25 hardcoded — actual
   #   rtl_sdr gain used was 20, this is a cosmetic mismatch)
   # direction computed from startAz (90-270 deg => NtoS), but this gets
   #   effectively overridden later: rebuild-passes.sh and compute_barcelona_px.py
   #   both recompute direction from local clock hour instead of trusting this field.
   bash rebuild-passes.sh
   echo "RAW_PATH=..."; echo "DECODED_DIR_PATH=..."   # for the next node to grep
   ```
   Other RTL-SDR consumers (ADS-B, AIS) are killed first since only one
   process can own the USB dongle. `rtl_biast -b 1` powers an external LNA
   over the antenna coax — near-mandatory at 137.9 MHz on a modest antenna.
   `--baseband_format=cu8` = RTL-SDR's native unsigned-8-bit interleaved I/Q.
4. **`Insert row`** — logs that SSH call's stdout/stderr to an n8n Data Table
   (`Satellite tracking output`) for debugging.
5. **`Image processing (map + projection)`** — a second SSH node that greps
   `RAW_PATH`/`DECODED_DIR_PATH` back out of step 3's stdout, then **reruns
   SatDump a second time** from a different, pinned install
   (`SatDump-1.2.2`, vs. step 3's `SatDump` — presumably the stable build
   that reliably produces the projected/corrected composites). It also
   manually 180°-rotates the raw channel PNGs (`MSU-MR-1/2/3.png`) via
   ImageMagick for `NtoS` passes — a server-side pixel rewrite, distinct from
   `index.html`'s client-side CSS flip on the composite images. Reruns
   `rebuild-passes.sh` once more.

Everything from here on (`satdump pipeline ... baseband ...`) is one call
that internally does everything in sections 2–7 below.

## 2. QPSK modulation (on the satellite)

LRPT sends 2 bits/symbol via 4 carrier phase states 90° apart:

1. Bitstream at this point = output of RS encode → convolutional encode →
   randomize (see sections 5–7, run in reverse on receive).
2. Serial-to-parallel: consecutive bits grouped into dibits.
3. **Differential encoding**: each dibit encodes a *phase change* relative to
   the previous symbol (e.g. 00→+0°, 01→+90°, 11→+180°, 10→+270°), not an
   absolute phase — this is what lets the receiver's Costas loop lock to any
   of 4 rotations and still recover correct data (see step 6.6 below).
4. Gray-coded mapping to one of 4 constellation points (45°/135°/225°/315°)
   — adjacent points differ by 1 bit, so a slip to the nearest neighbor
   flips only 1 bit.
5. I/Q split: `I=cos(θ)`, `Q=sin(θ)`, modulated onto carrier + 90°-shifted
   quadrature copy, summed.
6. Root-raised-cosine (RRC) pulse shaping on both I/Q streams before
   upconversion — band-limits the signal and (paired with a matched filter
   at the receiver) satisfies the Nyquist zero-ISI criterion.

## 3. RF capture

`rtl_sdr -f <freq> -s 2400000 -g 20 file.raw` — pure ADC dump, 2.4 Msamples/s,
20 dB tuner gain, unsigned 8-bit interleaved I/Q. No demodulation yet.

## 4. QPSK demodulation (inside SatDump)

1. DC offset / I-Q imbalance correction (RTL-SDR ADC/tuner artifacts).
2. Channel filter + decimation from 2.4 Msps down toward a few
   samples/symbol (LRPT symbol rate is far below 2.4 Msps).
3. AGC — received power swings 20+ dB over a pass.
4. Doppler/carrier frequency tracking — LEO Doppler at 137.9 MHz is several
   kHz, sweeping sign across the pass (zero at closest approach).
5. Matched filtering (RRC) — SNR-optimal sampling, matches transmitter's RRC.
6. Symbol timing recovery (Gardner / Mueller-&-Muller) — the RTL-SDR ADC
   clock and the satellite's symbol clock are unrelated free-running
   oscillators; this loop tracks continuous drift, not just a fixed offset.
7. Carrier phase tracking (Costas loop) — 4th-power (or decision-directed)
   phase detector strips QPSK's 4-fold symmetry to expose the residual
   carrier tone and steer an NCO.
8. Soft demapping — signed distance from each axis's decision boundary,
   preserved (not hard-sliced) because soft-decision Viterbi gains ~2 dB
   over hard-decision.
9. Differential decoding — undoes step 2.3, recovering phase transitions;
   simultaneously resolves the Costas loop's 90°-rotation ambiguity since
   only the *differences* need to be correct, regardless of which absolute
   rotation the loop locked to.

Output: soft channel-bit stream, equal (modulo noise) to the convolutional
encoder's original output on the satellite.

## 5. Viterbi decoding

LRPT uses a rate-1/2, constraint-length-7 convolutional code (64-state
trellis; 1 input bit → 2 output bits, each input's effect smeared across 7
consecutive output pairs — the common CCSDS-heritage code). Per received
symbol pair: compute a branch metric per trellis edge, add to each path's
accumulated metric, keep only the lowest-metric survivor into each of the 64
states (add-compare-select), and traceback after a fixed depth to emit the
maximum-likelihood bit sequence. Soft input (from step 4.8) is what buys the
~2 dB gain over hard-decision decoding — meaningful on a link this marginal.

Output: bitstream as it existed just before convolutional encoding — still
randomized, still framed as outer-coded CADU blocks.

## 6. Derandomization

The transmitter XORs the bitstream with a fixed PN sequence (LFSR-based,
standard CCSDS practice) before sending, so the signal always has enough bit
transitions for the loops above to track, and no DC bias develops from long
runs of identical bits. XOR is self-inverse, so the receiver XORs the same
sequence back in.

## 7. Frame sync + Reed-Solomon decoding

- **Frame sync**: correlate for the CADU sync marker (`0x1ACFFC1D`) to
  establish byte/frame alignment — Viterbi's output has no inherent framing.
- **RS(255,223) over GF(2⁸)**: 223 data bytes + 32 parity bytes per 255-byte
  codeword, correcting up to 16 erroneous *bytes* per block regardless of how
  many bits within them are wrong — targets burst errors from signal fades
  near the horizon, which Viterbi handles poorly. LRPT interleaves multiple
  RS codewords byte-by-byte across each frame so one physical burst spreads
  thin across several codewords instead of overwhelming one.
- Decoding: syndromes → Berlekamp-Massey (error locator polynomial) → Chien
  search (root-finding → error positions) → Forney algorithm (correction
  values). Deterministic algebra, not a probabilistic search like Viterbi.

Output: clean CADU frames — the `meteor_m2-x_lrpt.cadu` file kept per pass.
This is the boundary between signal processing and spacecraft-protocol /
instrument-data extraction.

## 8. Instrument data → geolocation → website

Covered in depth by comments in `add_map_overlay.py` and
`compute_barcelona_px.py`, and summarized in the main `CLAUDE.md`:

- SatDump separates CADU frames into per-channel MSU-MR images and writes
  `product.cbor` (TLE + per-scan-line timestamps + scan geometry:
  `corr_altit`, `corr_swath`, `corr_resol`, `roll_offset`, etc.).
- `compute_barcelona_px.py` SGP4-propagates the TLE at every scan-line
  timestamp, converts ECI→ECEF via GMST, and finds the scan line/column
  where the sensor's scan plane crossed a target lat/lon (using the same
  earth-curvature ground-distance geometry SatDump's own corrected-image
  raytracer uses), writing `barcelonaPx`/`citiesPx`/rotation into `meta.json`.
- `add_map_overlay.py` does a coarser version of the same propagation to get
  a bounding lat/lon box for Cartopy coastline/border overlays.
- `rebuild-passes.sh` aggregates every `decoded_*/meta.json` + PNG listing
  into the flat `passes.json` array that `index.html` fetches and renders,
  applying `shouldShow()` (which composites to display) and `needsFlip()`
  (CSS `scale(-1,-1)` for non-projected images from `NtoS` passes).
