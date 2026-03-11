# Multi-Camera Recording System - Reference Documentation

**Last Updated:** February 23, 2026

---

## System Overview

This is a **4-camera simultaneous recording system** running on a Raspberry Pi 5, designed for **motion capture data collection for computer vision training**. The system captures synchronized 1080p video at 30 FPS from four USB cameras for a client with specific requirements.

### Purpose
- Collect motion capture training data using 4 synchronized camera angles
- Generate computer vision training datasets
- Provide frame-level synchronization analysis and quality reports

---

## Hardware Specifications

### Raspberry Pi 5 Configuration
- **Model:** Raspberry Pi 5
- **Storage:** 512GB V30-rated microSD card (OS + video storage)
- **Power:** Battery-powered (portability requirement)
- **USB Cameras:** 4× ELP USB500W05G USB cameras
- **USB Hubs:** 2× powered USB hubs (cameras distributed across hubs)
- **Network:** Accessible via SSH over local network

### Camera Setup
- **Model:** ELP USB500W05G USB Camera
- **Resolution:** 1920×1080 (1080p)
- **Frame Rate:** 30 FPS (nominal)
- **Format:** MJPEG stream (hardware-encoded, stream copied to reduce CPU load)
- **Devices:** `/dev/video0`, `/dev/video2`, `/dev/video4`, `/dev/video6`
- **Color Coding:** 
  - `video0` → BLUE
  - `video2` → RED
  - `video4` → GREEN
  - `video6` → BLACK

### Camera Controls (v4l2)
Each camera is configured via `v4l2-ctl` with settings for:
- Power line frequency filtering (60Hz for US power grid)
- Exposure controls (dynamic framerate disabled)
- White balance options
- Brightness, contrast, saturation, gamma, gain
- Backlight compensation

---

## Network Setup & Access

### How This System Is Accessed
1. **Pi Location:** Raspberry Pi 5 runs Linux (likely Raspberry Pi OS)
2. **Remote Access:** SSH from Windows PC over local network
3. **Shared Folder:** Pi's working directory is mounted as `Z:` drive on Windows PC via Samba
4. **Workflow:** 
   - SSH terminal for running scripts on Pi
   - `Z:\camera_scripts` and `Z:\sessions` are the shared Pi folders
   - Edit/analyze files from Windows, execute on Pi

---

## Directory Structure

```
Z:\camera_scripts\          # Scripts directory on Pi
├── record.sh              # Main recording script
├── setup.sh               # System configuration & dependencies
├── monitor_recording.sh   # System monitoring during recording
├── frame_analyzer.py      # Post-recording frame analysis
├── frame_sync_check.py    # Visual sync verification tool
├── frame_analyzer_backup.py  # Backup of analyzer
└── SYSTEM_REFERENCE.md    # This file

Z:\sessions\               # Recording output directory
└── session_MMDD_HHMMSS\  # Each recording session folder
    ├── BLACK_BUS_*.mkv   # Camera video files (4 total)
    ├── BLUE_BUS_*.mkv
    ├── GREEN_BUS_*.mkv
    ├── RED_BUS_*.mkv
    ├── recording_metrics.json     # Recording metadata
    ├── report.md                  # Frame analysis report
    ├── frame_timestamps.json      # Cached timestamp data
    ├── system_monitor.log         # System performance log
    └── sync_check_*.png          # Sync verification images
```

---

## Scripts Reference

### 1. `setup.sh` - System Configuration
**Purpose:** One-time setup script for Pi 5 hardware optimization

**What it does:**
- Installs dependencies: `ffmpeg`, `v4l-utils`, `htop`, `usbutils`, `samba`
- Configures `/boot/firmware/config.txt`:
  - Enables USB max current
  - Sets CMA (Contiguous Memory Allocator) to 512MB for video
  - Optional: disable WiFi/Bluetooth to reduce interference
- Sets USB memory buffer to 256MB in kernel cmdline
- **Requires reboot** after running

**Usage:**
```bash
./setup.sh
sudo reboot
```

---

### 2. `record.sh` - Main Recording Script
**Purpose:** Record synchronized video from all 4 cameras

**Parameters:**
```bash
./record.sh [DURATION_SECONDS]
# Default: 60 seconds if not specified
```

**What it does:**
1. **Camera Detection:** Scans `/dev/video*` for available cameras
2. **v4l2 Configuration:** Sets power line frequency, exposure, white balance
3. **Disk Space Check:** Verifies sufficient storage (~15 MB/s per camera)
4. **Session Creation:** Creates `~/sessions/session_MMDD_HHMMSS/` directory
5. **FFmpeg Recording:** Launches 4 parallel `ffmpeg` processes:
   - Input: `-f v4l2 -input_format mjpeg`
   - Copy mode: `-c copy` (no re-encoding, stream copy)
   - Container: `.mkv` (Matroska)
6. **Live Dashboard:** Displays every 5 seconds:
   - Elapsed time
   - CPU temperature
   - RAM usage
   - File sizes per camera
7. **Post-Recording:**
   - Generates `recording_metrics.json` with frame counts, file sizes, FPS, temperature/RAM stats
   - Automatically calls `frame_analyzer.py` for quality report

**Key Variables:**
- `RES="1920x1080"` (fixed per client requirement)
- `FPS=30` (fixed per client requirement)
- `CONTAINER="mkv"` (can be changed to `mp4` if needed)

**Output Files:**
- 4× video files named by camera color and USB bus location
  - Example: `BLUE_BUS_1-1.3.mkv`
- `recording_metrics.json`

---

### 3. `monitor_recording.sh` - System Performance Monitor
**Purpose:** Log system stats during recording (optional diagnostic tool)

**Usage:**
```bash
./monitor_recording.sh <session_dir>
# Run AFTER starting record.sh in a separate terminal
```

**What it monitors:**
- CPU temperature
- Throttling status
- Disk I/O utilization and write speed
- Network TX/RX throughput
- Logs to `system_monitor.log` in session directory

**Log Format (CSV):**
```
timestamp,temp_c,throttled,disk_util_pct,disk_write_mbs,net_tx_mbs,net_rx_mbs
```

---

### 4. `frame_analyzer.py` - Post-Recording Analysis
**Purpose:** Analyze video files for frame drops, sync issues, and quality

**Usage:**
```bash
python3 frame_analyzer.py [recording_metrics.json]
# If no argument, uses latest session automatically
```

**Analysis Performed:**
1. **Frame Count Verification:**
   - Expected frames: `duration × 30 FPS`
   - Actual frames: extracted via `ffprobe`
   - Missing frames calculation
2. **Gap Detection:**
   - Identifies frame timestamp gaps > 1.5× normal (33.33ms)
   - Calculates dropped frame count per anomaly
3. **Cross-Camera Clustering:**
   - Detects when multiple cameras drop frames at same timestamp
   - Indicates system-wide issues (USB bandwidth, CPU throttling)
4. **FPS Calculation:**
   - Real FPS excluding anomalies
   - Steady gap analysis

**Output Files:**
- `report.md` - Detailed markdown report with:
  - Quick summary table (FPS, missing frames, loss %)
  - Anomaly timeline with severity icons
  - Multi-camera cluster detection
  - Performance metrics (temperature, RAM)
- `frame_timestamps.json` - Cached timestamp data for sync checking

---

### 5. `frame_sync_check.py` - Visual Synchronization Verification
**Purpose:** Extract frames at specific timestamps from all 4 cameras and create grid images for visual sync comparison

**Usage:**
```bash
python3 frame_sync_check.py [session_folder_path]
# If no path, uses latest session
```

**What it does:**
1. **Dependency Check:** Auto-installs `imageio`, `numpy`, `Pillow` if missing
2. **Timestamp Selection:**
   - Extracts frames at: 0s, 1s, 5s, 10s, 15s, 20s, 25s, and last frame
3. **Frame Extraction:** Uses `imageio` with ffmpeg backend to seek precise timestamps
4. **Grid Generation:** Creates 2×2 grid image with all 4 camera views
   - Labels each quadrant with camera name and timestamp
5. **Output:** Saves PNG files like `sync_check_0.00s.png` in session folder

**Use Case:** Manually verify frame synchronization across cameras by visually inspecting grid images

---

## Recording Workflow

### Standard Recording Session

1. **SSH into Pi:**
   ```bash
   ssh austin@<pi-ip-address>
   cd ~/camera_scripts
   ```

2. **Run Recording:**
   ```bash
   ./record.sh 60  # Record for 60 seconds
   ```

3. **Monitor Progress:**
   - Live dashboard updates every 5 seconds
   - Watch file sizes, temperature, RAM usage

4. **Automatic Analysis:**
   - Script auto-runs `frame_analyzer.py` after recording
   - Generates `report.md` and `recording_metrics.json`

5. **Optional: Visual Sync Check:**
   ```bash
   python3 frame_sync_check.py  # Uses latest session
   ```

6. **Review Results:**
   - Open `Z:\sessions\session_MMDD_HHMMSS\report.md` on Windows PC
   - Check frame loss percentage and anomalies
   - Review sync check grid images if generated

---

## Key Constraints & Settings

### Non-Flexible Client Requirements
- **Platform:** Must run on Raspberry Pi 5
- **Power:** Must operate on batteries (portable)
- **Camera Count:** Exactly 4 cameras
- **Resolution:** 1080p (1920×1080)
- **Frame Rate:** 30 FPS

### Optimized Settings
- **Container Format:** MKV (better for stream copy, no re-encoding overhead)
- **Codec Strategy:** MJPEG stream copy (no CPU-intensive encoding)
- **USB Buffer:** 256MB–1000MB allocated in kernel/runtime
- **Memory:** CMA set to 512MB for video subsystem
- **USB Distribution:** Cameras split across 2 powered hubs to balance bandwidth

### Typical Performance
- **Storage:** ~3–4 MB/s per camera (varies by scene complexity)
- **CPU Temp:** 45–50°C during recording (passive cooling)
- **RAM Usage:** 1–2 GB during recording
- **Frame Loss:** Target < 1% (typically 0–36 frames per 900-frame session)

---

## Common Issues & Troubleshooting

### Frame Drops
**Symptoms:** Missing frames, gaps in timestamps
**Causes:**
- USB bandwidth saturation
- CPU throttling (overheating)
- SD card write speed bottleneck

**Solutions:**
- Check `system_monitor.log` for disk I/O and throttling
- Verify SD card is V30-rated (30 MB/s sustained write)
- Ensure USB buffer settings applied (check `/proc/cmdline`)
- Monitor temperature (consider active cooling if > 55°C)

### Multi-Camera Clusters
**Symptoms:** All 4 cameras drop frames at same timestamp
**Causes:** System-wide resource contention (not individual camera issue)

**Solutions:**
- Reduce resolution temporarily to test (change `RES` in `record.sh`)
- Check for background processes consuming CPU/IO
- Verify USB hubs are powered (not bus-powered)

### Out of Sync Cameras
**Symptoms:** Cameras start at different timestamps
**Causes:** FFmpeg process startup timing varies

**Solutions:**
- Use `frame_sync_check.py` to verify actual frame content alignment
- Consider discarding first 1–2 seconds of footage in post-processing
- Check for system time drift (NTP sync)

---

## Metrics & Reporting

### `recording_metrics.json` Structure
```json
{
  "session": "session_0222_123427",
  "duration_seconds": 30,
  "target_fps": 30,
  "target_resolution": "1920x1080",
  "container": "mkv",
  "started": "2026-02-22T12:34:27-08:00",
  "recording_dir": "/home/austin/sessions/session_0222_123427",
  "temperatures": {
    "start_c": 45.5,
    "end_c": 47.2,
    "peak_c": 47.2
  },
  "ram_usage_mb": {
    "start": 1024,
    "end": 1456,
    "peak": 1512
  },
  "cameras": [
    {
      "file": "BLUE_BUS_1-1.3.mkv",
      "frames": 864,
      "size_mb": 139.0,
      "fps": 28.80,
      "mbps": 4.63
    },
    ...
  ]
}
```

### `report.md` Contents
- Quick summary table (FPS, frame loss %)
- Detailed anomaly timeline
- Multi-camera cluster detection
- Severity indicators:
  - ✅ No issues
  - ⚠️ Minor (< 5 frames)
  - 🔴 Major (≥ 5 frames)
  - 🔥 System-wide cluster

---

## Development Notes

### Backup Files
- `frame_analyzer_backup.py` - Previous version of analyzer (keep for rollback)

### Testing Mode
- `record.sh` has TESTING sections for system monitor integration
- Currently monitors via `monitor_recording.sh` spawned in background

### Future Enhancements
- Hardware timestamp synchronization (V4L2 buffer timestamps)
- Real-time frame drop alerts during recording
- Automatic retry logic on critical frame loss
- Battery level monitoring integration

---

## Quick Reference Commands

```bash
# One-time setup (run once, then reboot)
./setup.sh && sudo reboot

# Record 60-second session
./record.sh 60

# Analyze latest session
python3 frame_analyzer.py

# Check visual sync for latest session  
python3 frame_sync_check.py

# Analyze specific session
python3 frame_analyzer.py ~/sessions/session_0222_123427/recording_metrics.json

# Monitor system during recording (separate terminal)
./monitor_recording.sh ~/sessions/session_0222_123427

# List all sessions
ls -lht ~/sessions/

# Check USB devices
lsusb

# Check camera detection
ls -l /dev/video*

# View camera capabilities
v4l2-ctl -d /dev/video0 --list-formats-ext

# Check disk space
df -h /home/austin

# Monitor temperature (live)
watch -n 1 vcgencmd measure_temp
```

---

## Client Information

- **Purpose:** Motion capture for computer vision training
- **Camera Model:** ELP USB500W05G USB Camera
- **Requirements:**
  - Raspberry Pi 5 platform (portable, battery-powered)
  - 4 cameras synchronized
  - 1080p @ 30 FPS (non-negotiable)
  - Reliable frame delivery for ML training datasets

---

**For Questions or Issues:**
Review `report.md` in each session folder for detailed diagnostics. Check `system_monitor.log` for hardware performance data during recording.
