#!/usr/bin/env python3
"""
compute_pass_quality.py -- classify each pass as 'good' or 'poor' based on how
much of its corrected MSU-MR composite is taken up by signal-dropout bands
from a weak downlink (not the geo-projection padding around the swath).
Dropout bands are black in the reflective composites, but white in the
Thermal composite since it's inverted (1 - ch4). Night passes are rated from
the Thermal composite only, since the reflective ones are pure noise at night.

Adds to each pass's meta.json:
  'blackRatio': fraction of dropout-band pixels in the reference composite
                (0-1) -- near-black, or near-white for the Thermal composite
  'quality':    'good' or 'poor'

The good/poor cutoff is the blackRatio of decoded_202608121156, the pass this
project uses as the acceptability boundary -- passes with that much black or
more are 'poor'.

Uses ImageMagick's `convert` rather than PIL/numpy since those aren't
installed in every environment this repo is worked from.

Usage:
    python3 compute_pass_quality.py [decoded_dir ...]
    # No args = process all decoded_* in current directory
"""

import sys, os, json, glob, subprocess

THRESHOLD_DIR = 'decoded_202608121156'

# Composites derived from the same MSU-MR scanlines carry the same dropout
# bands, so any one of these available for a pass is representative of it.
# Each is paired with the colour its dropout bands show up as.
THERMAL_COMPOSITE = ('msu_mr_Thermal_Ch4_corrected.png', 'white')
DAY_COMPOSITES = [
    ('msu_mr_rgb_MSA_corrected.png', 'black'),
    ('msu_mr_rgb_AVHRR_3a21_False_Color_corrected.png', 'black'),
    ('msu_mr_rgb_AVHRR_221_False_Color_corrected.png', 'black'),
    THERMAL_COMPOSITE,
]
NIGHT_COMPOSITES = [THERMAL_COMPOSITE]


def dropout_ratio(png_path, color):
    """Fraction of pixels that are (near-)`color` ('black' or 'white').
    Near = within 2% fuzz of the pure colour -- catches dropout/no-data bands
    without flagging legitimately dark ocean or bright cloud pixels. Returns
    None if the PNG can't be read (e.g. truncated file from an interrupted
    decode)."""
    # Negating first turns near-white into near-black, so one measure covers both.
    negate = ['-negate'] if color == 'white' else []
    result = subprocess.run(
        ['convert', png_path, '-alpha', 'off', '-colorspace', 'Gray', *negate,
         '-fuzz', '2%', '-fill', 'black', '-opaque', 'black',
         '-fill', 'white', '+opaque', 'black',
         '-format', '%[fx:mean]', 'info:'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return 1.0 - float(result.stdout.strip())


def is_night(decoded_dir):
    meta_path = os.path.join(decoded_dir, 'meta.json')
    if not os.path.exists(meta_path):
        return False
    with open(meta_path) as f:
        return json.load(f).get('isDaylight') is False


def find_ratio(decoded_dir):
    """Try each corrected composite in priority order, skipping missing or
    unreadable files, and return (ratio, composite_name) for the first that
    works."""
    composites = NIGHT_COMPOSITES if is_night(decoded_dir) else DAY_COMPOSITES
    for name, color in composites:
        path = os.path.join(decoded_dir, 'MSU-MR', name)
        if not os.path.exists(path):
            continue
        ratio = dropout_ratio(path, color)
        if ratio is not None:
            return ratio, name
    return None, None


def process_dir(decoded_dir, threshold):
    name = os.path.basename(decoded_dir)
    ratio, composite = find_ratio(decoded_dir)
    if ratio is None:
        return None

    quality = 'poor' if ratio >= threshold else 'good'

    meta_path = os.path.join(decoded_dir, 'meta.json')
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    meta['blackRatio'] = round(ratio, 4)
    meta['quality'] = quality
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)

    print(f'  {name}: blackRatio={ratio:.4f} ({composite}) -> {quality}')
    return quality


if __name__ == '__main__':
    dirs = sys.argv[1:] if len(sys.argv) > 1 else sorted(glob.glob('decoded_*'))

    threshold_ratio, threshold_composite = find_ratio(THRESHOLD_DIR)
    if threshold_ratio is None:
        sys.exit(f'threshold pass {THRESHOLD_DIR} has no readable corrected composite')
    print(f'Threshold (from {THRESHOLD_DIR}, {threshold_composite}): blackRatio={threshold_ratio:.4f}\n')

    counts = {'good': 0, 'poor': 0}
    skipped = 0
    for d in dirs:
        result = process_dir(d, threshold_ratio)
        if result:
            counts[result] += 1
        else:
            skipped += 1

    print(f'\nDone: {counts["good"]} good, {counts["poor"]} poor, {skipped} skipped (no usable composite)')
