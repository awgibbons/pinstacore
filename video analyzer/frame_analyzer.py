#!/usr/bin/env python3

import os
import subprocess
import glob
import sys
import json
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
    avg_fps = parse_fraction(lines[0]) if len(lines) > 0 else None
    raw_fps = parse_fraction(lines[1]) if len(lines) > 1 else None
    if avg_fps and avg_fps > 0:
        return avg_fps
    if raw_fps and raw_fps > 0:
        return raw_fps
    raise ValueError("Could not determine nominal FPS from file metadata")


def get_video_duration(video_file):
    """Get video duration in seconds"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_file,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def analyze_video_file(video_file, nominal_fps):
    """Analyze a video file for frame drops and anomalies"""
    video_name = os.path.basename(video_file)
    
    # Get video properties
    duration = get_video_duration(video_file)
    expected_frames = int(duration * nominal_fps)
    
    # Extract timestamps
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "packet=pts_time",
        "-of", "compact=p=0:nk=1",
        video_file,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    
    timestamps = []
    for line in result.stdout.strip().split('\n'):
        if line.strip():
            try:
                ts = float(line.strip())
                timestamps.append(ts)
            except:
                pass
    
    actual_frames = len(timestamps)
    missing_frames = expected_frames - actual_frames
    
    # Calculate gaps between frames
    gaps = []
    if len(timestamps) > 1:
        for i in range(1, len(timestamps)):
            gap_ms = (timestamps[i] - timestamps[i-1]) * 1000
            gaps.append(gap_ms)
    
    # Identify anomalies (gaps > 1.5x normal gap)
    target_gap_ms = 1000.0 / nominal_fps
    anomaly_threshold = target_gap_ms * 1.5
    
    anomalies = []
    for i, gap in enumerate(gaps):
        if gap > anomaly_threshold:
            timestamp = timestamps[i] if i < len(timestamps) else i / nominal_fps
            dropped = int(round(gap / target_gap_ms)) - 1
            anomalies.append({
                'timestamp': timestamp,
                'gap_ms': gap,
                'dropped_frames': dropped
            })
    
    # Calculate real FPS (excluding anomalies)
    normal_gaps = [g for g in gaps if g < anomaly_threshold]
    if normal_gaps:
        total_normal_time = sum(normal_gaps) / 1000.0
        real_fps = actual_frames / total_normal_time if total_normal_time > 0 else nominal_fps
    else:
        real_fps = nominal_fps
    
    return {
        'file': video_name,
        'duration': duration,
        'expected_frames': expected_frames,
        'actual_frames': actual_frames,
        'missing_frames': missing_frames,
        'real_fps': real_fps,
        'anomalies': anomalies,
        'steady_gaps': normal_gaps,
        'timestamps': timestamps,
        'gaps': gaps
    }


def find_latest_metrics_file():
    """Find the most recent recording_metrics.json file"""
    metrics_files = glob.glob(os.path.expanduser("~/sessions/*/recording_metrics.json"))
    if not metrics_files:
        return None
    return max(metrics_files, key=os.path.getctime)


def load_metrics(metrics_file):
    """Load metrics from JSON file"""
    with open(metrics_file, 'r') as f:
        raw = f.read()

    if not raw.strip():
        raise ValueError(f"Metrics file is empty: {metrics_file}")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        lines = raw.splitlines()
        line = lines[exc.lineno - 1] if 0 < exc.lineno <= len(lines) else ""
        caret = " " * max(exc.colno - 1, 0) + "^"
        message = (
            f"Invalid JSON in {metrics_file} at line {exc.lineno} column {exc.colno}: {exc.msg}\n"
            f"{line}\n"
            f"{caret}"
        )
        raise ValueError(message) from exc


def main():
    # Determine metrics file
    if len(sys.argv) > 1:
        metrics_file = sys.argv[1]
    else:
        metrics_file = find_latest_metrics_file()
    
    if not metrics_file or not os.path.exists(metrics_file):
        print("Error: recording_metrics.json not found")
        sys.exit(1)
    
    print(f"Loading metrics from: {metrics_file}")
    try:
        metrics = load_metrics(metrics_file)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    session_dir = metrics['recording_dir']
    
    # Find video files
    container = metrics['container']
    video_files = sorted(glob.glob(os.path.join(session_dir, f"*.{container}")))
    
    if not video_files:
        print(f"Error: No video files (.{container}) found in {session_dir}")
        sys.exit(1)
    
    # Determine nominal FPS
    nominal_fps = get_nominal_fps(video_files[0])
    
    # Analyze all videos
    results = []
    all_anomalies = []
    multi_camera_clusters = []
    
    for video_file in video_files:
        try:
            result = analyze_video_file(video_file, nominal_fps)
            results.append(result)
        except Exception as e:
            print(f"Error analyzing {video_file}: {e}")
    
    # Generate markdown report
    report_path = os.path.join(session_dir, "report.md")
    
    with open(report_path, 'w', encoding='utf-8') as f:
        # Header
        f.write(f"# Recording Report: {metrics['session']}\n\n")
        f.write(f"**Started:** {metrics['started']} | **Target:** {metrics['target_resolution']} @ {metrics['target_fps']} fps × {metrics['duration_seconds']}s\n\n")
        f.write("---\n\n")
        
        # Quick Summary
        f.write("## Quick Summary\n\n")
        f.write("| Camera | FPS | Frames | Missing | Loss % | Issues |\n")
        f.write("|--------|-----|--------|---------|--------|--------|\n")
        
        colors = ['BLACK', 'BLUE', 'GREEN', 'RED']
        bus_ids = ['0-1.4', '1-1.3', '1-1.4', '0-1.3']
        
        for i, result in enumerate(results[:4] if len(results) >= 4 else results):
            loss_pct = 100 * result['missing_frames'] / result['expected_frames'] if result['expected_frames'] > 0 else 0
            issue = "WARNING: High loss" if loss_pct > 28 else ("OK" if result['real_fps'] > 28 else "WARNING: Low FPS")
            f.write(f"| BUS {bus_ids[i]} ({colors[i]}) | {result['real_fps']:.2f} | {result['actual_frames']} | {result['missing_frames']} | {loss_pct:.1f}% | {issue} |\n")
        
        # Total
        total_frames = sum([r['actual_frames'] for r in results])
        total_missing = sum([r['missing_frames'] for r in results])
        total_loss = 100 * total_missing / (total_frames + total_missing) if (total_frames + total_missing) > 0 else 0
        f.write(f"| **TOTAL** | — | {total_frames} | {total_missing} | {total_loss:.1f}% | — |\n\n")
        
        f.write("---\n\n")
        
        # Recording Diagnostics
        f.write("## Recording Diagnostics\n\n")
        f.write("| File | Size | Frames | FPS | MB/s | Status |\n")
        f.write("| --- | --- | --- | --- | --- | --- |\n")
        
        for result in results:
            expected = metrics['duration_seconds'] * metrics['target_fps']
            status = "[OK]" if result['actual_frames'] >= expected - 15 else "[!!]"
            
            # Find size from metrics
            size_mb = "?"
            for cam in metrics['cameras']:
                if cam['file'] == result['file']:
                    size_mb = cam.get('size_mb', '?')
                    mbps = cam.get('mbps', '?')
                    break
            
            f.write(f"| {result['file']} | {size_mb}MB | {result['actual_frames']}/{expected:.0f} | {result['real_fps']:.2f} | {mbps} | {status} |\n")
        
        # Save frame PTS data for frame_sync_check
        pts_data = {}
        for result in results:
            pts_data[result['file']] = {
                'fps': result['real_fps'],
                'timestamps': result['timestamps']
            }
        
        pts_file = os.path.join(session_dir, "frame_timestamps.json")
        with open(pts_file, 'w', encoding='utf-8') as f_pts:
            json.dump(pts_data, f_pts, indent=2)
        
        print(f"\n[*] Saved frame timestamps to: {pts_file}")
        
        # Temperatures and RAM
        f.write("\n**Temperature:** Start: {:.1f}°C | Peak: {:.1f}°C | End: {:.1f}°C\n".format(
            metrics['temperatures']['start_c'],
            metrics['temperatures']['peak_c'],
            metrics['temperatures']['end_c']
        ))
        
        # RAM usage (optional for backwards compatibility)
        if 'ram_usage_mb' in metrics:
            f.write("**RAM Usage:** Start: {}MB | Peak: {}MB | End: {}MB\n\n".format(
                metrics['ram_usage_mb']['start'],
                metrics['ram_usage_mb']['peak'],
                metrics['ram_usage_mb']['end']
            ))
        else:
            f.write("\n")
        
        f.write("---\n\n")
        
        # Camera Performance
        f.write("## Camera Performance Comparison\n\n")
        for i, result in enumerate(results[:4] if len(results) >= 4 else results):
            loss_pct = 100 * result['missing_frames'] / result['expected_frames'] if result['expected_frames'] > 0 else 0
            f.write(f"**BUS {bus_ids[i]} ({colors[i]})**  \n")
            f.write(f"- Duration: {result['duration']:.2f}s | Frames: {result['actual_frames']}/{result['expected_frames']} | Missing: {result['missing_frames']} ({loss_pct:.2f}%)\n")
            f.write(f"- Real FPS: {result['real_fps']:.2f} | Nominal FPS: {nominal_fps:.2f}\n\n")
        
        # Timing Statistics
        f.write("## Timing Statistics (Gap Between Frames)\n\n")
        f.write("| Metric | BUS 0-1.4 | BUS 1-1.3 | BUS 1-1.4 | BUS 0-1.3 |\n")
        f.write("|--------|-----------|-----------|-----------|----------|\n")
        
        for metric_name, metric_key in [('**Target**', 'target'), ('**Mean**', 'mean'), ('**Median**', 'median'), 
                                        ('**Std Dev**', 'std'), ('**Min**', 'min'), ('**Max**', 'max')]:
            row = f"| {metric_name} |"
            for i, result in enumerate(results[:4] if len(results) >= 4 else results):
                gaps = result['steady_gaps']
                if metric_key == 'target':
                    val = 1000.0 / nominal_fps
                elif metric_key == 'mean':
                    val = np.mean(gaps) if gaps else 0
                elif metric_key == 'median':
                    val = np.median(gaps) if gaps else 0
                elif metric_key == 'std':
                    val = np.std(gaps) if gaps else 0
                elif metric_key == 'min':
                    val = np.min(gaps) if gaps else 0
                elif metric_key == 'max':
                    val = np.max(gaps) if gaps else 0
                row += f" {val:.2f}ms |"
            f.write(row + "\n")
        
        # Major Frame Drops
        f.write("\n## Major Frame Drops (Anomalies > 50ms)\n\n")
        for i, result in enumerate(results[:4] if len(results) >= 4 else results):
            if result['anomalies']:
                anomaly_times = ', '.join([f"{a['timestamp']:.1f}s ({a['gap_ms']:.0f}ms)" for a in result['anomalies']])
                f.write(f"**BUS {bus_ids[i]} ({colors[i]})** — {len(result['anomalies'])} events\n")
                f.write(f"Drops at: {anomaly_times}\n\n")
            else:
                f.write(f"**BUS {bus_ids[i]} ({colors[i]})** — No major anomalies detected\n\n")
        
        # Cross-camera correlation
        if len(results) > 1:
            f.write("## Synchronization Events (Cross-Camera Analysis)\n\n")
            
            for result in results:
                for anomaly in result['anomalies']:
                    all_anomalies.append({
                        'file': result['file'],
                        'timestamp': anomaly['timestamp'],
                        'gap_ms': anomaly['gap_ms'],
                        'dropped': anomaly['dropped_frames']
                    })
            
            all_anomalies.sort(key=lambda x: x['timestamp'])
            
            clusters = []
            processed = set()
            time_window = 0.5
            
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
            
            f.write(f"{len(results)} cameras analyzed for cross-camera sync patterns:\n\n")
            
            if multi_camera_clusters:
                f.write("| Time | Cameras | Max Dropped | Avg Drops | Notes |\n")
                f.write("|------|---------|-------------|-----------|-------|\n")
                for idx, cluster in enumerate(multi_camera_clusters, 1):
                    time_str = f"~{cluster[0]['timestamp']:.1f}s"
                    cam_count = len(cluster)
                    max_drop = max([e['dropped'] for e in cluster])
                    avg_drop = sum([e['dropped'] for e in cluster]) / len(cluster)
                    
                    if idx == 1 and cam_count == 4:
                        notes = "**Startup sync** — All cameras"
                    else:
                        notes = "Frame stutter"
                    
                    f.write(f"| {time_str} | {cam_count}/4 | {max_drop} | {avg_drop:.1f} | {notes} |\n")
            else:
                f.write("- No correlated multi-camera stutters detected\n\n")
        
        f.write("\n---\n\n")
        f.write("Report generated successfully!\n")
    
    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
