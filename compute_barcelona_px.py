#!/usr/bin/env python3
"""
compute_barcelona_px.py — compute Barcelona's pixel position in each MSU-MR pass,
plus pixel positions of other major cities that happen to fall in frame.

Adds to each pass's meta.json:
  'barcelonaPx': {row, col, distKm, rotationDeg}
  'citiesPx':    [{name, row, col, distKm}, ...]  (only cities actually in frame)

Row: scan line where the along-track component of sat→target is zero
     (i.e. the satellite's scan plane is perpendicular to the target).
Col: cross-track component of sat→target projected onto ct = along × nadir,
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

# WGS84 ellipsoid (SatDump's own raytracer intersects this ellipsoid, not a sphere;
# using a plain sphere for city lat/lon->ECEF puts targets several to tens of km off
# at mid-latitudes since geodetic and geocentric latitude diverge by up to ~11.5').
WGS84_A  = 6378.137            # semi-major axis, km
WGS84_F  = 1 / 298.257223563   # flattening
WGS84_E2 = WGS84_F * (2 - WGS84_F)

# Major cities to label on the timelapse, if they land within the swath.
# (name, lat, lon)
CITIES = [
    ("Barcelona",  41.3874,   2.1686),
    ("Madrid",     40.4168,  -3.7038),
    ("Lisbon",     38.7223,  -9.1393),
    ("Paris",      48.8566,   2.3522),
    ("London",     51.5072,  -0.1276),
    ("Rome",       41.9028,  12.4964),
    ("Milan",      45.4642,   9.1900),
    ("Marseille",  43.2965,   5.3698),
    ("Toulouse",   43.6047,   1.4442),
    ("Zaragoza",   41.6488,  -0.8891),
    ("Valencia",   39.4699,  -0.3763),
    ("Seville",    37.3891,  -5.9845),
    ("Algiers",    36.7538,   3.0588),
    ("Tunis",      36.8065,  10.1815),
    ("Casablanca", 33.5731,  -7.5898),
    ("Geneva",     46.2044,   6.1432),
    ("Naples",     40.8518,  14.2681),
]

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

def latlon_to_ecef(lat, lon):
    """WGS84 ellipsoid surface point (alt=0) for a geodetic lat/lon, matching
    what SatDump's raytracer actually intersects -- not a sphere."""
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    n = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(lat_r)**2)
    return (
        n * math.cos(lat_r) * math.cos(lon_r),
        n * math.cos(lat_r) * math.sin(lon_r),
        n * (1 - WGS84_E2) * math.sin(lat_r),
    )

def propagate_track(timestamps, sat, timestamp_offset=0.0):
    """SGP4-propagate the satellite once for every scan-line timestamp, in ECEF.
    Returns a list of (r_ecef, v_ecef) or None (per timestamp), reused across
    every city's pixel lookup so we don't re-propagate per city.

    timestamp_offset mirrors SatDump's own raytracer (normal_line.cpp), which adds
    this to every raw timestamp before looking up the satellite's position -- it's
    a real correction, not just cosmetic, so a matching shift is applied here too."""
    track = []
    for ts0 in timestamps:
        ts = ts0 + timestamp_offset
        jd = 2440587.5 + ts / 86400.0
        try:
            e, r_eci, v_eci = sat.sgp4(int(jd), jd - int(jd))
        except Exception:
            track.append(None)
            continue
        if e != 0:
            track.append(None)
            continue
        track.append((eci_to_ecef(r_eci, ts), eci_to_ecef(v_eci, ts)))
    return track

def find_target_pixel(target_ecef, track, ifov_y, img_w, roll_offset=0.0):
    """Find the (row, col, distKm) of an ECEF target point within a pass, plus
    the satellite's ECEF position/velocity at the crossing (needed by the
    caller for rotation). Returns None if the target never crossed the scan
    plane or falls outside the swath.

    Row: scan line where the along-track component of sat->target is zero
         (i.e. the satellite's scan plane is perpendicular to the target).
    Col: the true satellite roll angle to the target, converted to ground
         arc-distance via the same geometry as SatDump's own earth-curvature
         correction (earth_curvature.cpp) -- see comment below for why this
         isn't just a linear cross-track projection.
    """
    best_i      = None
    best_along  = 1e9
    best_r_ecef = None
    best_v_ecef = None
    at_values   = []   # along-track at each valid timestamp (for sign-change check)

    for i, entry in enumerate(track):
        if entry is None:
            at_values.append(None)
            continue
        r_ecef, v_ecef = entry
        along = norm(v_ecef)
        d_vec = sub(target_ecef, r_ecef)
        at    = dot(d_vec, along)   # signed along-track (km)
        at_values.append(at)

        if abs(at) < best_along:
            best_along  = abs(at)
            best_i      = i
            best_r_ecef = r_ecef
            best_v_ecef = v_ecef

    if best_i is None:
        return None

    # Reject if the scan plane never crossed the target during this pass.
    # Filter out garbage timestamps (physically impossible along-track values;
    # max realistic is ~2200 km for a 10-min pass window at 820 km altitude).
    # Then check that the along-track component changes sign at least once,
    # meaning the scan plane actually passed through the target's position.
    clean_at = [v for v in at_values if v is not None and abs(v) < 2500]
    has_crossing = any((a > 0) != (b > 0) for a, b in zip(clean_at, clean_at[1:]))
    if not has_crossing:
        return None   # target's scan-plane crossing is outside this recording

    # --- Compute column via true roll angle, converted through SatDump's own
    # earth-curvature geometry ---
    #
    # A plain linear cross-track chord projection (ct = along x nadir, then
    # dot with the target vector) turns out to match SatDump's ground-distance
    # grid almost exactly *except* for one thing: earth_curvature.cpp builds
    # the corrected (ground-distance-linear) image by treating the raw image's
    # center column as roll angle 0 -- it has no notion of the sensor's real
    # boresight offset (roll_offset), which shifts true roll-0 a few tenths of
    # a degree away from the raw image's geometric center. So the corrected
    # image's nadir column is systematically off by roll_offset's worth of
    # ground distance (a few km) unless we correct for it here.
    r_unit = norm(best_r_ecef)
    nadir  = tuple(-x for x in r_unit)
    along  = norm(best_v_ecef)

    d_vec  = sub(target_ecef, best_r_ecef)
    dist   = math.sqrt(sum(x**2 for x in d_vec))
    p_unit = tuple(x / dist for x in d_vec)

    # True roll angle (signed, about the along-track axis) from nadir to the target.
    cos_a = dot(nadir, p_unit)
    sin_a = dot(cross3(nadir, p_unit), along)
    eta   = math.atan2(sin_a, cos_a)

    # Shift into the frame earth_curvature.cpp assumes (zero boresight offset),
    # then invert its ground-angle <-> satellite-angle relationship (law of
    # sines in the earth center / satellite / target triangle) to get the true
    # ground arc-distance -- which is exactly what the corrected image's
    # column axis is linear in.
    eta_geom = eta - math.radians(roll_offset)
    rs       = math.sqrt(sum(x**2 for x in best_r_ecef))
    sin_lam  = max(-1.0, min(1.0, (rs / EARTH_R) * math.sin(eta_geom)))
    ct_km    = EARTH_R * (math.asin(sin_lam) - eta_geom)   # negative = left, positive = right

    half_swath = img_w / 2.0
    if abs(ct_km) > half_swath + 100:
        return None   # target outside swath

    col = int(ct_km + half_swath)
    col = max(0, min(img_w - 1, col))
    row = best_i * ifov_y

    # Great-circle distance for diagnostics
    t_lat = math.degrees(math.atan2(target_ecef[2],
                         math.sqrt(target_ecef[0]**2 + target_ecef[1]**2)))
    t_lon = math.degrees(math.atan2(target_ecef[1], target_ecef[0]))
    s_lat = math.degrees(math.atan2(best_r_ecef[2],
                         math.sqrt(best_r_ecef[0]**2 + best_r_ecef[1]**2)))
    dlat = math.radians(t_lat - s_lat)
    dlon = math.radians(t_lon - math.degrees(math.atan2(best_r_ecef[1], best_r_ecef[0])))
    a    = (math.sin(dlat/2)**2
            + math.cos(math.radians(s_lat)) * math.cos(math.radians(t_lat))
            * math.sin(dlon/2)**2)
    dist_km = round(2 * math.asin(math.sqrt(a)) * EARTH_R, 1)

    return {'row': row, 'col': col, 'distKm': dist_km,
            'r_ecef': best_r_ecef, 'v_ecef': best_v_ecef}

def compute_rotation_deg(r_ecef, v_ecef, do_flip):
    # Rotation angle to align the satellite's along-track (N-S image axis) with the
    # screen vertical.  The orbit inclination (98.6°) makes the ground track deviate
    # ~11° from true north at 41°N.  We project the *true ECEF* velocity into local ENU
    # to get the exact azimuth, then compute the CSS rotate() correction.
    #
    # v_ecef here must be the actual rate of change of the ECEF position, which requires
    # subtracting the frame-rotation term (omega x r) -- just rotating v_eci by the same
    # GMST angle as the position (as eci_to_ecef does) leaves out this term and biases
    # the azimuth by a few degrees.
    omega_cross_r = (-EARTH_OMEGA * r_ecef[1], EARTH_OMEGA * r_ecef[0], 0.0)
    v_ecef_true   = tuple(v_ecef[i] - omega_cross_r[i] for i in range(3))

    r_xy       = math.sqrt(r_ecef[0]**2 + r_ecef[1]**2)
    r_lat_rad  = math.atan2(r_ecef[2], r_xy)
    r_lon_rad  = math.atan2(r_ecef[1], r_ecef[0])
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
    return round(((rotation_deg + 180.0) % 360.0) - 180.0, 1)   # normalise

def compute_barcelona_and_cities(cbor_path, do_flip):
    with open(cbor_path, 'rb') as f:
        data = cbor2.load(f)

    tle_raw = data.get('tle')
    if not tle_raw:
        return None, []

    images = data.get('images', [])
    if not images:
        return None, []

    img0       = images[0]
    timestamps = img0.get('timestamps', [])
    ifov_y     = img0.get('ifov_y', 1)
    if not timestamps:
        return None, []

    pcfg  = data.get('projection_cfg', {})
    img_w = pcfg.get('corr_swath')   # image width in pixels = swath in km (1 km/px)
    if not img_w:
        return None, []

    timestamp_offset = pcfg.get('timestamp_offset', 0.0)
    roll_offset      = pcfg.get('roll_offset', 0.0)

    try:
        sat = Satrec.twoline2rv(fix_tle(tle_raw['line1']), fix_tle(tle_raw['line2']))
    except Exception:
        return None, []

    track = propagate_track(timestamps, sat, timestamp_offset)

    b_ecef  = latlon_to_ecef(BARCELONA_LAT, BARCELONA_LON)
    bcn_hit = find_target_pixel(b_ecef, track, ifov_y, img_w, roll_offset)
    if bcn_hit is None:
        return None, []   # Barcelona not in frame -- no rotation reference, skip cities too

    rotation_deg = compute_rotation_deg(bcn_hit['r_ecef'], bcn_hit['v_ecef'], do_flip)
    barcelona_px = {'row': bcn_hit['row'], 'col': bcn_hit['col'],
                    'distKm': bcn_hit['distKm'], 'rotationDeg': rotation_deg}

    cities_px = []
    for name, lat, lon in CITIES:
        hit = find_target_pixel(latlon_to_ecef(lat, lon), track, ifov_y, img_w, roll_offset)
        if hit is not None:
            cities_px.append({'name': name, 'row': hit['row'], 'col': hit['col'],
                               'distKm': hit['distKm']})

    return barcelona_px, cities_px

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
    barcelona_px, cities_px = compute_barcelona_and_cities(cbor_path, do_flip)
    name = os.path.basename(decoded_dir)

    if barcelona_px is None:
        print(f'  {name}: Barcelona not in frame or no TLE data')
        return False

    meta['barcelonaPx'] = barcelona_px
    meta['citiesPx']    = cities_px
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2)

    print(f'  {name}: row={barcelona_px["row"]}, col={barcelona_px["col"]}, '
          f'dist={barcelona_px["distKm"]}km, cities={[c["name"] for c in cities_px]}')
    return True

if __name__ == '__main__':
    dirs = sys.argv[1:] if len(sys.argv) > 1 else sorted(glob.glob('decoded_*'))
    dirs = [d for d in dirs if os.path.isdir(d)]
    print(f'Processing {len(dirs)} directories...')
    ok = sum(1 for d in dirs if process_dir(d))
    print(f'Done: {ok}/{len(dirs)} passes have Barcelona in frame')
