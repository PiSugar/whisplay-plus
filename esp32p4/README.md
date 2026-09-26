# Whisplay Plus on Waveshare ESP32-P4-Module-DEV-KIT

This is a self-contained Metro-style hardware diagnostic for using the
Whisplay Plus HAT on the Waveshare ESP32-P4-Module-DEV-KIT. It covers the
ST7701P display, five-point GT911 touch, button, RGB LED, QMI8658B IMU,
MQC5883P magnetometer, and switchable Whisplay ES8389 / onboard ES8311 audio
paths. The current audio limitations are documented below.

The UI uses two synchronized RGB888 framebuffers in PSRAM, avoiding writes to
the framebuffer currently being scanned by the DSI peripheral.

## Hardware mapping

The Waveshare 40-pin header preserves Raspberry Pi HAT physical pin positions,
but its GPIO numbers are ESP32-P4 GPIO numbers:

| Whisplay signal | HAT physical pin | ESP32-P4 GPIO |
| --- | ---: | ---: |
| I2C SDA | 3 | 7 |
| I2C SCL | 5 | 8 |
| Panel reset | 7 | 23 |
| Button | 11 | 21 |
| Grove Port.X select | 13 | 20 |
| Backlight enable | 15 | 6 |
| RGB B | 16 | 5 |
| RGB G | 18 | 4 |
| RGB R | 22 | 1 |
| Touch reset | 31 | 26 |
| Touch interrupt | 33 | 48 |
| I2S BCLK | 12 | 22 |
| I2S LRCLK | 35 | 53 |
| I2SDIN: codec ASDOUT to ESP32-P4 | 38 | 27 |
| I2SDOUT: ESP32-P4 to codec DSDIN | 40 | 45 |

The 15-pin DSI connector carries DSI lanes, I2C, 3.3V, and ground. Panel reset,
backlight, and touch GPIOs come through the 40-pin HAT connector, so both
connectors must be fitted for this test.

## Build and flash

ESP-IDF 5.5 or newer is required:

```sh
cd esp32p4
idf.py set-target esp32p4
idf.py build
idf.py -p /dev/cu.usbmodem5C930067301 flash monitor
```

Expected serial output includes an I2C scan. A fully connected Whisplay Plus
should normally expose GT911 at `0x5d`, Whisplay ES8389 at `0x10`, the
Waveshare onboard ES8311 at `0x18`, QMI8658B at `0x6b`, and MQC5883P at
`0x2c`.

## Current audio status

The Sound tile has two selectable modes. `HAT ES8389` is the default;
`BOARD ES8311` switches the complete input and output path to the codec on the
Waveshare development board. I2S channels are deleted and recreated during a
switch because the two codecs use different buses and GPIO assignments.

| Mode | Microphone | Speaker | Current status |
| --- | --- | --- | --- |
| Whisplay ES8389 (`0x10`) | Working; the live meter responds to speech | Not working yet; no audible 1 kHz tone | Under investigation |
| Waveshare ES8311 (`0x18`) | Working through GPIO11 | Working through GPIO9 and the onboard NS4150B amplifier | Verified with a Mac microphone recording |

The ES8311 path uses MCLK GPIO13, BCLK GPIO12, LRCLK GPIO10, DAC data GPIO9,
ADC data GPIO11, and NS4150B `PA_CTRL` GPIO53. Its 1 kHz test measured about
`-35.8 dBFS` in the Mac microphone recording versus about `-83.3 dBFS` after
playback, a difference of roughly 47.5 dB. Playback explicitly detaches
GPIO53 from the I2S GPIO matrix before driving the amplifier enable high, then
returns to ES8311 microphone capture after the tone.

The ES8389 microphone is working and produces non-zero live levels. The
`HAT ES8389` playback target is always the WhisPlay HAT's own speaker path:
ES8389 `LOUTP/N` and `ROUTP/N` feed the two NS4150B amplifiers on the HAT, whose
outputs drive the WhisPlay HAT speaker connections (`H1`, `P2`, and `P3`). The
Demo never redirects ES8389 playback to the Waveshare development board's
speaker socket; that socket belongs exclusively to the separate `BOARD ES8311`
mode.

For the HAT speaker path, the ESP32-P4 generates active BCLK, LRCLK, and
non-zero PCM on WhisPlay `I2SDOUT` at its GPIO45 output pad; the ES8389 reaches
its documented normal state (`F1=0x0d`) and its DAC is unmuted. Playback uses
the production tester's
stereo PCM pattern, with the DAC registers set directly to the documented 0 dB
value (`0xBF`). The P4 adaptation additionally enables the ES8389 DAC analog
reference (`0x61` bit 5) and selects line-output mode for the HAT's NS4150B
amplifiers (`0x69` bits 1:0 clear). The GPIO45 output-pad loopback only confirms
the full-amplitude PCM inside the P4 pad; it does not prove a valid waveform at
the ES8389 side of HAT resistor `R18`. An ES8389-internal DAC-to-ADC loopback
also failed to detect that PCM for every tested I2S/left-justified polarity and
sampling-edge combination. The Mac microphone has not detected a stable 1 kHz
signal from the HAT speaker. A microphone recording remains the acceptance
test; register activity alone is not
considered a speaker pass.

The Waveshare board has a second, unavoidable conflict on this exact signal.
Its official `ESP32-P4-Module-DEV-KIT.pdf` schematic connects GPIO45—the
WhisPlay `I2SDOUT` signal on physical HAT pin 40—not only to that header but
directly to the gate of the onboard microSD power-switch
MOSFET `Q1` (AO3401), with `R7` pulling the gate down. Consequently every DSDIN
bit also switches the microSD power-control circuit. Stronger GPIO drive and an
8 kHz test (half the normal serial rate) did not restore playback. Do not insert
a microSD card while testing this mapping.

GPIO45 is not a reserved or otherwise unusual GPIO inside the ESP32-P4 itself;
the restriction comes from this Waveshare carrier-board connection. It can be
used as a static control output if the resulting microSD power state is
acceptable: low turns `Q1` on and powers the microSD rail, while high turns it
off. It should not be used for PWM, I2S, or another continuously toggling signal
on an unmodified board, because that also repeatedly switches the microSD power
circuit and loads the waveform through the MOSFET gate. Firmware cannot make
one shared electrical level simultaneously carry PCM/PWM and hold the microSD
power control stable; the two functions must be separated in hardware.

The clean hardware verification is to isolate HAT DSDIN from GPIO45 and drive
it from an otherwise unused P4 GPIO. On the WhisPlay HAT this can be done by
lifting/removing the GPIO45 side of series resistor `R18`, then jumpering the
ES8389 side of `R18` to a free Waveshare header GPIO and changing
`CONFIG_WHISPLAY_I2S_DOUT_GPIO` accordingly. Alternatively, isolate/remove the
Waveshare microSD `Q1` gate branch before re-testing GPIO45. Verify the selected
board revision and continuity before modifying hardware.

There is an important board-level conflict: Whisplay uses GPIO53 as ES8389
LRCLK, while the Waveshare board connects the same GPIO through `R52` (0 ohm)
to its NS4150B `PA_CTRL`. The demo handles this by making the two audio modes
mutually exclusive and fully remapping GPIO53 during a switch. They cannot be
operated simultaneously without physically isolating `R52`.

## Using the diagnostic UI

- Touch contacts, motion data, temperature, and compass status update in their
  Metro tiles.
- Tap the RGB/I/O tile, or press the HAT button, to cycle off/red/green/blue.
- Tap `HAT ES8389` or `BOARD ES8311` in the Sound tile to select the complete
  audio input/output path. Tap `RECORD 10S` to capture ten seconds of stereo
  16 kHz/16-bit PCM from the selected codec into PSRAM, then tap `PLAY CLIP` to
  play that recording separately. Recording and playback run in a background
  task, so touch feedback, animation, sensors, and the rest of the UI continue
  updating during both operations. The active button shows a percentage and a
  filling progress bar. During capture, the MIC meter is updated directly from
  each recorded PCM block. `PLAY CLIP` remains disabled until a complete
  recording exists. The clip remains in PSRAM when switching codecs,
  allowing audio captured by ES8389 to be played through ES8311, or vice versa.
  Codec switching is temporarily ignored while an operation is active. Live
  microphone capture resumes after playback finishes.
- The meter is live in both modes. A moving meter confirms capture only; it
  does not mark the speaker test as passed.
- A missing MQC5883P is shown as `MISS` rather than treated as a fatal error.

GPIOs and DSI lane rate can be changed under `Whisplay Plus hardware` in
`idf.py menuconfig`.

## Verified hardware

Tested on an ESP32-P4 revision v3.2 (ECO7) module with 16 MB flash and 32 MB
PSRAM using ESP-IDF v5.5.5. The test unit completed ST7701P DSI/DPI startup,
identified the touch controller as `911`, initialized both audio codecs, and
ran the double-buffered Metro UI without a DSI underrun or reset. ES8311 input
and output are verified; ES8389 input is verified but its speaker output is
not yet considered passing, as described above.

On ECO7, leave the DSI bus `phy_clk_src` as `0`, matching the Waveshare BSP.
`MIPI_DSI_PHY_CLK_SRC_DEFAULT` resolves to the unsupported `PLL_F20M` source in
this ESP-IDF/chip combination and aborts inside `esp_lcd_new_dsi_bus()`.

The tested HAT answered at `0x10`, `0x18`, `0x5d`, `0x6b`, `0x7c`, and `0x7e`.
No device answered at the documented MQC5883P address `0x2c`; the UI therefore
reports the magnetometer as `MISS` on this fitted HAT revision.
