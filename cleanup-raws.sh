#!/bin/bash
# Delete leftover raw IQ recordings (meteor_*.raw, ~7 GB each) that the n8n
# Tracking workflow didn't clean up -- e.g. when a pass's decode failed.
# A full record + decode cycle finishes well within MAX_AGE_MIN, so anything
# older is an orphan. Files still open by rtl_sdr/satdump are never touched.
BASE_DIR="/home/yannklein/satellite-images"
MAX_AGE_MIN="${MAX_AGE_MIN:-180}"

find "$BASE_DIR" -maxdepth 1 -name 'meteor_*.raw' -mmin +"$MAX_AGE_MIN" | while read -r raw; do
    if pgrep -f "$(basename "$raw")" >/dev/null; then
        echo "skip (in use): $raw"
        continue
    fi
    echo "delete: $raw ($(du -h "$raw" | cut -f1))"
    rm -f "$raw" "$raw.meta.json"
done
