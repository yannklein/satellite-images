# Signal chain: from METEOR-M2's camera to this gallery

*This document was drafted with AI assistance.*

This documents the full pipeline behind this project, from the satellite
taking an image to a pixel on this website, in the order it actually
happens: satellite side first (sections 1–2), then the ground side
(sections 3–9). Most of it is not derivable from this repo alone — the pass
scheduling/recording step lives in an n8n workflow (`Tracking`,
`https://n8n.yannklein.dev/workflow/36SkBVTjTIGDQ8iu`), and the demodulation/
FEC decoding happens inside SatDump's own DSP pipeline, invoked as a single
opaque command. This file exists so that context isn't lost.

## 1. How MSU-MR actually takes a "photo"

Nothing in the sections that follow makes sense until you know what's
actually being sent down from orbit. It isn't a JPEG the satellite snapped
and is now streaming down like a file transfer — there's no camera shutter
and no 2D sensor at all. MSU-MR ("Multispectral Scanning Unit — Medium
Resolution") builds an image the same way an old flatbed scanner or a
dot-matrix printer does: one line at a time, from two independent motions
at right angles to each other.

- **The fast axis (across the swath)** is the print head of a scanner
  moving back and forth. A small mirror inside the instrument continuously
  spins, sweeping a narrow sliver of view side-to-side underneath the
  satellite, perpendicular to its direction of travel. Several detectors
  sit behind that mirror — one per spectral channel — so every point the
  mirror sweeps across is measured simultaneously in ~6 different "colors"
  at once: a few visible/near-infrared bands (reflected sunlight — this is
  what makes the daytime, photo-like composites) and a few thermal infrared
  bands (heat the clouds/ground/sea emit themselves, which is why this
  satellite can still produce usable images of clouds at night). Instead of
  "how much light bounced off the page," it's measuring "how much
  light/heat came off the ground," in 6 channels per sweep instead of a
  scanner's 1.
- **The slow axis (along the swath)** is where the satellite's own orbital
  motion replaces the scanner's motor. MSU-MR doesn't move itself along
  this axis — by the time the mirror finishes one sweep and starts the
  next, the satellite (and its ~2800 km-wide view of the ground below) has
  moved slightly further along its orbit. Stack thousands of these sweeps
  end to end over the ~15 minutes of a pass and you get a full 2D image,
  exactly like a flatbed scanner's sensor bar building an image one pass at
  a time — except here "moving the sensor" means "letting the whole
  spacecraft fly forward."
- **Turning light into numbers**: each detector's raw output at any instant
  is just an analog voltage — more light/heat in, higher voltage out, no
  different in principle from one photosite on a digital camera's sensor
  chip. An analog-to-digital converter (ADC) samples that voltage many
  times per mirror sweep and rounds it off to a numeric brightness value
  per pixel per channel. This is the literal moment a physical measurement
  of sunlight/heat becomes a number the spacecraft's onboard electronics —
  and eventually this server — can work with.
- **Packaging for transmission**: those per-pixel, per-channel brightness
  numbers — plus timestamps and orbit/attitude telemetry needed later to
  figure out *where* each scan line was pointed (section 9) — get packed
  into fixed-size data frames, the same CADU frames that reappear at the
  very end of the decode chain in section 8. Think of it as the networking
  equivalent of chunking a byte stream into fixed-size packets, so the
  receiving side always knows where one unit of data ends and the next
  begins.

Everything from here through section 8 — Reed-Solomon encoding,
convolutional encoding, scrambling, QPSK modulation, the RF link itself, and
then all of that undone again on the ground — exists purely to get *these*
frames, the raw scanned brightness numbers above, from the satellite's
electronics down to a file on this server intact. None of it touches the
image content itself; it's all just a (very belt-and-suspenders) delivery
mechanism. Section 9 picks back up with what happens to those numbers once
they've survived the trip: how they become the PNGs in this repo, correctly
located on a map.

## 2. QPSK modulation (on the satellite)

Radio hardware can only really control one thing about a wave: its phase
(where it is in its cycle at a given instant — think of a clock hand's
angle). QPSK ("Quadrature Phase-Shift Keying") encodes bits by choosing
which of 4 clock-hand positions, 90° apart, the transmitter snaps to for
each symbol. Since there are 4 choices, each one encodes 2 bits (2² = 4)
instead of just 1 — double the data rate for the same number of "ticks" per
second, at the cost of needing more careful electronics to reliably tell
the 4 positions apart at the receiver. LRPT sends 2 bits/symbol this way:

1. **Bitstream**: the framed scan-line data from section 1, already run
   through RS encode → convolutional encode → randomize (sections 6–8
   below run this in reverse on receive).
2. **Serial-to-parallel**: consecutive bits are grouped into pairs
   ("dibits") — one pair per symbol, since QPSK needs 2 bits to pick among
   its 4 phase positions.
3. **Differential encoding**: each dibit encodes a *phase change* relative
   to the previous symbol (e.g. 00→+0°, 01→+90°, 11→+180°, 10→+270°), not
   an absolute phase. Instead of saying "point the clock hand at 90°," the
   transmitter says "turn the clock hand 90° from wherever it just was."
   This matters because, as section 5's step 7 explains, the receiver has
   a 1-in-4 chance of locking onto the signal "upside down" — consistently
   misreading every absolute angle by a fixed 90°/180°/270° offset. If the
   data were absolute angles, that offset would corrupt every single bit;
   because it's encoded as a *relative turn*, a constant offset cancels
   out — "turn 90° from a wrong starting point" still nets out to the
   correct 90° turn.
4. **Gray-coded mapping** puts each dibit at one of 4 constellation points
   (45°/135°/225°/315°), chosen so adjacent points differ by only 1 bit. If
   noise nudges the receiver's guess to the next position over — the most
   common kind of mistake — only 1 of the 2 bits comes out wrong instead of
   both.
5. **I/Q split**: `I=cos(θ)`, `Q=sin(θ)` — the trigonometry for actually
   generating that clock-hand angle `θ` as a real radio wave, modulated
   onto a carrier plus a 90°-shifted quadrature copy, then summed. Not a
   separate concept from step 4, just the "how" behind it.
6. **Root-raised-cosine (RRC) pulse shaping** smooths both I/Q streams
   before upconversion. Without it, switching abruptly between clock
   positions would splatter energy across nearby frequencies, like a car
   horn; this filter keeps the signal inside its allotted slice of spectrum
   (band-limiting) without smearing one symbol's signal into the next
   symbol's timeslot — satisfying the Nyquist zero-ISI criterion, paired
   with a matched filter at the receiver (section 5's step 5).

This is where the satellite's part of the story ends — from here down, a
radio wave carrying this signal is on its way to Barcelona, and everything
in the rest of this file happens on the ground.

## 3. Pass scheduling → recording trigger

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

The `rtl_sdr` line above is the RF capture detailed in section 4 below; the
`satdump pipeline ... baseband` line is one call that internally does
everything in sections 5–8 (demodulation through Reed-Solomon decoding) —
reversing, on the ground, everything the satellite did in sections 1–2
above.

## 4. RF capture

`rtl_sdr -f <freq> -s 2400000 -g 20 file.raw` does nothing clever — it just
points the antenna's raw electrical signal at an analog-to-digital
converter and writes down 2.4 million samples per second describing the
wave shape (2.4 Msamples/s, 20 dB tuner gain, unsigned 8-bit interleaved
I/Q). No demodulation happens yet; all the actual "decoding intelligence"
comes afterward, in software, applied to this recording. I/Q —
"in-phase/quadrature" — is just the standard way of representing that wave
as two numbers per sample instead of one, which is what lets section 2 talk
about angles and rotations at all.

## 5. QPSK demodulation (inside SatDump)

This is the reverse of section 2 — turning the raw recorded wave back into
the stream of bits the satellite started with. The tricky part isn't the
math, it's that the receiver knows almost nothing for certain going in: not
the exact frequency (the satellite is moving, so its signal is
Doppler-shifted, the same effect as a siren changing pitch as it passes
you), not the exact symbol rate clock, not the signal strength, and not
even *which* of the 4 rotations it's looking at. Steps 1–3 below clean up
recording artifacts, steps 4–7 are four independent "auto-tuning" loops
each chasing one unknown, and steps 8–9 turn the now-clean wave back into
bits.

1. **DC offset / I-Q imbalance correction** — cleans up artifacts
   introduced by the RTL-SDR's own ADC and tuner hardware, not the radio
   signal itself.
2. **Channel filter + decimation** — narrows the recording from the full
   2.4 Msps down toward the few samples/symbol LRPT's actual symbol rate
   needs.
3. **AGC (Automatic Gain Control)** — the satellite is close overhead one
   moment and near the horizon the next, so its signal strength varies by
   20+ dB over the ~15-minute pass; this loop continuously rescales the
   volume back to a workable range, the radio equivalent of a
   compressor/limiter on an audio track.
4. **Doppler/carrier frequency tracking** — because the satellite is
   approaching then receding, the received frequency drifts down through
   the true 137.9 MHz and out the other side over the course of a pass
   (several kHz at this frequency); this loop continuously re-estimates
   and corrects for that drift.
5. **Matched filtering (RRC)** — SNR-optimal sampling that mirrors the
   transmitter's RRC pulse shaping from section 2's step 6.
6. **Symbol timing recovery (Gardner / Mueller-&-Muller)** — think of two
   computers' clocks drifting apart without NTP: the receiver's sample
   clock and the satellite's symbol clock were never synchronized to begin
   with, so this loop is constantly figuring out "given the samples I have,
   exactly *when*, between samples, did the satellite actually send each
   symbol" — a continuous drift to track, not just a fixed offset to
   correct once.
7. **Carrier phase tracking (Costas loop)** — a feedback loop, the same
   idea as a PID controller, that keeps nudging the receiver's internal
   sense of "phase zero" to stay locked onto the incoming signal. It works
   by using a 4th-power (or decision-directed) phase detector to strip away
   QPSK's 4-fold symmetry and expose the residual carrier tone, then steers
   an NCO (numerically-controlled oscillator) with it. Because QPSK's 4
   clock positions are symmetric 90° apart, this loop has no way to tell
   *which* of the 4 it has locked onto — hence the "90°-rotation ambiguity"
   that step 9 below resolves.
8. **Soft demapping** — instead of forcing an immediate confident yes/no
   guess for each bit ("closer to 0 or 1"), this keeps a confidence score
   ("70% sure it's a 1") — the signed distance from each axis's decision
   boundary — and hands that uncertainty downstream to Viterbi decoding in
   section 6, which makes better overall decisions when it knows which
   bits to trust less. Soft-decision Viterbi gains ~2 dB over
   hard-decision, which matters on a link this marginal.
9. **Differential decoding** undoes section 2's step 3, recovering phase
   transitions. This is also where the Costas loop's 90°-rotation ambiguity
   from step 7 above gets resolved for free: only the *differences*
   between symbols need to be correct, regardless of which absolute
   rotation the loop locked onto.

Output: a soft channel-bit stream, equal (modulo noise) to the
convolutional encoder's original output on the satellite.

## 6. Viterbi decoding

Before transmission (section 2's step 1), every input bit was deliberately
smeared across several output bits by a **convolutional code** — a way of
adding redundancy so errors can be fixed later. LRPT uses a rate-1/2,
constraint-length-7 code: a tiny state machine (64-state trellis) that
outputs 2 bits per input bit, where each output pair depends not just on
the current input bit but on the previous 6 bits too. That overlap is the
whole point — it means any given error doesn't destroy one isolated bit of
information, it just slightly disagrees with several overlapping
constraints, and **Viterbi decoding** is the algorithm that uses those
overlapping constraints to figure out the most likely original bits despite
noise.

If you know dynamic programming or Dijkstra's shortest-path algorithm, this
is that: the state machine's possible states over time form a graph
("trellis"), each possible transition has a cost (how well it matches what
was actually received, using the confidence scores from section 5's step 8
— not just a hard 0/1), and Viterbi finds the lowest-total-cost path
through that graph exactly the way Dijkstra finds the shortest path through
a graph — just with a fixed number of steps and states instead of
arbitrary graph structure. Concretely, per received symbol pair: compute a
branch metric per trellis edge, add it to each path's accumulated metric,
keep only the lowest-metric survivor into each of the 64 states
(add-compare-select), and traceback after a fixed depth to emit the
maximum-likelihood bit sequence. The soft input from section 5 is what buys
the ~2 dB gain over hard-decision decoding.

Output: the bitstream as it existed just before convolutional encoding —
still randomized, still framed as outer-coded CADU blocks.

## 7. Derandomization

All of the tracking loops in section 5 (AGC, timing recovery, the Costas
loop) work by watching the signal change — they'd get confused by a long
stretch of identical bits (e.g. all zeros), which looks electrically like
"nothing happening." So before transmission, the satellite **XORs the
bitstream with a fixed, publicly-known pseudorandom sequence** (LFSR-based,
standard CCSDS practice) — not for secrecy, purely to guarantee the signal
always looks "busy" no matter what the real data is, so it always has
enough bit transitions for the loops above to track and no DC bias develops
from long runs of identical bits. This is conceptually the same trick as
whitening data before compression, just applied for a different reason
(radio tracking instead of compression ratios). XOR is self-inverse, so the
receiver undoes it by XORing the exact same sequence back in.

## 8. Frame sync + Reed-Solomon decoding

Two separate jobs happen here. First, **frame sync** solves the same kind
of problem as finding message boundaries in a raw TCP byte stream: Viterbi
decoding (section 6) just outputs one continuous river of bits with no
markers, so the receiver correlates for a known fixed pattern — the CADU
sync marker, `0x1ACFFC1D` — to establish byte/frame alignment.

Second, **Reed-Solomon decoding** is a different kind of error-correcting
code from Viterbi — the same family used on CDs/DVDs and QR codes — added
as a second layer specifically to mop up the *burst* errors (whole chunks
of consecutive bytes wrecked at once, e.g. from a brief signal fade as the
satellite dips toward the horizon) that Viterbi is bad at fixing but this
is good at, because it operates on whole bytes rather than individual
bits. LRPT uses **RS(255,223) over GF(2⁸)**: 223 data bytes + 32 parity
bytes per 255-byte codeword, correcting up to 16 erroneous *bytes* per
block regardless of how many bits within them are wrong. LRPT also
interleaves multiple RS codewords byte-by-byte across each frame, so one
physical burst spreads thin across several codewords instead of
overwhelming just one.

Unlike Viterbi's "most likely path" search, decoding here is exact algebra
with a guaranteed right answer, as long as no more than 16 bytes per
255-byte block are broken: syndromes are computed, then Berlekamp-Massey
finds the error locator polynomial, Chien search finds its roots (the error
positions), and the Forney algorithm computes the correction values —
mathematically solving for exactly which byte positions are wrong and what
their correct values should be.

Output: clean CADU frames — the `meteor_m2-x_lrpt.cadu` file kept per pass.
This is the boundary between signal processing and spacecraft-protocol /
instrument-data extraction: these frames are the same raw scan-line
brightness numbers described in section 1, now fully recovered
byte-for-byte.

## 9. Instrument data → geolocation → website

Section 1 explained how MSU-MR produces a long, thin strip of brightness
numbers per channel — one strip per rotation of the scan mirror, laid end
to end as the satellite flies. On its own, that's already an image (that's
what the raw `MSU-MR-1/2/3.png` channel files are), but it's a *distorted*
one: the satellite doesn't fly in a perfectly straight line over a flat
Earth, the mirror sweeps at a constant *angular* rate but the ground below
curves away at the swath edges, and the whole strip may need flipping
depending on which direction the satellite was heading. Turning that raw
strip into something that lines up correctly with a map means knowing, for
every single scan line, exactly where in the sky and over which patch of
ground the satellite was at that precise instant — using the same orbital
tracking data (TLE) that was used to predict the pass in the first place.
This is the last stop: what comes out of here is what you actually see on
the gallery page.

Covered in depth by comments in `add_map_overlay.py` and
`compute_barcelona_px.py`, and summarized in the main `CLAUDE.md`:

- **SatDump** separates CADU frames into per-channel MSU-MR images and
  writes `product.cbor` (TLE + per-scan-line timestamps + scan geometry:
  `corr_altit`, `corr_swath`, `corr_resol`, `roll_offset`, etc.).
- **`compute_barcelona_px.py`** SGP4-propagates the TLE at every scan-line
  timestamp, converts ECI→ECEF via GMST, and finds the scan line/column
  where the sensor's scan plane crossed a target lat/lon (using the same
  earth-curvature ground-distance geometry SatDump's own corrected-image
  raytracer uses), writing `barcelonaPx`/`citiesPx`/rotation into
  `meta.json`.
- **`add_map_overlay.py`** does a coarser version of the same propagation
  to get a bounding lat/lon box for Cartopy coastline/border overlays.
- **`rebuild-passes.sh`** aggregates every `decoded_*/meta.json` + PNG
  listing into the flat `passes.json` array that `index.html` fetches and
  renders, applying `shouldShow()` (which composites to display) and
  `needsFlip()` (CSS `scale(-1,-1)` for non-projected images from `NtoS`
  passes).
