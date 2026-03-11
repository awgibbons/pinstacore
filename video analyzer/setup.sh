#!/bin/bash
# Pi 5 Multi-Cam Deployment & Audit Script

echo "--- 1. Dependency Audit & Install ---"
PACKAGES=(ffmpeg v4l-utils htop usbutils samba)
for pkg in "${PACKAGES[@]}"; do
    if dpkg -l | grep -q " $pkg "; then
        echo "[OK] $pkg is already installed."
    else
        echo "[..] Installing $pkg..."
        sudo apt update && sudo apt install -y "$pkg"
    fi
done

echo -e "\n--- 2. Applying Hardware Tweaks ---"
CONFIG="/boot/firmware/config.txt"
CMDLINE="/boot/firmware/cmdline.txt"

# Force inject the 4 core tweaks
SETTINGS=(
    "usb_max_current_enable=1"
    "dtoverlay=vc4-kms-v3d,cma-512"
   #"dtoverlay=disable-wifi"
   #"dtoverlay=disable-bt"
)

for SETTING in "${SETTINGS[@]}"; do
    if ! grep -qF "$SETTING" "$CONFIG"; then
        echo "$SETTING" | sudo tee -a "$CONFIG"
        echo "[FIXED] Added $SETTING to config.txt"
    else
        echo "[OK] $SETTING already present."
    fi
done

# USB Buffer Fix (Set to 256MB)
if grep -q "usbcore.usbfs_memory_mb=" "$CMDLINE"; then
    sudo sed -i 's/usbcore.usbfs_memory_mb=[0-9]*/usbcore.usbfs_memory_mb=256/' "$CMDLINE"
    echo "[OK] USB buffer set to 256MB."
else
    sudo sed -i '$ s/$/ usbcore.usbfs_memory_mb=256/' "$CMDLINE"
    echo "[FIXED] Added 256MB USB buffer to cmdline.txt"
fi

echo -e "\n--- Setup Complete. PLEASE REBOOT to apply kernel changes. ---"
