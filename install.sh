#!/bin/bash
# Build and install script for P028TRM160 2.8" DSI panel driver
# Run this on the Raspberry Pi
#
# Usage:
#   ./install.sh         # Install for DSI0 (default)
#   ./install.sh dsi1    # Install for DSI1

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Determine DSI port
DSI_PORT="${1:-dsi0}"
case "$DSI_PORT" in
    dsi0)
        OVERLAY_DTS="p028trm160-overlay.dts"
        OVERLAY_NAME="p028trm160"
        OTHER_OVERLAY="p028trm160-dsi1"
        ;;
    dsi1)
        OVERLAY_DTS="p028trm160-dsi1-overlay.dts"
        OVERLAY_NAME="p028trm160-dsi1"
        OTHER_OVERLAY="p028trm160"
        ;;
    *)
        echo "Usage: $0 [dsi0|dsi1]"
        exit 1
        ;;
esac

echo "=== Installing for ${DSI_PORT^^} ==="

echo ""
echo "=== Building panel kernel module ==="
make clean 2>/dev/null || true
make

echo ""
echo "=== Compiling device tree overlay ==="
dtc -@ -H epapr -I dts -O dtb -o "${OVERLAY_NAME}.dtbo" "$OVERLAY_DTS"

echo ""
echo "=== Installing kernel module ==="
sudo cp panel-p028trm160.ko /lib/modules/$(uname -r)/kernel/drivers/gpu/drm/panel/
sudo depmod -a

# Update initramfs so the module is available at early boot
if [ -x /usr/sbin/update-initramfs ]; then
    echo ""
    echo "=== Updating initramfs ==="
    sudo update-initramfs -u -k "$(uname -r)"
fi

echo ""
echo "=== Installing device tree overlay ==="
sudo cp "${OVERLAY_NAME}.dtbo" /boot/firmware/overlays/

echo ""
echo "=== Configuring boot config ==="
# Remove any existing p028trm160 overlay lines (both dsi0 and dsi1 variants)
sudo sed -i "/dtoverlay=p028trm160/d" /boot/firmware/config.txt
sudo sed -i "/# P028TRM160 2.8\" DSI Panel/d" /boot/firmware/config.txt

# Add the correct overlay
sudo bash -c "cat >> /boot/firmware/config.txt << EOF

# P028TRM160 2.8\" DSI Panel on ${DSI_PORT^^}
dtoverlay=${OVERLAY_NAME}
EOF"
echo "Configured dtoverlay=${OVERLAY_NAME} in /boot/firmware/config.txt"

echo ""
echo "=== Installation complete ==="
echo "Please reboot to activate the display: sudo reboot"
