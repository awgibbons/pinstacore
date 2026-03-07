from flask import Flask, render_template, redirect, request, url_for, send_from_directory
import subprocess
import os
import time

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


def build_home_context(error_msg=None):
    is_recording = check_recording()

    if not is_recording:
        RECORDING_STATE["end_ts"] = None

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
        process = subprocess.Popen(["bash", RECORD_SCRIPT_PATH, str(duration)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # Quick check if script immediately fails
        time.sleep(0.5)
        retcode = process.poll()
        if retcode is not None:
            # Script exited immediately, something is wrong
            error_output = process.stdout.read().strip() if process.stdout else "Unknown Error"
            if not error_output:
                error_output = "Unknown Error: Script exited silently."
            return render_template('template_home.html', **build_home_context(error_msg=error_output))
        RECORDING_STATE["end_ts"] = time.time() + duration
        # Script is running, redirect immediately (auto-refresh will update status)
    return redirect(url_for('home'))

@app.route('/stop', methods=['POST'])
def stop_recording():
    if check_recording():
        os.system("killall -INT ffmpeg")
        time.sleep(1)
    RECORDING_STATE["end_ts"] = None
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
        sessions = folders
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
