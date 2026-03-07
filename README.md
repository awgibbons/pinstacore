# Instacore Camera Recording System

Remote camera recording system for Raspberry Pi 5 with multiple USB cameras.

## Quick Deploy to Raspberry Pi

SSH into your Pi and run:
```bash
cd ~
git clone https://github.com/awgibbons/pinstacore.git pinstacore
cd pinstacore
sudo bash setup_instacore.sh
```

## Updating After Changes

After the first setup, updates are simple and do not require re-running setup:
```bash
cd ~/pinstacore
git pull
sudo systemctl restart instacore-web.service
```

## Files

- `setup_instacore.sh` - Complete system setup script (run once)
- `start_cameras.sh` - Camera recording script
- `web_trigger.py` - Flask web server
- `template_*.html` - Web interface templates

Runtime behavior:
- The web service runs directly from the cloned repo directory.
- Recording sessions are saved under `~/sessions`.

## Access

After setup, connect to WiFi:
- Network: `instacore`
- Password: `ologic123`
- Web Interface: http://10.1.1.1
