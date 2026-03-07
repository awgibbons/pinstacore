# Instacore Camera Recording System

Remote camera recording system for Raspberry Pi 5 with multiple USB cameras.

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

You do not need to re-run `setup_instacore.sh` for normal code/template updates.

## If `git pull` Fails on the Pi

If you see "local changes would be overwritten":
```bash
cd ~/pinstacore
git status
git restore web_trigger.py start_cameras.sh
git pull --ff-only
```

This usually happens from local permission/working-tree drift, not intentional edits.

## Files

- `setup_instacore.sh` - Complete system setup script (run once)
- `start_cameras.sh` - Camera recording script
- `web_trigger.py` - Flask web server
- `template_*.html` - Web interface templates

Runtime behavior:
- The web service runs directly from the cloned repo directory.
- Recording sessions are saved under `~/sessions`.
