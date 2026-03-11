# Instacore Camera Recording System

Remote camera recording and drop-frame analysis system for Raspberry Pi 5 with multiple USB cameras.

## One-Time Setup on Raspberry Pi

SSH into your Pi and run:
```bash
cd ~
git clone https://github.com/awgibbons/pinstacore.git pinstacore
cd pinstacore
sudo bash setup_instacore.sh

# Optional but recommended: ignore chmod-only file changes in this repo
git config core.fileMode false
```

After reboot, connect to WiFi:
- Network: `instacore`
- Password: `ologic123`
- Web Interface: http://10.1.1.1

## What The Current System Does

- Records from any detected even-numbered V4L camera devices up to 8 cameras.
- Saves sessions to either:
	- `~/sessions`
	- `/mnt/sd/sessions`
- Shows one combined gallery in the web UI with a small source label (`Home` or `USB`).
- Stores per-session recording metadata in `recording_metrics.json`.
- Lets you manually run or re-run dropped-frame analysis after a session finishes.
- Generates:
	- `report.md` for readable results
	- `analysis.json` for structured results
	- `frame_timestamps.json` for extra timing data

## Recording Workflow

1. Open the web UI at `http://10.1.1.1`.
2. Select recording length.
3. Select destination (`Home` or `USB Drive`).
4. Press `START`.
5. Press `STOP` early if needed.

Notes:
- If no cameras are detected, the home page will show an error instead of hanging.
- Early-stop recordings still save usable session files and `recording_metrics.json`.
- Drop-frame analysis does not run automatically.

## Running Analysis

You can run analysis in either of these places:

1. From the `Latest session` box on the home page after a recording ends.
2. From the individual session page in the gallery.

Analysis behavior:
- Analysis runs in the background.
- You can re-run analysis later if you want fresh results.
- Session pages show whether analysis has not run yet, is running, completed, or failed.
- Once available, the session page provides links to `View Report` and `View JSON`.

Drop detection rule:
- A dropped-frame anomaly is flagged when the gap between adjacent frame timestamps is greater than `1.5x` the expected frame interval.

## Session Output

Each recording creates a folder like:

```text
~/sessions/session_MMDD_HHMMSS
```

or on USB:

```text
/mnt/sd/sessions/session_MMDD_HHMMSS
```

Typical contents:

- `camera_1.mkv` and other camera files
- `recording_metrics.json`
- `analysis_status.json` after analysis is requested
- `report.md` after analysis completes
- `analysis.json` after analysis completes
- `frame_timestamps.json` after analysis completes

## Normal Update Flow

### 1. On your development machine
```bash
git add .
git commit -m "Describe your change"
git push
```

### 2. On the Pi
```bash
cd ~/pinstacore
git pull --ff-only
sudo systemctl restart instacore-web.service
```

You do not need to re-run `setup_instacore.sh` for normal code or template updates.

## If `git pull` Fails on the Pi

If you see `local changes would be overwritten`:
```bash
cd ~/pinstacore
git status
git restore web_trigger.py start_cameras.sh template_home.html template_gallery.html template_session.html
git pull --ff-only
```

This usually happens from local permission or working-tree drift, not intentional edits.

## Main Files

- `setup_instacore.sh` - complete system setup script
- `start_cameras.sh` - recording script that creates the session folder and metrics file
- `session_analyzer.py` - manual dropped-frame analyzer
- `web_trigger.py` - Flask web server and analysis routing
- `template_home.html` - home screen
- `template_gallery.html` - combined gallery view
- `template_session.html` - session detail view

Runtime behavior:
- The web service runs directly from the cloned repo directory.
- Session folders are stored outside the repo under `~/sessions` or `/mnt/sd/sessions`.
- The legacy `video analyzer/` folder is reference material only and is not part of the current runtime flow.
