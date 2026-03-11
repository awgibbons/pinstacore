#!/usr/bin/env python3

import glob
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime


def parse_fraction(value):
    value = (value or "").strip()
    if not value or value == "0/0":
        return None
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            den_val = float(den)
            if den_val == 0:
                return None
            return float(num) / den_val
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def run_ffprobe(args):
    cmd = ["ffprobe", "-v", "error"] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def write_status(status_path, state, progress=None, error=None):
    if not status_path:
        return

    payload = {
        "state": state,
        "updated_at": int(datetime.utcnow().timestamp()),
    }
    if progress is not None:
        payload["progress"] = progress
    if error:
        payload["error"] = error

    try:
        with open(status_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    except OSError:
        pass


def get_nominal_fps(video_file):
    result = run_ffprobe(
        [
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_file,
        ]
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    avg_fps = parse_fraction(lines[0]) if len(lines) > 0 else None
    raw_fps = parse_fraction(lines[1]) if len(lines) > 1 else None
    if avg_fps and avg_fps > 0:
        return avg_fps
    if raw_fps and raw_fps > 0:
        return raw_fps
    raise ValueError("Unable to determine FPS from ffprobe metadata")


def get_video_duration(video_file):
    result = run_ffprobe(
        [
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_file,
        ]
    )
    return float(result.stdout.strip())


def get_frame_timestamps(video_file, progress_callback=None, progress_every=5000):
    result = run_ffprobe(
        [
            "-show_entries",
            "packet=pts_time",
            "-of",
            "compact=p=0:nk=1",
            video_file,
        ]
    )
    timestamps = []
    for raw in result.stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            timestamps.append(float(raw))
            if progress_callback and len(timestamps) % progress_every == 0:
                progress_callback(len(timestamps))
        except ValueError:
            pass
    if progress_callback:
        progress_callback(len(timestamps))
    return timestamps


def analyze_video(video_file, default_fps=30.0, threshold_multiplier=1.5, progress_callback=None):
    file_name = os.path.basename(video_file)
    try:
        nominal_fps = get_nominal_fps(video_file)
    except Exception:
        nominal_fps = float(default_fps)

    try:
        duration = get_video_duration(video_file)
    except Exception:
        duration = 0.0

    timestamps = get_frame_timestamps(video_file, progress_callback=progress_callback)
    actual_frames = len(timestamps)
    expected_frames = int(round(duration * nominal_fps)) if duration > 0 else actual_frames

    target_gap_ms = 1000.0 / nominal_fps if nominal_fps > 0 else 33.333
    threshold_ms = target_gap_ms * threshold_multiplier

    gaps = []
    for i in range(1, len(timestamps)):
        gaps.append((timestamps[i] - timestamps[i - 1]) * 1000.0)

    anomalies = []
    for i, gap_ms in enumerate(gaps):
        if gap_ms > threshold_ms:
            approx_dropped = max(0, int(round(gap_ms / target_gap_ms)) - 1)
            anomalies.append(
                {
                    "timestamp_s": timestamps[i],
                    "gap_ms": round(gap_ms, 3),
                    "estimated_dropped_frames": approx_dropped,
                }
            )

    normal_gaps = [g for g in gaps if g <= threshold_ms]
    if normal_gaps:
        mean_gap_ms = sum(normal_gaps) / len(normal_gaps)
        steady_fps = 1000.0 / mean_gap_ms if mean_gap_ms > 0 else nominal_fps
    else:
        steady_fps = nominal_fps

    missing_frames = max(0, expected_frames - actual_frames)
    loss_pct = (100.0 * missing_frames / expected_frames) if expected_frames > 0 else 0.0

    return {
        "file": file_name,
        "nominal_fps": round(nominal_fps, 3),
        "duration_s": round(duration, 3),
        "expected_frames": expected_frames,
        "actual_frames": actual_frames,
        "missing_frames": missing_frames,
        "loss_pct": round(loss_pct, 3),
        "steady_fps": round(steady_fps, 3),
        "target_gap_ms": round(target_gap_ms, 3),
        "threshold_gap_ms": round(threshold_ms, 3),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "timestamps": timestamps,
    }


def build_clustered_events(results, cluster_window_s=0.1):
    events = []
    for result in results:
        for anomaly in result["anomalies"]:
            events.append(
                {
                    "file": result["file"],
                    "timestamp_s": anomaly["timestamp_s"],
                    "estimated_dropped_frames": anomaly["estimated_dropped_frames"],
                    "gap_ms": anomaly["gap_ms"],
                }
            )

    events.sort(key=lambda item: item["timestamp_s"])
    clusters = []
    used = set()

    for i, event in enumerate(events):
        if i in used:
            continue
        cluster = [event]
        used.add(i)
        for j in range(i + 1, len(events)):
            if j in used:
                continue
            candidate = events[j]
            if abs(candidate["timestamp_s"] - event["timestamp_s"]) > cluster_window_s:
                break
            if candidate["file"] not in {row["file"] for row in cluster}:
                cluster.append(candidate)
                used.add(j)

        if len(cluster) >= 2:
            clusters.append(
                {
                    "timestamp_s": round(cluster[0]["timestamp_s"], 3),
                    "camera_count": len(cluster),
                    "files": [row["file"] for row in cluster],
                    "max_estimated_drop": max(row["estimated_dropped_frames"] for row in cluster),
                }
            )

    return clusters


def write_report(report_path, session_name, analysis_data):
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(f"# Drop-Frame Analysis: {session_name}\n\n")
        handle.write(f"Generated: {analysis_data['generated_at']}\n\n")
        handle.write("## Summary\n\n")
        handle.write("| File | Frames | Missing | Loss % | Steady FPS | Anomalies |\n")
        handle.write("|------|--------|---------|--------|------------|-----------|\n")

        for row in analysis_data["per_camera"]:
            handle.write(
                f"| {row['file']} | {row['actual_frames']}/{row['expected_frames']} | {row['missing_frames']} | {row['loss_pct']:.2f} | {row['steady_fps']:.2f} | {row['anomaly_count']} |\n"
            )

        handle.write("\n")
        handle.write(f"Threshold multiplier: {analysis_data['threshold_multiplier']}x expected frame gap\n\n")

        if analysis_data["clustered_events"]:
            handle.write("## Multi-Camera Correlated Events\n\n")
            handle.write("| Time (s) | Cameras | Max Estimated Drop | Files |\n")
            handle.write("|----------|---------|--------------------|-------|\n")
            for cluster in analysis_data["clustered_events"]:
                handle.write(
                    f"| {cluster['timestamp_s']:.3f} | {cluster['camera_count']} | {cluster['max_estimated_drop']} | {', '.join(cluster['files'])} |\n"
                )
            handle.write("\n")

        handle.write("## Notes\n\n")
        handle.write("- An anomaly is any adjacent-frame gap > 1.5x expected frame gap.\n")
        handle.write("- Estimated dropped frames are inferred from gap length and may not be exact.\n")


def main():
    parser = argparse.ArgumentParser(description="Analyze session recordings for dropped-frame anomalies.")
    parser.add_argument("metrics_path", help="Path to recording_metrics.json")
    parser.add_argument("--status-path", dest="status_path", default=None, help="Optional analysis_status.json path")
    args = parser.parse_args()

    metrics_path = args.metrics_path
    status_path = args.status_path
    if not os.path.exists(metrics_path):
        print(f"Metrics file not found: {metrics_path}")
        write_status(status_path, "failed", error=f"Metrics file not found: {metrics_path}")
        return 1

    with open(metrics_path, "r", encoding="utf-8") as handle:
        metrics = json.load(handle)

    session_dir = metrics.get("recording_dir")
    if not session_dir or not os.path.isdir(session_dir):
        print(f"Invalid recording_dir in metrics: {session_dir}")
        write_status(status_path, "failed", error=f"Invalid recording_dir in metrics: {session_dir}")
        return 1

    session_name = metrics.get("session") or os.path.basename(session_dir)
    container = (metrics.get("container") or "mkv").lower()

    video_files = sorted(glob.glob(os.path.join(session_dir, f"*.{container}")))
    if not video_files:
        for ext in ("mkv", "mp4", "mov", "avi", "m4v"):
            video_files.extend(sorted(glob.glob(os.path.join(session_dir, f"*.{ext}"))))

    if not video_files:
        print(f"No video files found in {session_dir}")
        write_status(status_path, "failed", error=f"No video files found in {session_dir}")
        return 1

    threshold_multiplier = 1.5
    target_fps = float(metrics.get("target_fps", 30))

    total_files = len(video_files)
    write_status(
        status_path,
        "running",
        progress={
            "total_files": total_files,
            "completed_files": 0,
            "current_file": os.path.basename(video_files[0]),
            "current_file_frames_processed": 0,
        },
    )

    per_camera = []
    for idx, video_file in enumerate(video_files):
        current_file = os.path.basename(video_file)

        def on_progress(frame_count):
            write_status(
                status_path,
                "running",
                progress={
                    "total_files": total_files,
                    "completed_files": idx,
                    "current_file": current_file,
                    "current_file_frames_processed": frame_count,
                },
            )

        result = analyze_video(
            video_file,
            default_fps=target_fps,
            threshold_multiplier=threshold_multiplier,
            progress_callback=on_progress,
        )
        per_camera.append(result)
        write_status(
            status_path,
            "running",
            progress={
                "total_files": total_files,
                "completed_files": idx + 1,
                "current_file": current_file,
                "current_file_frames_processed": result["actual_frames"],
            },
        )

    totals_expected = sum(item["expected_frames"] for item in per_camera)
    totals_actual = sum(item["actual_frames"] for item in per_camera)
    totals_missing = sum(item["missing_frames"] for item in per_camera)
    total_loss_pct = (100.0 * totals_missing / totals_expected) if totals_expected > 0 else 0.0

    analysis_data = {
        "session": session_name,
        "recording_dir": session_dir,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "threshold_multiplier": threshold_multiplier,
        "camera_count": len(per_camera),
        "totals": {
            "expected_frames": totals_expected,
            "actual_frames": totals_actual,
            "missing_frames": totals_missing,
            "loss_pct": round(total_loss_pct, 3),
        },
        "per_camera": per_camera,
        "clustered_events": build_clustered_events(per_camera),
    }

    timestamps_data = {
        item["file"]: {
            "fps": item["steady_fps"],
            "timestamps": item["timestamps"],
        }
        for item in per_camera
    }

    analysis_json_path = os.path.join(session_dir, "analysis.json")
    timestamps_path = os.path.join(session_dir, "frame_timestamps.json")
    report_path = os.path.join(session_dir, "report.md")

    with open(analysis_json_path, "w", encoding="utf-8") as handle:
        json.dump(analysis_data, handle, indent=2)

    with open(timestamps_path, "w", encoding="utf-8") as handle:
        json.dump(timestamps_data, handle, indent=2)

    write_report(report_path, session_name, analysis_data)

    print(f"Saved: {analysis_json_path}")
    print(f"Saved: {timestamps_path}")
    print(f"Saved: {report_path}")
    write_status(
        status_path,
        "complete",
        progress={
            "total_files": total_files,
            "completed_files": total_files,
            "current_file": None,
            "current_file_frames_processed": 0,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
