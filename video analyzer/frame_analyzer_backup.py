#!/usr/bin/env python3

import os
import subprocess
import glob
import sys
import atexit
import numpy as np
from collections import defaultdict
from datetime import datetime


def parse_fraction(frac_str):
    frac_str = frac_str.strip()
    if not frac_str or frac_str == "0/0":
        return None
    if "/" in frac_str:
        num, den = frac_str.split("/", 1)
        den_val = float(den)
        if den_val == 0:
            return None
        return float(num) / den_val
    return float(frac_str)


def get_nominal_fps(video_file):
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_file,
    ]

    result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    # ffprobe returns avg_frame_rate then r_frame_rate in this query order
    avg_fps = parse_fraction(lines[0]) if len(lines) > 0 else None
    raw_fps = parse_fraction(lines[1]) if len(lines) > 1 else None

    if avg_fps and avg_fps > 0:
        return avg_fps
    if raw_fps and raw_fps > 0:
        return raw_fps
    raise ValueError("Could not determine nominal FPS from file metadata")


def get_video_duration(video_file):
    """Get video duration in seconds"""
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_file,
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def get_resolution(video_file):
    """Get video resolution (width x height)"""
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_file,
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) >= 2:
        width = int(lines[0])
        height = int(lines[1])
        return f"{width}x{height}"
    return "unknown"


def extract_timestamps(video_file):
    """Extract all frame timestamps from a video file"""
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "frame=best_effort_timestamp_time",
        "-of", "csv=p=0",
        video_file
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
    timestamps = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            timestamps.append(float(line))
    return np.array(timestamps)


def analyze_video_file(video_file, nominal_fps):
    """Analyze a single video file for frame drops and timing issues"""
    # Get video info from file
    duration = get_video_duration(video_file)
    resolution = get_resolution(video_file)
    file_fps = get_nominal_fps(video_file)
    expected_frames = int(duration * nominal_fps)
    
    # Extract timestamps
    timestamps = extract_timestamps(video_file)
    actual_frames = len(timestamps)
    
    # Calculate gaps
    gaps = np.diff(timestamps) * 1000  # Convert to milliseconds
    target_gap_ms = 1000.0 / nominal_fps
    
    # Skip first few frames for steady-state analysis
    SKIP_FRAMES = 5
    steady_gaps = gaps[SKIP_FRAMES:] if len(gaps) > SKIP_FRAMES else gaps
    
    # Detect significant timing anomalies (gaps > 1.5x target)
    anomaly_threshold = target_gap_ms * 1.5
    anomalies = []
    
    for i, gap in enumerate(gaps):
        if gap > anomaly_threshold:
            frame_num = i + 1
            timestamp = timestamps[i]
            dropped_estimate = int(round(gap / target_gap_ms)) - 1
            anomalies.append({
                'frame': frame_num,
                'timestamp': timestamp,
                'gap_ms': gap,
                'dropped_frames': dropped_estimate
            })
    
    # Calculate "Real FPS" - FPS excluding anomaly gaps
    normal_gaps = [g for g in gaps if g < anomaly_threshold]
    if normal_gaps:
        total_normal_time = sum(normal_gaps) / 1000.0  # Convert ms to seconds
        real_fps = actual_frames / total_normal_time if total_normal_time > 0 else 0
    else:
        real_fps = nominal_fps
    
    return {
        'file': os.path.basename(video_file),
        'resolution': resolution,
        'fps': file_fps,
        'real_fps': real_fps,
        'duration': duration,
        'expected_frames': expected_frames,
        'actual_frames': actual_frames,
        'missing_frames': expected_frames - actual_frames,
        'anomalies': anomalies,
        'timestamps': timestamps,
        'gaps': gaps,
        'anomaly_threshold': anomaly_threshold,
        'steady_gaps': steady_gaps
    }

# --- 1. Locate the Latest Session ---
# Try local storage first, fall back to network mount if available
LOCAL_SESSIONS = "sessions"
NETWORK_MOUNT = "/mnt/windows_share"

if os.path.exists(LOCAL_SESSIONS):
    SESSIONS_BASE = LOCAL_SESSIONS
elif os.path.exists(NETWORK_MOUNT):
    SESSIONS_BASE = NETWORK_MOUNT
else:
    SESSIONS_BASE = LOCAL_SESSIONS

# Find all session folders
session_folders = glob.glob(os.path.join(SESSIONS_BASE, "session_*"))

if not session_folders:
    print(f"Error: No folders found in {SESSIONS_BASE}")
    exit(1)

# Sort by creation time to get the newest one
latest_session = max(session_folders, key=os.path.getmtime)

# Find all video files
video_files = glob.glob(os.path.join(latest_session, "*.mkv"))
video_files.extend(glob.glob(os.path.join(latest_session, "*.mp4")))

if not video_files:
    print(f"Error: No .mp4 or .mkv files found in {latest_session}")
    exit(1)

video_files.sort()

# Open report file for appending (must be done before writing to it)
report_path = os.path.join(latest_session, "record_report.md")
report_fp = open(report_path, "a", encoding="utf-8")

class Tee:
    def __init__(self, *files):
        self.files = files

    def write(self, data):
        for file in self.files:
            file.write(data)
            file.flush()

    def flush(self):
        for file in self.files:
            file.flush()

report_fp.write("\n## Frame Analysis (Per-Camera Details)\n\n")
report_fp.flush()

orig_stdout = sys.stdout
sys.stdout = Tee(sys.stdout, report_fp)

def close_report():
    if sys.stdout is not orig_stdout:
        sys.stdout = orig_stdout
    try:
        report_fp.close()
    except Exception:
        pass

atexit.register(close_report)

# Determine nominal FPS from first video
try:
    nominal_fps = get_nominal_fps(video_files[0])
except Exception as e:
    print(f"Error determining FPS: {e}")
    exit(1)

# --- 2. Analyze All Video Files ---
results = []
all_anomalies = []
multi_camera_clusters = []
for video_file in video_files:
    try:
        result = analyze_video_file(video_file, nominal_fps)
        results.append(result)
    except Exception as e:
        print(f"\nError analyzing {video_file}: {e}")

# Generate new nice format output
# Generate new nice format output
print("### Camera Performance Comparison\n")

colors = ['BLACK', 'BLUE', 'GREEN', 'RED']
result_by_idx = results[:4] if len(results) >= 4 else results

# Show each camera's stats in a compact way
for i, result in enumerate(result_by_idx):
    loss_pct = 100 * result['missing_frames'] / result['expected_frames'] if result['expected_frames'] > 0 else 0
    print(f"**BUS {bus_order[i].split()[1]} ({colors[i]})**  ")
    print(f"- Duration: {result['duration']:.2f}s | Frames: {result['actual_frames']}/{result['expected_frames']} | Missing: {result['missing_frames']} ({loss_pct:.2f}%)")
    print(f"- Real FPS: {result['real_fps']:.2f} | Nominal FPS: {nominal_fps:.2f}\n")

print("\n### Timing Statistics (Gap Between Frames)\n")
print("| Metric | BUS 0-1.4 | BUS 1-1.3 | BUS 1-1.4 | BUS 0-1.3 |")
print("|--------|-----------|-----------|-----------|-----------|")

for metric_name, metric_key in [('**Target**', 'target'), ('**Mean**', 'mean'), ('**Median**', 'median'), 
                                ('**Std Dev**', 'std'), ('**Min**', 'min'), ('**Max**', 'max')]:
    row = f"| {metric_name} |"
    for i, result in enumerate(result_by_idx):
        gaps = result['steady_gaps']
        if metric_key == 'target':
            val = 1000.0 / nominal_fps
        elif metric_key == 'mean':
            val = np.mean(gaps)
        elif metric_key == 'median':
            val = np.median(gaps)
        elif metric_key == 'std':
            val = np.std(gaps)
        elif metric_key == 'min':
            val = np.min(gaps)
        elif metric_key == 'max':
            val = np.max(gaps)
        row += f" {val:.2f}ms |"
    print(row)

# Print major frame drops per camera
print("\n### Major Frame Drops (Anomalies > 50ms)\n")
colors = ['BLACK', 'BLUE', 'GREEN', 'RED']
for i, result in enumerate(result_by_idx):
    if result['anomalies']:
        anomaly_times = ', '.join([f"{a['timestamp']:.1f}s ({a['gap_ms']:.0f}ms)" for a in result['anomalies']])
        print(f"**BUS {bus_order[i].split()[1]} ({colors[i]})** — {len(result['anomalies'])} events")
        print(f"📊 Drops at: {anomaly_times}\n")
    else:
        print(f"**BUS {bus_order[i].split()[1]} ({colors[i]})** — No major anomalies detected\n")

# --- 3. Cross-File Correlation Analysis ---
if len(results) > 1:
    print("## Synchronization Events (Cross-Camera Analysis)\n")
    print(f"{len(results)} cameras analyzed for cross-camera sync patterns:\n")
    
    # Collect all anomalies with their file info
    for result in results:
        for anomaly in result['anomalies']:
            all_anomalies.append({
                'file': result['file'],
                'timestamp': anomaly['timestamp'],
                'gap_ms': anomaly['gap_ms'],
                'dropped': anomaly['dropped_frames']
            })
    
    # Sort by timestamp
    all_anomalies.sort(key=lambda x: x['timestamp'])
    
    # Group anomalies that occur at the same time from different cameras
    clusters = []
    processed = set()
    time_window = 0.5  # seconds
    
    for i, anomaly in enumerate(all_anomalies):
        if i in processed:
            continue
        
        cluster = [anomaly]
        cameras_in_cluster = {anomaly['file']}
        processed.add(i)
        
        for j in range(i + 1, len(all_anomalies)):
            if j in processed:
                continue
            other = all_anomalies[j]
            
            if abs(other['timestamp'] - anomaly['timestamp']) > time_window:
                break
            
            if other['file'] not in cameras_in_cluster:
                if abs(other['timestamp'] - anomaly['timestamp']) < 0.1:
                    cluster.append(other)
                    cameras_in_cluster.add(other['file'])
                    processed.add(j)
        
        if len(cluster) >= 2:
            clusters.append(cluster)
    
    multi_camera_clusters = clusters
    
    if multi_camera_clusters:
        print(f"| Time | Cameras | Max Dropped | Avg Drops | Notes |")
        print("|------|---------|-------------|-----------|-------|")
        for idx, cluster in enumerate(multi_camera_clusters, 1):
            time_str = f"~{cluster[0]['timestamp']:.1f}s"
            cam_count = len(cluster)
            max_drop = max([e['dropped'] for e in cluster])
            avg_drop = sum([e['dropped'] for e in cluster]) / len(cluster)
            
            # Categorize the event
            if idx == 1 and cam_count == 4:
                notes = "**Startup sync** — All cameras"
            else:
                notes = f"Frame stutter"
            
            print(f"| {time_str} | {cam_count}/4 | {max_drop} | {avg_drop:.1f} | {notes} |")
    else:
        print("- No correlated multi-camera stutters detected")

# --- 4. Correlate with System Monitor Log ---
monitor_log = os.path.join(latest_session, "system_monitor.log")
if os.path.exists(monitor_log):
    print("\n## System Load Correlation\n")
    
    try:
        import csv
        stats_data = []
        with open(monitor_log, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                stats_entry = {
                    'timestamp': float(row['timestamp']),
                    'temp': float(row['temp_c']),
                    'throttled': row['throttled'],
                    'disk_util': float(row['disk_util_pct']),
                    'disk_write': float(row['disk_write_mbs']),
                    'net_tx_mbs': float(row.get('net_tx_mbs', 0)),
                    'net_rx_mbs': float(row.get('net_rx_mbs', 0))
                }
                stats_data.append(stats_entry)
        
        if stats_data and multi_camera_clusters:
            print("### Critical Events During Recording\n")
            print("| Event | Time | CPUTemp | DiskUtil | DiskWrite | Throttle | Status |")
            print("|-------|------|---------|----------|-----------|----------|--------|")
            
            for idx, cluster in enumerate(multi_camera_clusters[:5], 1):  # Show first 5
                event_time = cluster[0]['timestamp']
                relevant_stats = [
                    s for s in stats_data 
                    if abs((s['timestamp'] - stats_data[0]['timestamp']) - event_time) <= 0.5
                ]
                
                if relevant_stats:
                    avg_temp = np.mean([s['temp'] for s in relevant_stats])
                    max_disk = max([s['disk_util'] for s in relevant_stats])
                    max_write = max([s['disk_write'] for s in relevant_stats])
                    throttled = any([s['throttled'] != '0x0' for s in relevant_stats])
                    
                    temp_alert = "⚠️" if avg_temp > 75 else "✓"
                    disk_alert = "⚠️" if max_disk > 90 else "✓"
                    status = "Disk bottleneck" if max_disk > 90 else "Normal"
                    
                    print(f"| Stutter #{idx} | {event_time:.1f}s | {avg_temp:.1f}°C {temp_alert} | {max_disk:.1f}% {disk_alert} | {max_write:.1f} MB/s | {'YES' if throttled else 'No'} | {status} |")
            
            print("\n### Overall Session Statistics\n")
            print("| Metric | Min | Avg | Max | Alert |")
            print("|--------|-----|-----|-----|-------|")
            
            all_temps = [s['temp'] for s in stats_data]
            all_disk = [s['disk_util'] for s in stats_data]
            all_writes = [s['disk_write'] for s in stats_data]
            throttle_events = sum([1 for s in stats_data if s['throttled'] != '0x0'])
            
            temp_str = f"| **Temperature** | {min(all_temps):.1f}°C | {np.mean(all_temps):.1f}°C | {max(all_temps):.1f}°C | ✓ Normal |"
            disk_str = f"| **Disk Utilization** | {min(all_disk):.1f}% | {np.mean(all_disk):.1f}% | {max(all_disk):.1f}% |"
            if max(all_disk) > 100:
                disk_str += " ⚠️ Bottleneck |"
            else:
                disk_str += " ✓ Normal |"
            write_str = f"| **Disk Write Rate** | {min(all_writes):.1f} MB/s | {np.mean(all_writes):.1f} MB/s | {max(all_writes):.1f} MB/s | ✓ Normal |"
            throttle_str = f"| **Throttling** | — | {'Yes' if throttle_events > 0 else 'Never'} | — | {'⚠️ Events detected' if throttle_events > 0 else '✓ No throttle'} |"
            
            print(temp_str)
            print(disk_str)
            print(write_str)
            print(throttle_str)
        
    except Exception as e:
        print(f"Error reading system monitor log: {e}")

print("\n---\n")
print("✓ Analysis complete!")

# Update Quick Summary table with actual results
try:
    with open(report_path, 'r') as f:
        report_content = f.read()
    
    # Generate updated Quick Summary
    colors = ['BLACK', 'BLUE', 'GREEN', 'RED']
    bus_ids = ['0-1.4', '1-1.3', '1-1.4', '0-1.3']
    
    summary_lines = []
    for i, result in enumerate(result_by_idx[:4] if len(result_by_idx) >= 4 else result_by_idx):
        loss_pct = 100 * result['missing_frames'] / result['expected_frames'] if result['expected_frames'] > 0 else 0
        issue = "⚠️ High loss" if loss_pct > 28 else ("✓ OK" if result['real_fps'] > 28 else "⚠️ Low FPS")
        summary_lines.append(f"| BUS {bus_ids[i]} ({colors[i]}) | {result['real_fps']:.2f} | {result['actual_frames']} | {result['missing_frames']} | {loss_pct:.1f}% | {issue} |")
    
    # Total stats
    total_frames = sum([r['actual_frames'] for r in result_by_idx])
    total_missing = sum([r['missing_frames'] for r in result_by_idx])
    total_loss = 100 * total_missing / (total_frames + total_missing) if (total_frames + total_missing) > 0 else 0
    summary_lines.append(f"| **TOTAL** | — | {total_frames} | {total_missing} | {total_loss:.1f}% | {len(multi_camera_clusters)} sync |")
    
    # Replace placeholder with actual data
    old_summary = """| Camera | FPS | Frames | Missing | Loss % | Issues |
|--------|-----|--------|---------|--------|--------|
| BUS 0-1.4 (BLACK) | — | — | — | — | *Analysis pending* |
| BUS 1-1.3 (BLUE) | — | — | — | — | *Analysis pending* |
| BUS 1-1.4 (GREEN) | — | — | — | — | *Analysis pending* |
| BUS 0-1.3 (RED) | — | — | — | — | *Analysis pending* |
| **TOTAL** | — | — | — | — | *Running analysis...* |"""
    
    new_summary = "| Camera | FPS | Frames | Missing | Loss % | Issues |\n|--------|-----|--------|---------|--------|--------|\n" + "\n".join(summary_lines)
    
    updated_content = report_content.replace(old_summary, new_summary)
    
    with open(report_path, 'w') as f:
        f.write(updated_content)
    
    print(f"\n✅ Updated Quick Summary in {report_path}")
    
except Exception as e:
    print(f"⚠️ Could not update Quick Summary: {e}")

