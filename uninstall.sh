#!/bin/bash
# Whisplay Hat+ Driver Uninstall Script
set -e

HAT_OVERLAY="whisplay-plus"
DISPLAY_MODULE="st7701p_pisugar"
SOUND_MODULE="snd-soc-whisplay-soundcard"
if [ -f /boot/firmware/config.txt ]; then
    CONFIG="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    CONFIG="/boot/config.txt"
else
    echo "ERROR: cannot find Raspberry Pi boot config.txt." >&2
    exit 1
fi
BOOT_DIR="$(dirname "$CONFIG")"

echo "=== Whisplay Hat+ Driver Uninstaller ==="

# Remove module
rmmod ${SOUND_MODULE} 2>/dev/null || true
rmmod ${DISPLAY_MODULE} 2>/dev/null || true
rm -f /lib/modules/$(uname -r)/extra/${DISPLAY_MODULE}.ko
rm -f /lib/modules/$(uname -r)/extra/${SOUND_MODULE}.ko
rm -f /etc/modules-load.d/whisplay-hat-plus.conf
depmod -a

# Remove overlay
rm -f "${BOOT_DIR}/overlays/${HAT_OVERLAY}.dtbo"

# Remove config.txt entry
sed -i "/^dtoverlay=${HAT_OVERLAY}/d" "$CONFIG"
sed -i "/^dtoverlay=pisugar-st7701p/d" "$CONFIG"
sed -i "/^dtoverlay=whisplay-soundcard/d" "$CONFIG"

rm -rf /opt/whisplay-hat-plus
rm -f /usr/share/applications/whisplay-hat-plus-demo.desktop
rm -f \
    /usr/share/applications/whisplay-touch-test.desktop \
    /usr/share/applications/whisplay-sound-test.desktop \
    /usr/share/applications/whisplay-gyro-test.desktop

# Keep the Labwc native multi-touch setting. Restoring a whole rc.xml backup
# here could overwrite desktop changes made after the driver was installed.

update-initramfs -u

echo "=== Uninstall complete. Reboot to take effect. ==="
