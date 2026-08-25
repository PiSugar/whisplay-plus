#!/bin/bash
# Whisplay Hat+ Driver Installer
# Run on Raspberry Pi 5 with: sudo bash install.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HAT_OVERLAY="whisplay-plus"
DISPLAY_MODULE="st7701p_pisugar"
SOUND_MODULE="snd-soc-whisplay-soundcard"
CONFIG="/boot/firmware/config.txt"
MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"

configure_labwc_multitouch() {
    local system_config="/etc/xdg/labwc/rc.xml"
    local desktop_user="${SUDO_USER:-}"
    local desktop_home=""
    local desktop_group=""
    local user_config=""

    # Raspberry Pi OS enables mouse emulation for its built-in touchscreen
    # profiles. That collapses all GT911 contacts into pointerId=1 in Chromium.
    # Keep a one-time backup and enable native Wayland multi-touch instead.
    if [ -f "$system_config" ]; then
        cp -n "$system_config" "${system_config}.whisplay-backup"
        sed -i '/Goodix Capacitive TouchScreen/ s/mouseEmulation="yes"/mouseEmulation="no"/g' \
            "$system_config"
    fi

    if [ -z "$desktop_user" ] || [ "$desktop_user" = "root" ]; then
        echo "  WARN: SUDO_USER is unavailable; configured system Labwc defaults only."
        return
    fi

    desktop_home="$(getent passwd "$desktop_user" | cut -d: -f6)"
    desktop_group="$(id -gn "$desktop_user")"
    if [ -z "$desktop_home" ] || [ ! -d "$desktop_home" ]; then
        echo "  WARN: cannot find home directory for desktop user $desktop_user."
        return
    fi

    user_config="$desktop_home/.config/labwc/rc.xml"
    install -d -m 0755 -o "$desktop_user" -g "$desktop_group" \
        "$desktop_home/.config/labwc"

    if [ ! -f "$user_config" ]; then
        cat > "$user_config" <<'EOF'
<?xml version="1.0"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
	<touch deviceName="Goodix Capacitive TouchScreen" mapToOutput="DSI-1" mouseEmulation="no"/>
</openbox_config>
EOF
    else
        cp -n "$user_config" "${user_config}.whisplay-backup"
        if grep -q 'deviceName="Goodix Capacitive TouchScreen"' "$user_config"; then
            sed -i '/deviceName="Goodix Capacitive TouchScreen"/ s/mouseEmulation="yes"/mouseEmulation="no"/g' \
                "$user_config"
        elif grep -q '</openbox_config>' "$user_config"; then
            sed -i '/<\/openbox_config>/i\	<touch deviceName="Goodix Capacitive TouchScreen" mapToOutput="DSI-1" mouseEmulation="no"\/>' \
                "$user_config"
        else
            echo "  WARN: $user_config has no </openbox_config>; cannot add GT911 multi-touch entry."
        fi
    fi

    chown "$desktop_user:$desktop_group" "$user_config"
    echo "  Enabled native GT911 multi-touch for Labwc user $desktop_user."
}

echo "=== Whisplay Hat+ Driver Installer ==="

# Whisplay Hat+ defaults to DSI0. If HAT EEPROM auto-loads this overlay and
# the display is connected to DSI1, add this before other overlays in config.txt:
#   dtparam=display_dsi=1
if echo "$MODEL" | grep -Eq "Raspberry Pi 5|Compute Module 5"; then
    echo "Detected platform: $MODEL"
fi

echo "Display/touch overlay default: DSI0. Use dtparam=display_dsi=1 for DSI1."

# 1. Install build dependencies
echo "[1/6] Installing build dependencies..."
apt-get update -qq
if ! apt-get install -y \
    build-essential \
    raspberrypi-kernel-headers \
    device-tree-compiler \
    alsa-utils \
    libasound2-plugins \
    pipewire-pulse \
    pulseaudio-utils \
    python3-pygame \
    python3-smbus \
    sox; then
    echo "  raspberrypi-kernel-headers not available, trying linux-headers fallback..."
    apt-get install -y \
        build-essential \
        device-tree-compiler \
        alsa-utils \
        libasound2-plugins \
        pipewire-pulse \
        pulseaudio-utils \
        python3-pygame \
        python3-smbus \
        sox \
        "linux-headers-$(uname -r)" || \
    apt-get install -y \
        build-essential \
        device-tree-compiler \
        alsa-utils \
        libasound2-plugins \
        pipewire-pulse \
        pulseaudio-utils \
        python3-pygame \
        python3-smbus \
        sox \
        linux-headers-rpi-2712 \
        linux-headers-rpi-v8
fi
apt-get install -y python3-libgpiod 2>/dev/null || \
apt-get install -y python3-gpiod 2>/dev/null || \
echo "  WARN: python gpiod binding not installed; LED/button demo may need python3-libgpiod."
apt-get install -y python3-flask python3-qrcode openssl || \
echo "  WARN: phone-assisted GYRO calibration needs Flask, qrcode and OpenSSL."

# 2. Build kernel module
echo "[2/6] Building display and sound kernel modules..."
cd "$SCRIPT_DIR/display/driver"
make clean
make
cd "$SCRIPT_DIR/sound"
make clean
make
mkdir -p /lib/modules/$(uname -r)/extra/
cp "$SCRIPT_DIR/display/driver/${DISPLAY_MODULE}.ko" /lib/modules/$(uname -r)/extra/
cp "$SCRIPT_DIR/sound/${SOUND_MODULE}.ko" /lib/modules/$(uname -r)/extra/
depmod -a

# 3. Set up auto-load
echo "[3/6] Configuring module auto-load..."
cat > /etc/modules-load.d/whisplay-hat-plus.conf <<EOF
${DISPLAY_MODULE}
${SOUND_MODULE}
EOF

# 4. Compile and install device tree overlay
echo "[4/6] Building and installing aggregate device tree overlay..."
cd "$SCRIPT_DIR/overlay"
cpp -nostdinc -I /usr/src/linux-headers-$(uname -r | sed 's/-v8.*/-common-rpi/;s/-2712.*/-common-rpi/')/include \
    -undef -x assembler-with-cpp ${HAT_OVERLAY}.dts | \
    dtc -@ -I dts -O dtb -o ${HAT_OVERLAY}.dtbo - 2>/dev/null || \
cpp -nostdinc -I /usr/src/linux-headers-*-common-rpi/include \
    -undef -x assembler-with-cpp ${HAT_OVERLAY}.dts | \
    dtc -@ -I dts -O dtb -o ${HAT_OVERLAY}.dtbo -
cp ${HAT_OVERLAY}.dtbo /boot/firmware/overlays/

# 5. Configure config.txt
echo "[5/6] Configuring boot parameters..."
for param in i2c_arm=on i2s=on spi=on; do
    if ! grep -q "^dtparam=${param}" "$CONFIG"; then
        echo "dtparam=${param}" >> "$CONFIG"
    fi
done

if ! grep -q "^dtoverlay=${HAT_OVERLAY}" "$CONFIG"; then
    if grep -q "^\[all\]" "$CONFIG"; then
        sed -i "/^\[all\]/a dtoverlay=${HAT_OVERLAY}" "$CONFIG"
    else
        echo "dtoverlay=${HAT_OVERLAY}" >> "$CONFIG"
    fi
    echo "  Added dtoverlay=${HAT_OVERLAY} to config.txt"
else
    echo "  dtoverlay=${HAT_OVERLAY} already in config.txt"
fi

sed -i '/^dtoverlay=pisugar-st7701p/d' "$CONFIG" 2>/dev/null || true
sed -i '/^dtoverlay=whisplay-soundcard/d' "$CONFIG" 2>/dev/null || true
sed -i '/^dtoverlay=wm8960-soundcard/d' "$CONFIG" 2>/dev/null || true
sed -i '/^dtoverlay=es8389-soundcard/d' "$CONFIG" 2>/dev/null || true

# Ensure vc4-kms-v3d is enabled
if ! grep -q "^dtoverlay=vc4-kms-v3d" "$CONFIG"; then
    echo "  WARNING: dtoverlay=vc4-kms-v3d not found in config.txt"
    echo "  You may need to add it manually for display to work"
fi

# Disable Labwc mouse emulation so GTK/Chromium applications receive every
# GT911 contact as an independent Wayland touch point.
configure_labwc_multitouch

# 6. Install demo and update initramfs
echo "[6/6] Installing desktop demo and updating initramfs..."
rm -rf /opt/whisplay-hat-plus
mkdir -p /opt/whisplay-hat-plus
cp -a "$SCRIPT_DIR/demo" /opt/whisplay-hat-plus/
cp -a "$SCRIPT_DIR/sensors" /opt/whisplay-hat-plus/
chmod +x /opt/whisplay-hat-plus/demo/run-whisplay-hat-plus-demo.sh
chmod +x \
    /opt/whisplay-hat-plus/demo/test_apps/touch/run.sh \
    /opt/whisplay-hat-plus/demo/test_apps/sound/run_sound_test.sh \
    /opt/whisplay-hat-plus/demo/test_apps/gyro/run_gyro_test.sh
install -m 644 "$SCRIPT_DIR/demo/whisplay-hat-plus-demo.desktop" /usr/share/applications/
for desktop in "$SCRIPT_DIR"/demo/test_apps/*/*.desktop; do
    install -m 644 "$desktop" /usr/share/applications/
done

cat > /etc/asound.conf <<'EOF'
pcm.!default {
    type plug
    slave.pcm "whisplaysound"
}

ctl.!default {
    type hw
    card whisplaysound
}
EOF

update-initramfs -u

echo ""
echo "=== Installation complete ==="
echo "Please reboot to activate: sudo reboot"
echo ""
echo "After reboot, verify with:"
echo "  dmesg | grep -i st7701     # panel driver"
echo "  dmesg | grep -i goodix     # touch driver"
echo "  aplay -l | grep -i whisplay # sound card"
echo "  python3 /opt/whisplay-hat-plus/sensors/whisplay_sensors.py # import check"
echo "  cat /proc/bus/input/devices # touch input device"
echo "  /opt/whisplay-hat-plus/demo/run-whisplay-hat-plus-demo.sh"
echo "  /opt/whisplay-hat-plus/demo/test_apps/touch/run.sh"
echo "  /opt/whisplay-hat-plus/demo/test_apps/sound/run_sound_test.sh"
echo "  /opt/whisplay-hat-plus/demo/test_apps/gyro/run_gyro_test.sh"
