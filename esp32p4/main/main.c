/*
 * Whisplay Plus bring-up for the Waveshare ESP32-P4-Module-DEV-KIT.
 *
 * This intentionally stays below LVGL: it verifies the HAT GPIO mapping,
 * scans the shared I2C bus, reads GT911 touch reports, exercises the button
 * and RGB LED, and asks the DSI peripheral to generate color bars.
 */

#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_ldo_regulator.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#include "demo_ui.h"
#include "whisplay_audio.h"
#include "whisplay_sensors.h"

#define LCD_H_RES 480
#define LCD_V_RES 640
#define LCD_DPI_CLOCK_MHZ 24
#define LCD_DSI_LANES 2
#define LCD_DPHY_LDO_CHANNEL 3
#define LCD_DPHY_VOLTAGE_MV 2500
#define GT911_I2C_ADDRESS 0x5d
#define GT911_PRODUCT_ID_REGISTER 0x8140
#define GT911_COMMAND_REGISTER 0x8040
#define GT911_CONFIG_VERSION_REGISTER 0x8047
#define GT911_CONFIG_BYTES 184
#define GT911_CONFIG_CHECKSUM_REGISTER 0x80ff
#define GT911_CONFIG_REFRESH_REGISTER 0x8100
#define GT911_SENSOR_ID_REGISTER 0x814a
#define GT911_STATUS_REGISTER 0x814e
#define GT911_POINTS_REGISTER 0x814f
#define GT911_MAX_TOUCHES 5

static const char *TAG = "whisplay-p4";
static volatile uint32_t display_frame_sequence;

static bool IRAM_ATTR display_frame_complete(esp_lcd_panel_handle_t panel,
                                             esp_lcd_dpi_panel_event_data_t *event,
                                             void *user_ctx)
{
    (void)panel;
    (void)event;
    (void)user_ctx;
    ++display_frame_sequence;
    return false;
}

typedef struct {
    uint8_t command;
    uint8_t data[16];
    uint8_t data_size;
    uint16_t delay_ms;
} panel_command_t;

#define CMD(_command, _delay, ...)                                            \
    {                                                                         \
        .command = (_command), .data = {__VA_ARGS__},                         \
        .data_size = sizeof((uint8_t[]){__VA_ARGS__}), .delay_ms = (_delay),  \
    }

/* Same known-good vendor-tail-3ca0 sequence used by the Raspberry Pi driver. */
static const panel_command_t panel_init_commands[] = {
    CMD(0xFF, 0, 0x77, 0x01, 0x00, 0x00, 0x13),
    CMD(0xEF, 0, 0x08),
    CMD(0x3A, 0, 0x77),
    CMD(0xFF, 0, 0x77, 0x01, 0x00, 0x00, 0x10),
    CMD(0xC0, 0, 0x4F, 0x00),
    CMD(0xC1, 0, 0x0C, 0x02),
    CMD(0xC2, 0, 0x37, 0x06),
    CMD(0xB0, 0, 0x40, 0xC3, 0x96, 0x0C, 0x12, 0x08, 0x07, 0x06, 0x06, 0x20, 0x03, 0x92, 0x10, 0x66, 0xAF, 0x9D),
    CMD(0xB1, 0, 0x40, 0xC3, 0x95, 0x0E, 0x11, 0x07, 0x0A, 0x09, 0x09, 0x22, 0x05, 0x11, 0x0F, 0x28, 0xEE, 0xD9),
    CMD(0xFF, 0, 0x77, 0x01, 0x00, 0x00, 0x11),
    CMD(0xB0, 0, 0x3D),
    CMD(0xB1, 0, 0x7F),
    CMD(0xB2, 0, 0x89),
    CMD(0xB3, 0, 0x80),
    CMD(0xB5, 0, 0x49),
    CMD(0xB7, 0, 0x85),
    CMD(0xB8, 0, 0x23),
    CMD(0xC1, 0, 0x78),
    CMD(0xC2, 0, 0x78),
    CMD(0xD0, 0, 0x88),
    CMD(0xE0, 0, 0x00, 0x00, 0x02),
    CMD(0xE1, 0, 0x07, 0xC0, 0x07, 0xC0, 0x06, 0xC0, 0x06, 0xC0, 0x00, 0x24, 0x24),
    CMD(0xE2, 0, 0x20, 0x20, 0x22, 0x22, 0x8D, 0xC0, 0x00, 0x00, 0x8C, 0xC0, 0x00, 0x00, 0x00),
    CMD(0xE3, 0, 0x00, 0x00, 0x22, 0x22),
    CMD(0xE4, 0, 0x44, 0x44),
    CMD(0xE5, 0, 0x09, 0x91, 0x3C, 0xA0, 0x0B, 0x93, 0x3C, 0xA0, 0x0D, 0x8D, 0x3C, 0xA0, 0x0F, 0x8F, 0x3C, 0xA0),
    CMD(0xE6, 0, 0x00, 0x00, 0x22, 0x22),
    CMD(0xE7, 0, 0x44, 0x44),
    CMD(0xE8, 0, 0x08, 0x90, 0x3C, 0xA0, 0x0A, 0x92, 0x3C, 0xA0, 0x0C, 0x8C, 0x3C, 0xA0, 0x0E, 0x8E, 0x3C, 0xA0),
    CMD(0xEB, 0, 0x02, 0x01, 0xE4, 0xE4, 0x88, 0x00, 0x00),
    CMD(0xED, 0, 0xFF, 0x04, 0x56, 0x7F, 0xBA, 0x2F, 0xFF, 0xFF, 0xFF, 0xFF, 0xF2, 0xAB, 0xF7, 0x65, 0x40, 0xFF),
    CMD(0xEF, 0, 0x08, 0x08, 0x08, 0x45, 0x3F, 0x54),
    CMD(0xFF, 0, 0x77, 0x01, 0x00, 0x00, 0x00),
    CMD(0x11, 120),
    CMD(0x29, 20),
};

static esp_err_t configure_output(gpio_num_t pin, int level)
{
    const gpio_config_t config = {
        .pin_bit_mask = 1ULL << pin,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&config), TAG, "configure GPIO %d", pin);
    return gpio_set_level(pin, level);
}

static esp_err_t reset_touch_controller(void)
{
    const gpio_num_t reset = CONFIG_WHISPLAY_TOUCH_RESET_GPIO;
    const gpio_num_t interrupt = CONFIG_WHISPLAY_TOUCH_INT_GPIO;

    ESP_RETURN_ON_ERROR(configure_output(interrupt, 0), TAG, "configure touch INT");
    ESP_RETURN_ON_ERROR(configure_output(reset, 0), TAG, "assert touch reset");
    vTaskDelay(pdMS_TO_TICKS(20));
    ESP_RETURN_ON_ERROR(gpio_set_level(reset, 1), TAG, "release touch reset");
    // GT911 samples INT while reset is released.  Keeping it low selects the
    // 0x5d address; it must stay driven for at least 5 ms afterwards.
    vTaskDelay(pdMS_TO_TICKS(10));

    const gpio_config_t input_config = {
        .pin_bit_mask = 1ULL << interrupt,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_NEGEDGE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&input_config), TAG, "release touch INT");
    // The controller needs 50 ms after INT is released before register access.
    vTaskDelay(pdMS_TO_TICKS(60));
    return ESP_OK;
}

static esp_err_t scan_i2c(i2c_master_bus_handle_t *ret_bus)
{
    const i2c_master_bus_config_t config = {
        .i2c_port = 0,
        .sda_io_num = CONFIG_WHISPLAY_I2C_SDA_GPIO,
        .scl_io_num = CONFIG_WHISPLAY_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus = NULL;
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&config, &bus), TAG, "create I2C bus");

    unsigned int found = 0;
    for (uint8_t address = 1; address < 0x7f; ++address) {
        if (i2c_master_probe(bus, address, 20) == ESP_OK) {
            ESP_LOGI(TAG, "I2C device found at 0x%02x", address);
            ++found;
        }
    }
    ESP_LOGI(TAG, "I2C scan complete: %u device(s)", found);
    *ret_bus = bus;
    return ESP_OK;
}

static esp_err_t gt911_read(i2c_master_dev_handle_t device, uint16_t reg, void *data, size_t size)
{
    const uint8_t address[] = {(uint8_t)(reg >> 8), (uint8_t)reg};
    return i2c_master_transmit_receive(device, address, sizeof(address), data, size, 100);
}

static esp_err_t gt911_write_u8(i2c_master_dev_handle_t device, uint16_t reg, uint8_t value)
{
    const uint8_t transaction[] = {(uint8_t)(reg >> 8), (uint8_t)reg, value};
    return i2c_master_transmit(device, transaction, sizeof(transaction), 100);
}

static esp_err_t gt911_write(i2c_master_dev_handle_t device, uint16_t reg,
                             const uint8_t *data, size_t size)
{
    if (size > GT911_CONFIG_BYTES) {
        return ESP_ERR_INVALID_SIZE;
    }
    uint8_t transaction[2 + GT911_CONFIG_BYTES];
    transaction[0] = (uint8_t)(reg >> 8);
    transaction[1] = (uint8_t)reg;
    for (size_t i = 0; i < size; ++i) {
        transaction[2 + i] = data[i];
    }
    return i2c_master_transmit(device, transaction, size + 2, 100);
}

static esp_err_t initialize_touch(i2c_master_bus_handle_t bus, i2c_master_dev_handle_t *ret_device)
{
    const i2c_device_config_t config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = GT911_I2C_ADDRESS,
        .scl_speed_hz = 400000,
    };
    i2c_master_dev_handle_t device = NULL;
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(bus, &config, &device), TAG, "add GT911");

    uint8_t product_id[4] = {0};
    ESP_RETURN_ON_ERROR(gt911_read(device, GT911_PRODUCT_ID_REGISTER, product_id, sizeof(product_id)),
                        TAG, "read GT911 product ID");
    ESP_LOGI(TAG, "GT911 product ID: %c%c%c%c", product_id[0], product_id[1], product_id[2], product_id[3]);

    // Use the GT911 wake sequence from the vendor driver: INT high for at
    // least 5 ms, then release it back to an input. Merely writing command 0
    // does not wake a controller which entered sleep via command 5.
    ESP_RETURN_ON_ERROR(configure_output(CONFIG_WHISPLAY_TOUCH_INT_GPIO, 1),
                        TAG, "drive GT911 wake pulse");
    vTaskDelay(pdMS_TO_TICKS(6));
    const gpio_config_t interrupt_input = {
        .pin_bit_mask = 1ULL << CONFIG_WHISPLAY_TOUCH_INT_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_NEGEDGE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&interrupt_input), TAG, "release GT911 wake pulse");
    vTaskDelay(pdMS_TO_TICKS(10));

    // Explicitly select normal operation as well, for warm-reset robustness.
    ESP_RETURN_ON_ERROR(gt911_write_u8(device, GT911_COMMAND_REGISTER, 0), TAG,
                        "wake GT911");
    vTaskDelay(pdMS_TO_TICKS(10));

    uint8_t config_bytes[5] = {0};
    ESP_RETURN_ON_ERROR(gt911_read(device, GT911_CONFIG_VERSION_REGISTER,
                                   config_bytes, sizeof(config_bytes)), TAG,
                        "read GT911 geometry");
    const uint16_t configured_x = config_bytes[1] | ((uint16_t)config_bytes[2] << 8);
    const uint16_t configured_y = config_bytes[3] | ((uint16_t)config_bytes[4] << 8);
    ESP_LOGI(TAG, "GT911 config v%u geometry=%ux%u INT=%d", config_bytes[0],
             configured_x, configured_y,
             gpio_get_level(CONFIG_WHISPLAY_TOUCH_INT_GPIO));

    uint8_t full_config[GT911_CONFIG_BYTES] = {0};
    uint8_t config_tail[2] = {0};
    uint8_t sensor_id = 0;
    ESP_RETURN_ON_ERROR(gt911_read(device, GT911_CONFIG_VERSION_REGISTER,
                                   full_config, sizeof(full_config)), TAG,
                        "read complete GT911 config");
    ESP_RETURN_ON_ERROR(gt911_read(device, GT911_CONFIG_CHECKSUM_REGISTER,
                                   config_tail, sizeof(config_tail)), TAG,
                        "read GT911 config tail");
    ESP_RETURN_ON_ERROR(gt911_read(device, GT911_SENSOR_ID_REGISTER,
                                   &sensor_id, sizeof(sensor_id)), TAG,
                        "read GT911 sensor ID");
    uint8_t config_sum = config_tail[0];
    for (size_t i = 0; i < sizeof(full_config); ++i) {
        config_sum = (uint8_t)(config_sum + full_config[i]);
    }
    ESP_LOGI(TAG,
             "GT911 config checksum=%s stored=0x%02x refresh=%u sensor-id=%u touch-max=%u module-switch=0x%02x",
             config_sum == 0 ? "OK" : "BAD", config_tail[0], config_tail[1],
             sensor_id & 0x07, full_config[5] & 0x0f, full_config[6]);

    // GT911 supports at most five contacts. This panel arrived with a valid
    // checksum but an impossible value of 10 in CFG_MAX_TOUCH_POINTS, which
    // leaves the scan engine alive on I2C yet prevents it from publishing
    // contacts. Repair only that field and use the documented checksum/fresh
    // handshake; subsequent boots do not rewrite an already-valid config.
    const uint8_t configured_touch_max = full_config[5] & 0x0f;
    if (configured_touch_max == 0 || configured_touch_max > GT911_MAX_TOUCHES) {
        ESP_LOGW(TAG, "GT911 invalid max contacts %u; repairing to %u",
                 configured_touch_max, GT911_MAX_TOUCHES);
        full_config[5] = (full_config[5] & 0xf0) | GT911_MAX_TOUCHES;
        uint8_t sum = 0;
        for (size_t i = 0; i < sizeof(full_config); ++i) {
            sum = (uint8_t)(sum + full_config[i]);
        }
        const uint8_t checksum = (uint8_t)(~sum + 1U);
        ESP_RETURN_ON_ERROR(gt911_write(device, GT911_CONFIG_VERSION_REGISTER,
                                        full_config, sizeof(full_config)), TAG,
                            "write repaired GT911 config");
        ESP_RETURN_ON_ERROR(gt911_write_u8(device, GT911_CONFIG_CHECKSUM_REGISTER,
                                           checksum), TAG,
                            "write repaired GT911 checksum");
        ESP_RETURN_ON_ERROR(gt911_write_u8(device, GT911_CONFIG_REFRESH_REGISTER, 1),
                            TAG, "refresh repaired GT911 config");
        vTaskDelay(pdMS_TO_TICKS(120));
        ESP_LOGI(TAG, "GT911 contact configuration repaired and refreshed");
    }

    // Discard a stale report which may have survived the address-select reset.
    ESP_RETURN_ON_ERROR(gt911_write_u8(device, GT911_STATUS_REGISTER, 0), TAG, "clear GT911 status");
    *ret_device = device;
    return ESP_OK;
}

static void poll_touch(i2c_master_dev_handle_t device, demo_state_t *state)
{
    static TickType_t last_contact_report;
    uint8_t status = 0;
    esp_err_t err = gt911_read(device, GT911_STATUS_REGISTER, &status, sizeof(status));
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "GT911 status read failed: %s", esp_err_to_name(err));
        return;
    }
    const TickType_t now = xTaskGetTickCount();
    if ((status & 0x80) == 0) {
        // GT911 release reports can be very short-lived. Avoid leaving the UI
        // permanently in a pressed state if polling misses that one report.
        if (state->touch_count > 0 &&
            now - last_contact_report >= pdMS_TO_TICKS(80)) {
            state->touch_count = 0;
        }
        // Match the vendor driver: acknowledge every poll, including a
        // not-ready report. This prevents a zero-status transaction from
        // blocking the next scan on some GT911 firmware revisions.
        err = gt911_write_u8(device, GT911_STATUS_REGISTER, 0);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "GT911 idle status clear failed: %s", esp_err_to_name(err));
        }
        return;
    }

    const uint8_t count = status & 0x0f;
    state->touch_count = 0;
    if (count > GT911_MAX_TOUCHES) {
        ESP_LOGW(TAG, "GT911 invalid touch count: %u", count);
    } else if (count > 0) {
        uint8_t points[GT911_MAX_TOUCHES * 8] = {0};
        err = gt911_read(device, GT911_POINTS_REGISTER, points, count * 8);
        if (err == ESP_OK) {
            for (uint8_t i = 0; i < count; ++i) {
                const uint8_t *point = &points[i * 8];
                const uint16_t raw_x = point[1] | ((uint16_t)point[2] << 8);
                const uint16_t raw_y = point[3] | ((uint16_t)point[4] << 8);
                const uint16_t size = point[5] | ((uint16_t)point[6] << 8);
                // Match the established Raspberry Pi device-tree orientation.
                const uint16_t x = raw_x < LCD_H_RES ? LCD_H_RES - 1 - raw_x : raw_x;
                const uint16_t y = raw_y < LCD_V_RES ? LCD_V_RES - 1 - raw_y : raw_y;
                state->touches[i].id = point[0] & 0x0f;
                state->touches[i].x = x;
                state->touches[i].y = y;
                state->touches[i].raw_x = raw_x;
                state->touches[i].raw_y = raw_y;
                ESP_LOGD(TAG, "touch[%u] id=%u x=%u y=%u raw=%u,%u size=%u",
                         i, state->touches[i].id, x, y, raw_x, raw_y, size);
            }
            state->touch_count = count;
            last_contact_report = xTaskGetTickCount();
            ++state->touch_events;
        } else {
            ESP_LOGW(TAG, "GT911 point read failed: %s", esp_err_to_name(err));
        }
    }

    err = gt911_write_u8(device, GT911_STATUS_REGISTER, 0);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "GT911 status clear failed: %s", esp_err_to_name(err));
    }
}

static esp_err_t initialize_controls(void)
{
    ESP_RETURN_ON_ERROR(configure_output(CONFIG_WHISPLAY_RGB_RED_GPIO, 1), TAG, "configure red LED");
    ESP_RETURN_ON_ERROR(configure_output(CONFIG_WHISPLAY_RGB_GREEN_GPIO, 1), TAG, "configure green LED");
    ESP_RETURN_ON_ERROR(configure_output(CONFIG_WHISPLAY_RGB_BLUE_GPIO, 1), TAG, "configure blue LED");

    const gpio_config_t button_config = {
        .pin_bit_mask = 1ULL << CONFIG_WHISPLAY_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    return gpio_config(&button_config);
}

static void set_rgb(unsigned int step)
{
    // The HAT RGB LED is active-low. Steps are off, red, green and blue.
    gpio_set_level(CONFIG_WHISPLAY_RGB_RED_GPIO, step == 1 ? 0 : 1);
    gpio_set_level(CONFIG_WHISPLAY_RGB_GREEN_GPIO, step == 2 ? 0 : 1);
    gpio_set_level(CONFIG_WHISPLAY_RGB_BLUE_GPIO, step == 3 ? 0 : 1);
}

static esp_err_t initialize_display(esp_lcd_panel_handle_t *ret_panel,
                                    uint8_t **ret_framebuffer0,
                                    uint8_t **ret_framebuffer1)
{
    ESP_RETURN_ON_ERROR(configure_output(CONFIG_WHISPLAY_BACKLIGHT_GPIO, 0), TAG, "backlight off");
    ESP_RETURN_ON_ERROR(configure_output(CONFIG_WHISPLAY_PANEL_RESET_GPIO, 0), TAG, "assert panel reset");
    vTaskDelay(pdMS_TO_TICKS(12));
    ESP_RETURN_ON_ERROR(gpio_set_level(CONFIG_WHISPLAY_PANEL_RESET_GPIO, 1), TAG, "release panel reset");
    vTaskDelay(pdMS_TO_TICKS(25));

    static esp_ldo_channel_handle_t dphy_power = NULL;
    const esp_ldo_channel_config_t ldo_config = {
        .chan_id = LCD_DPHY_LDO_CHANNEL,
        .voltage_mv = LCD_DPHY_VOLTAGE_MV,
    };
    ESP_RETURN_ON_ERROR(esp_ldo_acquire_channel(&ldo_config, &dphy_power), TAG, "power DSI PHY");

    esp_lcd_dsi_bus_handle_t dsi_bus = NULL;
    const esp_lcd_dsi_bus_config_t bus_config = {
        .bus_id = 0,
        .num_data_lanes = LCD_DSI_LANES,
        // Match the Waveshare ESP32-P4 BSP.  On ECO7 silicon the IDF
        // "default" currently selects PLL_F20M, which the DSI LL rejects.
        .phy_clk_src = 0,
        .lane_bit_rate_mbps = CONFIG_WHISPLAY_DSI_LANE_BITRATE_MBPS,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_dsi_bus(&bus_config, &dsi_bus), TAG, "create DSI bus");

    esp_lcd_panel_io_handle_t io = NULL;
    const esp_lcd_dbi_io_config_t io_config = {
        .virtual_channel = 0,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_dbi(dsi_bus, &io_config, &io), TAG, "create DBI command IO");

    const esp_lcd_dpi_panel_config_t dpi_config = {
        .dpi_clk_src = MIPI_DSI_DPI_CLK_SRC_DEFAULT,
        .dpi_clock_freq_mhz = LCD_DPI_CLOCK_MHZ,
        .virtual_channel = 0,
        .pixel_format = LCD_COLOR_PIXEL_FORMAT_RGB888,
        .num_fbs = 2,
        .video_timing = {
            .h_size = LCD_H_RES,
            .v_size = LCD_V_RES,
            .hsync_back_porch = 60,
            .hsync_pulse_width = 20,
            .hsync_front_porch = 60,
            .vsync_back_porch = 20,
            .vsync_pulse_width = 4,
            .vsync_front_porch = 20,
        },
        .flags.use_dma2d = true,
    };
    esp_lcd_panel_handle_t panel = NULL;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_dpi(dsi_bus, &dpi_config, &panel), TAG, "create DPI panel");

    for (size_t i = 0; i < sizeof(panel_init_commands) / sizeof(panel_init_commands[0]); ++i) {
        const panel_command_t *command = &panel_init_commands[i];
        ESP_RETURN_ON_ERROR(
            esp_lcd_panel_io_tx_param(io, command->command, command->data, command->data_size),
            TAG, "send panel command 0x%02x", command->command);
        if (command->delay_ms != 0) {
            vTaskDelay(pdMS_TO_TICKS(command->delay_ms));
        }
    }

    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(panel), TAG, "initialize DPI panel");
    const esp_lcd_dpi_panel_event_callbacks_t callbacks = {
        .on_frame_buf_complete = display_frame_complete,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_dpi_panel_register_event_callbacks(panel, &callbacks, NULL),
                        TAG, "register DPI callbacks");
    ESP_RETURN_ON_ERROR(esp_lcd_dpi_panel_get_frame_buffer(
                            panel, 2, (void **)ret_framebuffer0, (void **)ret_framebuffer1),
                        TAG, "get DPI framebuffers");
    ESP_RETURN_ON_ERROR(gpio_set_level(CONFIG_WHISPLAY_BACKLIGHT_GPIO, 1), TAG, "backlight on");
    *ret_panel = panel;
    return ESP_OK;
}

static esp_err_t display_present(esp_lcd_panel_handle_t panel, uint8_t *framebuffer)
{
    // draw_bitmap() selects this internal framebuffer without copying. Wait
    // through two frame-complete events so the old front buffer is definitely
    // no longer being scanned, even if an ISR lands between the sequence read
    // and the buffer selection below.
    const uint32_t sequence_before_draw = display_frame_sequence;
    ESP_RETURN_ON_ERROR(esp_lcd_panel_draw_bitmap(panel, 0, 0, LCD_H_RES, LCD_V_RES,
                                                  framebuffer),
                        TAG, "select DPI framebuffer");
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(100);
    while ((uint32_t)(display_frame_sequence - sequence_before_draw) < 2) {
        if ((int32_t)(xTaskGetTickCount() - deadline) >= 0) {
            return ESP_ERR_TIMEOUT;
        }
        vTaskDelay(1);
    }
    return ESP_OK;
}

void app_main(void)
{
    ESP_LOGI(TAG, "Whisplay Plus / Waveshare ESP32-P4 bring-up");
    ESP_LOGI(TAG, "GPIO: reset=%d backlight=%d touch-reset=%d touch-int=%d I2C=%d/%d",
             CONFIG_WHISPLAY_PANEL_RESET_GPIO, CONFIG_WHISPLAY_BACKLIGHT_GPIO,
             CONFIG_WHISPLAY_TOUCH_RESET_GPIO, CONFIG_WHISPLAY_TOUCH_INT_GPIO,
             CONFIG_WHISPLAY_I2C_SDA_GPIO, CONFIG_WHISPLAY_I2C_SCL_GPIO);

    ESP_ERROR_CHECK(reset_touch_controller());
    i2c_master_bus_handle_t i2c_bus = NULL;
    ESP_ERROR_CHECK(scan_i2c(&i2c_bus));
    i2c_master_dev_handle_t touch = NULL;
    ESP_ERROR_CHECK(initialize_touch(i2c_bus, &touch));
    ESP_ERROR_CHECK(initialize_controls());

    whisplay_sensors_t sensors;
    const bool imu_ok = whisplay_sensors_init(i2c_bus, &sensors) == ESP_OK;
    whisplay_audio_t audio;
    bool audio_ok = whisplay_audio_init(i2c_bus, &audio) == ESP_OK;
    if (audio_ok) {
        ESP_LOGI(TAG, "probing onboard ES8311 microphone before returning to default ES8389");
        audio_ok = whisplay_audio_set_onboard_codec(&audio, true) == ESP_OK;
        uint16_t onboard_peak = 0;
        if (audio_ok) {
            vTaskDelay(pdMS_TO_TICKS(300));
            audio.last_mic_raw_peak = 0;
            for (int i = 0; i < 20; ++i) {
                whisplay_audio_read_mic_level(&audio);
                if (audio.last_mic_raw_peak > onboard_peak) {
                    onboard_peak = audio.last_mic_raw_peak;
                }
            }
            ESP_LOGI(TAG, "onboard ES8311 microphone probe raw peak=%u", onboard_peak);
            audio_ok = whisplay_audio_set_onboard_codec(&audio, false) == ESP_OK;
        }
    }

    esp_lcd_panel_handle_t panel = NULL;
    uint8_t *framebuffers[2] = {NULL, NULL};
    ESP_ERROR_CHECK(initialize_display(&panel, &framebuffers[0], &framebuffers[1]));
    ESP_ERROR_CHECK(esp_lcd_dpi_panel_set_pattern(panel, MIPI_DSI_PATTERN_NONE));

    demo_state_t state = {
        .display_ok = true,
        .touch_ok = true,
        .audio_ok = audio_ok,
        .imu_ok = imu_ok,
        .mag_ok = sensors.mag_ok,
    };
    unsigned int back_buffer = 1;
    demo_ui_render(framebuffers[back_buffer], &state);
    ESP_ERROR_CHECK(display_present(panel, framebuffers[back_buffer]));
    back_buffer ^= 1;
    ESP_LOGI(TAG, "Metro hardware diagnostic UI enabled");

    // Run one deterministic startup loopback so audio can be verified without
    // relying on a touch event. Manual tile tests remain available afterwards.
    if (state.audio_ok) {
        vTaskDelay(pdMS_TO_TICKS(1200));
        ++state.tone_events;
        state.audio_testing = true;
        demo_ui_render(framebuffers[back_buffer], &state);
        ESP_ERROR_CHECK(display_present(panel, framebuffers[back_buffer]));
        back_buffer ^= 1;
        ESP_LOGI(TAG, "starting automatic speaker/microphone loopback test");
        whisplay_audio_play_tone(&audio);
        state.audio_testing = false;
        state.mic_level = whisplay_audio_read_mic_level(&audio);
        demo_ui_render(framebuffers[back_buffer], &state);
        ESP_ERROR_CHECK(display_present(panel, framebuffers[back_buffer]));
        back_buffer ^= 1;
    }

    int last_button = gpio_get_level(CONFIG_WHISPLAY_BUTTON_GPIO);
    TickType_t last_button_change = 0;
    TickType_t last_sensor_read = 0;
    TickType_t last_render = 0;
    bool touch_armed = true;
    for (;;) {
        poll_touch(touch, &state);

        state.audio_pressed = state.touch_count > 0 &&
                              demo_ui_hit_audio(state.touches[0].x, state.touches[0].y);
        state.rgb_pressed = state.touch_count > 0 &&
                            demo_ui_hit_rgb(state.touches[0].x, state.touches[0].y);

        if (state.touch_count == 0) {
            touch_armed = true;
        } else if (touch_armed) {
            touch_armed = false;
            const uint16_t x = state.touches[0].x;
            const uint16_t y = state.touches[0].y;
            state.ripple_active = true;
            state.ripple_x = x;
            state.ripple_y = y;
            state.ripple_age = 0;
            ESP_LOGI(TAG,
                     "touch down raw=(%u,%u) mapped=(%u,%u): sound=%d mode=%d io=%d",
                     state.touches[0].raw_x, state.touches[0].raw_y, x, y,
                     demo_ui_hit_audio(x, y), demo_ui_hit_audio_mode(x, y),
                     demo_ui_hit_rgb(x, y));
            if (demo_ui_hit_audio_mode(x, y) && state.audio_ok) {
                state.audio_onboard = !state.audio_onboard;
                const esp_err_t mode_result =
                    whisplay_audio_set_onboard_codec(&audio, state.audio_onboard);
                if (mode_result != ESP_OK) {
                    state.audio_onboard = !state.audio_onboard;
                    ESP_LOGE(TAG, "audio mode switch failed: %s", esp_err_to_name(mode_result));
                } else {
                    state.mic_level = 0;
                    ESP_LOGI(TAG, "audio mode switched to %s",
                             state.audio_onboard ? "onboard ES8311" : "Whisplay ES8389");
                }
                demo_ui_render(framebuffers[back_buffer], &state);
                ESP_ERROR_CHECK(display_present(panel, framebuffers[back_buffer]));
                back_buffer ^= 1;
            } else if (demo_ui_hit_audio(x, y) && state.audio_ok) {
                ++state.tone_events;
                state.audio_testing = true;
                ESP_LOGI(TAG, "Sound tile pressed at (%u,%u), starting audio test", x, y);
                demo_ui_render(framebuffers[back_buffer], &state);
                ESP_ERROR_CHECK(display_present(panel, framebuffers[back_buffer]));
                back_buffer ^= 1;
                whisplay_audio_play_tone(&audio);
                state.audio_testing = false;
                state.mic_level = whisplay_audio_read_mic_level(&audio);
                demo_ui_render(framebuffers[back_buffer], &state);
                ESP_ERROR_CHECK(display_present(panel, framebuffers[back_buffer]));
                back_buffer ^= 1;
            } else if (demo_ui_hit_rgb(x, y)) {
                state.rgb_step = (state.rgb_step + 1) % 4;
                set_rgb(state.rgb_step);
                ESP_LOGI(TAG, "I/O tile pressed at (%u,%u), RGB step=%u", x, y,
                         state.rgb_step);
                demo_ui_render(framebuffers[back_buffer], &state);
                ESP_ERROR_CHECK(display_present(panel, framebuffers[back_buffer]));
                back_buffer ^= 1;
            }
        }

        const int button = gpio_get_level(CONFIG_WHISPLAY_BUTTON_GPIO);
        const TickType_t now = xTaskGetTickCount();
        if (button != last_button && now - last_button_change >= pdMS_TO_TICKS(30)) {
            last_button = button;
            last_button_change = now;
            state.button_pressed = button;
            ESP_LOGI(TAG, "button %s", button ? "pressed" : "released");
            if (button) {
                ++state.button_events;
                state.rgb_step = (state.rgb_step + 1) % 4;
                set_rgb(state.rgb_step);
                ESP_LOGI(TAG, "RGB LED step: %u", state.rgb_step);
            }
        }

        if (now - last_sensor_read >= pdMS_TO_TICKS(50)) {
            last_sensor_read = now;
            whisplay_sensor_sample_t sample;
            whisplay_sensors_read(&sensors, &sample);
            state.ax_g = sample.ax_g;
            state.ay_g = sample.ay_g;
            state.az_g = sample.az_g;
            state.gx_dps = sample.gx_dps;
            state.gy_dps = sample.gy_dps;
            state.gz_dps = sample.gz_dps;
            state.temperature_c = sample.temperature_c;
            state.mag_x = sample.mag_x;
            state.mag_y = sample.mag_y;
            state.mag_z = sample.mag_z;
            state.heading_deg = sample.heading_deg;
            state.mag_status = sample.mag_status;
        }
        if (state.audio_ok) {
            const uint8_t mic = whisplay_audio_read_mic_level(&audio);
            state.mic_level = (state.mic_level * 3 + mic) / 4;
        }
        const TickType_t render_interval = pdMS_TO_TICKS(state.ripple_active ? 50 : 100);
        if (now - last_render >= render_interval) {
            last_render = now;
            state.uptime_s = now * portTICK_PERIOD_MS / 1000;
            demo_ui_render(framebuffers[back_buffer], &state);
            ESP_ERROR_CHECK(display_present(panel, framebuffers[back_buffer]));
            back_buffer ^= 1;
            if (state.ripple_active && ++state.ripple_age >= 7) {
                state.ripple_active = false;
                state.ripple_age = 0;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
