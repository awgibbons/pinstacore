#!/bin/bash

DURATION=${1:-3600}
DEST_BASE=${2:-"${HOME}/sessions"}
RES="1920x1080"
FPS=30
CONTAINER="mkv"
START_TS=$(date +%s)

# 1. Detect available cameras (up to 8 devices on even video nodes)
CAMERAS=()
for idx in 0 2 4 6 8 10 12 14; do
    dev="/dev/video${idx}"
    if [ -e "$dev" ]; then
        CAMERAS+=("$dev")
    fi
done

if [ "${#CAMERAS[@]}" -eq 0 ]; then
    echo "ERROR: No cameras found!"
    exit 1
fi

S_DIR="${DEST_BASE}/session_$(date +%m%d_%H%M%S)"
mkdir -p "$S_DIR"
echo "Recording ${#CAMERAS[@]} cameras to: $S_DIR for $DURATION seconds..."
METRICS_FILE="$S_DIR/recording_metrics.json"

# Expand the USB memory buffer
echo 1000 | tee /sys/module/usbcore/parameters/usbfs_memory_mb > /dev/null

# 2. Configure hardware
for dev in "${CAMERAS[@]}"; do
    (
        v4l2-ctl -d "$dev" -c power_line_frequency=2
        v4l2-ctl -d "$dev" -c exposure_dynamic_framerate=0
        v4l2-ctl -d "$dev" -c auto_exposure=3
        v4l2-ctl -d "$dev" -c white_balance_automatic=1
        #v4l2-ctl -d "$dev" -c exposure_time_absolute=100
    ) &
done

# Hold here for just a split second until all parallel config tasks finish
wait 

# 3. Start Recording
PIDS=()
CAM_NAMES=()
LOG_FILES=()
for i in "${!CAMERAS[@]}"; do
    dev="${CAMERAS[$i]}"
    cam_name="camera_$((i+1))"
    log_file="$S_DIR/${cam_name}.ffmpeg.log"

    ffmpeg -y -hide_banner -loglevel error -thread_queue_size 4096 -f v4l2 -input_format mjpeg -s "$RES" -framerate "$FPS" -i "$dev" -t "$DURATION" -c copy "$S_DIR/${cam_name}.${CONTAINER}" 2>"$log_file" &
    
    PIDS+=($!)
    CAM_NAMES+=("$cam_name")
    LOG_FILES+=("$log_file")
done

EXIT_CODES=()
for i in "${!PIDS[@]}"; do
    pid="${PIDS[$i]}"
    if wait "$pid"; then
        EXIT_CODES+=(0)
    else
        code=$?
        EXIT_CODES+=("$code")
        echo "WARNING: ${CAM_NAMES[$i]} ffmpeg exited with code $code" >&2
    fi
done
echo "Recording complete."

# 4. Write post-recording metrics for later analysis runs.
END_TS=$(date +%s)
ACTUAL_DURATION=$((END_TS - START_TS))
if [ "$ACTUAL_DURATION" -lt 1 ]; then
        ACTUAL_DURATION=1
fi

CAMERA_JSON=""
FIRST=1
for f in "$S_DIR"/*."$CONTAINER"; do
        [ -e "$f" ] || continue

        FILENAME=$(basename "$f")
        FRAME_COUNT=$(ffprobe -v error -show_entries packet=pts_time -of compact=p=0:nk=1 "$f" | wc -l)
        SIZE_BYTES=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo "0")
        SIZE_MB=$(awk -v bytes="$SIZE_BYTES" 'BEGIN {printf "%.1f", bytes / 1048576}')
        FPS_ACTUAL=$(awk -v cnt="$FRAME_COUNT" -v dur="$ACTUAL_DURATION" 'BEGIN {printf "%.2f", cnt / dur}')
        EXIT_CODE=0
        LOG_NAME="${FILENAME%.*}.ffmpeg.log"
        for idx in "${!CAM_NAMES[@]}"; do
            if [ "${CAM_NAMES[$idx]}.${CONTAINER}" = "$FILENAME" ]; then
                EXIT_CODE="${EXIT_CODES[$idx]}"
                LOG_NAME=$(basename "${LOG_FILES[$idx]}")
                break
            fi
        done

        if [ $FIRST -eq 0 ]; then
                CAMERA_JSON="${CAMERA_JSON},"
        fi
        FIRST=0

        CAMERA_JSON="${CAMERA_JSON}
        {
            \"device\": \"$FILENAME\",
            \"file\": \"$FILENAME\",
            \"frames\": $FRAME_COUNT,
            \"size_mb\": $SIZE_MB,
            \"fps\": $FPS_ACTUAL,
            \"exit_code\": $EXIT_CODE,
            \"ffmpeg_log\": \"$LOG_NAME\"
        }"
done

cat > "$METRICS_FILE" << EOF
{
    "session": "$(basename "$S_DIR")",
    "recording_dir": "$S_DIR",
    "container": "$CONTAINER",
    "target_resolution": "$RES",
    "target_fps": $FPS,
    "requested_duration_seconds": $DURATION,
    "duration_seconds": $ACTUAL_DURATION,
    "started_unix": $START_TS,
    "ended_unix": $END_TS,
    "camera_count": ${#CAMERAS[@]},
    "cameras": [${CAMERA_JSON}
    ]
}
EOF

echo "Metrics saved to: $METRICS_FILE"
