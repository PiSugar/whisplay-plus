#!/usr/bin/env bash
# Launch the KDE Plasma touch test app with the first available desktop browser.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
page_path="${script_dir}/index.html"
page_url="file://${page_path}"
mode="${1:-}"

install_launcher() {
  local launcher_root launcher_dir launcher_path
  launcher_root="${XDG_DATA_HOME:-${HOME}/.local/share}"
  launcher_dir="${launcher_root}/applications"
  launcher_path="${launcher_dir}/pisugar-touch-test.desktop"

  mkdir -p "${launcher_dir}"
  cat > "${launcher_path}" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=PiSugar Touch Test
Name[zh_CN]=PiSugar 触控测试
Comment=Touchscreen and multitouch diagnostics
Comment[zh_CN]=触控屏和多点触控检测
Exec=${script_dir}/run.sh
Terminal=false
Categories=Utility;HardwareSettings;
StartupNotify=true
EOF
  chmod +x "${launcher_path}"

  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${launcher_dir}" >/dev/null 2>&1 || true
  fi

  printf 'Installed KDE launcher: %s\n' "${launcher_path}"
}

case "${mode}" in
  ""|"--fullscreen"|"--windowed"|"--kiosk")
    ;;
  "--install-launcher")
    install_launcher
    exit 0
    ;;
  "--help"|"-h")
    printf 'Usage: %s [--fullscreen|--windowed|--kiosk|--install-launcher]\n' "$0"
    exit 0
    ;;
  *)
    printf 'Unknown option: %s\n' "${mode}" >&2
    printf 'Usage: %s [--fullscreen|--windowed|--kiosk|--install-launcher]\n' "$0" >&2
    exit 2
    ;;
esac

if [ ! -f "${page_path}" ]; then
  printf 'Missing app file: %s\n' "${page_path}" >&2
  exit 1
fi

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
unset DISPLAY

if [ -f "${script_dir}/touch_test_launcher.py" ]; then
  exec python3 "${script_dir}/touch_test_launcher.py" "${mode}"
fi

browser=""
for candidate in chromium-browser chromium google-chrome-stable google-chrome firefox-esr firefox angelfish; do
  if command -v "${candidate}" >/dev/null 2>&1; then
    browser="${candidate}"
    break
  fi
done

if [ -z "${browser}" ]; then
  if command -v xdg-open >/dev/null 2>&1; then
    exec xdg-open "${page_url}"
  fi
  printf 'No supported browser found. Open this file manually:\n%s\n' "${page_path}" >&2
  exit 1
fi

case "${browser}" in
  chromium*|google-chrome*)
    chromium_flags=(
      "--disable-pinch"
      "--overscroll-history-navigation=0"
      "--disable-features=TouchpadOverscrollHistoryNavigation"
    )
    case "${mode}" in
      ""|"--fullscreen"|"--kiosk")
        chromium_flags+=("--kiosk")
        exec "${browser}" "${chromium_flags[@]}" "${page_url}"
        ;;
      "--windowed")
        chromium_flags+=("--new-window" "--app=${page_url}")
        ;;
    esac
    exec "${browser}" "${chromium_flags[@]}"
    ;;
  firefox*|firefox-esr)
    if [ "${mode}" = "--windowed" ]; then
      exec "${browser}" --new-window "${page_url}"
    else
      exec "${browser}" --kiosk "${page_url}"
    fi
    ;;
  angelfish)
    exec "${browser}" "${page_url}"
    ;;
esac
