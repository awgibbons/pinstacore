#!/bin/bash

DURATION=${1:-3600}
RES="1920x1080"
FPS=30
CONTAINER="mkv"

# 1. Detect available cameras
CAMERAS=()
for dev in /dev/video0 /dev/video2 /dev/video4 /dev/video6; do
    if [ -e "$dev" ]; then
        CAMERAS+=("$dev")
    fi
done

if [ "${#CAMERAS[@]}" -eq 0 ]; then
    echo "ERROR: No cameras found!"
    exit 1
fi

S_DIR="/home/instacore/sessions/session_$(date +%m%d_%H%M%S)"
mkdir -p "$S_DIR"
echo "Recording ${#CAMERAS[@]} cameras to: $S_DIR for $DURATION seconds..."

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
for i in "${!CAMERAS[@]}"; do
    dev="${CAMERAS[$i]}"
    cam_name="camera_$((i+1))"

    ffmpeg -y -hide_banner -loglevel error -thread_queue_size 4096 -f v4l2 -input_format mjpeg -s "$RES" -framerate "$FPS" -i "$dev" -t "$DURATION" -c copy "$S_DIR/${cam_name}.${CONTAINER}" &
    
    PIDS+=($!)
done

wait "${PIDS[@]}" 2>/dev/null || true
echo "Recording complete."
