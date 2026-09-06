# Whisplay Hat+ Driver for Raspberry Pi 5

Whisplay Hat+ driver project for Raspberry Pi OS Desktop. It bundles the existing 2.8 inch MIPI-DSI display/touch driver with the Whisplay Sound card driver and a desktop demo that verifies display, touch, speaker, microphone, RGB LED, button, QMI8658B IMU and QMC6309 magnetometer.

## Hardware

| Function | Part / Interface |
| --- | --- |
| Display | ST7701P, 480x640, MIPI-DSI 2-lane, RGB888 |
| Touch | GT911, DSI connector I2C, address `0x5d` |
| Sound | Whisplay Sound, ES8389 `0x10` or WM8960 `0x1a`, I2S + I2C1 |
| IMU | QMI8658B, I2C1 address `0x6b`, expected ID `0x05` |
| Magnetometer | QMC6309, I2C1 address `0x7c`, expected ID `0x90` |
| RGB LED | Same physical pins as Whisplay runtime: BOARD `22/18/16` |
| Button | Same physical pin as Whisplay runtime: BOARD `11`, pressed = high |
| Grove Port.X | I2C / UART, selected by BOARD `13` (GPIO27): low = UART, high = I2C |

Sensor coordinate frames:

- Screen coordinate frame uses the 480x640 screen as reference. With the screen facing the user, the lower-left corner is the origin, `X+` points right, `Y+` points up, and `Z+` points out of the screen.
- QMC6309 magnetometer raw axes point to screen `Y-`, `X+`, and `Z+`. Convert raw magnetic readings to screen coordinates as `screen_x = raw_y`, `screen_y = -raw_x`, `screen_z = raw_z`.
- QMI8658B gyroscope raw axes point to screen `X+`, `Y-`, and `Z-`. Convert raw gyro readings to screen coordinates as `screen_x = raw_x`, `screen_y = -raw_y`, `screen_z = -raw_z`.
- `demo/whisplay_hat_plus_demo/default_gyro_calibration.json` records the current phone-assisted compass calibration as initial data for calibration version `8`.

Display/touch GPIO mapping:

| Function | GPIO |
| --- | --- |
| Panel reset | GPIO4, active low |
| Backlight enable | GPIO22, active high by default |
| Touch reset | GPIO6 |
| Touch IRQ | GPIO13, falling edge |
| Grove Port.X mode select | GPIO27 (BOARD pin 13): low = UART, high = I2C |

## Layout

```text
display/driver/                 ST7701P DRM panel kernel module
display/dts/display.dtsi         ST7701P + GT911 overlay fragments
overlay/whisplay-plus.dts        Aggregate overlay, includes display/sound/sensors
sound/                          Whisplay Sound kernel module
sound/dts/sound.dtsi             Whisplay Sound overlay fragments
sensors/sensors.dtsi             QMI8658B/QMC6309 overlay fragments
sensors/whisplay_sensors.py     User-space QMI8658B/QMC6309 sensor helper
demo/                           Raspberry Pi OS Desktop hardware demo
demo/whisplay_hat_plus_demo/gyro_map.py  Reusable gyro/compass simulated map
demo/test_apps/                 Standalone touch, sound and GYRO test apps
install.sh                      One-shot installer
uninstall.sh                    Uninstaller
```

## Install

Run on Raspberry Pi OS Desktop:

```bash
cd ~/whisplay-plus
sudo bash install.sh
sudo reboot
```

The installer:

- installs build/runtime dependencies;
- builds `st7701p_pisugar.ko`;
- builds `snd-soc-whisplay-soundcard.ko`;
- builds and installs the aggregate `whisplay-plus.dtbo`;
- enables `i2c_arm`, `i2s` and `spi`;
- adds `dtoverlay=whisplay-plus` to `/boot/firmware/config.txt`;
- disables Labwc mouse emulation for the GT911 so browser test apps receive
  independent multi-touch contacts;
- installs the desktop demo to `/opt/whisplay-hat-plus`.

`whisplay-plus` defaults to DSI0. If a HAT EEPROM auto-loads
`whisplay-plus.dtbo` and the display is connected to DSI1, add this near the
top of `/boot/firmware/config.txt`, before any other `dtoverlay=` line:

```ini
dtparam=display_dsi=1
```

Do not add an empty `dtoverlay=` line for this use case; that disables
automatic HAT EEPROM overlay loading globally.

The current hardware uses an active-high backlight signal. For an older board
whose backlight is active low, use this overlay line instead:

```ini
dtoverlay=whisplay-plus,backlight_active_low
```

If the overlay is loaded automatically from the HAT EEPROM, add
`dtparam=backlight_active_low=on` before the overlay is loaded instead.

### Grove Port.X mode

The overlay drives GPIO27 (BOARD pin 13) high by default, selecting I2C on
Grove Port.X:

```ini
dtoverlay=whisplay-plus
```

Select UART by adding the `port_x_uart` parameter. This makes GPIO27 output a
physical low level:

```ini
dtoverlay=whisplay-plus,port_x_uart
```

If the HAT EEPROM loads the overlay automatically, select UART before the
overlay is loaded instead:

```ini
dtparam=port_x_uart=on
```

The selector only routes Port.X to UART; UART must also be enabled in
`/boot/firmware/config.txt` when it is used:

```ini
enable_uart=1
```

## Verify

After reboot:

```bash
dmesg | grep -Ei 'st7701|goodix'
aplay -l | grep -i whisplay
cat /proc/bus/input/devices | grep -A5 -i goodix
python3 /opt/whisplay-hat-plus/sensors/whisplay_sensors.py
```

Expected sensor IDs:

```text
QMI8658B addr=0x6b id=0x05
QMC6309  addr=0x7c id=0x90
```

## Desktop Demo

Launch from the Raspberry Pi OS application menu as **Whisplay Hat+ Demo**, or run:

```bash
/opt/whisplay-hat-plus/demo/run-whisplay-hat-plus-demo.sh
```

The demo is fullscreen and shows:

- display size and recent touch points;
- Whisplay Sound sink/source and mic peak;
- speaker tone test at 100%;
- microphone recording test at 100%;
- RGB LED cycle;
- button pressed/released state;
- QMI8658B acceleration, gyro and temperature;
- QMC6309 magnetic vector and heading.

The circular simulated map is implemented in `demo/whisplay_hat_plus_demo/gyro_map.py`. `GyroMap` owns the map position, linear-acceleration motion model, gravity tracking, heading rotation, touch handling and the complete 480x640 modal rendering. Pass screen-frame IMU samples as `(ax, ay, az, gx, gy, gz)` and a fused compass heading to `GyroMap.update()`; attitude-only changes are removed before acceleration is integrated into map movement.

Three standalone hardware diagnostics are installed with the demo:

```bash
/opt/whisplay-hat-plus/demo/test_apps/touch/run.sh
/opt/whisplay-hat-plus/demo/test_apps/sound/run_sound_test.sh
/opt/whisplay-hat-plus/demo/test_apps/gyro/run_gyro_test.sh
```

Their source, desktop entries, icons and runtime notes are kept under `demo/test_apps/`.

Use **Cal** to calibrate sensors:

1. hold the device still for about 2 seconds to capture gyro bias;
2. choose the known compass direction (N/E/S/W), press **Cal**, then keep the device facing that direction for a few seconds to capture the compass heading offset.

Press `Esc`, `q`, or the **Quit** button to exit.

## Notes

- QMC6309 uses address `0x7c` on this Whisplay Hat+ design. Do not convert it to `0x3e` for the current board.
- The magnetometer is sensitive to the onboard speaker, magnets, cables and nearby metal. A hard-iron/soft-iron calibration is still recommended for accurate heading.
- The demo uses user-space I2C for QMI8658B/QMC6309 so it can run without an IIO kernel driver.
- HAT EEPROM can name `whisplay-plus.dtbo`; users can override the display port with `dtparam=display_dsi=1` and old-board backlight polarity with `dtparam=backlight_active_low=on` while keeping EEPROM auto-loading enabled.
