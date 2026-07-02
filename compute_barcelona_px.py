#!/usr/bin/env python3
"""
compute_barcelona_px.py — compute Barcelona's pixel position in each MSU-MR pass.
Adds 'barcelonaPx': {row, col, distKm} to each pass's meta.json.

Row: scan line where the along-track component of sat→Barcelona is zero
     (i.e. the satellite's scan plane is perpendicular to Barcelona).
Col: cross-track component of sat→Barcelona projected onto ct = along × nadir,
     giving the correct signed direction in raw image space for both StoN and
     NtoS passes (NtoS raw images are east-on-left, matching the ECEF cross-track
     sign before the display flip is applied).

Usage:
    python3 compute_barcelona_px.py [decoded_dir ...]
    # No args = process all decoded_* in current directory
"""

import sys, os, json, math, glob
from datetime import datetime
import cbor2
from sgp4.api import Satrec

BARCELONA_LAT = 41.38
BARCELONA_LON = 2.17
EARTH_R       = 6371.0
EARTH_OMEGA   = 7.2921159e-5   # rad/s, Earth's rotation rate (for ECI->ECEF velocity)

def fix_tle(line):
    body = line[:-1]
    checksum = sum(int(c) if c.isdigit() else (1 if c == '-' else 0) for c in body) % 10
    return body + str(checksum)

def eci_to_ecef(r, ts):
    x, y, z = r
    jd    = 2440587.5 + ts / 86400.0
    t_ut1 = (jd - 2451545.0) / 36525.0
    gmst  = (67310.54841 + (876600*3600 + 8640184.812866)*t_ut1
             + 0.093104*t_ut1**2 - 6.2e-6*t_ut1**3) % 86400
    theta = gmst * 2 * math.pi / 86400.0
    return (x*math.cos(theta) + y*math.sin(theta),
           -x*math.sin(theta) + y*math.cos(theta),
            z)

def dot(a, b):   return sum(ai*bi for ai, bi in zip(a, b))
def cross3(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def norm(v):
    m = math.sqrt(sum(x**2 for x in v))
    return tuple(x/m for x in v)
def sub(a, b):   return tuple(ai-bi for ai, bi in zip(a, b))

def pass_is_ntos(decoded_dir, meta):
    """Replicates rebuild-passes.sh's direction heuristic (hour-of-day based),
    since that is what index.html actually uses to decide whether to flip the
    image -- NOT the satellite's true ascending/descending motion. The rotation
    formula must branch on the same thing the flip does, or the two disagree."""
    time_str = meta.get('time')
    if time_str:
        hour = int(time_str.split(':')[0])
    else:
        hour = int(datetime.fromtimestamp(os.path.getmtime(decoded_dir)).strftime('%H'))
    return not (5 <= hour < 14)   # StoN if 05:00-13:59, else NtoS

def compute_barcelona_pixel(cbor_path, do_flip):
    with open(cbor_path, 'rb') as f:
        data = cbor2.load(f)

    tle_raw = data.get('tle')
    if not tle_raw:
        return None

    images = data.get('images', [])
    if not images:
        return None

    img0       = images[0]
    timestamps = img0.get('timestamps', [])
    ifov_y     = img0.get('ifov_y', 1)
    if not timestamps:
        return None

    pcfg  = data.get('projection_cfg', {})
    img_w = pcfg.get('corr_swath')   # image width in pixels = swath in km (1 km/px)
    if not img_w:
        return None

    try:
        sat = Satrec.twoline2rv(fix_tle(tle_raw['line1']), fix_tle(tle_raw['line2']))
    except Exception:
        return None

    # Barcelona in ECEF (km, spherical Earth is good enough here)
    b_ecef = (
        EARTH_R * math.cos(math.radians(BARCELONA_LAT)) * math.cos(math.radians(BARCELONA_LON)),
        EARTH_R * math.cos(math.radians(BARCELONA_LAT)) * math.sin(math.radians(BARCELONA_LON)),
        EARTH_R * math.sin(math.radians(BARCELONA_LAT)),
    )

    # --- Find best scan line: minimise |along-track component| ---
    # This is the line whose scan plane is perpendicular to Barcelona,
    # rather than the naïve "sub-satellite lat closest to Barcelona lat".
    best_i      = None
    best_along  = 1e9
    best_r_ecef = None
    best_v_ecef = None
    at_values   = []   # along-track at each valid timestamp (for sign-change check)

    for i, ts in enumerate(timestamps):
        jd = 2440587.5 + ts / 86400.0
        try:
            e, r_eci, v_eci = sat.sgp4(int(jd), jd - int(jd))
        except Exception:
            at_values.append(None)
            continue
        if e != 0:
            at_values.append(None)
            continue

        r_ecef = eci_to_ecef(r_eci, ts)
        v_ecef = eci_to_ecef(v_eci, ts)
        along  = norm(v_ecef)
        d_vec  = sub(b_ecef, r_ecef)
        at     = dot(d_vec, along)   # signed along-track (km)
        at_values.append(at)

        if abs(at) < best_along:
            best_along  = abs(at)
            best_i      = i
            best_r_ecef = r_ecef
            best_v_ecef = v_ecef

    if best_i is None:
        return None

    # Reject if the scan plane never crossed Barcelona during this pass.
    # Filter out garbage timestamps (physically impossible along-track values;
    # max realistic is ~2200 km for a 10-min pass window at 820 km altitude).
    # Then check that the along-track component changes sign at least once,
    # meaning the scan plane actually passed through Barcelona's position.
    clean_at = [v for v in at_values if v is not None and abs(v) < 2500]
    has_crossing = any((a > 0) != (b > 0) for a, b in zip(clean_at, clean_at[1:]))
    if not has_crossing:
        return None   # Barcelona's scan-plane crossing is outside this recording

    # --- Compute column via ECEF cross-track vector ---
    # ct = along × nadir  (points right-of-velocity in image space for both pass directions)
    r_unit = norm(best_r_ecef)
    nadir  = tuple(-x for x in r_unit)
    along  = norm(best_v_ecef)
    ct     = norm(cross3(along, nadir))

    d_vec  = sub(b_ecef, best_r_ecef)
    ct_km  = dot(d_vec, ct)   # negative = left in raw image, positive = right

    half_swath = img_w / 2.0
    if abs(ct_km) > half_swath + 100:
        return None   # Barcelona outside swath

    col = int((ct_km / half_swath + 1.0) / 2.0 * img_w)
    col = max(0, min(img_w - 1, col))
    row = best_i * ifov_y

    # Great-circle distance for diagnostics
    b_lat = math.degrees(math.atan2(b_ecef[2],
                         math.sqrt(b_ecef[0]**2 + b_ecef[1]**2)))
    b_lon = math.degrees(math.atan2(b_ecef[1], b_ecef[0]))
    s_lat = math.degrees(math.atan2(best_r_ecef[2],
                         math.sqrt(best_r_ecef[0]**2 + best_r_ecef[1]**2)))
    dlat = math.radians(b_lat - s_lat)
    dlon = math.radians(b_lon - math.degrees(math.atan2(best_r_ecef[1], best_r_ecef[0])))
    a    = (math.sin(dlat/2)**2
            + math.cos(math.radians(s_lat)) * math.cos(math.radians(b_lat))
            * math.sin(dlon/2)**2)
    dist_km = round(2 * math.asin(math.sqrt(a)) * EARTH_R, 1)

    # Rotation angle to align the satellite's along-track (N-S image axis) with the
    # screen vertical.  The orbit inclination (98.6°) makes the ground track deviate
    # ~11° from true north at 41°N.  We project the *true ECEF* velocity into local ENU
    # to get the exact azimuth, then compute the CSS rotate() correction.
    #
    # v_ecef here must be the actual rate of change of the ECEF position, which requires
    # subtracting the frame-rotation term (omega x r) -- just rotating v_eci by the same
    # GMST angle as the position (as eci_to_ecef does) leaves out this term and biases
    # the azimuth by a few degrees.
    omega_cross_r = (-EARTH_OMEGA * best_r_ecef[1], EARTH_OMEGA * best_r_ecef[0], 0.0)
    v_ecef_true   = tuple(best_v_ecef[i] - omega_cross_r[i] for i in range(3))

    r_xy       = math.sqrt(best_r_ecef[0]**2 + best_r_ecef[1]**2)
    r_lat_rad  = math.atan2(best_r_ecef[2], r_xy)
    r_lon_rad  = math.atan2(best_r_ecef[1], best_r_ecef[0])
    east_hat   = (-math.sin(r_lon_rad), math.cos(r_lon_rad), 0.0)
    north_hat  = (-math.sin(r_lat_rad)*math.cos(r_lon_rad),
                  -math.sin(r_lat_rad)*math.sin(r_lon_rad),
                   math.cos(r_lat_rad))
    v_e = dot(v_ecef_true, east_hat)
    v_n = dot(v_ecef_true, north_hat)
    az  = math.degrees(math.atan2(v_e, v_n))   # true bearing of "raw image down" direction

    # Raw image "down" (increasing row/time) always starts at screen-bearing 180°
    # (straight down) before any CSS transform. We want its *real* bearing (az) to end
    # up displayed at that same screen bearing once rotate() is applied, so the whole
    # image reads north-up.
    #
    #   StoN (no flip): screen-bearing before rotate = 180° -> rotate by (az - 180°)
    #   NtoS (scale(-1,-1) flip applied first): flip adds 180° to screen-bearing,
    #     giving 0° (straight up) before rotate -> rotate by (az - 0°) = az
    #
    # do_flip must match index.html's needsFlip() (hour-of-day direction label), not
    # the satellite's true ascending/descending motion -- those disagree for some
    # passes, and it's the actual applied flip that the rotation must compensate for.
    if do_flip:       # NtoS label, flip applied
        rotation_deg = az
    else:             # StoN label, no flip
        rotation_deg = az - 180.0
    rotation_deg = round(((rotation_deg + 180.0) % 360.0) - 180.0, 1)   # normalise

    return {'row': row, 'col': col, 'distKm': dist_km, 'rotationDeg': rotation_deg}

def process_dir(decoded_dir):
    cbor_path = os.path.join(decoded_dir, 'MSU-MR', 'product.cbor')
    meta_path = os.path.join(decoded_dir, 'meta.json')

    if not os.path.exists(cbor_path):
        return False

    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    do_flip = pass_is_ntos(decoded_dir, meta)
    result  = compute_barcelona_pixel(cbor_path, do_flip)
    name    = os.path.basename(decoded_dir)

    if result is None:
        print(f'  {name}: Barcelona not in frame or no TLE data')
        return False

    meta['barcelonaPx'] = result
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)

    print(f'  {name}: row={result["row"]}, col={result["col"]}, dist={result["distKm"]}km')
    return True

if __name__ == '__main__':
    dirs = sys.argv[1:] if len(sys.argv) > 1 else sorted(glob.glob('decoded_*'))
    dirs = [d for d in dirs if os.path.isdir(d)]
    print(f'Processing {len(dirs)} directories...')
    ok = sum(1 for d in dirs if process_dir(d))
    print(f'Done: {ok}/{len(dirs)} passes have Barcelona in frame')
