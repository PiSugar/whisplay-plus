#!/bin/bash
set -euo pipefail

repo_dir="/home/pi/whisplay-plus"
repo_url="${WHISPLAY_PLUS_REPO:-https://github.com/PiSugar/whisplay-plus.git}"
repo_ref="${WHISPLAY_PLUS_REF:-main}"
boot_dir="/boot/firmware"

if [ ! -f "${boot_dir}/config.txt" ]; then
  boot_dir="/boot"
fi
if [ ! -f "${boot_dir}/config.txt" ]; then
  echo "Raspberry Pi boot config was not found" >&2
  exit 1
fi

if [ ! -d "$repo_dir/.git" ]; then
  mkdir -p /home/pi
  git clone --depth 1 --branch "$repo_ref" "$repo_url" "$repo_dir"
else
  git -C "$repo_dir" fetch --depth 1 origin "$repo_ref"
  git -C "$repo_dir" checkout --force FETCH_HEAD
fi
chown -R pi:pi "$repo_dir"

target_kernels=()
while IFS= read -r kernel_release; do
  target_kernels+=("$kernel_release")
done < <(find /lib/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -V)

if [ "${#target_kernels[@]}" -eq 0 ]; then
  echo "No target kernels found under /lib/modules" >&2
  exit 1
fi

for kernel_release in "${target_kernels[@]}"; do
  kernel_dir="/lib/modules/${kernel_release}"
  build_dir="${kernel_dir}/build"
  if [ ! -d "$build_dir" ]; then
    echo "Kernel headers are missing for ${kernel_release}: ${build_dir}" >&2
    exit 1
  fi

  echo "Building Whisplay Hat+ modules for ${kernel_release}"
  make -C "$repo_dir/display/driver" KDIR="$build_dir" clean
  make -C "$repo_dir/display/driver" KDIR="$build_dir"
  make -C "$repo_dir/sound" KDIR="$build_dir" clean
  make -C "$repo_dir/sound" KDIR="$build_dir"
  install -D -m 0644 \
    "$repo_dir/display/driver/st7701p_pisugar.ko" \
    "${kernel_dir}/extra/st7701p_pisugar.ko"
  install -D -m 0644 \
    "$repo_dir/sound/snd-soc-whisplay-soundcard.ko" \
    "${kernel_dir}/extra/snd-soc-whisplay-soundcard.ko"
  depmod -a "$kernel_release"
  if command -v update-initramfs >/dev/null 2>&1; then
    update-initramfs -u -k "$kernel_release"
  fi
done

cat > /etc/modules-load.d/whisplay-hat-plus.conf <<'EOF'
st7701p_pisugar
snd-soc-whisplay-soundcard
EOF

(
  cd "$repo_dir/overlay"
  cpp -nostdinc -undef -x assembler-with-cpp whisplay-plus.dts | \
    dtc -@ -I dts -O dtb -o whisplay-plus.dtbo -
)
install -D -m 0644 \
  "$repo_dir/overlay/whisplay-plus.dtbo" \
  "${boot_dir}/overlays/whisplay-plus.dtbo"

config_txt="${boot_dir}/config.txt"
sed -i \
  -e '/^[[:space:]]*dtoverlay=whisplay-plus\([,[:space:]].*\)\?$/d' \
  -e '/^[[:space:]]*dtoverlay=pisugar-st7701p\([,[:space:]].*\)\?$/d' \
  -e '/^[[:space:]]*dtoverlay=whisplay-soundcard\([,[:space:]].*\)\?$/d' \
  -e '/^[[:space:]]*dtoverlay=wm8960-soundcard\([,[:space:]].*\)\?$/d' \
  -e '/^[[:space:]]*dtoverlay=es8389-soundcard\([,[:space:]].*\)\?$/d' \
  "$config_txt"

for param in i2c_arm=on i2s=on spi=on; do
  if ! grep -q "^dtparam=${param}$" "$config_txt"; then
    echo "dtparam=${param}" >> "$config_txt"
  fi
done

cat >> "$config_txt" <<'EOF'

# Whisplay Hat+ (managed by the whisplay-plus release image)
[pi3]
dtparam=i2c_csi_dsi=on
camera_auto_detect=0
dtoverlay=whisplay-plus,display_dsi=1
[pi4]
dtparam=i2c_csi_dsi=on
camera_auto_detect=0
dtoverlay=whisplay-plus,display_dsi=1
[pi5]
dtoverlay=whisplay-plus
[all]
EOF

rm -rf /opt/whisplay-hat-plus
install -d -m 0755 /opt/whisplay-hat-plus
cp -a "$repo_dir/demo" /opt/whisplay-hat-plus/
cp -a "$repo_dir/sensors" /opt/whisplay-hat-plus/
chmod +x \
  /opt/whisplay-hat-plus/demo/run-whisplay-hat-plus-demo.sh \
  /opt/whisplay-hat-plus/demo/test_apps/touch/run.sh \
  /opt/whisplay-hat-plus/demo/test_apps/sound/run_sound_test.sh \
  /opt/whisplay-hat-plus/demo/test_apps/gyro/run_gyro_test.sh
install -m 0644 \
  "$repo_dir/demo/whisplay-hat-plus-demo.desktop" \
  /usr/share/applications/whisplay-hat-plus-demo.desktop
for desktop in "$repo_dir"/demo/test_apps/*/*.desktop; do
  install -m 0644 "$desktop" /usr/share/applications/
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

system_labwc=/etc/xdg/labwc/rc.xml
if [ -f "$system_labwc" ]; then
  cp -n "$system_labwc" "${system_labwc}.whisplay-backup"
  sed -i '/Goodix Capacitive TouchScreen/ s/mouseEmulation="yes"/mouseEmulation="no"/g' "$system_labwc"
fi

install -d -m 0755 -o pi -g pi /home/pi/.config/labwc
cat > /home/pi/.config/labwc/rc.xml <<'EOF'
<?xml version="1.0"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
	<touch deviceName="Goodix Capacitive TouchScreen" mapToOutput="DSI-1" mouseEmulation="no"/>
</openbox_config>
EOF
chown pi:pi /home/pi/.config/labwc/rc.xml
chown -R pi:pi "$repo_dir"
