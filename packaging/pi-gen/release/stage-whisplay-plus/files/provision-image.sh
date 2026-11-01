#!/bin/bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export APT_LISTCHANGES_FRONTEND=none
export UCF_FORCE_CONFOLD=1

repo_url="${WHISPLAY_PLUS_REPO:-https://github.com/PiSugar/whisplay-plus.git}"
repo_ref="${WHISPLAY_PLUS_REF:-main}"
repo_version="${WHISPLAY_PLUS_RELEASE_VERSION:-$repo_ref}"
wifi_country="${WIFI_COUNTRY:-GB}"

install_networkmanager_polkit_rule() {
  install -d -m 0755 /etc/polkit-1/rules.d
  cat > /etc/polkit-1/rules.d/49-whisplay-networkmanager.rules <<'EOF'
polkit.addRule(function(action, subject) {
  if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 && subject.isInGroup("netdev")) {
    return polkit.Result.YES;
  }
});
EOF
  chmod 0644 /etc/polkit-1/rules.d/49-whisplay-networkmanager.rules
}

apt-get update
apt-get install -y \
  alsa-utils \
  bluez \
  build-essential \
  cloud-guest-utils \
  curl \
  device-tree-compiler \
  ffmpeg \
  git \
  i2c-tools \
  jq \
  libasound2-plugins \
  libsox-fmt-mp3 \
  mpg123 \
  openssl \
  pipewire-pulse \
  pulseaudio-utils \
  python3-dev \
  python3-flask \
  python3-libgpiod \
  python3-numpy \
  python3-pygame \
  python3-qrcode \
  python3-smbus \
  raspi-config \
  rfkill \
  sox \
  sudo \
  unzip \
  xz-utils

if ! apt-get install -y raspberrypi-kernel-headers; then
  apt-get install -y linux-headers-rpi-2712 linux-headers-rpi-v8
fi

if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_spi 0
  raspi-config nonint do_i2c 0
  raspi-config nonint do_wifi_country "$wifi_country" || true
fi

boot_config=/boot/firmware/config.txt
if [ ! -f "$boot_config" ]; then
  boot_config=/boot/config.txt
fi
if [ -f "$boot_config" ]; then
  sed -i '/^[[:space:]]*dtoverlay=disable-wifi[[:space:]]*$/d' "$boot_config"
fi
rfkill unblock wifi || true

mkdir -p /etc/wpa_supplicant
if [ ! -f /etc/wpa_supplicant/wpa_supplicant.conf ]; then
  cat > /etc/wpa_supplicant/wpa_supplicant.conf <<EOF
ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev
update_config=1
country=${wifi_country}
EOF
  chmod 0600 /etc/wpa_supplicant/wpa_supplicant.conf
fi

mkdir -p /etc/systemd/system/multi-user.target.wants
ln -sf \
  /etc/systemd/system/whisplay-expand-rootfs.service \
  /etc/systemd/system/multi-user.target.wants/whisplay-expand-rootfs.service

if ! command -v node >/dev/null 2>&1 || ! node --version | grep -q '^v20\.'; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

/usr/local/lib/whisplay-plus-image/install-whisplay-plus.sh

pisugar_installer="$(mktemp)"
curl -fsSL https://cdn.pisugar.com/release/pisugar-power-manager.sh -o "$pisugar_installer"
sed -i \
  -e '/^local_host=/d' \
  -e '/^local_ip=/d' \
  -e '/Now navigate to .*8421/d' \
  "$pisugar_installer"
bash "$pisugar_installer" -c release
rm -f "$pisugar_installer"

for service_defaults in /etc/default/pisugar-server /etc/default/pisugar-poweroff; do
  if [ -f "$service_defaults" ]; then
    sed -i -E "s/--model '.*'/--model 'PiSugar 3'/g" "$service_defaults"
  fi
done

ensure_pisugar_auth() {
  local config_path="$1"
  local tmp_json
  mkdir -p "$(dirname "$config_path")"
  if [ ! -f "$config_path" ]; then
    echo '{}' > "$config_path"
  fi
  tmp_json="$(mktemp)"
  if jq '. + {digest_auth: ["admin","admin"]}' "$config_path" > "$tmp_json"; then
    mv "$tmp_json" "$config_path"
  else
    rm -f "$tmp_json"
    echo '{"digest_auth":["admin","admin"]}' > "$config_path"
  fi
  chmod 0600 "$config_path"
}

ensure_pisugar_auth /etc/pisugar-server/config.json
if [ -f /etc/pisugar/config.json ]; then
  ensure_pisugar_auth /etc/pisugar/config.json
fi

getent group netdev >/dev/null 2>&1 || groupadd -r netdev
usermod -aG netdev pi
install_networkmanager_polkit_rule
/usr/local/lib/whisplay-plus-image/install-sugar-wifi-conf.sh
install_networkmanager_polkit_rule

mkdir -p /etc/whisplay-image
cat > /etc/whisplay-image/whisplay-plus-release <<EOF
WHISPLAY_PLUS_RELEASE_VERSION=$repo_version
WHISPLAY_PLUS_REF=$repo_ref
WHISPLAY_PLUS_REPO=$repo_url
EOF
