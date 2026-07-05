# PiSugar 2.8" MIPI-DSI 显示屏驱动（Raspberry Pi 5）

完整的 ST7701P 面板 + FT6336U 触摸屏驱动包，包括内核模块、设备树 overlay 和自动化安装脚本。

---

## 📋 硬件规格

| 项目 | 规格 |
|------|------|
| **显示屏** | ST7701P, 480×640 pixels, MIPI-DSI 2-Lane, RGB888 |
| **刷新率** | ~60 Hz |
| **触摸芯片** | FT6336U, I2C 地址 0x38, 支持 2 点触控 |
| **触摸 I2C 总线** | DSI 连接器内置 I2C（`i2c_csi_dsi1`） |
| **背光** | GPIO22 控制（低电平有效） |
| **平台** | Raspberry Pi 5 (BCM2712), Kernel 6.12+ |

### GPIO 管脚映射

| 功能 | GPIO | 有效电平 | 说明 |
|------|------|----------|------|
| 面板复位 (RST) | GPIO4 | 低 | MIPI-DSI 面板复位 |
| 背光使能 (LEDC) | GPIO22 | 低 | 背光 PWM 使能 |
| 触摸复位 (TPRST) | GPIO6 | 低 | FT6336U 硬件复位 |
| 触摸中断 (TPINT) | GPIO13 | — | *未连通（采用轮询模式）* |

---

## 📁 文件结构

```
pisugar-st7701p-driver/
├── driver/
│   ├── st7701p_pisugar.c              # MIPI-DSI 面板驱动源码
│   ├── Makefile                        # 内核模块编译配置
│   └── Module.symvers                  # 编译中间产物
├── overlay/
│   └── pisugar-st7701p-overlay.dts    # 设备树 overlay（面板+触摸）
├── install.sh                          # 一键安装脚本
├── uninstall.sh                        # 卸载脚本
├── 2.8TR+ST7701S MIPI 2Lane .txt      # ST7701P 初始化序列参考
└── README.md                           # 本文件
```

---

## ⚡ 快速开始（推荐）

### 步骤 1：复制到树莓派

```bash
scp -r pisugar-st7701p-driver pi@raspberrypi.local:~/
```

### 步骤 2：执行一键安装

```bash
ssh pi@raspberrypi.local
cd ~/pisugar-st7701p-driver
sudo bash install.sh
```

脚本会自动：
- 安装编译依赖
- 编译面板驱动模块
- 编译设备树 overlay
- 配置 `/boot/firmware/config.txt`
- 更新 initramfs

### 步骤 3：重启并验证

```bash
sudo reboot
# 重启后...
dmesg | grep -E "st7701|edt_ft5x06"
cat /proc/bus/input/devices | grep -A3 "11-0038"
```

---

## 🔧 手动安装（可选）

若需要更细粒度的控制，按以下步骤操作：

### 1. 安装必要工具和内核头文件

```bash
sudo apt update
sudo apt install -y raspberrypi-kernel-headers device-tree-compiler build-essential
```

### 2. 编译面板驱动模块

```bash
cd driver
make clean
make
```

输出应为 `st7701p_pisugar.ko`。安装到系统：

```bash
sudo mkdir -p /lib/modules/$(uname -r)/extra
sudo cp st7701p_pisugar.ko /lib/modules/$(uname -r)/extra/
sudo depmod -a
```

配置开机自动加载：

```bash
echo st7701p_pisugar | sudo tee /etc/modules-load.d/st7701p_pisugar.conf
```

### 3. 编译设备树 overlay

```bash
cd ../overlay

# 使用 C 预处理器展开宏，然后编译为二进制 overlay
cpp -nostdinc \
    -I /usr/src/linux-headers-$(uname -r | sed 's/-v8/-common-rpi/; s/-2712/-common-rpi/')/include \
    -undef -x assembler-with-cpp pisugar-st7701p-overlay.dts | \
    dtc -@ -I dts -O dtb -o pisugar-st7701p.dtbo -

# 安装到 overlays 目录
sudo cp pisugar-st7701p.dtbo /boot/firmware/overlays/
```

### 4. 配置启动参数

编辑 `/boot/firmware/config.txt`：

```bash
sudo nano /boot/firmware/config.txt
```

确保包含以下行（如无则追加）：

```ini
# GPU 驱动
dtoverlay=vc4-kms-v3d

# PiSugar 显示屏驱动
dtoverlay=pisugar-st7701p
```

### 5. 重启系统

```bash
sudo reboot
```

---

## ✅ 验证安装

### 检查驱动加载状态

```bash
# 查看面板驱动日志
dmesg | grep -i st7701
# 预期输出：panel-st7701p-pisugar 某某: init_sequence done, display on

# 查看触摸驱动日志
dmesg | grep -i edt
# 预期输出：edt_ft5x06 11-0038: probe success...

# 查看 I2C 设备
i2cdetect -y 4
# 预期在地址 0x38 看到触摸芯片
```

### 查看输入设备

```bash
cat /proc/bus/input/devices | grep -A5 "11-0038"
# 应显示 "11-0038 EP0110M09" 的输入设备信息
```

### 实时测试触摸

```bash
sudo apt install -y evtest
sudo evtest /dev/input/event5
# 拖动手指在屏幕上，观察坐标变化
```

---

## 🔌 技术细节

### 面板驱动

**文件**：`driver/st7701p_pisugar.c`

- 标准 Linux DRM MIPI-DSI 面板驱动框架
- 作为外置内核模块（in-tree）编译，兼容不同内核版本
- 初始化序列来源：`2.8TR+ST7701S MIPI 2Lane .txt`

**复位时序**：
1. GPIO4 拉低，等待 10ms（T_rtp）
2. GPIO4 拉高，等待 20-25ms（T_ivd）
3. 发送初始化寄存器序列
4. 发送 Exit Sleep 命令，延迟 120ms
5. 发送 Display On 命令

**显示模式参数**：
```
Clock: 26.8 MHz
Timings: 480x640 RGB888, video burst mode
Porch: H=(30, 12, 30), V=(20, 4, 20)
```

**可调模块参数**：

`st7701p_pisugar` 支持在 `/etc/modprobe.d/st7701p_pisugar.conf` 中配置显示链路和面板初始化参数，例如：

```ini
options st7701p_pisugar video_mode=burst pixel_format=rgb888 dsi_lpm=1 dsi_non_continuous_clock=1 mode_clock_khz=26800 mode_hfp=30 mode_hsync=12 mode_hbp=30 mode_vfp=20 mode_vsync=4 mode_vbp=20 gip_e5e8_variant=rg28xx
```

`gip_e5e8_variant` 用于选择 BK1 E5/E8 GIP/source 映射。默认 `rg28xx` 在蓝绿斜向渐变边缘上可减轻亮点/斜亮线；如果需要回到供应商原始序列，可设为 `vendor`，然后重启验证：

```ini
options st7701p_pisugar gip_e5e8_variant=vendor
```

### 触摸驱动

**驱动**：Linux 内核自带 `edt_ft5x06`

- Compatible：`focaltech,ft6236`
- I2C 地址：0x38
- 总线：`i2c_csi_dsi1`（DSI 连接器内置 I2C）

**特殊说明**：
- GPIO13（触摸中断线）在此硬件上**未物理连通**
- 驱动自动回退到**定时器轮询模式**：
  - 轮询间隔：17ms（约 60fps）
  - 无需配置，驱动自动启用
  - 触摸响应延迟 < 50ms，体感流畅

**触摸校准**（如需）：
编辑 `overlay/pisugar-st7701p-overlay.dts`：

```dts
touch@38 {
    touchscreen-size-x = <480>;
    touchscreen-size-y = <640>;
    touchscreen-inverted-x;    // 如需翻转 X 轴
    touchscreen-inverted-y;    // 如需翻转 Y 轴
    touchscreen-swapped-x-y;   // 如需交换 X/Y 轴
};
```

---

## 🗑️ 卸载

### 一键卸载

```bash
sudo bash uninstall.sh
sudo reboot
```

### 手动卸载

```bash
# 移除模块
sudo rmmod st7701p_pisugar
rm -f /lib/modules/$(uname -r)/extra/st7701p_pisugar.ko
rm -f /etc/modules-load.d/st7701p_pisugar.conf

# 移除 overlay
sudo rm -f /boot/firmware/overlays/pisugar-st7701p.dtbo

# 编辑 config.txt 移除相关行
sudo nano /boot/firmware/config.txt
# 删除：dtoverlay=pisugar-st7701p

# 更新并重启
sudo depmod -a
sudo update-initramfs -u
sudo reboot
```

---

## 🐛 常见问题排查

| 现象 | 根本原因 | 解决方案 |
|------|----------|---------|
| **启动后无显示** | DSI 排线未正确插入或驱动未加载 | 检查 DSI 排线连接；运行 `dmesg \| grep st7701` |
| **有背光无画面** | 面板驱动加载失败 | `lsmod \| grep st7701p` 检查加载状态；查看 `dmesg` 错误 |
| **画面花屏/闪烁** | Timing 参数不匹配 | 编辑 `st7701p_pisugar.c` 中的 mode timing 参数 |
| **触摸完全无响应** | 触摸驱动未加载或 I2C 总线异常 | `dmesg \| grep edt` 检查；`i2cdetect -y 4` 扫描地址 0x38 |
| **触摸坐标偏移** | 屏幕尺寸配置错误 | 调整 overlay 中的 `touchscreen-size-x/y` |
| **触摸反向/倒置** | 坐标映射反向 | 在 overlay 中添加 `touchscreen-inverted-x/y` 或 `touchscreen-swapped-x-y` |

---

## 📝 配置文件速查

### `/boot/firmware/config.txt` 示例完整配置

```ini
[pi5]
# GPU 显示驱动
dtoverlay=vc4-kms-v3d

# PiSugar 2.8" 显示屏 + 触摸
dtoverlay=pisugar-st7701p

# 可选：DWC2 USB OTG（如使用）
# dtoverlay=dwc2,dr_mode=host
```

---

## 📖 进阶开发

### 修改初始化序列

编辑 `driver/st7701p_pisugar.c`，函数 `st7701p_init_sequence()`。参考 `2.8TR+ST7701S MIPI 2Lane .txt`。

### 修改显示模式参数

在 `st7701p_get_modes()` 中调整：

```c
static const struct drm_display_mode mode = {
    .clock = 26800,        // 像素时钟 MHz * 100
    .hdisplay = 480,
    .hsync_start = 510,
    .hsync_end = 522,
    .htotal = 552,
    .vdisplay = 640,
    .vsync_start = 660,
    .vsync_end = 664,
    .vtotal = 684,
};
```

### 构建为内置驱动

若需要集成到内核源码树（不作为外置模块），参考 Linux 驱动开发指南。

---

## 📄 许可证

GPL v2

---

## 📞 支持

问题或建议请提交 Issue。技术细节可查阅：
- [Linux DRM Panel Driver](https://www.kernel.org/doc/html/latest/gpu/drm/index.html)
- [EDT FT5x06 Touchscreen Driver](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/input/touchscreen/edt-ft5x06.c)
- [Raspberry Pi Device Tree Documentation](https://www.raspberrypi.com/documentation/computers/device-tree.html)
