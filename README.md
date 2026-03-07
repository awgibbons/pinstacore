# Instacore Camera Recording System

Remote camera recording system for Raspberry Pi 5 with multiple USB cameras.

## Quick Deploy to Raspberry Pi

SSH into your Pi and run:
```bash
cd ~
git clone https://github.com/awgibbons/pinstacore.git instacore_scripts
cd instacore_scripts
sudo bash setup_instacore.sh
```

## Files

- `setup_instacore.sh` - Complete system setup script (run once)
- `start_cameras.sh` - Camera recording script
- `web_trigger.py` - Flask web server
- `template_*.html` - Web interface templates

## Access

After setup, connect to WiFi:
- Network: `instacore`
- Password: `ologic123`
- Web Interface: http://10.1.1.1
