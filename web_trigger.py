from flask import Flask, render_template, redirect, request, url_for, send_from_directory
import subprocess
import os
import time
from datetime import datetime

# Resolve paths from this script location so service can run from any repo path.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=BASE_DIR)

RECORD_SCRIPT_PATH = os.path.join(BASE_DIR, "start_cameras.sh")
SESSIONS_DIR = os.path.expanduser("~/sessions")

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
}

# Ensure the sessions directory exists even if we haven't recorded yet
os.makedirs(SESSIONS_DIR, exist_ok=True)

# --- UTILITY FUNCTIONS ---

def check_recording():
    try:
        subprocess.check_output(["pgrep", "-x", "ffmpeg"])
        return True
    except subprocess.CalledProcessError:
        return False


def get_latest_session_dir():
    if not os.path.isdir(SESSIONS_DIR):
        return None

    candidates = [
        os.path.join(SESSIONS_DIR, entry)
        for entry in os.listdir(SESSIONS_DIR)
        if os.path.isdir(os.path.join(SESSIONS_DIR, entry))
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

    recording_size = "0 B"
    latest_session = get_latest_session_dir()
    if latest_session:
        recording_size = format_size(get_directory_size_bytes(latest_session))

    return {
        "is_recording": is_recording,
        "error_msg": error_msg,
        "duration_options": DURATION_OPTIONS,
        "selected_duration": RECORDING_STATE["duration"],
        "remaining_seconds": remaining_seconds,
        "remaining_label": remaining_label,
        "recording_size": recording_size,
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

    RECORDING_STATE["duration"] = duration

    if not check_recording():
        try:
            # Fire-and-forget so the HTTP request returns immediately.
            subprocess.Popen(
                ["bash", RECORD_SCRIPT_PATH, str(duration)],
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

@app.route('/stop', methods=['POST'])
def stop_recording():
    if check_recording():
        os.system("killall -INT ffmpeg")
        time.sleep(1)
    RECORDING_STATE["end_ts"] = None
    RECORDING_STATE["pending_until"] = None
    return redirect(url_for('home'))

# --- NEW GALLERY ROUTES ---

@app.route('/gallery')
def gallery():
    # List folders in the sessions directory, sorted newest first
    sessions = []
    if os.path.exists(SESSIONS_DIR):
        folders = [f for f in os.listdir(SESSIONS_DIR) if os.path.isdir(os.path.join(SESSIONS_DIR, f))]
        # Sort descending so newest is at the top
        folders.sort(reverse=True)
        for folder in folders:
            session_dir = os.path.join(SESSIONS_DIR, folder)
            duration_seconds = get_sample_video_duration_seconds(session_dir)
            duration_label = "Unknown"
            if duration_seconds is not None:
                duration_label = format_duration_label(duration_seconds)

            sessions.append(
                {
                    "name": folder,
                    "display_time": format_session_datetime(folder),
                    "size": format_size(get_directory_size_bytes(session_dir)),
                    "duration": duration_label,
                }
            )
    return render_template('template_gallery.html', sessions=sessions)

@app.route('/gallery/<session_name>')
def session_detail(session_name):
    target_dir = os.path.join(SESSIONS_DIR, session_name)
    files = []
    # Ensure no path traversal tricks and that the folder exists
    if os.path.isdir(target_dir) and ".." not in session_name:
        # Get only the files, sorted alphabetically
        all_files = [f for f in os.listdir(target_dir) if os.path.isfile(os.path.join(target_dir, f))]
        all_files.sort()
        files = all_files
    return render_template('template_session.html', session_name=session_name, files=files)

@app.route('/download/<session_name>/<filename>')
def download_file(session_name, filename):
    # Flask's send_from_directory is specifically designed to safely serve files
    target_dir = os.path.join(SESSIONS_DIR, session_name)
    if ".." not in session_name and ".." not in filename:
        return send_from_directory(target_dir, filename, as_attachment=True)
    return "Invalid request.", 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
