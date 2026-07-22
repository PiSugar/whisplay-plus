#!/bin/bash
# PiSugar 2.8" ST7701P Display + FT6336U Touch - Uninstall Script
set -e

OVERLAY_NAME="pisugar-st7701p"
MODULE_NAME="st7701p_pisugar"
CONFIG="/boot/firmware/config.txt"

echo "=== PiSugar ST7701P Display Driver Uninstaller ==="

# Remove module
rmmod ${MODULE_NAME} 2>/dev/null || true
rm -f /lib/modules/$(uname -r)/extra/${MODULE_NAME}.ko
rm -f /etc/modules-load.d/${MODULE_NAME}.conf
depmod -a

# Remove overlay
rm -f /boot/firmware/overlays/${OVERLAY_NAME}.dtbo

# Remove config.txt entry
sed -i "/^dtoverlay=${OVERLAY_NAME}/d" "$CONFIG"

update-initramfs -u

echo "=== Uninstall complete. Reboot to take effect. ==="
