#!/usr/bin/env python3
"""
add_map_overlay.py - Draw coastlines/borders on a METEOR satellite image
using TLE + timestamp geolocation from SatDump's product.cbor.

Usage:
    python3 add_map_overlay.py <product.cbor> <input.png> <output.png>
"""

import sys, math, warnings
warnings.filterwarnings('ignore')
import numpy as np
import cbor2
from PIL import Image
from sgp4.api import Satrec
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def fix_tle(line):
    """Fix SatDump's non-standard TLE checksum character."""
    body = line[:-1]
    checksum = sum(int(c) if c.isdigit() else (1 if c == '-' else 0) for c in body) % 10
    return body + str(checksum)

def eci_to_latlon(r, ts):
    """Convert ECI coordinates to geographic lat/lon using GMST."""
    x, y, z = r
    jd = 2440587.5 + ts / 86400.0
    t_ut1 = (jd - 2451545.0) / 36525.0
    gmst = (67310.54841 + (876600*3600 + 8640184.812866)*t_ut1 +
            0.093104*t_ut1**2 - 6.2e-6*t_ut1**3) % 86400
    gmst_rad = gmst * 2 * math.pi / 86400.0
    xe = x * math.cos(-gmst_rad) - y * math.sin(-gmst_rad)
    ye = x * math.sin(-gmst_rad) + y * math.cos(-gmst_rad)
    ze = z
    r_xy = math.sqrt(xe**2 + ye**2)
    lat = math.degrees(math.atan2(ze, r_xy))
    lon = math.degrees(math.atan2(ye, xe))
    return lat, lon

def compute_bounds(cbor_path):
    """Compute image geographic bounds from product.cbor."""
    with open(cbor_path, 'rb') as f:
        data = cbor2.load(f)

    pcfg = data['projection_cfg']
    tle = pcfg['tle']
    timestamps = pcfg['timestamps']
    # Use actual image dimensions for swath, not corr_swath (which is HRPT full swath)
    image_width_km = pcfg['image_width'] * pcfg['corr_resol']

    l1 = fix_tle(tle['line1'])
    l2 = fix_tle(tle['line2'])
    sat = Satrec.twoline2rv(l1, l2)

    lats, lons = [], []
    for ts in timestamps:
        if ts < 0:  # -1.0 is SatDump's sentinel for invalid timestamp
            continue
        jd = 2440587.5 + ts / 86400.0
        e, r, v = sat.sgp4(int(jd), jd - int(jd))
        if e == 0:
            lat, lon = eci_to_latlon(r, ts)
            lats.append(lat)
            lons.append(lon)

    lats = np.array(lats)
    lons = np.array(lons)

    # Account for latitude when converting swath km to degrees longitude
    center_lat = float(np.median(lats))
    center_lon = float(np.median(lons))
    swath_deg = (image_width_km / 2) / (111.0 * math.cos(math.radians(center_lat)))
    margin = 3.0

    lat_min = lats.min() - margin
    lat_max = lats.max() + margin
    lon_min = center_lon - swath_deg - margin
    lon_max = center_lon + swath_deg + margin

    # Clamp to valid range
    lat_min = max(-90, lat_min)
    lat_max = min(90, lat_max)
    lon_min = max(-180, lon_min)
    lon_max = min(180, lon_max)

    return lat_min, lat_max, lon_min, lon_max

def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <product.cbor> <input.png> <output.png>")
        sys.exit(1)

    cbor_path = sys.argv[1]
    input_png = sys.argv[2]
    output_png = sys.argv[3]

    print(f"Computing geographic bounds from {cbor_path}...")
    lat_min, lat_max, lon_min, lon_max = compute_bounds(cbor_path)
    print(f"Bounds: lat [{lat_min:.1f}, {lat_max:.1f}], lon [{lon_min:.1f}, {lon_max:.1f}]")

    print(f"Loading {input_png}...")
    img = Image.open(input_png).convert('RGB')
    img_w, img_h = img.size
    print(f"Image size: {img_w}x{img_h}")

    # Force output to same pixel dimensions as input for consistent gallery display
    dpi = 100
    fig_w = img_w / dpi
    fig_h = img_h / dpi

    fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1], projection=ccrs.PlateCarree())
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    # Draw satellite image as background
    ax.imshow(img,
              origin='upper',
              extent=[lon_min, lon_max, lat_min, lat_max],
              transform=ccrs.PlateCarree(),
              aspect='auto',
              interpolation='bilinear')

    # Draw map overlay features
    ax.add_feature(cfeature.COASTLINE.with_scale('50m'),
                   linewidth=1.0, edgecolor='yellow', alpha=0.95, zorder=10)
    ax.add_feature(cfeature.BORDERS.with_scale('50m'),
                   linewidth=0.6, edgecolor='yellow', alpha=0.75,
                   linestyle='--', zorder=10)
    ax.add_feature(cfeature.LAKES.with_scale('50m'),
                   linewidth=0.4, edgecolor='cyan', facecolor='none',
                   alpha=0.6, zorder=10)

    # Major cities (name, lon, lat)
    cities = [
        ("Lisbon",      -9.14,  38.72),
        ("Porto",       -8.61,  41.15),
        ("Madrid",      -3.70,  40.42),
        ("Seville",     -5.99,  37.39),
        ("Valencia",    -0.38,  39.47),
        ("Barcelona",    2.17,  41.39),
        ("Bilbao",      -2.93,  43.26),
        ("Toulouse",     1.44,  43.60),
        ("Marseille",    5.37,  43.30),
        ("Lyon",         4.83,  45.75),
        ("Genoa",        8.93,  44.41),
        ("Nice",         7.26,  43.71),
        ("Rome",        12.50,  41.90),
        ("Palma",        2.65,  39.57),
        ("Algiers",      3.06,  36.74),
        ("Oran",        -0.63,  35.70),
        ("Casablanca",  -7.59,  33.57),
    ]

    for city_name, city_lon, city_lat in cities:
        if lon_min < city_lon < lon_max and lat_min < city_lat < lat_max:
            ax.plot(city_lon, city_lat, 'o',
                    color='white', markersize=4, transform=ccrs.PlateCarree(),
                    zorder=20)
            ax.text(city_lon + 0.15, city_lat + 0.15, city_name,
                    fontsize=7, color='white', fontweight='bold',
                    transform=ccrs.PlateCarree(), zorder=20,
                    bbox=dict(facecolor='black', alpha=0.4, pad=1, linewidth=0))

    print(f"Saving {output_png}...")
    fig.savefig(output_png, dpi=150, bbox_inches='tight', pad_inches=0,
                facecolor='black')
    plt.close(fig)
    print("Done!")

if __name__ == '__main__':
    main()
