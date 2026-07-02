#!/bin/bash
BASE_DIR="/home/yannklein/satellite-images"
PASS_FILE="$BASE_DIR/passes.json"
TMP_FILE=$(mktemp)

for decoded_dir in $(ls -td "$BASE_DIR"/decoded_* 2>/dev/null); do
    decoded_name=$(basename "$decoded_dir")

    META_FILE="$decoded_dir/meta.json"
    if [ -f "$META_FILE" ]; then
        satellite=$(jq -r '.satellite // "Unknown"' "$META_FILE")
        maxEl=$(jq -r '.maxEl // null' "$META_FILE")
        frequency=$(jq -r '.frequency // null' "$META_FILE")
        gain=$(jq -r '.gain // null' "$META_FILE")
        barcelonaPx=$(jq -c '.barcelonaPx // null' "$META_FILE")
        exclude=$(jq -r '.exclude // false' "$META_FILE")
        meta_date=$(jq -r '.date // null' "$META_FILE")
        meta_time=$(jq -r '.time // null' "$META_FILE")
        if [ "$meta_date" != "null" ] && [ -n "$meta_date" ]; then
            timestamp="$meta_date"
            time="$meta_time"
        else
            timestamp=$(stat -c %y "$decoded_dir" | cut -d' ' -f1)
            time="$(stat -c %y "$decoded_dir" | cut -d' ' -f2 | cut -d':' -f1-2) CEST"
        fi
    else
        satellite="Unknown"; maxEl="null"; frequency="null"; gain="null"; barcelonaPx="null"; exclude="false"
        timestamp=$(stat -c %y "$decoded_dir" | cut -d' ' -f1)
        time="$(stat -c %y "$decoded_dir" | cut -d' ' -f2 | cut -d':' -f1-2) CEST"
    fi

    png_files=$(find "$decoded_dir/MSU-MR" -maxdepth 1 -name "*.png" -size +0c 2>/dev/null | sort | xargs -I{} basename {} | jq -R . | jq -s .)
    if [ -z "$png_files" ]; then png_files="[]"; fi

    hour=$(echo "$time" | cut -d':' -f1)
    if [ "$((10#$hour))" -ge 5 ] && [ "$((10#$hour))" -lt 14 ]; then
    	direction="StoN"
    else
    	direction="NtoS"
    fi

    jq -n \
        --arg date "$timestamp" \
        --arg time "$time" \
        --arg satellite "$satellite" \
        --arg folder "$decoded_name" \
        --argjson imgs "$png_files" \
        --argjson maxEl "$maxEl" \
        --argjson frequency "$frequency" \
        --argjson gain "$gain" \
	--arg direction "$direction" \
	--argjson barcelonaPx "$barcelonaPx" \
	--argjson exclude "$exclude" \
	'{date: $date, time: $time, satellite: $satellite, folder: $folder, imgs: $imgs, maxEl: $maxEl, frequency: $frequency, gain: $gain, direction: $direction, barcelonaPx: $barcelonaPx, exclude: $exclude}' >> "$TMP_FILE"
done

if [ -s "$TMP_FILE" ]; then
    jq -s '.' "$TMP_FILE" > "$PASS_FILE"
else
    echo "[]" > "$PASS_FILE"
fi

rm -f "$TMP_FILE"
echo "Done! $(cat $PASS_FILE | jq length) passes registered"
