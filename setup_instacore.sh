#!/bin/bash

# ==========================================
# Instacore Complete Setup Script
# Raspberry Pi 5 Camera Recording System
# ==========================================

echo "========================================"
echo " Starting Instacore System Setup..."
echo "========================================"

# ==========================================
# 1. System Dependencies
# ==========================================
echo -e "\n>>> [1/6] Installing system dependencies..."

# Update system first
apt-get update

# Install all required packages
PACKAGES=(ffmpeg v4l-utils htop usbutils samba python3-flask)
for pkg in "${PACKAGES[@]}"; do
    if dpkg -l | grep -q " $pkg "; then
        echo "[OK] $pkg is already installed."
    else
        echo "[..] Installing $pkg..."
        apt-get install -y "$pkg"
    fi
done

# ==========================================
# 2. Hardware Configuration
# ==========================================
echo -e "\n>>> [2/6] Applying hardware optimizations..."

CONFIG="/boot/firmware/config.txt"
CMDLINE="/boot/firmware/cmdline.txt"

# Apply hardware tweaks for USB cameras
SETTINGS=(
    "usb_max_current_enable=1"
    "dtoverlay=vc4-kms-v3d,cma-512"
)

for SETTING in "${SETTINGS[@]}"; do
    if ! grep -qF "$SETTING" "$CONFIG"; then
        echo "$SETTING" | tee -a "$CONFIG"
        echo "[FIXED] Added $SETTING to config.txt"
    else
        echo "[OK] $SETTING already present."
    fi
done

# USB Buffer Fix (Set to 256MB)
if grep -q "usbcore.usbfs_memory_mb=" "$CMDLINE"; then
    sed -i 's/usbcore.usbfs_memory_mb=[0-9]*/usbcore.usbfs_memory_mb=256/' "$CMDLINE"
    echo "[OK] USB buffer set to 256MB."
else
    sed -i '$ s/$/ usbcore.usbfs_memory_mb=256/' "$CMDLINE"
    echo "[FIXED] Added 256MB USB buffer to cmdline.txt"
fi

# ==========================================
# 3. Deploy Application Files
# ==========================================
echo -e "\n>>> [3/6] Deploying application files..."

# Ensure we're running from the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Copy all application files to /home/instacore/
cp "$SCRIPT_DIR/web_trigger.py" /home/instacore/web_trigger.py
cp "$SCRIPT_DIR/start_cameras.sh" /home/instacore/start_cameras.sh
cp "$SCRIPT_DIR/template_home.html" /home/instacore/template_home.html
cp "$SCRIPT_DIR/template_gallery.html" /home/instacore/template_gallery.html
cp "$SCRIPT_DIR/template_session.html" /home/instacore/template_session.html

# Set proper permissions
chmod +x /home/instacore/start_cameras.sh
chmod +x /home/instacore/web_trigger.py
chown -R instacore:instacore /home/instacore/

echo "[OK] Application files deployed."

# ==========================================
# 4. Create Systemd Service
# ==========================================
echo -e "\n>>> [4/6] Configuring auto-boot service..."

cat << 'EOF' > /etc/systemd/system/instacore-web.service
[Unit]
Description=Instacore Camera Web Trigger
After=network.target

[Service]
User=root
WorkingDirectory=/home/instacore
ExecStart=/usr/bin/python3 /home/instacore/web_trigger.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable instacore-web.service

echo "[OK] Systemd service created and enabled."

# ==========================================
# 5. Configure WiFi Hotspot
# ==========================================
echo -e "\n>>> [5/6] Configuring WiFi hotspot..."

# Delete existing hotspot profile if it exists
nmcli con delete InstacoreHotspot 2>/dev/null || true

# Create the new hotspot configuration
nmcli con add type wifi ifname wlan0 con-name InstacoreHotspot autoconnect yes ssid instacore mode ap
nmcli con modify InstacoreHotspot 802-11-wireless-security.key-mgmt wpa-psk 802-11-wireless-security.psk "ologic123"
nmcli con modify InstacoreHotspot ipv4.method shared ipv4.addresses 10.1.1.1/24
nmcli con modify InstacoreHotspot connection.autoconnect-priority 99

echo "[OK] WiFi hotspot configured."

# ==========================================
# 6. Start Services
# ==========================================
echo -e "\n>>> [6/6] Starting services..."

systemctl restart instacore-web.service
nmcli con up InstacoreHotspot 2>/dev/null || echo "[NOTE] Hotspot will activate after reboot."

# ==========================================
# Setup Complete
# ==========================================
echo ""
echo "========================================"
echo " ✓ SETUP COMPLETE!"
echo "========================================"
echo ""
echo "NEXT STEPS:"
echo "1. Reboot the system to apply hardware changes:"
echo "   sudo reboot"
echo ""
echo "2. After reboot, connect to WiFi:"
echo "   Network: instacore"
echo "   Password: ologic123"
echo ""
echo "3. Access the web interface:"
echo "   http://10.1.1.1"
echo ""
echo "========================================"

