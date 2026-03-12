#!/bin/bash
set -euo pipefail

# Configure Raspberry Pi for phone-direct Ethernet control (no extra hardware).
# - Keeps web UI on 10.1.1.1 via eth0
# - Turns Wi-Fi off for lower power
# - Uses NetworkManager shared IPv4 mode so Android gets DHCP automatically

CONNECTION_NAME="InstacorePhoneEth"
ETH_IFACE="eth0"
ETH_CIDR="10.1.1.1/24"
WIFI_CONNECTION_NAME="InstacoreHotspot"

if [ "${EUID}" -ne 0 ]; then
    echo "Run as root: sudo bash setup_phone_ethernet.sh"
    exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
    echo "ERROR: nmcli not found. Install NetworkManager first."
    exit 1
fi

echo "== Instacore phone-direct Ethernet setup =="
echo "Ethernet interface: ${ETH_IFACE}"
echo "Pi web UI address:   ${ETH_CIDR%/*}"

echo "[1/5] Ensuring Wi-Fi hotspot autoconnect is disabled (if present)..."
if nmcli -t -f NAME connection show | grep -Fxq "${WIFI_CONNECTION_NAME}"; then
    nmcli connection modify "${WIFI_CONNECTION_NAME}" connection.autoconnect no || true
fi

echo "[2/5] Creating/updating dedicated Ethernet connection..."
if nmcli -t -f NAME connection show | grep -Fxq "${CONNECTION_NAME}"; then
    nmcli connection modify "${CONNECTION_NAME}" \
        connection.type ethernet \
        connection.interface-name "${ETH_IFACE}" \
        connection.autoconnect yes \
        connection.autoconnect-priority 100 \
        ipv4.method shared \
        ipv4.addresses "${ETH_CIDR}" \
        ipv6.method disabled
else
    nmcli connection add type ethernet ifname "${ETH_IFACE}" con-name "${CONNECTION_NAME}" \
        ipv4.method shared ipv4.addresses "${ETH_CIDR}" ipv6.method disabled \
        connection.autoconnect yes connection.autoconnect-priority 100
fi

# Prevent other eth0 ethernet profiles from racing autoconnect.
while IFS= read -r line; do
    name="${line%%:*}"
    ctype="${line#*:}"
    [ "${ctype}" = "ethernet" ] || continue
    [ "${name}" = "${CONNECTION_NAME}" ] && continue

    iface_name="$(nmcli -g connection.interface-name connection show "${name}" 2>/dev/null || true)"
    if [ "${iface_name}" = "${ETH_IFACE}" ]; then
        nmcli connection modify "${name}" connection.autoconnect no || true
    fi
done < <(nmcli -t -f NAME,TYPE connection show)

echo "[3/5] Turning Wi-Fi radio off now..."
nmcli radio wifi off || true

echo "[4/5] Disabling Wi-Fi at boot (power saving)..."
CONFIG_PATH=""
if [ -f /boot/firmware/config.txt ]; then
    CONFIG_PATH="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    CONFIG_PATH="/boot/config.txt"
fi

if [ -n "${CONFIG_PATH}" ]; then
    cp "${CONFIG_PATH}" "${CONFIG_PATH}.bak.$(date +%Y%m%d_%H%M%S)"
    sed -i '/^dtoverlay=disable-wifi$/d' "${CONFIG_PATH}"
    echo "dtoverlay=disable-wifi" >> "${CONFIG_PATH}"
    echo "Updated ${CONFIG_PATH}"
else
    echo "WARNING: Could not find /boot/firmware/config.txt or /boot/config.txt"
    echo "Wi-Fi boot-disable overlay was not written."
fi

echo "[5/5] Bringing Ethernet profile up..."
if nmcli connection up "${CONNECTION_NAME}" >/dev/null 2>&1; then
    echo "Ethernet profile activated."
else
    echo "No cable/link yet. Profile is saved and will auto-activate when phone is plugged in."
fi

echo ""
echo "Done."
echo "Expected web UI URL when phone is connected: http://${ETH_CIDR%/*}"
echo ""
echo "Recommended: reboot once to fully apply Wi-Fi power-disable overlay:"
echo "  sudo reboot"
echo ""
echo "Verify after reboot:"
echo "  nmcli -p device status"
echo "  ip -4 addr show ${ETH_IFACE}"
echo ""
echo "Rollback hints:"
echo "  nmcli connection delete ${CONNECTION_NAME}"
echo "  sed -i '/^dtoverlay=disable-wifi$/d' ${CONFIG_PATH:-/boot/firmware/config.txt}"
