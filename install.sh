#!/bin/bash
# Build and install script for P028TRM160 2.8" DSI panel driver
# Run this on the Raspberry Pi

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Building panel kernel module ==="
make clean 2>/dev/null || true
make

echo ""
echo "=== Compiling device tree overlay ==="
dtc -@ -H epapr -I dts -O dtb -o p028trm160.dtbo p028trm160-overlay.dts

echo ""
echo "=== Installing kernel module ==="
sudo cp panel-p028trm160.ko /lib/modules/$(uname -r)/kernel/drivers/gpu/drm/panel/
sudo depmod -a

echo ""
echo "=== Installing device tree overlay ==="
sudo cp p028trm160.dtbo /boot/firmware/overlays/

echo ""
echo "=== Configuring boot config ==="
# Check if overlay is already configured
if ! grep -q "dtoverlay=p028trm160" /boot/firmware/config.txt; then
    # Add the overlay to config.txt
    sudo bash -c 'cat >> /boot/firmware/config.txt << EOF

# P028TRM160 2.8" DSI Panel on DSI0
dtoverlay=p028trm160
EOF'
    echo "Added dtoverlay=p028trm160 to /boot/firmware/config.txt"
else
    echo "Overlay already configured in /boot/firmware/config.txt"
fi

echo ""
echo "=== Installation complete ==="
echo "Please reboot to activate the display: sudo reboot"
