#!/bin/bash

# ==========================================
# Instacore Complete Setup Script
# Raspberry Pi 5 Camera Recording System
# ==========================================

echo "========================================"
echo " Starting Instacore System Setup..."
echo "========================================"

# Resolve repo path and the invoking user home so runtime stays repo-local.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-$(whoami)}"
APP_HOME="$(eval echo "~${APP_USER}")"

# If this script runs inside a cloned git repo, set local git behavior once.
if [ -d "$SCRIPT_DIR/.git" ]; then
    git -C "$SCRIPT_DIR" config core.fileMode false
    git config --global --add safe.directory "$SCRIPT_DIR"
fi

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
# 3. Prepare Local Runtime Files
# ==========================================
echo -e "\n>>> [3/6] Preparing runtime files in repo directory..."

echo "[OK] Runtime files ready at: $SCRIPT_DIR"

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
Environment=HOME=__APP_HOME__
WorkingDirectory=__SCRIPT_DIR__
ExecStart=/usr/bin/python3 __SCRIPT_DIR__/web_trigger.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sed -i "s|__SCRIPT_DIR__|$SCRIPT_DIR|g" /etc/systemd/system/instacore-web.service
sed -i "s|__APP_HOME__|$APP_HOME|g" /etc/systemd/system/instacore-web.service

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

