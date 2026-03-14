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
- DSI port: DSI0
- Backlight: Active-low (GPIO22 LOW = on)

## Supported Platforms

- Raspberry Pi Compute Module 5

## Build & Install

```bash
# On the Raspberry Pi:
sudo apt-get install -y raspberrypi-kernel-headers device-tree-compiler

cd ~/2.8inch_DSI_Driver
chmod +x install.sh
./install.sh

sudo reboot
```

## Manual Build

```bash
# Build kernel module
make

# Compile device tree overlay
dtc -@ -H epapr -I dts -O dtb -o p028trm160.dtbo p028trm160-overlay.dts

# Install
sudo cp panel-p028trm160.ko /lib/modules/$(uname -r)/kernel/drivers/gpu/drm/panel/
sudo depmod -a
sudo cp p028trm160.dtbo /boot/firmware/overlays/

# Add to /boot/firmware/config.txt:
#   dtoverlay=p028trm160
```

## Overlay Parameters

| Parameter       | Description                           |
| --------------- | ------------------------------------- |
| `sizex`         | Touch X resolution (default 480)      |
| `sizey`         | Touch Y resolution (default 640)      |
| `disable_touch` | Set to disable the touch controller   |
| `lanes`         | DSI lanes: 1 (default) or 2           |

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
- `p028trm160-overlay.dts` — Device tree overlay (DSI0 + FT6236 touch)
- `Makefile` — Kernel module build
- `install.sh` — Automated build & install script
- `doc/` — Hardware documentation and datasheets




