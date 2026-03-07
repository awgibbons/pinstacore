from flask import Flask, render_template, redirect, url_for, send_from_directory
import subprocess
import os
import time

# Resolve paths from this script location so service can run from any repo path.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=BASE_DIR)

RECORD_SCRIPT_PATH = os.path.join(BASE_DIR, "start_cameras.sh")
SESSIONS_DIR = os.path.expanduser("~/sessions")

# Ensure the sessions directory exists even if we haven't recorded yet
os.makedirs(SESSIONS_DIR, exist_ok=True)

# --- UTILITY FUNCTIONS ---

def check_recording():
    try:
        subprocess.check_output(["pgrep", "-x", "ffmpeg"])
        return True
    except subprocess.CalledProcessError:
        return False

# --- ROUTES ---

@app.route('/')
def home():
    return render_template('template_home.html', is_recording=check_recording(), error_msg=None)

@app.route('/start', methods=['POST'])
def start_recording():
    if not check_recording():
        process = subprocess.Popen(["bash", RECORD_SCRIPT_PATH], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # Quick check if script immediately fails
        time.sleep(0.5)
        retcode = process.poll()
        if retcode is not None:
            # Script exited immediately, something is wrong
            error_output = process.stdout.read().strip() if process.stdout else "Unknown Error"
            if not error_output:
                error_output = "Unknown Error: Script exited silently."
            return render_template('template_home.html', is_recording=False, error_msg=error_output)
        # Script is running, redirect immediately (auto-refresh will update status)
    return redirect(url_for('home'))

@app.route('/stop', methods=['POST'])
def stop_recording():
    if check_recording():
        os.system("killall -INT ffmpeg")
        time.sleep(1) 
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
