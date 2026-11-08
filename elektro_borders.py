#!/usr/bin/env python3
"""Draw coastlines + country borders on an Elektro-L3 (14.5W) full-disk image.
usage: elektro_borders.py in.png out.png [ne_dir] [style]
style: "natural" (default: white borders, yellow coast) or "ir" (thin yellow lines
for the color-enhanced IR images, see elektro_ir_enhanced.py).
Needs ne_50m_coastline.geojson and ne_50m_admin_0_boundary_lines_land.geojson (Natural Earth)
in ne_dir (default: folder of this script).
No cartopy: the CGMS geostationary projection is computed directly.
"""
import sys, os, json
import numpy as np
from PIL import Image, ImageDraw

# Disk geometry of the 2784x2784 Elektro LRIT full disk, fitted on the 15:30 channel 4 image
CX, CY = 1389.5, 1391.7          # disk centre (px)
R_EQ_PX = 1359.4                 # equatorial radius (px)
LON0 = -14.5                     # sub-satellite longitude
H = 42164.16                     # distance satellite - Earth centre (km)
R_EQ, R_POL = 6378.137, 6356.7523
PX_PER_RAD = R_EQ_PX / np.arcsin(R_EQ / H)
SS = 2                           # supersampling for smooth lines

# (geojson, RGBA, width) per style, drawn in order
STYLES = {
    "natural": [("ne_50m_admin_0_boundary_lines_land.geojson", (255, 255, 255, 170), 2),
                ("ne_50m_coastline.geojson", (255, 220, 60, 230), 3)],
    "ir":      [("ne_50m_admin_0_boundary_lines_land.geojson", (255, 255, 0, 88), 2),
                ("ne_50m_coastline.geojson", (255, 255, 0, 255), 3)],
}

def project(lon, lat):
    lon, lat = np.radians(lon), np.radians(lat)
    clat = np.arctan((R_POL**2 / R_EQ**2) * np.tan(lat))
    e2 = 1 - R_POL**2 / R_EQ**2
    rl = R_POL / np.sqrt(1 - e2 * np.cos(clat)**2)
    dl = lon - np.radians(LON0)
    px, py, pz = rl*np.cos(clat)*np.cos(dl), rl*np.cos(clat)*np.sin(dl), rl*np.sin(clat)
    r1, r2, r3 = H - px, -py, pz
    rn = np.sqrt(r1**2 + r2**2 + r3**2)
    visible = H * px - (px**2 + py**2 + pz**2) > 0
    x = np.arctan(-r2 / r1)
    y = np.arcsin(r3 / rn)
    return CX + x * PX_PER_RAD, CY - y * PX_PER_RAD, visible

def lines(path):
    for f in json.load(open(path))["features"]:
        g = f["geometry"]
        parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        for p in parts:
            yield np.array(p)

def draw(img, ne_dir, style="natural"):
    W, Hh = img.size
    ov = Image.new("RGBA", (W*SS, Hh*SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for name, color, width in STYLES[style]:
        for p in lines(os.path.join(ne_dir, name)):
            x, y, vis = project(p[:, 0], p[:, 1])
            # split each line where it goes behind the Earth
            seg = []
            for xi, yi, v in zip(x, y, vis):
                if v:
                    seg.append((xi*SS, yi*SS))
                elif len(seg) > 1:
                    d.line(seg, fill=color, width=width*SS//2); seg = []
                else:
                    seg = []
            if len(seg) > 1:
                d.line(seg, fill=color, width=width*SS//2)
    ov = ov.resize((W, Hh), Image.LANCZOS)
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

if __name__ == "__main__":
    ne_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(os.path.abspath(__file__))
    style = sys.argv[4] if len(sys.argv) > 4 else "natural"
    draw(Image.open(sys.argv[1]), ne_dir, style).save(sys.argv[2])
    print(sys.argv[2])
