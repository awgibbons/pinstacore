#!/bin/bash
set -euo pipefail

# Enable I2C bus 1 at 400kHz for ICM-20948 IMU
# GPIO2 = SDA, GPIO3 = SCL (Raspberry Pi standard I2C1 pins)

if [ "${EUID}" -ne 0 ]; then
    echo "Run as root: sudo bash setup_i2c.sh"
    exit 1
fi

echo "== Instacore I2C setup for ICM-20948 =="

# Find boot config
CONFIG=""
if [ -f /boot/firmware/config.txt ]; then
    CONFIG="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    CONFIG="/boot/config.txt"
else
    echo "ERROR: Cannot find boot config.txt"
    exit 1
fi

cp "$CONFIG" "${CONFIG}.bak.$(date +%Y%m%d_%H%M%S)"
echo "Backed up $CONFIG"

# Enable I2C
if grep -q "^dtparam=i2c_arm=on" "$CONFIG"; then
    echo "[OK] dtparam=i2c_arm=on already present"
else
    sed -i '/^dtparam=i2c_arm=/d' "$CONFIG"
    echo "dtparam=i2c_arm=on" >> "$CONFIG"
    echo "[ADDED] dtparam=i2c_arm=on"
fi

# Set 400kHz fast mode
if grep -q "^dtparam=i2c_arm_baudrate=400000" "$CONFIG"; then
    echo "[OK] i2c_arm_baudrate=400000 already present"
else
    sed -i '/^dtparam=i2c_arm_baudrate=/d' "$CONFIG"
    echo "dtparam=i2c_arm_baudrate=400000" >> "$CONFIG"
    echo "[ADDED] dtparam=i2c_arm_baudrate=400000"
fi

# Load i2c-dev at boot
MODULES_FILE="/etc/modules"
if grep -q "^i2c-dev$" "$MODULES_FILE" 2>/dev/null; then
    echo "[OK] i2c-dev already in $MODULES_FILE"
else
    echo "i2c-dev" >> "$MODULES_FILE"
    echo "[ADDED] i2c-dev to $MODULES_FILE"
fi

# Try to load the module now (works without reboot if I2C overlay is already active)
modprobe i2c-dev 2>/dev/null && echo "[OK] i2c-dev module loaded" || echo "[NOTE] Module will load after reboot"

# Install i2c-tools for diagnostics (i2cdetect)
if dpkg -l i2c-tools 2>/dev/null | grep -q "^ii"; then
    echo "[OK] i2c-tools already installed"
else
    echo "[..] Installing i2c-tools..."
    apt-get install -y i2c-tools
fi

# Install smbus2 Python library
if python3 -c "import smbus2" 2>/dev/null; then
    echo "[OK] smbus2 already installed"
elif python3 -c "import smbus" 2>/dev/null; then
    echo "[OK] smbus (legacy) already installed"
else
    echo "[..] Installing smbus2..."
    pip3 install --break-system-packages smbus2 2>/dev/null \
        || pip3 install smbus2 2>/dev/null \
        || apt-get install -y python3-smbus
fi

echo ""
echo "Done. Reboot to activate I2C:"
echo "  sudo reboot"
echo ""
echo "After reboot, verify the IMU is detected:"
echo "  i2cdetect -y 1"
echo "  (expect 0x68 or 0x69 to appear in the grid)"
echo ""
echo "Rollback:"
echo "  sed -i '/^dtparam=i2c_arm/d' $CONFIG"
echo "  sed -i '/^i2c-dev/d' /etc/modules"
