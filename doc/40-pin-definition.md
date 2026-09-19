GPIO TABLE OF 40-PIN HEADER

| Signal | Raspberry Pi Pin | GPIO   |
| ------ | ---------------- | ------ |
| 3.3V   | Pin 1            |        |
| 5V     | Pin 4            |        |
| GND    | Pin 14           |        |
| LEDC   | Pin 15           | GPIO22 |
| PORT.X SEL | Pin 13       | GPIO27 |
| RST    | Pin 7            | GPIO4  |
| TPRST  | Pin 31           | GPIO6  |
| TPINT  | Pin 33           | GPIO13 |

`PORT.X SEL` selects the Grove Port.X interface: drive it low (`0`) for I2C
or high (`1`) for UART. The `whisplay-plus` overlay selects I2C by default; use
the `port_x_uart` overlay parameter to select UART.
