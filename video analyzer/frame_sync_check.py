#!/usr/bin/env python3
r"""
Frame Sync Checker - Extracts frames from 4 camera files at specific timestamps
and creates a grid image for visual synchronization analysis.

Usage:
    python3 frame_sync_check.py [session_folder_path]

If no path provided, uses the latest session in Z:\sessions\
"""

import os
import sys
import glob
import json
import subprocess
from pathlib import Path
from datetime import datetime


def check_and_install_dependencies():
    """Check for required packages and install if missing"""
    
    # Check for imageio and imageio-ffmpeg (better for frame extraction)
    try:
        import imageio
        print("✅ imageio is installed")
    except ImportError:
        print("❌ imageio not found, installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "imageio", "imageio[ffmpeg]"])
            print("✅ imageio with ffmpeg plugin installed successfully")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to install imageio: {e}")
            sys.exit(1)
    
    # Check for numpy
    try:
        import numpy
        print("✅ numpy is installed")
    except ImportError:
        print("❌ numpy not found, installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "numpy"])
            print("✅ numpy installed successfully")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to install numpy: {e}")
            sys.exit(1)
    
    # Check for Pillow (for image manipulation)
    try:
        from PIL import Image, ImageDraw, ImageFont
        print("✅ Pillow is installed")
    except ImportError:
        print("❌ Pillow not found, installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow"])
            print("✅ Pillow installed successfully")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to install Pillow: {e}")
            sys.exit(1)
    
    print("\n✅ All dependencies are available!\n")


# Run dependency check before importing
check_and_install_dependencies()

# Now import after ensuring dependencies exist
import numpy as np
import imageio
from PIL import Image, ImageDraw, ImageFont


def find_latest_session(base_path="Z:\\sessions"):
    """Find the most recent session folder"""
    session_folders = glob.glob(os.path.join(base_path, "session_*"))
    if not session_folders:
        print(f"Error: No session folders found in {base_path}")
        return None
    return max(session_folders, key=os.path.getctime)


def load_metrics(session_dir):
    """Load recording metrics from JSON"""
    metrics_file = os.path.join(session_dir, "recording_metrics.json")
    if not os.path.exists(metrics_file):
        print(f"Warning: {metrics_file} not found")
        return None
    
    with open(metrics_file, 'r') as f:
        return json.load(f)


def load_frame_timestamps(session_dir):
    """Load cached frame timestamps from frame_analyzer output"""
    pts_file = os.path.join(session_dir, "frame_timestamps.json")
    
    if not os.path.exists(pts_file):
        print(f"⚠️  Warning: {pts_file} not found")
        print("   (Run frame_analyzer.py on the Pi first to generate this data)")
        return None
    
    with open(pts_file, 'r') as f:
        return json.load(f)


def calculate_startup_offsets(frame_timestamps):
    """
    Calculate startup time offset for each camera.
    Assumes the camera with the earliest first frame started first (offset = 0)
    Others' offsets are calculated relative to that.
    
    Returns dict: {filename: offset_seconds}
    """
    offsets = {}
    
    # Find which camera has the earliest first frame
    earliest_pts = float('inf')
    earliest_camera = None
    
    for filename, data in frame_timestamps.items():
        first_pts = data['timestamps'][0]
        if first_pts < earliest_pts:
            earliest_pts = first_pts
            earliest_camera = filename
    
    if not earliest_camera:
        return None
    
    # Calculate offsets relative to earliest camera
    for filename, data in frame_timestamps.items():
        first_pts = data['timestamps'][0]
        # If this camera's first frame is at PTS X, and earliest was at PTS Y,
        # then this camera started (X - Y) seconds later
        offsets[filename] = earliest_pts - first_pts
    
    print("📊 Startup offsets (relative to earliest camera):")
    for filename, offset in sorted(offsets.items(), key=lambda x: x[1]):
        if offset == 0:
            print(f"  {filename}: 0.0s (reference)")
        elif offset > 0:
            print(f"  {filename}: +{offset:.3f}s (started {offset*1000:.0f}ms later)")
        else:
            print(f"  {filename}: {offset:.3f}s (started {abs(offset)*1000:.0f}ms earlier)")
    
    return offsets


def find_matching_frames_by_pts(video_files, frame_timestamps, target_time, startup_offsets):
    """
    Find frames from all 4 videos with matching actual timestamps.
    
    Simple formula:
    - For each camera, find the frame closest to: target_time + offset
    - This accounts for both different starts and different frame rates automatically
    """
    matching_frames = []
    
    for vid in video_files:
        vid_name = os.path.basename(vid)
        
        if vid_name not in frame_timestamps:
            print(f"  ⚠️  {vid_name} not in timestamp data")
            return None
        
        timestamps = frame_timestamps[vid_name]['timestamps']
        offset = startup_offsets[vid_name]
        
        # Target PTS for this camera: adjust by its startup offset
        target_pts = target_time + offset
        
        # Find frame closest to this target PTS
        best_idx = 0
        best_diff = abs(timestamps[0] - target_pts)
        
        for idx, ts in enumerate(timestamps):
            diff = abs(ts - target_pts)
            if diff < best_diff:
                best_diff = diff
                best_idx = idx
        
        # Bounds check
        if best_idx >= len(timestamps):
            best_idx = len(timestamps) - 1
        
        actual_ts = timestamps[best_idx]
        
        print(f"  📹 {vid_name} → frame #{best_idx} (PTS: {actual_ts:.3f}s, target: {target_pts:.3f}s, offset: {offset*1000:+.0f}ms)")
        matching_frames.append((best_idx, actual_ts))
    
    return matching_frames


def get_frame_by_number(video_file, frame_number):
    """Extract a specific frame number from a video file using imageio"""
    try:
        print(f"    Reading frame {frame_number}...", end=" ", flush=True)
        
        reader = imageio.get_reader(video_file, 'ffmpeg', pixelformat='rgb24')
        
        # Get frame
        frame = reader.get_data(frame_number)
        reader.close()
        
        print("✓")
        return frame
    
    except Exception as e:
        print(f"✗")
        print(f"    ⚠️ Error: {str(e)[:100]}")
        return None


def get_video_duration(video_file):
    """Get video duration in seconds using imageio"""
    try:
        reader = imageio.get_reader(video_file, 'ffmpeg')
        meta = reader.get_meta_data()
        fps = meta.get('fps', 30)
        nframes = len(reader)
        reader.close()
        
        duration = nframes / fps
        return duration
    except Exception as e:
        print(f"  ⚠️  Error getting duration: {e}")
        return None


def add_text_overlay(frame_array, session_name, timestamp_str):
    """Add session name and timestamp text overlay to frame using Pillow"""
    if frame_array is None:
        return None
    
    # Convert numpy array to PIL Image (imageio uses RGB)
    frame_pil = Image.fromarray(frame_array)
    
    h, w = frame_pil.size[1], frame_pil.size[0]  # PIL uses (width, height)
    
    # Create overlay with semi-transparent background
    overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    
    # Semi-transparent rectangle at bottom
    overlay_draw.rectangle(
        [(10, h-70), (w-10, h-5)],
        fill=(0, 0, 0, 77)  # 30% opacity
    )
    
    # Convert back to RGB for overlay
    frame_overlay = Image.new('RGB', (w, h))
    frame_overlay.paste(frame_pil)
    frame_overlay.paste(overlay, (0, 0), overlay)
    
    # Draw text
    draw = ImageDraw.Draw(frame_overlay)
    
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except:
        font = ImageFont.load_default()
    
    text_color = (0, 255, 0)  # Green
    
    # Session name
    draw.text(
        (20, h-55),
        f"Session: {session_name}",
        fill=text_color,
        font=font
    )
    
    # Timestamp
    draw.text(
        (20, h-25),
        f"Time: {timestamp_str}",
        fill=text_color,
        font=font
    )
    
    return np.array(frame_overlay)


def create_grid(frames, session_name, timestamps, output_path):
    """Create a 2x2 grid of frames with labels and overlay using Pillow"""
    if len(frames) != 4 or len(timestamps) != 4:
        print("Error: Need exactly 4 frames and 4 timestamps")
        return False
    
    # Check if all frames are valid
    valid_frames = []
    valid_times = []
    for frame, ts in zip(frames, timestamps):
        if frame is not None:
            valid_frames.append(frame)
            valid_times.append(ts)
        else:
            print(f"  Skipping invalid frame at {ts}s")
    
    if len(valid_frames) == 0:
        print("Error: No valid frames extracted")
        return False
    
    # Resize frames to consistent size
    target_h, target_w = 720, 1280
    
    resized_frames = []
    labels = ["BLACK", "BLUE", "GREEN", "RED"]
    
    for i, (frame, ts) in enumerate(zip(valid_frames[:4], valid_times[:4])):
        # Convert numpy array to PIL Image
        frame_pil = Image.fromarray(frame)
        
        # Resize using Pillow
        frame_pil = frame_pil.resize((target_w, target_h), Image.Resampling.LANCZOS)
        
        # Add overlay with session and timestamp
        time_str = f"{ts:.1f}s"
        frame_array = np.array(frame_pil)
        frame_array = add_text_overlay(frame_array, session_name, time_str)
        frame_pil = Image.fromarray(frame_array)
        
        # Add camera label at top
        draw = ImageDraw.Draw(frame_pil)
        
        # Semi-transparent background for label
        overlay = Image.new('RGBA', (target_w, target_h), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle([(0, 0), (target_w, 60)], fill=(0, 0, 0, 102))  # 40% opacity
        
        frame_rgb = Image.new('RGB', (target_w, target_h))
        frame_rgb.paste(frame_pil)
        frame_rgb.paste(overlay, (0, 0), overlay)
        
        # Draw label
        draw = ImageDraw.Draw(frame_rgb)
        try:
            font = ImageFont.truetype("arial.ttf", 32)
        except:
            font = ImageFont.load_default()
        
        draw.text(
            (20, 15),
            f"Camera: {labels[i]}",
            fill=(0, 255, 0),
            font=font
        )
        
        resized_frames.append(frame_rgb)
    
    # Pad with black frames if needed
    while len(resized_frames) < 4:
        black = Image.new('RGB', (target_w, target_h), (0, 0, 0))
        resized_frames.append(black)
    
    # Create 2x2 grid using Pillow
    grid_w = target_w * 2
    grid_h = target_h * 2
    grid = Image.new('RGB', (grid_w, grid_h))
    
    grid.paste(resized_frames[0], (0, 0))
    grid.paste(resized_frames[1], (target_w, 0))
    grid.paste(resized_frames[2], (0, target_h))
    grid.paste(resized_frames[3], (target_w, target_h))
    
    # Save
    try:
        grid.save(output_path, 'JPEG', quality=90)
        print(f"  ✅ Saved: {os.path.basename(output_path)}")
        return True
    except Exception as e:
        print(f"  ❌ Failed to save grid: {e}")
        return False


def main():
    # Determine session folder
    if len(sys.argv) > 1:
        session_dir = sys.argv[1]
    else:
        print("🔍 Finding latest session...")
        session_dir = find_latest_session()
    
    if not session_dir or not os.path.exists(session_dir):
        print(f"Error: Session directory not found: {session_dir}")
        sys.exit(1)
    
    session_name = os.path.basename(session_dir)
    print(f"📁 Using session: {session_name}")
    
    # Load cached frame timestamps
    frame_timestamps = load_frame_timestamps(session_dir)
    if not frame_timestamps:
        print("\n❌ Cannot proceed without frame_timestamps.json")
        print("   Make sure frame_analyzer.py has been run on the Pi first.")
        sys.exit(1)
    
    print(f"✅ Loaded frame timestamp data")
    
    # Calculate startup offsets
    startup_offsets = calculate_startup_offsets(frame_timestamps)
    if not startup_offsets:
        print("Error: Could not calculate startup offsets")
        sys.exit(1)
    
    # Load metrics for duration
    metrics = load_metrics(session_dir)
    if not metrics:
        print("Warning: Could not load metrics")
        duration = 60
    else:
        duration = metrics['duration_seconds']
        print(f"⏱️  Duration: {duration}s")
    
    # Find video files
    video_files = sorted(glob.glob(os.path.join(session_dir, "*.mkv")))
    video_files.extend(sorted(glob.glob(os.path.join(session_dir, "*.mp4"))))
    
    if len(video_files) < 4:
        print(f"Error: Expected 4 video files, found {len(video_files)}")
        sys.exit(1)
    
    video_files = video_files[:4]
    print(f"✅ Found {len(video_files)} video files")
    
    # Create output directory
    output_dir = os.path.join(session_dir, "frame_sync")
    os.makedirs(output_dir, exist_ok=True)
    print(f"📂 Output directory: {output_dir}")
    
    # Determine timestamps to extract
    # Since network seeking is slow, just extract first 5 seconds
    # This shows startup sync which you said is off by 100s of ms
    timestamps = [0.5, 1.0, 2.0, 3.0, 5.0]
    
    print(f"\n🎬 Extracting first 5 seconds (faster than seeking):")
    
    # Extract frames and create grids using real PTS data
    for idx, ts in enumerate(timestamps, 1):
        print(f"\n[{idx}/{len(timestamps)}] Extracting frames at global time {ts:.1f}s...")
        
        # Find matching frames across all 4 cameras using actual PTS timestamps + startup offsets
        frame_info = find_matching_frames_by_pts(video_files, frame_timestamps, ts, startup_offsets)
        
        if not frame_info:
            print(f"  ❌ Could not find matching frames at {ts:.1f}s")
            continue
        
        frames = []
        actual_timestamps = []
        
        for i, (frame_num, actual_ts) in enumerate(frame_info):
            frame = get_frame_by_number(video_files[i], frame_num)
            frames.append(frame)
            actual_timestamps.append(actual_ts)
        
        # Create grid
        ts_str = f"{ts:06.1f}".replace(".", "_")
        output_file = os.path.join(output_dir, f"sync_{idx:02d}_{ts_str}s.jpg")
        
        create_grid(frames, session_name, timestamps=actual_timestamps, output_path=output_file)
    
    print(f"\n✅ Frame sync check complete!")
    print(f"📊 All images saved to: {output_dir}")


if __name__ == "__main__":
    main()
