#!/bin/bash
# Rebuild elektro.json (the ELEKTRO-L3 full-disk gallery data consumed by
# index.html). Optionally syncs new images in first from a SatDump
# `elektro_lrit` live output dir:
#
#   bash rebuild-elektro.sh                       # just rescan elektro/
#   bash rebuild-elektro.sh ~/elektro_test        # copy new slots, then rescan
#
# Layout: elektro/<SATELLITE>/<YYYY-MM-DD_HH-MM-SS>/*.png (+ product.cbor),
# exactly as SatDump writes it under IMAGES/. For every slot with channels
# 1-3, elektro_natcolor.py builds natural_color.png and elektro_borders.py
# draws coastlines/borders on it (natural_color_borders.png) -- those two are
# what index.html features, along with ir_ch4/ir_ch9_enhanced_borders.png
# (elektro_ir_enhanced.py: color-enhanced IR channels 4 and 9 + borders,
# lost-data strips repaired from the other IR channel);
# everything else is listed as raw links.
# Full-res PNGs are 2784x2784 and up to ~9MB, so the featured ones get a ~900px JPG
# in preview/ for the thumbnails; the lightbox still opens the full PNG.
#
# Optional per-slot meta.json: {"quality": "poor"} collapses that slot on the
# page (e.g. a capture full of dropout strips).
BASE_DIR="/home/yannklein/satellite-images"
ELEKTRO_DIR="$BASE_DIR/elektro"
OUT_FILE="$BASE_DIR/elektro.json"
PY="$BASE_DIR/.venv/bin/python"     # numpy, pillow, scipy
NE_DIR="$BASE_DIR/naturalearth"     # ne_50m_coastline + admin_0_boundary_lines_land geojson

# Run by the n8n "ELEKTRO processing" workflow every few minutes: never let two
# runs overlap (a slot's first processing can take longer than the interval).
exec 9>/tmp/rebuild-elektro.lock
flock -n 9 || { echo "rebuild-elektro already running, skipping"; exit 0; }

if [ -n "$1" ]; then
    SRC="$1/IMAGES"
    [ -d "$SRC" ] || { echo "no IMAGES/ under $1" >&2; exit 1; }
    mkdir -p "$ELEKTRO_DIR"
    # Copy, never move: the source is usually a capture SatDump is still
    # writing into. Only files untouched for 2+ minutes are taken, so a PNG
    # caught mid-write is never copied (cp -u would then never fix it, the
    # copy being newer than the source). -u keeps re-runs cheap and picks up
    # channels that land after the composite (SatDump writes a slot's files
    # over several minutes), and a source rewritten later is copied again.
    (cd "$SRC" && find . -type f -mmin +2 -print0) | while IFS= read -r -d '' f; do
        f=${f#./}
        mkdir -p "$ELEKTRO_DIR/$(dirname "$f")"
        cp -u "$SRC/$f" "$ELEKTRO_DIR/$f"
    done
fi

entries=()
for slot_dir in $(ls -d "$ELEKTRO_DIR"/*/*/ 2>/dev/null | sort -r); do
    slot_dir=${slot_dir%/}
    slot=$(basename "$slot_dir")                       # 2026-11-08_15-30-00 (UTC)
    sat=$(basename "$(dirname "$slot_dir")")           # ELEKTRO-L3
    ch1=$(ls "$slot_dir"/L3_1_*.png 2>/dev/null | head -1)
    ch2=$(ls "$slot_dir"/L3_2_*.png 2>/dev/null | head -1)
    ch3=$(ls "$slot_dir"/L3_3_*.png 2>/dev/null | head -1)
    nat="$slot_dir/natural_color.png"
    if [ -n "$ch1" ] && [ -n "$ch2" ] && [ -n "$ch3" ]; then
        if [ ! -f "$nat" ] || [ "$ch1" -nt "$nat" ] || [ "$ch2" -nt "$nat" ] || [ "$ch3" -nt "$nat" ]; then
            "$PY" -W ignore "$BASE_DIR/elektro_natcolor.py" "$ch1" "$ch2" "$ch3" "$nat" >/dev/null
        fi
        if [ ! -f "$slot_dir/natural_color_borders.png" ] || [ "$nat" -nt "$slot_dir/natural_color_borders.png" ]; then
            "$PY" "$BASE_DIR/elektro_borders.py" "$nat" "$slot_dir/natural_color_borders.png" "$NE_DIR" >/dev/null
        fi
    fi

    # Each IR channel is passed the other one as partner so lost-data strips
    # get rebuilt from it (see elektro_ir_enhanced.py).
    for ch in 4 9; do
        [ $ch = 4 ] && pch=9 || pch=4
        raw=$(ls "$slot_dir"/L3_${ch}_*.png 2>/dev/null | head -1)
        partner=$(ls "$slot_dir"/L3_${pch}_*.png 2>/dev/null | head -1)
        out="$slot_dir/ir_ch${ch}_enhanced_borders.png"
        if [ -n "$raw" ] && { [ ! -f "$out" ] || [ "$raw" -nt "$out" ] || { [ -n "$partner" ] && [ "$partner" -nt "$out" ]; }; }; then
            "$PY" -W ignore "$BASE_DIR/elektro_ir_enhanced.py" "$raw" "$out" "$NE_DIR" $partner >/dev/null
        fi
    done

    pngs=$(cd "$slot_dir" && ls *.png 2>/dev/null)
    [ -z "$pngs" ] && continue
    quality=$(jq -r '.quality // "unrated"' "$slot_dir/meta.json" 2>/dev/null || echo unrated)

    # Thumbnails only exist for the featured composites (ELEKTRO_WANTED in
    # index.html); raw channels are plain links to the full PNG.
    mkdir -p "$slot_dir/preview"
    for png in natural_color.png natural_color_borders.png ir_ch4_enhanced_borders.png ir_ch9_enhanced_borders.png; do
        [ -f "$slot_dir/$png" ] || continue
        jpg="$slot_dir/preview/${png%.png}.jpg"
        if [ ! -f "$jpg" ] || [ "$slot_dir/$png" -nt "$jpg" ]; then
            convert "$slot_dir/$png" -resize 900x900 -quality 82 "$jpg"
        fi
    done

    imgs_json=$(printf '%s\n' $pngs | jq -R . | jq -s .)
    # Newest mtime in the slot: appended as ?v= to image URLs so Cloudflare
    # (max-age 4h) doesn't keep serving an older render under the same name.
    rev=$(cd "$slot_dir" && stat -c %Y $pngs preview/*.jpg 2>/dev/null | sort -n | tail -1)
    entries+=("$(jq -n \
        --arg satellite "$sat" \
        --arg id "${sat}_${slot}" \
        --arg folder "elektro/$sat/$slot" \
        --arg date "${slot:0:10}" \
        --arg time "${slot:11:2}:${slot:14:2}" \
        --arg quality "$quality" \
        --argjson rev "${rev:-0}" \
        --argjson imgs "$imgs_json" \
        '{id: $id, satellite: $satellite, folder: $folder, date: $date, time: $time, quality: $quality, rev: $rev, imgs: $imgs}')")
done

# Write-then-rename: the page polls elektro.json, never serve it half-written.
if [ ${#entries[@]} -eq 0 ]; then
    echo '[]' > "$OUT_FILE.tmp"
else
    printf '%s\n' "${entries[@]}" | jq -s . > "$OUT_FILE.tmp"
fi
mv "$OUT_FILE.tmp" "$OUT_FILE"
echo "elektro.json: ${#entries[@]} slot(s)"
