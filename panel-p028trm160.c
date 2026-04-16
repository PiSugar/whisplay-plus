// SPDX-License-Identifier: GPL-2.0
/*
 * Panel driver for P028TRM160-IPS-CTP 2.8" 480x640 DSI panel
 * Based on Sitronix ST7701S controller
 *
 * Copyright (C) 2026 PiSugar
 */

#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>

#include <video/mipi_display.h>

#include <drm/drm_mipi_dsi.h>
#include <drm/drm_modes.h>
#include <drm/drm_panel.h>

/* ST7701S Command2 BKx Selection */
#define DSI_CMD2BKX_SEL         0xFF

/* Command2 BK0 commands */
#define DSI_CMD2_BK0_LNESET     0xC0
#define DSI_CMD2_BK0_PORCTRL    0xC1
#define DSI_CMD2_BK0_INVSEL     0xC2
#define DSI_CMD2_BK0_PVGAMCTRL  0xB0
#define DSI_CMD2_BK0_NVGAMCTRL  0xB1

/* Command2 BK1 commands */
#define DSI_CMD2_BK1_VRHS       0xB0
#define DSI_CMD2_BK1_VCOM       0xB1
#define DSI_CMD2_BK1_VGHSS      0xB2
#define DSI_CMD2_BK1_TESTCMD    0xB3
#define DSI_CMD2_BK1_VGLS       0xB5
#define DSI_CMD2_BK1_PWCTLR1    0xB7
#define DSI_CMD2_BK1_PWCTLR2    0xB8
#define DSI_CMD2_BK1_SPD1       0xC1
#define DSI_CMD2_BK1_SPD2       0xC2
#define DSI_CMD2_BK1_MIPISET1   0xD0

struct p028trm160_panel {
	struct drm_panel panel;
	struct mipi_dsi_device *dsi;
	struct gpio_desc *reset;
	struct gpio_desc *backlight_gpio;
};

static inline struct p028trm160_panel *to_p028trm160(struct drm_panel *panel)
{
	return container_of(panel, struct p028trm160_panel, panel);
}

#define P028_DSI_WRITE(dsi, seq...)                                    \
	do {                                                             \
		static const u8 d[] = { seq };                               \
		int ret;                                                     \
		ret = mipi_dsi_dcs_write_buffer(dsi, d, ARRAY_SIZE(d));      \
		if (ret < 0)                                                 \
			return ret;                                              \
	} while (0)

static int p028trm160_init_sequence(struct mipi_dsi_device *dsi)
{
	/* Command2 BK3 Selection: Enable unknown block */
	P028_DSI_WRITE(dsi, DSI_CMD2BKX_SEL, 0x77, 0x01, 0x00, 0x00, 0x13);
	P028_DSI_WRITE(dsi, 0xEF, 0x08);

	/* Command2 BK0 Selection: Display function setting */
	P028_DSI_WRITE(dsi, DSI_CMD2BKX_SEL, 0x77, 0x01, 0x00, 0x00, 0x10);

	/* Line Setting: (0x4F + 1) * 8 = 640 lines */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK0_LNESET, 0x4F, 0x00);

	/* Porch Control: VBP=12, VFP=2 */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK0_PORCTRL, 0x0C, 0x02);

	/* Inversion Selection & Frame Rate Control */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK0_INVSEL, 0x37, 0x06);

	/* Positive Voltage Gamma Control */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK0_PVGAMCTRL,
		0x40, 0xC3, 0x96, 0x0C, 0x12, 0x08, 0x07, 0x06,
		0x06, 0x20, 0x03, 0x92, 0x10, 0x66, 0xAF, 0x9D);

	/* Negative Voltage Gamma Control */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK0_NVGAMCTRL,
		0x40, 0xC3, 0x95, 0x0E, 0x11, 0x07, 0x0A, 0x09,
		0x09, 0x22, 0x05, 0x11, 0x0F, 0x28, 0xEE, 0xD9);

	/* Command2 BK1 Selection: Power related setting */
	P028_DSI_WRITE(dsi, DSI_CMD2BKX_SEL, 0x77, 0x01, 0x00, 0x00, 0x11);

	/* VOP/VRHS Amplitude Setting: 0x3D */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK1_VRHS, 0x3D);
	/* VCOM Amplitude Setting: 0x7F */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK1_VCOM, 0x7F);
	/* VGH/VGL Setting: 0x89 */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK1_VGHSS, 0x89);
	/* Test Command: 0x80 */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK1_TESTCMD, 0x80);
	/* VGL Voltage Setting: 0x49 */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK1_VGLS, 0x49);
	/* Power Control 1: 0x85 */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK1_PWCTLR1, 0x85);
	/* Power Control 2: 0x23 */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK1_PWCTLR2, 0x23);
	/* SPD1: 0x78 */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK1_SPD1, 0x78);
	/* SPD2: 0x78 */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK1_SPD2, 0x78);
	/* MIPI Setting 1: 0x88 */
	P028_DSI_WRITE(dsi, DSI_CMD2_BK1_MIPISET1, 0x88);

	/* GIP setting (E0-EF in BK1) */
	P028_DSI_WRITE(dsi, 0xE0, 0x00, 0x00, 0x02);
	P028_DSI_WRITE(dsi, 0xE1,
		0x07, 0xC0, 0x07, 0xC0, 0x06, 0xC0, 0x06, 0xC0,
		0x00, 0x24, 0x24);
	P028_DSI_WRITE(dsi, 0xE2,
		0x20, 0x20, 0x22, 0x22, 0x8D, 0xC0, 0x00, 0x00,
		0x8C, 0xC0, 0x00, 0x00, 0x00);
	P028_DSI_WRITE(dsi, 0xE3, 0x00, 0x00, 0x22, 0x22);
	P028_DSI_WRITE(dsi, 0xE4, 0x44, 0x44);
	P028_DSI_WRITE(dsi, 0xE5,
		0x09, 0x91, 0x10, 0xFA, 0x0B, 0x93, 0x10, 0xFA,
		0x0D, 0x8D, 0x10, 0xFA, 0x0F, 0x8F, 0x10, 0xFA);
	P028_DSI_WRITE(dsi, 0xE6, 0x00, 0x00, 0x22, 0x22);
	P028_DSI_WRITE(dsi, 0xE7, 0x44, 0x44);
	P028_DSI_WRITE(dsi, 0xE8,
		0x08, 0x90, 0x10, 0xFA, 0x0A, 0x92, 0x10, 0xFA,
		0x0C, 0x8C, 0x10, 0xFA, 0x0E, 0x8E, 0x10, 0xFA);
	P028_DSI_WRITE(dsi, 0xEB,
		0x02, 0x01, 0xE4, 0xE4, 0x88, 0x00, 0x00);
	P028_DSI_WRITE(dsi, 0xED,
		0xFF, 0x04, 0x56, 0x7F, 0xBA, 0x2F, 0xFF, 0xFF,
		0xFF, 0xFF, 0xF2, 0xAB, 0xF7, 0x65, 0x40, 0xFF);
	P028_DSI_WRITE(dsi, 0xEF,
		0x08, 0x08, 0x08, 0x45, 0x3F, 0x54);

	/* Disable command2 (back to User Command) */
	P028_DSI_WRITE(dsi, DSI_CMD2BKX_SEL, 0x77, 0x01, 0x00, 0x00, 0x00);

	/* Vendor init doesn't include SET_PIXEL_FORMAT; panel default is RGB888 */

	return 0;
}

static int p028trm160_prepare(struct drm_panel *panel)
{
	struct p028trm160_panel *ctx = to_p028trm160(panel);
	struct mipi_dsi_device *dsi = ctx->dsi;
	int ret;

	dev_info(&dsi->dev, "prepare() called\n");

	/* Hardware reset */
	if (ctx->reset) {
		gpiod_set_value_cansleep(ctx->reset, 1);
		msleep(50);
		gpiod_set_value_cansleep(ctx->reset, 0);
		msleep(200);
	}

	/* Send init sequence while DSI is still in LP/command mode */
	ret = p028trm160_init_sequence(dsi);
	if (ret) {
		dev_err(&dsi->dev, "Panel init sequence failed: %d\n", ret);
		return ret;
	}
	dev_info(&dsi->dev, "init_sequence OK\n");

	ret = mipi_dsi_dcs_exit_sleep_mode(dsi);
	if (ret) {
		dev_err(&dsi->dev, "Failed to exit sleep mode: %d\n", ret);
		return ret;
	}
	dev_info(&dsi->dev, "exit_sleep_mode OK, waiting 120ms\n");
	msleep(120);

	/* INVON after sleep_out - white background makes video effect visible */
	{
		static const u8 invon_cmd[] = { 0x21 };
		ret = mipi_dsi_dcs_write_buffer(dsi, invon_cmd, sizeof(invon_cmd));
		dev_info(&dsi->dev, "INVON ret=%d\n", ret);
	}

	dev_info(&dsi->dev, "prepare() done\n");
	return 0;
}

static int p028trm160_enable(struct drm_panel *panel)
{
	struct p028trm160_panel *ctx = to_p028trm160(panel);
	struct mipi_dsi_device *dsi = ctx->dsi;
	int ret;

	dev_info(&dsi->dev, "enable() called\n");

	ret = mipi_dsi_dcs_set_display_on(dsi);
	if (ret) {
		dev_err(&dsi->dev, "Failed to set display on: %d\n", ret);
		return ret;
	}
	dev_info(&dsi->dev, "display_on OK\n");
	msleep(20);

	if (ctx->backlight_gpio)
		gpiod_set_value_cansleep(ctx->backlight_gpio, 1);

	dev_info(&dsi->dev, "enable() done\n");
	return 0;
}

static int p028trm160_disable(struct drm_panel *panel)
{
	struct p028trm160_panel *ctx = to_p028trm160(panel);
	struct mipi_dsi_device *dsi = ctx->dsi;

	/* Disable backlight */
	if (ctx->backlight_gpio)
		gpiod_set_value_cansleep(ctx->backlight_gpio, 0);

	mipi_dsi_dcs_set_display_off(dsi);
	msleep(20);
	mipi_dsi_dcs_enter_sleep_mode(dsi);
	msleep(120);

	return 0;
}

static int p028trm160_unprepare(struct drm_panel *panel)
{
	struct p028trm160_panel *ctx = to_p028trm160(panel);

	if (ctx->reset)
		gpiod_set_value_cansleep(ctx->reset, 1);  /* assert reset (pin LOW) */

	return 0;
}

static const struct drm_display_mode p028trm160_mode = {
	/*
	 * Horizontal: htotal must match INVSEL RTNI register (C2h param2).
	 * RTNI=6 => PCLK = 512 + (6 * 16) = 608.
	 * Vertical: VBP=12, VFP=2 from PORCTRL (C1h). VSYNC=10.
	 * Clock: 608 * 664 * 60 / 1000 = 24223 kHz
	 */
	.clock		= 24223,
	.hdisplay	= 480,
	.hsync_start	= 480 + 64,	/* hactive + hfp */
	.hsync_end	= 480 + 64 + 4,	/* hactive + hfp + hsync */
	.htotal		= 480 + 64 + 4 + 60,	/* hactive + hfp + hsync + hbp = 608 */
	.vdisplay	= 640,
	.vsync_start	= 640 + 2,	/* vactive + vfp */
	.vsync_end	= 640 + 2 + 10,	/* vactive + vfp + vsync */
	.vtotal		= 640 + 2 + 10 + 12,	/* vactive + vfp + vsync + vbp */
	.width_mm	= 43,
	.height_mm	= 57,
	.flags		= DRM_MODE_FLAG_PHSYNC | DRM_MODE_FLAG_PVSYNC,
	.type		= DRM_MODE_TYPE_DRIVER | DRM_MODE_TYPE_PREFERRED,
};

static int p028trm160_get_modes(struct drm_panel *panel,
				struct drm_connector *connector)
{
	struct drm_display_mode *mode;

	mode = drm_mode_duplicate(connector->dev, &p028trm160_mode);
	if (!mode)
		return -ENOMEM;

	drm_mode_set_name(mode);
	drm_mode_probed_add(connector, mode);

	connector->display_info.width_mm = mode->width_mm;
	connector->display_info.height_mm = mode->height_mm;

	return 1;
}

static const struct drm_panel_funcs p028trm160_funcs = {
	.prepare = p028trm160_prepare,
	.enable = p028trm160_enable,
	.disable = p028trm160_disable,
	.unprepare = p028trm160_unprepare,
	.get_modes = p028trm160_get_modes,
};

static int p028trm160_probe(struct mipi_dsi_device *dsi)
{
	struct device *dev = &dsi->dev;
	struct p028trm160_panel *ctx;
	int ret;

	ctx = devm_kzalloc(dev, sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	ctx->dsi = dsi;
	mipi_dsi_set_drvdata(dsi, ctx);

	dsi->mode_flags = MIPI_DSI_MODE_VIDEO | MIPI_DSI_MODE_VIDEO_BURST |
			  MIPI_DSI_MODE_LPM;
	dsi->format = MIPI_DSI_FMT_RGB888;
	dsi->lanes = 2;

	/* Check device tree for lane count override */
	of_property_read_u32(dev->of_node, "dsi-lanes", &dsi->lanes);

	ctx->reset = devm_gpiod_get_optional(dev, "reset", GPIOD_OUT_LOW);
	if (IS_ERR(ctx->reset)) {
		dev_err(dev, "Failed to get reset GPIO\n");
		return PTR_ERR(ctx->reset);
	}

	ctx->backlight_gpio = devm_gpiod_get_optional(dev, "backlight", GPIOD_OUT_LOW);
	if (IS_ERR(ctx->backlight_gpio)) {
		dev_err(dev, "Failed to get backlight GPIO\n");
		return PTR_ERR(ctx->backlight_gpio);
	}

	drm_panel_init(&ctx->panel, dev, &p028trm160_funcs,
		       DRM_MODE_CONNECTOR_DSI);
	ctx->panel.prepare_prev_first = true;

	ret = drm_panel_of_backlight(&ctx->panel);
	if (ret)
		return ret;

	drm_panel_add(&ctx->panel);

	ret = mipi_dsi_attach(dsi);
	if (ret < 0) {
		dev_err(dev, "Failed to attach DSI: %d\n", ret);
		drm_panel_remove(&ctx->panel);
		return ret;
	}

	dev_info(dev, "P028TRM160 2.8\" 480x640 DSI panel initialized\n");
	return 0;
}

static void p028trm160_remove(struct mipi_dsi_device *dsi)
{
	struct p028trm160_panel *ctx = mipi_dsi_get_drvdata(dsi);

	mipi_dsi_detach(dsi);
	drm_panel_remove(&ctx->panel);
}

static const struct of_device_id p028trm160_of_match[] = {
	{ .compatible = "pisugar,p028trm160" },
	{ /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, p028trm160_of_match);

static struct mipi_dsi_driver p028trm160_driver = {
	.probe = p028trm160_probe,
	.remove = p028trm160_remove,
	.driver = {
		.name = "panel-p028trm160",
		.of_match_table = p028trm160_of_match,
	},
};
module_mipi_dsi_driver(p028trm160_driver);

MODULE_AUTHOR("PiSugar");
MODULE_DESCRIPTION("P028TRM160 2.8\" 480x640 DSI Panel Driver (ST7701S)");
MODULE_LICENSE("GPL");
