from flask import Flask, render_template, redirect, request, url_for, send_from_directory, Response
import subprocess
import os
import time
import threading
import shutil
import json
from datetime import datetime

# Resolve paths from this script location so service can run from any repo path.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=BASE_DIR)

RECORD_SCRIPT_PATH = os.path.join(BASE_DIR, "start_cameras.sh")
ANALYZER_SCRIPT_PATH = os.path.join(BASE_DIR, "session_analyzer.py")
UPDATE_SCRIPT_PATH = os.path.join(BASE_DIR, "update_instacore.sh")
UPDATE_STATUS_PATH = os.path.join(BASE_DIR, "update_status.json")
SESSIONS_HOME_DIR = os.path.expanduser("~/sessions")
SESSIONS_USB_DIR = "/mnt/sd/sessions"
ANALYSIS_TIMEOUT_SECONDS = int(os.environ.get("ANALYSIS_TIMEOUT_SECONDS", "0") or "0")

DESTINATION_OPTIONS = [
    {"key": "home", "label": "Home", "path": SESSIONS_HOME_DIR, "hint": "~/sessions"},
    {"key": "usb", "label": "USB Drive", "path": SESSIONS_USB_DIR, "hint": "/mnt/sd"},
]
DEFAULT_DESTINATION = "home"
DESTINATION_MAP = {item["key"]: item for item in DESTINATION_OPTIONS}

DURATION_OPTIONS = [
    (60, "1 minute"),
    (600, "10 minutes"),
    (1800, "30 minutes"),
    (3600, "1 hour"),
    (14400, "4 hours"),
]
ALLOWED_DURATIONS = {seconds for seconds, _ in DURATION_OPTIONS}

RECORDING_STATE = {
    "duration": 3600,
    "end_ts": None,
    "pending_until": None,
    "destination": DEFAULT_DESTINATION,
}

# Ensure the sessions directory exists even if we haven't recorded yet
os.makedirs(SESSIONS_HOME_DIR, exist_ok=True)

# --- UTILITY FUNCTIONS ---

def check_recording():
    try:
        subprocess.check_output(["pgrep", "-x", "ffmpeg"])
        return True
    except subprocess.CalledProcessError:
        return False


def get_recordable_cameras(max_cameras=8):
    cameras = []
    for idx in range(max_cameras):
        node = f"/dev/video{idx * 2}"
        if os.path.exists(node):
            cameras.append(node)
    return cameras


def get_destination_path(destination_key):
    item = DESTINATION_MAP.get(destination_key)
    if not item:
        return None
    return item["path"]


def get_destination_usage_path(destination_key):
    path = get_destination_path(destination_key)
    if not path:
        return None

    if destination_key == "usb":
        if os.path.ismount("/mnt/sd") or os.path.isdir("/mnt/sd"):
            return "/mnt/sd"
        return path

    return os.path.expanduser("~")


def get_status_path(session_dir):
    return os.path.join(session_dir, "analysis_status.json")


def get_metrics_path(session_dir):
    return os.path.join(session_dir, "recording_metrics.json")


def get_analysis_path(session_dir):
    return os.path.join(session_dir, "analysis.json")


def get_report_path(session_dir):
    return os.path.join(session_dir, "report.md")


def get_analysis_log_path(session_dir):
    return os.path.join(session_dir, "analysis_runner.log")


def load_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write_json_file(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


def read_update_status():
    status = load_json_file(UPDATE_STATUS_PATH)
    if status:
        return status
    return {"state": "idle", "message": "No update run yet.", "updated_at": None, "details": ""}


def set_update_status(state, message, details=""):
    write_json_file(
        UPDATE_STATUS_PATH,
        {
            "state": state,
            "message": message,
            "updated_at": int(time.time()),
            "details": details,
        },
    )


def read_analysis_status(session_dir):
    status_path = get_status_path(session_dir)
    status = load_json_file(status_path)
    if status:
        return status

    if os.path.exists(get_analysis_path(session_dir)) or os.path.exists(get_report_path(session_dir)):
        return {"state": "complete", "updated_at": int(time.time())}

    return {"state": "not_run", "updated_at": None}


def set_analysis_status(session_dir, state, error=None, progress=None):
    payload = {
        "state": state,
        "updated_at": int(time.time()),
    }
    if error:
        payload["error"] = error
    if progress is not None:
        payload["progress"] = progress
    write_json_file(get_status_path(session_dir), payload)


def get_session_dir(destination_key, session_name):
    base = get_destination_path(destination_key)
    if not base:
        return None
    if ".." in session_name:
        return None
    target = os.path.join(base, session_name)
    if not os.path.isdir(target):
        return None
    return target


def gather_sessions():
    sessions = []
    for item in DESTINATION_OPTIONS:
        destination_key = item["key"]
        base = item["path"]
        if not os.path.isdir(base):
            continue

        for name in os.listdir(base):
            session_dir = os.path.join(base, name)
            if not os.path.isdir(session_dir):
                continue

            duration_seconds = get_sample_video_duration_seconds(session_dir)
            duration_label = "Unknown"
            if duration_seconds is not None:
                duration_label = format_duration_label(duration_seconds)

            status = read_analysis_status(session_dir)
            sessions.append(
                {
                    "name": name,
                    "destination_key": destination_key,
                    "destination_label": item["label"],
                    "display_time": format_session_datetime(name),
                    "size": format_size(get_directory_size_bytes(session_dir)),
                    "duration": duration_label,
                    "mtime": os.path.getmtime(session_dir),
                    "analysis_state": status.get("state", "not_run"),
                    "analysis_error": status.get("error"),
                    "has_report": os.path.exists(get_report_path(session_dir)),
                }
            )

    sessions.sort(key=lambda row: row["mtime"], reverse=True)
    return sessions


def get_latest_session_summary():
    latest = None
    for item in DESTINATION_OPTIONS:
        base = item["path"]
        if not os.path.isdir(base):
            continue

        for name in os.listdir(base):
            session_dir = os.path.join(base, name)
            if not os.path.isdir(session_dir):
                continue

            mtime = os.path.getmtime(session_dir)
            if latest is None or mtime > latest["mtime"]:
                status = read_analysis_status(session_dir)
                latest = {
                    "name": name,
                    "destination_key": item["key"],
                    "destination_label": item["label"],
                    "display_time": format_session_datetime(name),
                    "size": format_size(get_directory_size_bytes(session_dir)),
                    "mtime": mtime,
                    "analysis_state": status.get("state", "not_run"),
                    "analysis_error": status.get("error"),
                    "has_report": os.path.exists(get_report_path(session_dir)),
                }

    return latest


def is_destination_available(destination_key):
    path = get_destination_path(destination_key)
    if not path:
        return False

    if destination_key == "usb":
        return os.path.isdir("/mnt/sd") and os.access("/mnt/sd", os.W_OK)

    return True


def get_destination_label(destination_key):
    item = DESTINATION_MAP.get(destination_key)
    if not item:
        return "Unknown"
    return f"{item['label']} ({item['hint']})"


def get_latest_session_dir(sessions_dir):
    if not os.path.isdir(sessions_dir):
        return None

    candidates = [
        os.path.join(sessions_dir, entry)
        for entry in os.listdir(sessions_dir)
        if os.path.isdir(os.path.join(sessions_dir, entry))
    ]
    if not candidates:
        return None

    return max(candidates, key=os.path.getmtime)


def get_directory_size_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                total += os.path.getsize(file_path)
            except OSError:
                pass
    return total


def get_free_space_bytes(path):
    try:
        _, _, free = shutil.disk_usage(path)
        return free
    except OSError:
        return 0


def get_destination_free_space_bytes(destination_key):
    usage_path = get_destination_usage_path(destination_key)
    if not usage_path:
        return 0
    return get_free_space_bytes(usage_path)


def format_size(num_bytes):
    value = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return "0 B"


def format_remaining(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_duration_label(seconds):
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_session_datetime(session_name):
    # Expected format: session_MMDD_HHMMSS
    if not session_name.startswith("session_"):
        return session_name

    ts_part = session_name[len("session_"):]
    try:
        parsed = datetime.strptime(ts_part, "%m%d_%H%M%S")
        parsed = parsed.replace(year=datetime.now().year)
        return parsed.strftime("%b %d, %Y at %I:%M:%S %p")
    except ValueError:
        return session_name


def get_sample_video_duration_seconds(session_dir):
    candidates = []
    for name in os.listdir(session_dir):
        path = os.path.join(session_dir, name)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext in {".mkv", ".mp4", ".mov", ".avi", ".m4v"}:
            candidates.append(path)

    if not candidates:
        return None

    sample = sorted(candidates)[0]
    try:
        output = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                sample,
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).strip()
        return int(float(output))
    except (subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_video_duration_seconds(video_path):
    try:
        output = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
        return float(output)
    except (subprocess.CalledProcessError, ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def build_session_recording_summary(session_dir):
    metrics = load_json_file(get_metrics_path(session_dir)) or {}
    expected_seconds = metrics.get("requested_duration_seconds")
    recorder_seconds = metrics.get("duration_seconds")

    per_file_durations = []
    all_files = sorted(os.listdir(session_dir)) if os.path.isdir(session_dir) else []
    for name in all_files:
        path = os.path.join(session_dir, name)
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in {".mkv", ".mp4", ".mov", ".avi", ".m4v"}:
            continue
        duration_seconds = get_video_duration_seconds(path)
        per_file_durations.append(
            {
                "file": name,
                "seconds": duration_seconds,
                "label": format_duration_label(duration_seconds) if duration_seconds is not None else "Unknown",
            }
        )

    valid_durations = [row["seconds"] for row in per_file_durations if row["seconds"] is not None]
    shortest_seconds = min(valid_durations) if valid_durations else None
    longest_seconds = max(valid_durations) if valid_durations else None

    duration_warning = None
    if expected_seconds:
        if recorder_seconds and recorder_seconds < (expected_seconds - 5):
            duration_warning = "Recorder stopped before the requested duration."
        elif shortest_seconds is not None and shortest_seconds < (expected_seconds * 0.95):
            duration_warning = "One or more camera files are shorter than the requested duration."

    return {
        "expected_seconds": expected_seconds,
        "expected_label": format_duration_label(expected_seconds) if expected_seconds else "Unknown",
        "recorder_seconds": recorder_seconds,
        "recorder_label": format_duration_label(recorder_seconds) if recorder_seconds else "Unknown",
        "shortest_seconds": shortest_seconds,
        "shortest_label": format_duration_label(shortest_seconds) if shortest_seconds is not None else "Unknown",
        "longest_seconds": longest_seconds,
        "longest_label": format_duration_label(longest_seconds) if longest_seconds is not None else "Unknown",
        "duration_warning": duration_warning,
        "per_file_durations": per_file_durations,
    }


def build_home_context(error_msg=None):
    ffmpeg_running = check_recording()
    now = time.time()
    pending_until = RECORDING_STATE["pending_until"]
    pending_start = bool(
        RECORDING_STATE["end_ts"]
        and pending_until
        and now < pending_until
    )

    is_recording = ffmpeg_running or pending_start

    # If ffmpeg has started, clear startup-pending mode.
    if ffmpeg_running:
        RECORDING_STATE["pending_until"] = None

    # If nothing is recording and startup grace has elapsed, clear stale timer state.
    if (not ffmpeg_running) and (not pending_start):
        RECORDING_STATE["end_ts"] = None
        RECORDING_STATE["pending_until"] = None

    remaining_seconds = None
    remaining_label = "--:--"
    if is_recording and RECORDING_STATE["end_ts"]:
        remaining_seconds = max(0, int(RECORDING_STATE["end_ts"] - time.time()))
        remaining_label = format_remaining(remaining_seconds)

    destination_key = RECORDING_STATE["destination"]
    sessions_dir = get_destination_path(destination_key) or SESSIONS_HOME_DIR

    recording_size = "0 B"
    latest_session = get_latest_session_dir(sessions_dir)
    if latest_session:
        recording_size = format_size(get_directory_size_bytes(latest_session))

    free_space = format_size(get_destination_free_space_bytes(destination_key))

    destination_options = []
    for item in DESTINATION_OPTIONS:
        destination_options.append(
            {
                "key": item["key"],
                "label": item["label"],
                "hint": item["hint"],
                "available": is_destination_available(item["key"]),
            }
        )

    latest_session = None
    if not is_recording:
        latest_session = get_latest_session_summary()

    update_status = read_update_status()

    return {
        "is_recording": is_recording,
        "error_msg": error_msg,
        "duration_options": DURATION_OPTIONS,
        "selected_duration": RECORDING_STATE["duration"],
        "remaining_seconds": remaining_seconds,
        "remaining_label": remaining_label,
        "recording_size": recording_size,
        "free_space": free_space,
        "selected_destination": destination_key,
        "recording_destination": get_destination_label(destination_key),
        "destination_options": destination_options,
        "latest_session": latest_session,
        "update_status": update_status,
    }

# --- ROUTES ---

@app.route('/')
def home():
    return render_template('template_home.html', **build_home_context())

@app.route('/start', methods=['POST'])
def start_recording():
    duration_raw = request.form.get("duration", "3600")
    try:
        duration = int(duration_raw)
    except ValueError:
        duration = 3600

    if duration not in ALLOWED_DURATIONS:
        duration = 3600

    destination_key = request.form.get("destination", DEFAULT_DESTINATION)
    if destination_key not in DESTINATION_MAP:
        destination_key = DEFAULT_DESTINATION

    if not is_destination_available(destination_key):
        return render_template(
            'template_home.html',
            **build_home_context(error_msg="USB destination is unavailable. Check that /mnt/sd is mounted and writable."),
        )

    destination_path = get_destination_path(destination_key) or SESSIONS_HOME_DIR

    try:
        os.makedirs(destination_path, exist_ok=True)
    except OSError as exc:
        return render_template(
            'template_home.html',
            **build_home_context(error_msg=f"Cannot access destination: {exc}"),
        )

    RECORDING_STATE["duration"] = duration
    RECORDING_STATE["destination"] = destination_key

    cameras = get_recordable_cameras(max_cameras=8)
    if not cameras:
        return render_template(
            'template_home.html',
            **build_home_context(error_msg="No cameras detected. Connect at least one camera and try again."),
        )

    if not check_recording():
        try:
            # Fire-and-forget so the HTTP request returns immediately.
            subprocess.Popen(
                ["bash", RECORD_SCRIPT_PATH, str(duration), destination_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            RECORDING_STATE["end_ts"] = time.time() + duration
            # Allow a short window for ffmpeg to appear before first status poll.
            RECORDING_STATE["pending_until"] = time.time() + 8
        except Exception as exc:
            return render_template('template_home.html', **build_home_context(error_msg=f"Failed to start recording: {exc}"))
    return redirect(url_for('home'))

@app.route('/api/session-size')
def api_session_size():
    destination_key = RECORDING_STATE["destination"]
    sessions_dir = get_destination_path(destination_key) or SESSIONS_HOME_DIR

    recording_size = "0 B"
    latest_session = get_latest_session_dir(sessions_dir)
    if latest_session:
        recording_size = format_size(get_directory_size_bytes(latest_session))
    free_space = format_size(get_destination_free_space_bytes(destination_key))
    return {
        "size": recording_size,
        "free_space": free_space,
        "destination": get_destination_label(destination_key),
    }


@app.route('/api/destination-info')
def api_destination_info():
    destination_key = request.args.get("destination", DEFAULT_DESTINATION)
    if destination_key not in DESTINATION_MAP:
        destination_key = DEFAULT_DESTINATION

    return {
        "destination": get_destination_label(destination_key),
        "free_space": format_size(get_destination_free_space_bytes(destination_key)),
        "available": is_destination_available(destination_key),
    }


@app.route('/api/update-status')
def api_update_status():
    return read_update_status()


@app.route('/update-software', methods=['POST'])
def update_software():
    ffmpeg_running = check_recording()
    pending_until = RECORDING_STATE["pending_until"]
    pending_start = bool(RECORDING_STATE["end_ts"] and pending_until and time.time() < pending_until)
    if ffmpeg_running or pending_start:
        return render_template(
            'template_home.html',
            **build_home_context(error_msg="Cannot update software while a recording is in progress."),
        )

    status = read_update_status()
    if status.get("state") in {"running", "restarting"}:
        return redirect(url_for('home'))

    if not os.path.exists(UPDATE_SCRIPT_PATH):
        return render_template(
            'template_home.html',
            **build_home_context(error_msg="Update script not found on device."),
        )

    set_update_status("running", "Starting software update...")
    try:
        subprocess.Popen(
            ["bash", UPDATE_SCRIPT_PATH],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        set_update_status("failed", f"Failed to start update: {exc}")
        return render_template(
            'template_home.html',
            **build_home_context(error_msg=f"Failed to start update: {exc}"),
        )

    return redirect(url_for('home'))

@app.route('/stop', methods=['POST'])
def stop_recording():
    def stop_ffmpeg_async():
        os.system("killall -INT ffmpeg")
        time.sleep(0.5)

    if check_recording():
        # Fire off stop in background thread so HTTP request returns immediately.
        thread = threading.Thread(target=stop_ffmpeg_async, daemon=True)
        thread.start()

    RECORDING_STATE["end_ts"] = None
    RECORDING_STATE["pending_until"] = None
    return redirect(url_for('home'))

# --- NEW GALLERY ROUTES ---

@app.route('/gallery')
def gallery():
    sessions = gather_sessions()
    return render_template('template_gallery.html', sessions=sessions)

@app.route('/gallery/<destination_key>/<session_name>')
def session_detail(destination_key, session_name):
    target_dir = get_session_dir(destination_key, session_name)
    if not target_dir:
        return "Session not found.", 404

    files = []
    all_files = [f for f in os.listdir(target_dir) if os.path.isfile(os.path.join(target_dir, f))]
    all_files.sort()
    files = all_files

    status = read_analysis_status(target_dir)
    recording_summary = build_session_recording_summary(target_dir)
    return render_template(
        'template_session.html',
        session_name=session_name,
        destination_key=destination_key,
        destination_label=get_destination_label(destination_key),
        files=files,
        recording_summary=recording_summary,
        analysis_state=status.get("state", "not_run"),
        analysis_error=status.get("error"),
        analysis_progress=status.get("progress"),
        analysis_progress_json=json.dumps(status.get("progress")) if status.get("progress") is not None else "null",
        has_report=os.path.exists(get_report_path(target_dir)),
        has_analysis_json=os.path.exists(get_analysis_path(target_dir)),
        has_analysis_log=os.path.exists(get_analysis_log_path(target_dir)),
    )

@app.route('/download/<destination_key>/<session_name>/<filename>')
def download_file(destination_key, session_name, filename):
    target_dir = get_session_dir(destination_key, session_name)
    if target_dir and ".." not in filename:
        return send_from_directory(target_dir, filename, as_attachment=True)
    return "Invalid request.", 400

@app.route('/delete/<destination_key>/<session_name>', methods=['POST'])
def delete_session(destination_key, session_name):
    target_dir = get_session_dir(destination_key, session_name)
    if target_dir:
        try:
            shutil.rmtree(target_dir)
        except Exception:
            pass
    return redirect(url_for('gallery'))


def analyze_session_async(session_dir):
    metrics_path = get_metrics_path(session_dir)
    if not os.path.exists(metrics_path):
        set_analysis_status(session_dir, "failed", error="recording_metrics.json not found")
        return

    set_analysis_status(
        session_dir,
        "running",
        progress={
            "total_files": 0,
            "completed_files": 0,
            "current_file": None,
            "current_file_frames_processed": 0,
        },
    )
    def _to_text(value):
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, memoryview):
            return value.tobytes().decode("utf-8", errors="replace")
        return str(value)

    try:
        timeout_seconds = ANALYSIS_TIMEOUT_SECONDS if ANALYSIS_TIMEOUT_SECONDS > 0 else None
        proc = subprocess.run(
            ["python3", ANALYZER_SCRIPT_PATH, metrics_path, "--status-path", get_status_path(session_dir)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        combined_output = (proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")
        try:
            with open(get_analysis_log_path(session_dir), "w", encoding="utf-8") as handle:
                handle.write(combined_output)
        except OSError:
            pass
    except subprocess.TimeoutExpired as exc:
        partial = ""
        if exc.stdout:
            partial += _to_text(exc.stdout)
        if exc.stderr:
            if partial:
                partial += "\n"
            partial += _to_text(exc.stderr)
        timeout_msg = f"Analysis timed out after {ANALYSIS_TIMEOUT_SECONDS}s"
        try:
            with open(get_analysis_log_path(session_dir), "w", encoding="utf-8") as handle:
                handle.write(timeout_msg + "\n\n")
                handle.write(partial)
        except OSError:
            pass
        set_analysis_status(
            session_dir,
            "failed",
            error=f"{timeout_msg}. Increase ANALYSIS_TIMEOUT_SECONDS or set it to 0 for no timeout.",
        )
        return
    except Exception as exc:
        set_analysis_status(session_dir, "failed", error=str(exc))
        return

    if proc.returncode == 0:
        set_analysis_status(
            session_dir,
            "complete",
            progress={
                "total_files": 0,
                "completed_files": 0,
                "current_file": None,
                "current_file_frames_processed": 0,
            },
        )
    else:
        error = (proc.stderr or proc.stdout or "Analyzer failed").strip()
        set_analysis_status(session_dir, "failed", error=(error[:320] + " (see analysis_runner.log)"))


@app.route('/analyze/<destination_key>/<session_name>', methods=['POST'])
def run_analysis(destination_key, session_name):
    session_dir = get_session_dir(destination_key, session_name)
    if not session_dir:
        return "Session not found.", 404

    thread = threading.Thread(target=analyze_session_async, args=(session_dir,), daemon=True)
    thread.start()
    return redirect(url_for('session_detail', destination_key=destination_key, session_name=session_name))


@app.route('/api/analysis-status/<destination_key>/<session_name>')
def api_analysis_status(destination_key, session_name):
    session_dir = get_session_dir(destination_key, session_name)
    if not session_dir:
        return {"error": "Session not found"}, 404

    status = read_analysis_status(session_dir)
    return {
        "state": status.get("state", "not_run"),
        "error": status.get("error"),
        "progress": status.get("progress"),
        "has_report": os.path.exists(get_report_path(session_dir)),
        "has_analysis_json": os.path.exists(get_analysis_path(session_dir)),
        "has_analysis_log": os.path.exists(get_analysis_log_path(session_dir)),
        "report_url": url_for('view_analysis_report', destination_key=destination_key, session_name=session_name),
        "json_url": url_for('view_analysis_json', destination_key=destination_key, session_name=session_name),
        "log_url": url_for('view_analysis_log', destination_key=destination_key, session_name=session_name),
    }


@app.route('/analysis/report/<destination_key>/<session_name>')
def view_analysis_report(destination_key, session_name):
    session_dir = get_session_dir(destination_key, session_name)
    if not session_dir:
        return "Session not found.", 404

    report_file = get_report_path(session_dir)
    if not os.path.exists(report_file):
        return "Analysis report not found.", 404
    return send_from_directory(session_dir, os.path.basename(report_file), as_attachment=False)


@app.route('/analysis/json/<destination_key>/<session_name>')
def view_analysis_json(destination_key, session_name):
    session_dir = get_session_dir(destination_key, session_name)
    if not session_dir:
        return "Session not found.", 404

    analysis_file = get_analysis_path(session_dir)
    if not os.path.exists(analysis_file):
        return "Analysis data not found.", 404
    return send_from_directory(session_dir, os.path.basename(analysis_file), as_attachment=False)


@app.route('/analysis/log/<destination_key>/<session_name>')
def view_analysis_log(destination_key, session_name):
    session_dir = get_session_dir(destination_key, session_name)
    if not session_dir:
        return "Session not found.", 404

    log_file = get_analysis_log_path(session_dir)
    if not os.path.exists(log_file):
        return "Analysis log not found.", 404
    return send_from_directory(session_dir, os.path.basename(log_file), as_attachment=False)


@app.route('/preview')
def preview():
    cameras = get_recordable_cameras(max_cameras=8)
    cam_indices = list(range(len(cameras)))
    is_recording = check_recording()
    try:
        selected_cam = int(request.args.get('cam', '0'))
    except ValueError:
        selected_cam = 0
    if selected_cam < 0 or selected_cam >= len(cameras):
        selected_cam = 0
    return render_template(
        'template_preview.html',
        cam_indices=cam_indices,
        selected_cam=selected_cam,
        is_recording=is_recording,
    )


@app.route('/preview/snapshot')
def preview_snapshot():
    try:
        cam_idx = int(request.args.get('cam', '0'))
    except ValueError:
        cam_idx = 0

    cameras = get_recordable_cameras(max_cameras=8)
    if cam_idx < 0 or cam_idx >= len(cameras):
        return Response('Camera not found', status=404, mimetype='text/plain')

    node = cameras[cam_idx]
    try:
        result = subprocess.run(
            [
                'ffmpeg', '-y',
                '-f', 'v4l2', '-input_format', 'mjpeg',
                '-framerate', '5',
                '-i', node,
                '-frames:v', '1',
                '-vf', 'scale=320:-1',
                '-f', 'image2',
                'pipe:1',
            ],
            capture_output=True,
            timeout=6,
        )
        if result.returncode != 0 or not result.stdout:
            return Response('Camera unavailable', status=503, mimetype='text/plain')
        return Response(result.stdout, mimetype='image/jpeg')
    except subprocess.TimeoutExpired:
        return Response('Camera unavailable', status=503, mimetype='text/plain')
    except OSError:
        return Response('Camera unavailable', status=503, mimetype='text/plain')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
