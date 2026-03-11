#!/bin/bash

# System monitor for recording sessions
# Logs CPU temp, throttling, disk I/O, and USB stats during recording

if [ -z "$1" ]; then
    echo "Usage: ./monitor_recording.sh <session_dir>"
    echo "Run this AFTER starting record.sh"
    echo "Example: ./monitor_recording.sh sessions/session_0220_170530"
    exit 1
fi

SESSION_DIR="$1"
LOG_FILE="${SESSION_DIR}/system_monitor.log"

if [ ! -d "$SESSION_DIR" ]; then
    echo "Error: Session directory $SESSION_DIR not found"
    exit 1
fi

echo "Monitoring system stats for session: $SESSION_DIR"
echo "Logging to: $LOG_FILE"
echo "Press Ctrl+C to stop"
echo ""

# Header
echo "timestamp,temp_c,throttled,disk_util_pct,disk_write_mbs,net_tx_mbs,net_rx_mbs" > "$LOG_FILE"

SAMPLE_INTERVAL=1  # Sample once per second (aligns with iostat 1s window)

# Get network interface (usually eth0)
NET_INTERFACE=$(ip route | grep default | awk '{print $5}' | head -n1)
if [ -z "$NET_INTERFACE" ]; then
    NET_INTERFACE="eth0"
fi

# Determine the block device backing the session directory (e.g., mmcblk0)
DISK_DEVICE=$(df -P "$SESSION_DIR" | tail -n1 | awk '{print $1}')
DISK_DEVICE=${DISK_DEVICE##*/}
# iostat often reports the base device (mmcblk0) rather than the partition (mmcblk0p2)
DISK_DEVICE_BASE=$(echo "$DISK_DEVICE" | sed -E 's/p?[0-9]+$//')
if [ -z "$DISK_DEVICE_BASE" ]; then
    DISK_DEVICE_BASE="mmcblk0"
fi

# Track previous network stats for calculating rates
PREV_TX_BYTES=0
PREV_RX_BYTES=0
PREV_TIME=0
WARMUP=1

while true; do
    TIMESTAMP=$(date +%s.%N)
    
    # CPU Temperature (remove 'C and temp= prefix)
    TEMP=$(vcgencmd measure_temp | cut -d'=' -f2 | tr -d "'C")
    
    # Throttle status (0x0 = good, anything else = throttling)
    THROTTLED=$(vcgencmd get_throttled | cut -d'=' -f2)
    
    # Disk I/O stats - use 2 samples, take the 2nd one (current activity, not since-boot average)
    # Column 23 = %util, Column 9 = wkB/s (write kilobytes per second)
    DISK_STATS=$(iostat -x -d 1 2 | awk -v dev="$DISK_DEVICE" -v base="$DISK_DEVICE_BASE" '$1==dev || $1==base{line=$0} END{if (line!="") {split(line,a," "); printf "%.1f,%.1f", a[23], a[9]/1024}}')
    
    if [ -z "$DISK_STATS" ]; then
        DISK_STATS="0.0,0.0"
    fi
    
    # Network stats - read from /proc/net/dev
    # Format: "  eth0: RX_bytes RX_packets RX_errs ... TX_bytes TX_packets ..."
    # After colon: col1=RX_bytes col9=TX_bytes
    NET_STATS=$(cat /proc/net/dev | grep "$NET_INTERFACE" | awk -F: '{print $2}' | awk '{print $1","$9}')
    
    if [ -n "$NET_STATS" ] && [ "$PREV_TIME" != "0" ]; then
        CUR_RX=$(echo "$NET_STATS" | cut -d',' -f1)
        CUR_TX=$(echo "$NET_STATS" | cut -d',' -f2)
        
        TIME_DELTA=$(echo "$TIMESTAMP - $PREV_TIME" | bc)
        RX_DELTA=$(echo "$CUR_RX - $PREV_RX_BYTES" | bc)
        TX_DELTA=$(echo "$CUR_TX - $PREV_TX_BYTES" | bc)
        
        # Convert bytes/sec to MB/sec (bytes need to be divided by 1048576)
        if [ $(echo "$TIME_DELTA > 0" | bc) -eq 1 ]; then
            NET_TX_MBS=$(echo "scale=1; $TX_DELTA / $TIME_DELTA / 1048576" | bc)
            NET_RX_MBS=$(echo "scale=1; $RX_DELTA / $TIME_DELTA / 1048576" | bc)
        else
            NET_TX_MBS="0.0"
            NET_RX_MBS="0.0"
        fi
        
        # Handle negative values (shouldn't happen, but just in case)
        # NOTE: If bc outputs negative values, the default zeros below will be used.
    else
        NET_TX_MBS="0.0"
        NET_RX_MBS="0.0"
    fi
    
    PREV_TIME=$TIMESTAMP
    PREV_TX_BYTES=$(echo "$NET_STATS" | cut -d',' -f2)
    PREV_RX_BYTES=$(echo "$NET_STATS" | cut -d',' -f1)

    if [ "$WARMUP" -eq 1 ]; then
        WARMUP=0
        sleep $SAMPLE_INTERVAL
        continue
    fi
    
    echo "${TIMESTAMP},${TEMP},${THROTTLED},${DISK_STATS},${NET_TX_MBS},${NET_RX_MBS}" >> "$LOG_FILE"
    
    # Display live summary
    printf "\r[%s] Temp: %5s°C | Disk: %s | Net TX: %5.1f MB/s RX: %5.1f MB/s" \
        "$(date +%H:%M:%S)" "$TEMP" "$DISK_STATS" "$NET_TX_MBS" "$NET_RX_MBS"
    
    sleep $SAMPLE_INTERVAL
done
