#!/bin/bash

# Ensure this script runs under bash even if invoked via "sh record.sh"
if [ -z "${BASH_VERSION:-}" ]; then
    exec /bin/bash "$0" "$@"
fi

# 1. Configurable Variables
DURATION=${1:-60}
RES="1920x1080" # Change to 1920x1080 or 1280x720 as needed
#RES="1280x720"
#RES="640x480"
FPS=30
CONTAINER="mkv"  # Options: mkv, mp4

# Reduce USB bandwidth reservation for cameras
#sudo modprobe -r uvcvideo 2>/dev/null
#sudo modprobe uvcvideo quirks=128

# Detect available cameras
CAMERAS=()
for dev in /dev/video0 /dev/video2 /dev/video4 /dev/video6; do
    if [ -e "$dev" ]; then
        CAMERAS+=("$dev")
    fi
done

NUM_CAMERAS=${#CAMERAS[@]}

if [ "$NUM_CAMERAS" -eq 0 ]; then
    echo "ERROR: No cameras found!"
    echo "Script exiting."
    exit 1
fi

# Configure camera settings for each device
# To see all available controls: v4l2-ctl -d /dev/video0 --list-ctrls-menus
# 
# USER CONTROLS:
#   brightness                 : -64 to 64     (default: 0)     - Adjust image brightness
#   contrast                   : 0 to 64       (default: 32)    - Adjust contrast level
#   saturation                 : 0 to 128      (default: 64)    - Color saturation intensity
#   hue                        : -40 to 40     (default: 0)     - Color hue adjustment
#   white_balance_automatic    : bool          (default: 1)     - Auto white balance on/off
#   gamma                      : 72 to 500     (default: 100)   - Gamma correction
#   gain                       : 0 to 100      (default: 0)     - Sensor gain (ISO sensitivity)
#   power_line_frequency       : 0/1/2         (default: 1)     - Anti-flicker: 0=Disabled, 1=50Hz, 2=60Hz
#   white_balance_temperature  : 2800 to 6500  (default: 4600)  - Manual WB temp in Kelvin (inactive if auto WB on)
#   sharpness                  : 0 to 6        (default: 3)     - Image sharpness
#   backlight_compensation     : 0 to 2        (default: 1)     - Backlight compensation level
#
# CAMERA CONTROLS:
#   auto_exposure              : 1/3           (default: 3)     - 1=Manual Mode, 3=Aperture Priority (auto)
#   exposure_time_absolute     : 1 to 5000     (default: 157)   - Exposure time in 100µs units (inactive if auto)
#   exposure_dynamic_framerate : bool          (default: 0)     - Allow framerate reduction for longer exposure
#
for dev in "${CAMERAS[@]}"; do
    # sudo sh -c "echo 2048 > /sys/bus/usb/devices/*/video4linux/*/bandwidth_alloced 2>/dev/null" || true
    timeout 5 sudo v4l2-ctl -d "$dev" -c power_line_frequency=2           # Set to 60Hz filtering (US power grid)
    timeout 5 sudo v4l2-ctl -d "$dev" -c exposure_dynamic_framerate=0     # Don't limit framerate to increase exposure
    timeout 5 sudo v4l2-ctl -d "$dev" -c auto_exposure=1                  # Aperture Priority Mode (auto exposure)
    timeout 5 sudo v4l2-ctl -d "$dev" -c white_balance_automatic=0        # Disable automatic white balance
    timeout 5 sudo v4l2-ctl -d "$dev" -c exposure_time_absolute=100     # Manual exposure (only works with auto_exposure=1)
done

S_DIR="$HOME/sessions/session_$(date +%m%d_%H%M%S)"
#S_DIR="old_card/home/austin/sessions/session_$(date +%m%d_%H%M%S)"
#S_DIR="/media/austin/USBKEYCHAIN/sessions/session_$(date +%m%d_%H%M%S)"
AVAILABLE_KB=$(df . --output=avail | tail -n1)
REQUIRED_KB=$((DURATION * 15 * 1024)) # Lower estimate for 720p

if [ "$AVAILABLE_KB" -lt "$REQUIRED_KB" ]; then
    echo "ERROR: Disk space low on local storage!"
    exit 1
fi

mkdir -p "$S_DIR"
echo 1000 | sudo tee /sys/module/usbcore/parameters/usbfs_memory_mb > /dev/null

SESSION_NAME=$(basename "$S_DIR")
METRICS_FILE="$S_DIR/recording_metrics.json"

echo "Recording cameras to: $S_DIR"
echo ""

# 2. Hardware Names with USB VID:PID Identification
# Extract unique USB identifiers for camera model distinction

get_usb_ids() {
    local dev=$1
    # Get the USB bus path from the device
    # /dev/videoX -> find parent USB device -> extract VID:PID
    local dev_path=$(timeout 2 readlink -f "$dev" 2>/dev/null || echo "")
    if [ -z "$dev_path" ]; then
        echo "unknown:unknown:"
        return 1
    fi
    local parent_path="${dev_path%/*}"
    
    # Navigate up to find the video4linux device, then to USB device
    local max_iterations=20
    local iter=0
    while [ "$parent_path" != "/" ] && [ $iter -lt $max_iterations ]; do
        if [ -f "$parent_path/idVendor" ] && [ -f "$parent_path/idProduct" ]; then
            local vid=$(cat "$parent_path/idVendor" 2>/dev/null || echo "unknown")
            local pid=$(cat "$parent_path/idProduct" 2>/dev/null || echo "unknown")
            local serial=$(cat "$parent_path/serial" 2>/dev/null || echo "")
            echo "${vid}:${pid}:${serial}"
            return 0
        fi
        parent_path="${parent_path%/*}"
        iter=$((iter + 1))
    done
    echo "unknown:unknown:"
}

get_camera_model() {
    local dev=$1
    # Try to get model name from v4l2-ctl
    timeout 5 v4l2-ctl --device="$dev" --info 2>/dev/null | grep "Card" | sed 's/.*Card[[:space:]]*:[[:space:]]*//' | head -1
}

get_name() {
    local dev=$1
    local video_num="${dev##*video}"
    local color
    case "$video_num" in
        0) color="BLUE" ;;
        2) color="RED" ;;
        4) color="GREEN" ;;
        6) color="BLACK" ;;
        *) color="v${video_num}" ;;
    esac
    
    # Get USB IDs and model
    local usb_ids=$(get_usb_ids "$dev")
    local vid_pid=$(echo "$usb_ids" | cut -d':' -f1-2)
    local serial=$(echo "$usb_ids" | cut -d':' -f3)
    
    local model=$(get_camera_model "$dev" | head -1 | sed 's/^[[:space:]]*//g' | sed 's/[[:space:]]*$//g' | tr ' ' '_' | cut -d'_' -f1-3)
    
    # Build filename: COLOR_MODEL_VIDPID_SERIAL_BUS
    local bus_info=$(timeout 5 v4l2-ctl --device=$dev --info 2>/dev/null | grep 'Bus info' | awk '{print $4}' | tr -d '\n' | cut -d'.' -f2-)
    
    if [ -n "$model" ]; then
        if [ -n "$serial" ] && [ "$serial" != "unknown" ]; then
            echo "${color}_${model}_${vid_pid}_${serial}_BUS_${bus_info}"
        else
            echo "${color}_${model}_${vid_pid}_BUS_${bus_info}"
        fi
    else
        # Fallback if model name not available
        if [ -n "$serial" ] && [ "$serial" != "unknown" ]; then
            echo "${color}_${vid_pid}_${serial}_BUS_${bus_info}"
        else
            echo "${color}_${vid_pid}_BUS_${bus_info}"
        fi
    fi
}

# 3. START RECORDING (MJPEG Stream Copy)
PIDS=()
NAMES=()
for dev in "${CAMERAS[@]}"; do
    name=$(get_name "$dev") || { echo "ERROR: Failed to get device name for $dev"; exit 1; }
    NAMES+=("$name")
    ffmpeg -y -hide_banner -loglevel error -thread_queue_size 4096 -f v4l2 -input_format mjpeg -s "$RES" -framerate "$FPS" -i "$dev" -t "$DURATION" -c copy "$S_DIR/${name}.${CONTAINER}" &
    PIDS+=($!)
done

# 4. LIVE DASHBOARD
echo "Recording in progress..."
echo ""

SECONDS_ELAPSED=0
declare -A FILESIZES
TEMP_START=""
TEMP_END=""
TEMP_PEAK=0
RAM_START=""
RAM_END=""
RAM_PEAK=0

while [ "$SECONDS_ELAPSED" -le "$DURATION" ] && kill -0 ${PIDS[0]} 2>/dev/null; do
    if [ $((SECONDS_ELAPSED % 5)) -eq 0 ] || [ "$SECONDS_ELAPSED" -eq "$DURATION" ]; then
        CUR_TEMP=$(vcgencmd measure_temp | cut -d'=' -f2 | tr -d '\n')
        
        # Track temps (remove degree symbol for JSON compatibility)
        TEMP_NUM="${CUR_TEMP%°*}"
        TEMP_NUM="${TEMP_NUM%\'*}"  # Fallback if encoding is weird
        
        if [ -z "$TEMP_START" ]; then
            TEMP_START="$TEMP_NUM"
        fi
        TEMP_END="$TEMP_NUM"
        
        # Check if peak temp (floating point comparison)
        if (( $(echo "$TEMP_NUM > $TEMP_PEAK" | bc -l) )); then
            TEMP_PEAK=$(printf "%.1f" "$TEMP_NUM")
        fi
        
        # Track RAM usage (in MB)
        CUR_RAM=$(awk '/MemAvailable:/ {avail=$2} /MemTotal:/ {total=$2} END {used=(total-avail)/1024; printf "%.0f", used}' /proc/meminfo)
        
        if [ -z "$RAM_START" ]; then
            RAM_START="$CUR_RAM"
        fi
        RAM_END="$CUR_RAM"
        
        # Check if peak RAM
        if [ "$CUR_RAM" -gt "$RAM_PEAK" ]; then
            RAM_PEAK="$CUR_RAM"
        fi
        
        # Show live progress in terminal
        printf "[%3ds | %5s°C | %4dMB] " "$SECONDS_ELAPSED" "$TEMP_NUM" "$CUR_RAM"
        for i in "${!NAMES[@]}"; do
            SIZE=$(du -m "$S_DIR/${NAMES[$i]}.${CONTAINER}" 2>/dev/null | cut -f1 || echo "0")
            printf "%s:%3dMB  " "${NAMES[$i]:0:3}" "$SIZE"
            FILESIZES[$i]=$SIZE
        done
        echo ""
    fi
    sleep 1
    SECONDS_ELAPSED=$((SECONDS_ELAPSED + 1))
done

echo ""
echo "---"
echo ""


# Wait for any remaining ffmpeg processes to finish
wait ${PIDS[@]} 2>/dev/null || true

# ============================================================
# TESTING: Stop system monitor (comment out when not testing)
# ============================================================
if [ -n "${MONITOR_PID:-}" ] && kill -0 $MONITOR_PID 2>/dev/null; then
    kill $MONITOR_PID 2>/dev/null
    echo -e "\n[TESTING] System monitor stopped"
fi
# ============================================================

# 5. Generate Metrics JSON
EXPECTED=$((DURATION * FPS))
echo ""
echo "### Recording Complete - Collecting Metrics"
echo ""

# Collect camera metrics with USB identifiers
CAMERA_JSON=""
FIRST=1
CAM_INDEX=0

for f in "$S_DIR"/*."$CONTAINER"; do
    FILENAME=$(basename "$f")
    CNTS=$(ffprobe -v error -show_entries packet=pts -of compact=p=0:nk=1 "$f" | wc -l)
    SIZE_BYTES=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null)
    SIZE_MB=$(awk -v bytes="$SIZE_BYTES" 'BEGIN {printf "%.1f", bytes / 1048576}')
    FPS_ACTUAL=$(awk -v cnt="$CNTS" -v dur="$DURATION" 'BEGIN {printf "%.2f", cnt / dur}')
    MBPS=$(awk -v bytes="$SIZE_BYTES" -v dur="$DURATION" 'BEGIN {printf "%.1f", (bytes / 1048576) / dur}')
    
    # Get USB identifiers for this camera
    if [ "$CAM_INDEX" -lt "${#CAMERAS[@]}" ]; then
        dev="${CAMERAS[$CAM_INDEX]}"
        usb_ids=$(get_usb_ids "$dev")
        vid_pid=$(echo "$usb_ids" | cut -d':' -f1-2)
        serial=$(echo "$usb_ids" | cut -d':' -f3)
        model=$(get_camera_model "$dev" | head -1 | sed 's/^[[:space:]]*//g' | sed 's/[[:space:]]*$//g')
        bus_info=$(v4l2-ctl --device=$dev --info 2>/dev/null | grep 'Bus info' | awk '{print $4}' | tr -d '\n' | cut -d'.' -f2-)
    else
        vid_pid="unknown:unknown"
        serial=""
        model=""
        bus_info=""
    fi
    
    if [ $FIRST -eq 0 ]; then
        CAMERA_JSON="${CAMERA_JSON},"
    fi
    FIRST=0
    
    CAMERA_JSON="${CAMERA_JSON}
    {
      \"file\": \"$FILENAME\",
      \"frames\": $CNTS,
      \"size_mb\": $SIZE_MB,
      \"fps\": $FPS_ACTUAL,
      \"mbps\": $MBPS,
      \"usb_vid_pid\": \"$vid_pid\",
      \"serial\": \"$serial\",
      \"model\": \"$model\",
      \"bus_info\": \"$bus_info\"
    }"
    
    CAM_INDEX=$((CAM_INDEX + 1))
done

# Write JSON metrics file
cat > "$METRICS_FILE" << EOF
{
  "session": "$SESSION_NAME",
  "duration_seconds": $DURATION,
  "target_fps": $FPS,
  "target_resolution": "$RES",
  "container": "$CONTAINER",
  "started": "$(date -Iseconds)",
  "recording_dir": "$S_DIR",
  "temperatures": {
    "start_c": $TEMP_START,
    "end_c": $TEMP_END,
    "peak_c": $TEMP_PEAK
  },
  "ram_usage_mb": {
    "start": $RAM_START,
    "end": $RAM_END,
    "peak": $RAM_PEAK
  },
  "cameras": [$CAMERA_JSON
  ]
}
EOF

echo "✓ Recording complete! Metrics saved to:"
echo "  $METRICS_FILE"
echo ""
echo "📊 Running frame analysis..."
python3 "$HOME/camera_scripts/frame_analyzer.py" "$METRICS_FILE"
