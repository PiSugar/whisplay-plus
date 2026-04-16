#!/bin/bash
# PiSugar 2.8" ST7701P Display + FT6336U Touch - Install Script
# Run on Raspberry Pi 5 with: sudo bash install.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OVERLAY_NAME="pisugar-st7701p"
MODULE_NAME="st7701p_pisugar"
CONFIG="/boot/firmware/config.txt"

echo "=== PiSugar ST7701P Display Driver Installer ==="

# 1. Install build dependencies
echo "[1/6] Installing build dependencies..."
apt-get update -qq
apt-get install -y raspberrypi-kernel-headers device-tree-compiler

# 2. Build kernel module
echo "[2/6] Building panel driver module..."
cd "$SCRIPT_DIR/driver"
make clean
make
cp ${MODULE_NAME}.ko /lib/modules/$(uname -r)/extra/ 2>/dev/null || {
    mkdir -p /lib/modules/$(uname -r)/extra/
    cp ${MODULE_NAME}.ko /lib/modules/$(uname -r)/extra/
}
depmod -a

# 3. Set up auto-load
echo "[3/6] Configuring module auto-load..."
echo "$MODULE_NAME" > /etc/modules-load.d/${MODULE_NAME}.conf

# 4. Compile and install device tree overlay
echo "[4/6] Building and installing device tree overlay..."
cd "$SCRIPT_DIR/overlay"
cpp -nostdinc -I /usr/src/linux-headers-$(uname -r | sed 's/-v8.*/-common-rpi/;s/-2712.*/-common-rpi/')/include \
    -undef -x assembler-with-cpp ${OVERLAY_NAME}-overlay.dts | \
    dtc -@ -I dts -O dtb -o ${OVERLAY_NAME}.dtbo - 2>/dev/null || \
cpp -nostdinc -I /usr/src/linux-headers-*-common-rpi/include \
    -undef -x assembler-with-cpp ${OVERLAY_NAME}-overlay.dts | \
    dtc -@ -I dts -O dtb -o ${OVERLAY_NAME}.dtbo -
cp ${OVERLAY_NAME}.dtbo /boot/firmware/overlays/

# 5. Configure config.txt
echo "[5/6] Configuring boot parameters..."
if ! grep -q "^dtoverlay=${OVERLAY_NAME}" "$CONFIG"; then
    # Add overlay after [all] section if it exists, otherwise append
    if grep -q "^\[all\]" "$CONFIG"; then
        sed -i "/^\[all\]/a dtoverlay=${OVERLAY_NAME}" "$CONFIG"
    else
        echo "" >> "$CONFIG"
        echo "dtoverlay=${OVERLAY_NAME}" >> "$CONFIG"
    fi
    echo "  Added dtoverlay=${OVERLAY_NAME} to config.txt"
else
    echo "  dtoverlay=${OVERLAY_NAME} already in config.txt"
fi

# Ensure vc4-kms-v3d is enabled
if ! grep -q "^dtoverlay=vc4-kms-v3d" "$CONFIG"; then
    echo "  WARNING: dtoverlay=vc4-kms-v3d not found in config.txt"
    echo "  You may need to add it manually for display to work"
fi

# 6. Update initramfs
echo "[6/6] Updating initramfs..."
update-initramfs -u

echo ""
echo "=== Installation complete ==="
echo "Please reboot to activate: sudo reboot"
echo ""
echo "After reboot, verify with:"
echo "  dmesg | grep -i st7701     # panel driver"
echo "  dmesg | grep -i edt        # touch driver"
echo "  cat /proc/bus/input/devices # touch input device"
