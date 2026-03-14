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

- Raspberry Pi Compute Module 5

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

### DSI1 Touch Note

On the CM5 IO Board, DSI1's I2C bus routes through GPIO0/GPIO1.
If touch is not detected after switching to DSI1, check:
1. FPC cable orientation at the DSI1 connector (try flipping contacts up/down)
2. The I2C bus can be verified with: `sudo i2cdetect -y 0` (look for `38`)

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
| `sizex`         | Touch X resolution (default 480)      |
| `sizey`         | Touch Y resolution (default 640)      |
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




