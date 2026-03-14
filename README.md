# 2.8inch DSI LCD Driver with Touchscreen

Driver for the P028TRM160-IPS-CTP 2.8" 480x640 DSI LCD panel with capacitive touch,
based on the ST7701S display controller and FocalTech FT6236 touch controller.

## Hardware

| Signal         | Raspberry Pi Pin | GPIO   |
| -------------- | ---------------- | ------ |
| 3.3V           | Pin 1            |        |
| 5V             | Pin 4            |        |
| GND            | Pin 14           |        |
| LEDC (BL)      | Pin 15           | GPIO22 |
| RST (Display)  | Pin 18           | GPIO24 |
| TPRST (Touch)  | Pin 33           | GPIO13 |
| TPINT (Touch)  | Pin 31           | GPIO6  |

- Display: MIPI DSI 1-lane, 480x640, RGB888
- Touch: FocalTech FT6236 on DSI I2C bus (addr 0x38), 5-point multitouch
- DSI port: DSI0 or DSI1
- Backlight: Active-low (GPIO22 LOW = on)

## Supported Platforms

- Raspberry Pi 5 Model B（DSI0 / DSI1 均已验证）
- Raspberry Pi Compute Module 5（DSI0 已验证；DSI1 触摸需 CM5 IO Board J6 跳线，见 [doc/cm5-io-board-dsi1-i2c-issue.md](doc/cm5-io-board-dsi1-i2c-issue.md)）

## Build & Install

```bash
# On the Raspberry Pi:
sudo apt-get install -y raspberrypi-kernel-headers device-tree-compiler

cd ~/2.8inch_DSI_Driver
chmod +x install.sh

# Install for DSI0 (default)
./install.sh

# Or install for DSI1
./install.sh dsi1

sudo reboot
```

## DSI Port Selection

The display can be connected to either DSI0 or DSI1. Select the correct port
in `/boot/firmware/config.txt`:

```ini
# For DSI0 (default)
dtoverlay=p028trm160

# For DSI1
dtoverlay=p028trm160-dsi1
```

> **Note:** Only one overlay should be active at a time. The `install.sh` script
> handles this automatically.

### DSI1 I2C Notes

- **Pi 5 Model B**: DSI1 触摸即插即用，I2C bus 11，无需额外配置。
- **CM5 IO Board**: DSI1 的 I2C 线路需要通过 **J6 跳线帽** 桥接才能连通。
  不短接 J6 时触摸芯片无法被检测到。详见 [doc/cm5-io-board-dsi1-i2c-issue.md](doc/cm5-io-board-dsi1-i2c-issue.md)。

验证 I2C 连通性：
```bash
# Pi 5 DSI1
sudo i2cdetect -y 11   # 应在 0x38 看到 UU 或 38

# CM5 DSI0
sudo i2cdetect -y 10   # 应在 0x38 看到 UU 或 38

# CM5 DSI1（需 J6 跳线）
sudo i2cdetect -y 0    # 应在 0x38 看到 UU 或 38
```

## Manual Build

```bash
# Build kernel module
make

# Compile device tree overlay (choose one)
dtc -@ -H epapr -I dts -O dtb -o p028trm160.dtbo p028trm160-overlay.dts          # DSI0
dtc -@ -H epapr -I dts -O dtb -o p028trm160-dsi1.dtbo p028trm160-dsi1-overlay.dts # DSI1

# Install
sudo cp panel-p028trm160.ko /lib/modules/$(uname -r)/kernel/drivers/gpu/drm/panel/
sudo depmod -a
sudo cp p028trm160*.dtbo /boot/firmware/overlays/

# Add to /boot/firmware/config.txt (choose one):
#   dtoverlay=p028trm160        # DSI0
#   dtoverlay=p028trm160-dsi1   # DSI1
```

## Overlay Parameters

| Parameter       | Description                           |
| --------------- | ------------------------------------- |
| `sizex`         | Touch X resolution (default 240)      |
| `sizey`         | Touch Y resolution (default 320)      |
| `disable_touch` | Set to disable the touch controller   |
| `lanes`         | DSI lanes: 1 (default) or 2           |

## Touch Mode

The touch panel has two operating modes controlled by the labwc compositor:

| Mode | Behavior | Use Case |
| ---- | -------- | -------- |
| **Native touch** | Swipe to scroll, tap to click | Web browsing, document reading |
| **Mouse emulation** | Touch acts as mouse cursor, drag to select | Text selection, precise control |

Toggle between modes:

```bash
# Toggle (switches to the other mode)
./toggle-touch-mode.sh

# Switch to specific mode
./toggle-touch-mode.sh touch   # swipe = scroll
./toggle-touch-mode.sh mouse   # drag = select
```

The script modifies `~/.config/labwc/rc.xml` and hot-reloads labwc — no reboot needed.

## Architecture Notes

The panel driver splits initialization into two stages due to the Raspberry Pi
RP1 DSI bridge driver's enable ordering:

- **prepare()**: GPIO reset sequence only (runs before DSI PHY is configured)
- **enable()**: ST7701S init commands, sleep out, display on, backlight
  (runs after DSI host PHY is fully initialized and active)

This is necessary because the `drm-rp1-dsi` bridge driver configures the DSI
PHY in its `bridge.pre_enable` callback, which executes *after* the panel's
`prepare()` in the DRM bridge chain pre-enable (reverse) ordering.

## Files

- `panel-p028trm160.c` — Kernel panel driver module (ST7701S init sequence)
- `p028trm160-overlay.dts` — Device tree overlay for DSI0
- `p028trm160-dsi1-overlay.dts` — Device tree overlay for DSI1
- `Makefile` — Kernel module build
- `install.sh` — Automated build & install script
- `toggle-touch-mode.sh` — Toggle touch between native touch and mouse emulation
- `doc/` — Hardware documentation and datasheets




