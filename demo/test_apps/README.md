# Whisplay hardware test apps

These standalone fullscreen applications were consolidated from the Raspberry Pi hardware test environment.

| App | Entry point | Runtime requirements |
| --- | --- | --- |
| Touch Test | `touch/run.sh` | Python 3 and Chromium, Firefox, or Angelfish |
| Sound Test | `sound/run_sound_test.sh` | pygame, PipeWire/PulseAudio tools (`pactl`, `parecord`, `paplay`) |
| GYRO Test | `gyro/run_gyro_test.sh` | pygame, Flask, SMBus, OpenSSL, QMI8658B and QMC6309 |

The GYRO app imports the shared simulated map from `demo/whisplay_hat_plus_demo/gyro_map.py` and uses `demo/whisplay_hat_plus_demo/default_gyro_calibration.json` as the checked-in initial calibration reference. Phone-assisted calibration creates its HTTPS certificate and private key at runtime under `~/.config/whisplay-gyro-test/certs`; generated keys and user calibration files must not be committed.

`install.sh` copies this directory to `/opt/whisplay-hat-plus/demo/test_apps`, installs the three desktop entries, and installs their SVG icons with the rest of the demo.
