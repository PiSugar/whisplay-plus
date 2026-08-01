// SPDX-License-Identifier: GPL-2.0
/*
 * PiSugar 2.8" ST7701P 480x640 MIPI-DSI panel driver
 *
 * Hardware:
 *   Panel:  ST7701P, 480x640, MIPI-DSI 2-lane, RGB888
 *   Touch:  GT911 on matching DSI I2C bus (handled by kernel goodix_ts)
 *
 * GPIO mapping (active-low accent):
 *   RST   -> GPIO4   (panel reset, active-low)
 *   LEDC  -> GPIO22  (backlight enable, active-low)
 *   TPRST -> GPIO6   (touch reset, active-low, in overlay)
 *   TPINT -> GPIO13  (touch interrupt, active-low)
 *
 * Init sequence from: "2.8TR+ST7701S MIPI 2Lane .txt"
 */

#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of_device.h>
#include <linux/regulator/consumer.h>
#include <linux/slab.h>
#include <linux/string.h>

#include <drm/drm_connector.h>
#include <drm/drm_mipi_dsi.h>
#include <drm/drm_modes.h>
#include <drm/drm_panel.h>

struct st7701p_panel {
	struct drm_panel panel;
	struct mipi_dsi_device *dsi;
	struct regulator *power;
	struct gpio_desc *reset_gpio;
	struct gpio_desc *ledc_gpio;
	struct mutex cmd_lock;
	bool prepared;
};

static char *video_mode = "burst";
module_param(video_mode, charp, 0644);
MODULE_PARM_DESC(video_mode, "DSI video mode: burst, sync-pulse, or sync-event");

static bool dsi_lpm = true;
module_param(dsi_lpm, bool, 0644);
MODULE_PARM_DESC(dsi_lpm, "Use low-power mode for DSI command transfers");

static bool dsi_non_continuous_clock = true;
module_param(dsi_non_continuous_clock, bool, 0644);
MODULE_PARM_DESC(dsi_non_continuous_clock, "Use non-continuous DSI clock");

static int mode_clock_khz = 26800;
module_param(mode_clock_khz, int, 0644);
MODULE_PARM_DESC(mode_clock_khz, "Preferred DRM mode pixel clock in kHz");

static int mode_hfp = 30;
module_param(mode_hfp, int, 0644);
MODULE_PARM_DESC(mode_hfp, "DRM mode horizontal front porch in pixels");

static int mode_hsync = 12;
module_param(mode_hsync, int, 0644);
MODULE_PARM_DESC(mode_hsync, "DRM mode horizontal sync width in pixels");

static int mode_hbp = 30;
module_param(mode_hbp, int, 0644);
MODULE_PARM_DESC(mode_hbp, "DRM mode horizontal back porch in pixels");

static int mode_vfp = 20;
module_param(mode_vfp, int, 0644);
MODULE_PARM_DESC(mode_vfp, "DRM mode vertical front porch in lines");

static int mode_vsync = 4;
module_param(mode_vsync, int, 0644);
MODULE_PARM_DESC(mode_vsync, "DRM mode vertical sync width in lines");

static int mode_vbp = 20;
module_param(mode_vbp, int, 0644);
MODULE_PARM_DESC(mode_vbp, "DRM mode vertical back porch in lines");

static char *pixel_format = "rgb888";
module_param(pixel_format, charp, 0644);
MODULE_PARM_DESC(pixel_format, "DSI pixel format: rgb888 or rgb666");

static char *gip_e5e8_variant = "vendor-tail-3ca0";
module_param(gip_e5e8_variant, charp, 0644);
MODULE_PARM_DESC(gip_e5e8_variant, "GIP E5/E8 mapping variant: rg28xx, vendor-tail-3ca0, or vendor");

static inline struct st7701p_panel *to_st7701p_panel(struct drm_panel *panel)
{
	return container_of(panel, struct st7701p_panel, panel);
}

static int st7701p_write_seq(struct st7701p_panel *ctx, const u8 *data, size_t len)
{
	ssize_t ret;

	ret = mipi_dsi_generic_write(ctx->dsi, data, len);
	if (ret < 0)
		return ret;

	return 0;
}

static ssize_t test_cmd_show(struct device *dev,
			     struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf,
			  "Write hex bytes to send a generic MIPI DSI command, e.g.:\n"
			  "  echo 'ff 77 01 00 00 10' > test_cmd\n"
			  "  echo 'b0 40 c3 96 0c 12 08 07 06 06 20 03 92 10 66 af 9d' > test_cmd\n");
}

static ssize_t test_cmd_store(struct device *dev, struct device_attribute *attr,
			      const char *buf, size_t count)
{
	struct st7701p_panel *ctx = dev_get_drvdata(dev);
	char *tmp, *p, *token;
	u8 data[64];
	int len = 0;
	int ret = 0;

	if (!ctx)
		return -ENODEV;

	tmp = kstrdup(buf, GFP_KERNEL);
	if (!tmp)
		return -ENOMEM;

	p = tmp;
	while ((token = strsep(&p, " \t\r\n,;")) != NULL) {
		unsigned int value;

		if (!*token)
			continue;
		if (len >= ARRAY_SIZE(data)) {
			ret = -E2BIG;
			goto out;
		}
		ret = kstrtouint(token, 16, &value);
		if (ret)
			goto out;
		if (value > 0xff) {
			ret = -ERANGE;
			goto out;
		}
		data[len++] = value;
	}

	if (!len) {
		ret = -EINVAL;
		goto out;
	}

	mutex_lock(&ctx->cmd_lock);
	ret = st7701p_write_seq(ctx, data, len);
	mutex_unlock(&ctx->cmd_lock);

out:
	kfree(tmp);
	return ret ? ret : count;
}

static DEVICE_ATTR_RW(test_cmd);

#define ST7701P_WRITE_SEQ(ctx, seq...) ({ \
	const u8 d[] = { seq }; \
	st7701p_write_seq((ctx), d, ARRAY_SIZE(d)); \
})

static int st7701p_init_sequence(struct st7701p_panel *ctx)
{
	int ret;
	u8 colmod = 0x77;

	if (!strcmp(pixel_format, "rgb666"))
		colmod = 0x66;

	ret = ST7701P_WRITE_SEQ(ctx, 0xFF, 0x77, 0x01, 0x00, 0x00, 0x13);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xEF, 0x08);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0x3A, colmod);
	if (ret)
		return ret;

	ret = ST7701P_WRITE_SEQ(ctx, 0xFF, 0x77, 0x01, 0x00, 0x00, 0x10);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xC0, 0x4F, 0x00);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xC1, 0x0C, 0x02);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xC2, 0x37, 0x06);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xB0, 0x40, 0xC3, 0x96, 0x0C, 0x12, 0x08, 0x07,
				  0x06, 0x06, 0x20, 0x03, 0x92, 0x10, 0x66, 0xAF, 0x9D);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xB1, 0x40, 0xC3, 0x95, 0x0E, 0x11, 0x07, 0x0A,
				  0x09, 0x09, 0x22, 0x05, 0x11, 0x0F, 0x28, 0xEE, 0xD9);
	if (ret)
		return ret;

	ret = ST7701P_WRITE_SEQ(ctx, 0xFF, 0x77, 0x01, 0x00, 0x00, 0x11);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xB0, 0x3D);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xB1, 0x7F);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xB2, 0x89);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xB3, 0x80);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xB5, 0x49);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xB7, 0x85);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xB8, 0x23);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xC1, 0x78);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xC2, 0x78);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xD0, 0x88);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xE0, 0x00, 0x00, 0x02);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xE1, 0x07, 0xC0, 0x07, 0xC0, 0x06, 0xC0, 0x06,
				  0xC0, 0x00, 0x24, 0x24);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xE2, 0x20, 0x20, 0x22, 0x22, 0x8D, 0xC0, 0x00,
				  0x00, 0x8C, 0xC0, 0x00, 0x00, 0x00);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xE3, 0x00, 0x00, 0x22, 0x22);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xE4, 0x44, 0x44);
	if (ret)
		return ret;
	if (!strcmp(gip_e5e8_variant, "rg28xx")) {
		ret = ST7701P_WRITE_SEQ(ctx, 0xE5, 0x0C, 0x90, 0xA0, 0xA0,
					  0x0E, 0x92, 0xA0, 0xA0,
					  0x08, 0x8C, 0xA0, 0xA0,
					  0x0A, 0x8E, 0xA0, 0xA0);
	} else if (!strcmp(gip_e5e8_variant, "vendor-tail-3ca0")) {
		ret = ST7701P_WRITE_SEQ(ctx, 0xE5, 0x09, 0x91, 0x3C, 0xA0,
					  0x0B, 0x93, 0x3C, 0xA0,
					  0x0D, 0x8D, 0x3C, 0xA0,
					  0x0F, 0x8F, 0x3C, 0xA0);
	} else {
		if (strcmp(gip_e5e8_variant, "vendor"))
			dev_warn(&ctx->dsi->dev, "unknown gip_e5e8_variant=%s, using vendor\n",
				 gip_e5e8_variant);
		ret = ST7701P_WRITE_SEQ(ctx, 0xE5, 0x09, 0x91, 0x10, 0xFA,
					  0x0B, 0x93, 0x10, 0xFA,
					  0x0D, 0x8D, 0x10, 0xFA,
					  0x0F, 0x8F, 0x10, 0xFA);
	}
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xE6, 0x00, 0x00, 0x22, 0x22);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xE7, 0x44, 0x44);
	if (ret)
		return ret;
	if (!strcmp(gip_e5e8_variant, "rg28xx")) {
		ret = ST7701P_WRITE_SEQ(ctx, 0xE8, 0x0D, 0x91, 0xA0, 0xA0,
					  0x0F, 0x93, 0xA0, 0xA0,
					  0x09, 0x8D, 0xA0, 0xA0,
					  0x0B, 0x8F, 0xA0, 0xA0);
	} else if (!strcmp(gip_e5e8_variant, "vendor-tail-3ca0")) {
		ret = ST7701P_WRITE_SEQ(ctx, 0xE8, 0x08, 0x90, 0x3C, 0xA0,
					  0x0A, 0x92, 0x3C, 0xA0,
					  0x0C, 0x8C, 0x3C, 0xA0,
					  0x0E, 0x8E, 0x3C, 0xA0);
	} else {
		ret = ST7701P_WRITE_SEQ(ctx, 0xE8, 0x08, 0x90, 0x10, 0xFA,
					  0x0A, 0x92, 0x10, 0xFA,
					  0x0C, 0x8C, 0x10, 0xFA,
					  0x0E, 0x8E, 0x10, 0xFA);
	}
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xEB, 0x02, 0x01, 0xE4, 0xE4, 0x88, 0x00, 0x00);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xED, 0xFF, 0x04, 0x56, 0x7F, 0xBA, 0x2F, 0xFF,
				  0xFF, 0xFF, 0xFF, 0xF2, 0xAB, 0xF7, 0x65, 0x40, 0xFF);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xEF, 0x08, 0x08, 0x08, 0x45, 0x3F, 0x54);
	if (ret)
		return ret;
	ret = ST7701P_WRITE_SEQ(ctx, 0xFF, 0x77, 0x01, 0x00, 0x00, 0x00);
	if (ret)
		return ret;

	ret = mipi_dsi_dcs_exit_sleep_mode(ctx->dsi);
	if (ret)
		return ret;
	msleep(120);

	ret = mipi_dsi_dcs_set_display_on(ctx->dsi);
	if (ret)
		return ret;
	msleep(20);

	return 0;
}

static int st7701p_prepare(struct drm_panel *panel)
{
	struct st7701p_panel *ctx = to_st7701p_panel(panel);
	int ret;

	if (ctx->prepared)
		return 0;

	if (ctx->power) {
		ret = regulator_enable(ctx->power);
		if (ret)
			return ret;
	}

	if (ctx->reset_gpio) {
		gpiod_set_value_cansleep(ctx->reset_gpio, 1);
		usleep_range(10000, 12000);
		gpiod_set_value_cansleep(ctx->reset_gpio, 0);
		usleep_range(20000, 25000);
	}

	ret = st7701p_init_sequence(ctx);
	if (ret) {
		dev_err(&ctx->dsi->dev, "init_sequence failed: %d\n", ret);
		goto err_power;
	}
	dev_info(&ctx->dsi->dev, "init_sequence done, display on\n");

	if (ctx->ledc_gpio)
		gpiod_set_value_cansleep(ctx->ledc_gpio, 1);

	ctx->prepared = true;
	return 0;

err_power:
	if (ctx->power)
		regulator_disable(ctx->power);
	return ret;
}

static int st7701p_unprepare(struct drm_panel *panel)
{
	struct st7701p_panel *ctx = to_st7701p_panel(panel);

	if (!ctx->prepared)
		return 0;

	if (ctx->ledc_gpio)
		gpiod_set_value_cansleep(ctx->ledc_gpio, 0);

	mipi_dsi_dcs_set_display_off(ctx->dsi);
	mipi_dsi_dcs_enter_sleep_mode(ctx->dsi);
	msleep(80);

	if (ctx->reset_gpio)
		gpiod_set_value_cansleep(ctx->reset_gpio, 1);

	if (ctx->power)
		regulator_disable(ctx->power);

	ctx->prepared = false;
	return 0;
}

static int st7701p_get_modes(struct drm_panel *panel,
				    struct drm_connector *connector)
{
	struct st7701p_panel *ctx = to_st7701p_panel(panel);
	const struct drm_display_mode mode = {
		.clock = mode_clock_khz,
		.hdisplay = 480,
		.hsync_start = 480 + mode_hfp,
		.hsync_end = 480 + mode_hfp + mode_hsync,
		.htotal = 480 + mode_hfp + mode_hsync + mode_hbp,
		.vdisplay = 640,
		.vsync_start = 640 + mode_vfp,
		.vsync_end = 640 + mode_vfp + mode_vsync,
		.vtotal = 640 + mode_vfp + mode_vsync + mode_vbp,
		.flags = DRM_MODE_FLAG_NHSYNC | DRM_MODE_FLAG_NVSYNC,
		.type = DRM_MODE_TYPE_DRIVER | DRM_MODE_TYPE_PREFERRED,
	};
	struct drm_display_mode *m;

	m = drm_mode_duplicate(connector->dev, &mode);
	if (!m)
		return -ENOMEM;

	drm_mode_set_name(m);
	drm_mode_probed_add(connector, m);

	connector->display_info.width_mm = 43;
	connector->display_info.height_mm = 57;
	connector->display_info.bpc = 8;
	dev_info(&ctx->dsi->dev, "mode clock=%d kHz htotal=%d vtotal=%d hporch=%d/%d/%d vporch=%d/%d/%d\n",
		 mode_clock_khz, mode.htotal, mode.vtotal,
		 mode_hfp, mode_hsync, mode_hbp,
		 mode_vfp, mode_vsync, mode_vbp);

	return 1;
}

static const struct drm_panel_funcs st7701p_panel_funcs = {
	.prepare = st7701p_prepare,
	.unprepare = st7701p_unprepare,
	.get_modes = st7701p_get_modes,
};

static int st7701p_probe(struct mipi_dsi_device *dsi)
{
	struct device *dev = &dsi->dev;
	struct st7701p_panel *ctx;
	unsigned long mode_flags;
	int ret;

	ctx = devm_kzalloc(dev, sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	ctx->dsi = dsi;
	mutex_init(&ctx->cmd_lock);
	ctx->power = devm_regulator_get_optional(dev, "power");
	if (IS_ERR(ctx->power)) {
		if (PTR_ERR(ctx->power) == -ENODEV)
			ctx->power = NULL;
		else
			return PTR_ERR(ctx->power);
	}

	ctx->reset_gpio = devm_gpiod_get_optional(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(ctx->reset_gpio))
		return PTR_ERR(ctx->reset_gpio);

	ctx->ledc_gpio = devm_gpiod_get_optional(dev, "ledc", GPIOD_OUT_LOW);
	if (IS_ERR(ctx->ledc_gpio))
		return PTR_ERR(ctx->ledc_gpio);

	mode_flags = MIPI_DSI_MODE_VIDEO;
	if (!strcmp(video_mode, "burst")) {
		mode_flags |= MIPI_DSI_MODE_VIDEO_BURST;
	} else if (!strcmp(video_mode, "sync-pulse")) {
		mode_flags |= MIPI_DSI_MODE_VIDEO_SYNC_PULSE;
	} else if (!strcmp(video_mode, "sync-event")) {
		/* Plain VIDEO mode is sync-event on DSI hosts that distinguish it. */
	} else {
		dev_warn(dev, "unknown video_mode=%s, using burst\n", video_mode);
		mode_flags |= MIPI_DSI_MODE_VIDEO_BURST;
	}
	if (dsi_lpm)
		mode_flags |= MIPI_DSI_MODE_LPM;
	if (dsi_non_continuous_clock)
		mode_flags |= MIPI_DSI_CLOCK_NON_CONTINUOUS;

	dsi->mode_flags = mode_flags;
	if (!strcmp(pixel_format, "rgb666")) {
		dsi->format = MIPI_DSI_FMT_RGB666;
	} else if (!strcmp(pixel_format, "rgb888")) {
		dsi->format = MIPI_DSI_FMT_RGB888;
	} else {
		dev_warn(dev, "unknown pixel_format=%s, using rgb888\n", pixel_format);
		dsi->format = MIPI_DSI_FMT_RGB888;
	}
	dsi->lanes = 2;
	dev_info(dev, "DSI flags: video_mode=%s pixel_format=%s lpm=%d non_continuous_clock=%d flags=0x%lx format=%u\n",
		 video_mode, pixel_format, dsi_lpm, dsi_non_continuous_clock,
		 mode_flags, dsi->format);
	dev_info(dev, "GIP E5/E8 variant: %s\n", gip_e5e8_variant);

	drm_panel_init(&ctx->panel, dev, &st7701p_panel_funcs,
		       DRM_MODE_CONNECTOR_DSI);
	ctx->panel.prepare_prev_first = true;

	drm_panel_add(&ctx->panel);
	mipi_dsi_set_drvdata(dsi, ctx);

	ret = mipi_dsi_attach(dsi);
	if (ret) {
		drm_panel_remove(&ctx->panel);
		return ret;
	}

	ret = device_create_file(dev, &dev_attr_test_cmd);
	if (ret) {
		mipi_dsi_detach(dsi);
		drm_panel_remove(&ctx->panel);
		return ret;
	}

	return 0;
}

static void st7701p_shutdown(struct mipi_dsi_device *dsi)
{
	struct st7701p_panel *ctx = mipi_dsi_get_drvdata(dsi);

	if (!ctx)
		return;

	st7701p_unprepare(&ctx->panel);
}

static void st7701p_remove(struct mipi_dsi_device *dsi)
{
	struct st7701p_panel *ctx = mipi_dsi_get_drvdata(dsi);
	int ret;

	ret = mipi_dsi_detach(dsi);
	if (ret)
		dev_warn(&dsi->dev, "failed to detach from DSI host: %d\n", ret);

	device_remove_file(&dsi->dev, &dev_attr_test_cmd);
	drm_panel_remove(&ctx->panel);
}

static const struct of_device_id st7701p_of_match[] = {
	{ .compatible = "pisugar,st7701p-480x640" },
	{ }
};
MODULE_DEVICE_TABLE(of, st7701p_of_match);

static struct mipi_dsi_driver st7701p_driver = {
	.probe = st7701p_probe,
	.remove = st7701p_remove,
	.shutdown = st7701p_shutdown,
	.driver = {
		.name = "panel-st7701p-pisugar",
		.of_match_table = st7701p_of_match,
	},
};
module_mipi_dsi_driver(st7701p_driver);

MODULE_AUTHOR("PiSugar");
MODULE_DESCRIPTION("PiSugar 2.8\" ST7701P 480x640 MIPI-DSI panel driver");
MODULE_LICENSE("GPL");
