# CM5 IO Board DSI1 I2C 触摸不工作问题

IO board原理图下载：

https://pip.raspberrypi.com/categories/1098-design-files

https://pip-assets.raspberrypi.com/categories/1098-design-files/documents/RP-008099-DD-1-CM5%20IO%20Board,%20revision%202,%20KiCAD%20files..zip

> **状态：待验证**

## 问题描述

P028TRM160-IPS-CTP 2.8寸 DSI 屏幕接在 CM5 IO Board 的 DSI1 接口时，显示正常但触摸（FT6336U, I2C 地址 0x38）完全无响应。`i2cdetect` 扫描 bus 0（i2c_csi_dsi1）全部为空，无任何 ACK。

同一块屏幕接在 DSI0 接口时，显示和触摸均正常工作。

## 原理图分析

通过分析 CM5 IO Board Rev.2 KiCAD 原理图（`CM5_HighSpeed.kicad_sch`），发现：

### 两个 DSI FPC 连接器

| 连接器 | DSI 端口 | Pin 20 (SCL) | Pin 21 (SDA) | I2C 网络 |
|--------|----------|-------------|-------------|---------|
| **J5** (22-pin FPC) | DSI0 | SCL0 | SDA0 | 直连 CM5 Pin 80/82 |
| **J16** (22-pin FPC) | DSI1 | SCL1 | SDA1 | **需通过 J6 跳线桥接** |

### J6 跳线排针（2×2）

J6 是一个 2×2 排针，作用是将 CM5 的 ID_EEPROM I2C 总线桥接到 DSI1 FPC 连接器的 I2C 线路：

| J6 引脚 | 左侧（CM5 端） | 右侧（J16 FPC 端） |
|---------|---------------|-------------------|
| 1 ↔ 2 | ID_SC (CM5 Pin 35 = GPIO0) | SCL1 (J16 Pin 20) |
| 3 ↔ 4 | ID_SD (CM5 Pin 36 = GPIO1) | SDA1 (J16 Pin 21) |

### 根本原因

**J6 未短接时，DSI1 连接器的 I2C 线路（SCL1/SDA1）悬空，未连接到 CM5 的任何 I2C 总线。** 因此 `i2cdetect` 无法检测到触摸芯片。

DSI0 的 I2C（SCL0/SDA0）直接连线到 CM5，无需跳线，所以即插即用。

## 解决方案（待验证）

在 CM5 IO Board 上找到 **J6** 排针（2×2 pin header），安装两个跳线帽：

1. 短接 **Pin 1-2**（ID_SC ↔ SCL1）
2. 短接 **Pin 3-4**（ID_SD ↔ SDA1）

短接后，CM5 的 GPIO0/GPIO1（ID_SC/ID_SD，对应 `i2c_csi_dsi1` 即 bus 0）将物理连通到 J16 FPC 连接器的 I2C 引脚，触摸芯片应可正常通信。

## 验证步骤

1. 关机，在 J6 上安装跳线帽（1-2 和 3-4）
2. 将屏幕 FPC 接到 J16（DSI1）
3. 确认 `config.txt` 中加载 `dtoverlay=p028trm160-dsi1`
4. 开机后执行：
   ```bash
   i2cdetect -y 0
   ```
   预期在地址 `0x38` 处看到 ACK
5. 确认触摸设备正常绑定：
   ```bash
   ls /sys/bus/i2c/devices/0-0038/
   dmesg | grep -i "ft\|edt\|touch"
   ```

## 注意事项

- J6 桥接的是 CM5 的 **ID_EEPROM I2C 总线**（GPIO0/GPIO1）。如果 40-pin GPIO Header 上同时接有带 EEPROM 的 HAT，可能产生总线冲突。单独使用 DSI1 触摸时无此问题。
- I2C 上拉电阻：原理图显示 R6 (2.2KΩ) 和 R7 (2.2KΩ) 分别将 SDA1 和 SCL1 上拉至 GPIO_VREF，上拉已由板载电阻提供。

## 参考

- 原理图文件：`RP-008099-DD-1-CM5 IO Board, revision 2, KiCAD files./CM5_HighSpeed.kicad_sch`
- DSI1 DTS overlay：`p028trm160-dsi1-overlay.dts`
