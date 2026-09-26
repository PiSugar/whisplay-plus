#include "whisplay_audio.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "esp_check.h"
#include "audio_codec_ctrl_if.h"
#include "audio_codec_if.h"
#include "esp_codec_dev_defaults.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "es8311_codec.h"
#include "es8389_codec.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#define ES8389_ADDRESS 0x10
#define AUDIO_SAMPLE_RATE 16000
#define TONE_FREQUENCY_HZ 1000
#define TONE_WINDOW_FRAMES 256
#define TONE_GOERTZEL_COEFFICIENT 1.8477591f // 2*cos(2*pi*1000/16000)
#define ONBOARD_I2S_MCLK_GPIO 13
#define ONBOARD_I2S_BCLK_GPIO 12
#define ONBOARD_I2S_LRCLK_GPIO 10
#define ONBOARD_I2S_DOUT_GPIO 9
#define ONBOARD_I2S_DIN_GPIO 11
#define ONBOARD_PA_GPIO 53

static const char *TAG = "whisplay-audio";
static const audio_codec_ctrl_if_t *s_codec_ctrl;
static const audio_codec_if_t *s_codec_if;
static es8389_codec_cfg_t s_codec_config;
static bool s_codec_open;
static i2c_master_dev_handle_t s_direct_codec;
static i2c_master_bus_handle_t s_i2c_bus;
static const audio_codec_ctrl_if_t *s_es8311_ctrl;
static const audio_codec_gpio_if_t *s_es8311_gpio;
static const audio_codec_if_t *s_es8311_if;

static esp_err_t create_i2s_tx(whisplay_audio_t *audio)
{
    if (audio->tx != NULL) return ESP_OK;
    i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    channel_config.dma_desc_num = 6;
    channel_config.dma_frame_num = 240;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&channel_config, &audio->tx, NULL), TAG,
                        "create I2S TX channel");
    const i2s_std_config_t standard_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = CONFIG_WHISPLAY_I2S_BCLK_GPIO,
            .ws = CONFIG_WHISPLAY_I2S_LRCLK_GPIO,
            .dout = CONFIG_WHISPLAY_I2S_DOUT_GPIO,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {0},
        },
    };
    const esp_err_t result = i2s_channel_init_std_mode(audio->tx, &standard_config);
    if (result != ESP_OK) {
        i2s_del_channel(audio->tx);
        audio->tx = NULL;
    }
    return result;
}

static esp_err_t create_onboard_i2s_tx(whisplay_audio_t *audio)
{
    if (audio->tx != NULL) return ESP_OK;
    i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    channel_config.dma_desc_num = 6;
    channel_config.dma_frame_num = 240;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&channel_config, &audio->tx, NULL), TAG,
                        "create onboard I2S TX channel");
    const i2s_std_config_t standard_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = ONBOARD_I2S_MCLK_GPIO,
            .bclk = ONBOARD_I2S_BCLK_GPIO,
            .ws = ONBOARD_I2S_LRCLK_GPIO,
            .dout = ONBOARD_I2S_DOUT_GPIO,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {0},
        },
    };
    const esp_err_t result = i2s_channel_init_std_mode(audio->tx, &standard_config);
    if (result != ESP_OK) {
        i2s_del_channel(audio->tx);
        audio->tx = NULL;
    }
    return result;
}

static esp_err_t create_onboard_i2s_rx(whisplay_audio_t *audio)
{
    if (audio->rx != NULL) return ESP_OK;
    i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    channel_config.dma_desc_num = 4;
    channel_config.dma_frame_num = 480;
    channel_config.auto_clear = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&channel_config, NULL, &audio->rx), TAG,
                        "create onboard I2S RX channel");
    const i2s_std_config_t standard_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = ONBOARD_I2S_MCLK_GPIO,
            .bclk = ONBOARD_I2S_BCLK_GPIO,
            .ws = ONBOARD_I2S_LRCLK_GPIO,
            .dout = I2S_GPIO_UNUSED,
            .din = ONBOARD_I2S_DIN_GPIO,
            .invert_flags = {0},
        },
    };
    const esp_err_t result = i2s_channel_init_std_mode(audio->rx, &standard_config);
    if (result != ESP_OK) {
        i2s_del_channel(audio->rx);
        audio->rx = NULL;
    }
    return result;
}

static esp_err_t create_i2s_rx(whisplay_audio_t *audio, int din_gpio)
{
    if (audio->rx != NULL) return ESP_OK;
    i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    channel_config.dma_desc_num = 4;
    channel_config.dma_frame_num = 480;
    channel_config.auto_clear = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&channel_config, NULL, &audio->rx), TAG,
                        "create I2S RX channel");
    const i2s_std_config_t standard_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = CONFIG_WHISPLAY_I2S_BCLK_GPIO,
            .ws = CONFIG_WHISPLAY_I2S_LRCLK_GPIO,
            .dout = I2S_GPIO_UNUSED,
            .din = din_gpio,
            .invert_flags = {0},
        },
    };
    const esp_err_t result = i2s_channel_init_std_mode(audio->rx, &standard_config);
    if (result != ESP_OK) {
        i2s_del_channel(audio->rx);
        audio->rx = NULL;
    }
    return result;
}

static esp_err_t create_i2s_pair(whisplay_audio_t *audio, int din_gpio)
{
    if (audio->tx != NULL || audio->rx != NULL) return ESP_ERR_INVALID_STATE;
    i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    channel_config.dma_desc_num = 6;
    channel_config.dma_frame_num = 240;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&channel_config, &audio->tx, &audio->rx), TAG,
                        "create full-duplex I2S channel pair");
    const i2s_std_slot_config_t slot_config =
        I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO);
    const i2s_std_config_t tx_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = slot_config,
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = CONFIG_WHISPLAY_I2S_BCLK_GPIO,
            .ws = CONFIG_WHISPLAY_I2S_LRCLK_GPIO,
            .dout = CONFIG_WHISPLAY_I2S_DOUT_GPIO,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {0},
        },
    };
    const i2s_std_config_t rx_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = slot_config,
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = CONFIG_WHISPLAY_I2S_BCLK_GPIO,
            .ws = CONFIG_WHISPLAY_I2S_LRCLK_GPIO,
            .dout = I2S_GPIO_UNUSED,
            .din = din_gpio,
            .invert_flags = {0},
        },
    };
    esp_err_t result = i2s_channel_init_std_mode(audio->tx, &tx_config);
    if (result == ESP_OK) result = i2s_channel_init_std_mode(audio->rx, &rx_config);
    if (result != ESP_OK) {
        i2s_del_channel(audio->rx);
        i2s_del_channel(audio->tx);
        audio->rx = NULL;
        audio->tx = NULL;
    }
    return result;
}

typedef struct {
    whisplay_audio_t *audio;
    volatile bool running;
    // 0 = silence, 1 = left-only tone, 2 = right-only tone.  Testing the
    // channels separately also works with mono/differential HAT routing.
    volatile uint8_t tone_channel;
    volatile bool stopped;
    volatile bool failed;
} audio_writer_context_t;

typedef struct {
    whisplay_audio_t *audio;
    volatile bool running;
    volatile bool stopped;
    volatile uint8_t peak;
    volatile uint16_t raw_peak;
} audio_reader_context_t;

static esp_err_t codec_write(i2c_master_dev_handle_t codec, uint8_t reg, uint8_t value)
{
    const uint8_t data[] = {reg, value};
    return i2c_master_transmit(codec, data, sizeof(data), 100);
}

static esp_err_t codec_read(i2c_master_dev_handle_t codec, uint8_t reg, uint8_t *value);
static esp_err_t codec_configure_whisplay_mclk(void);

static esp_err_t codec_result(int result, const char *operation)
{
    if (result == ESP_CODEC_DEV_OK) {
        return ESP_OK;
    }
    ESP_LOGE(TAG, "%s failed: 0x%x", operation, result);
    return ESP_FAIL;
}

static esp_err_t onboard_es8311_enable(bool speaker)
{
    if (s_es8311_if == NULL) {
        audio_codec_i2c_cfg_t ctrl_cfg = {
            .port = 0,
            .addr = ES8311_CODEC_DEFAULT_ADDR,
            .bus_handle = s_i2c_bus,
        };
        s_es8311_ctrl = audio_codec_new_i2c_ctrl(&ctrl_cfg);
        ESP_RETURN_ON_FALSE(s_es8311_ctrl != NULL, ESP_FAIL, TAG, "create onboard ES8311 I2C control");
        s_es8311_gpio = audio_codec_new_gpio();
        ESP_RETURN_ON_FALSE(s_es8311_gpio != NULL, ESP_FAIL, TAG, "create onboard PA GPIO control");
        es8311_codec_cfg_t cfg = {
            .ctrl_if = s_es8311_ctrl,
            .gpio_if = s_es8311_gpio,
            .codec_mode = ESP_CODEC_DEV_WORK_MODE_BOTH,
            .pa_pin = ONBOARD_PA_GPIO,
            .pa_reverted = false,
            .master_mode = false,
            .use_mclk = true,
            .digital_mic = false,
            .invert_mclk = false,
            .invert_sclk = false,
            .no_dac_ref = false,
            .mclk_div = 256,
        };
        s_es8311_if = es8311_codec_new(&cfg);
        ESP_RETURN_ON_FALSE(s_es8311_if != NULL, ESP_FAIL, TAG, "create onboard ES8311 codec");
    }
    esp_codec_dev_sample_info_t fs = {
        .bits_per_sample = 16,
        .channel = 2,
        .sample_rate = AUDIO_SAMPLE_RATE,
        .mclk_multiple = 256,
    };
    ESP_RETURN_ON_ERROR(codec_result(s_es8311_if->set_fs(s_es8311_if, &fs), "ES8311 set_fs"),
                        TAG, "configure onboard ES8311 format");
    ESP_RETURN_ON_ERROR(codec_result(s_es8311_if->set_vol(s_es8311_if, -6.0f), "ES8311 set volume"),
                        TAG, "configure onboard ES8311 volume");
    ESP_RETURN_ON_ERROR(codec_result(s_es8311_if->set_mic_gain(s_es8311_if, 30.0f),
                                     "ES8311 set mic gain"),
                        TAG, "configure onboard ES8311 microphone gain");
    if (s_es8311_if->mute != NULL) {
        ESP_RETURN_ON_ERROR(codec_result(s_es8311_if->mute(s_es8311_if, !speaker),
                                         "ES8311 set DAC mute"),
                            TAG, "set onboard ES8311 DAC state");
    }
    ESP_RETURN_ON_ERROR(codec_result(s_es8311_if->enable(s_es8311_if, true), "ES8311 enable"),
                        TAG, "enable onboard ES8311 and PA");
    // GPIO53 is multiplexed with the HAT LRCLK and is repeatedly detached from
    // the GPIO matrix during card switching. Reassert the ordinary GPIO output
    // configuration after ES8311 enable instead of relying on the codec
    // driver's one-time PA setup.
    // gpio_set_direction() alone does not detach a peripheral output already
    // routed through the GPIO matrix. GPIO53 was Whisplay LRCLK immediately
    // before this mode, so reset the pad first or I2S continues to override
    // the software PA level.
    ESP_RETURN_ON_ERROR(gpio_reset_pin(ONBOARD_PA_GPIO), TAG,
                        "detach onboard PA control from I2S matrix");
    ESP_RETURN_ON_ERROR(gpio_set_direction(ONBOARD_PA_GPIO, GPIO_MODE_OUTPUT), TAG,
                        "configure onboard PA control");
    ESP_RETURN_ON_ERROR(gpio_input_enable(ONBOARD_PA_GPIO), TAG,
                        "enable onboard PA control readback");
    ESP_RETURN_ON_ERROR(gpio_set_level(ONBOARD_PA_GPIO, speaker ? 1 : 0), TAG,
                        "set onboard PA control");
    vTaskDelay(pdMS_TO_TICKS(5));
    ESP_RETURN_ON_FALSE(gpio_get_level(ONBOARD_PA_GPIO) == (speaker ? 1 : 0),
                        ESP_FAIL, TAG, "onboard PA control did not latch");
    ESP_LOGI(TAG, "onboard ES8311 %s enabled: MCLK=%d BCLK=%d LRCLK=%d DOUT=%d DIN=%d PA=%d level=%d",
             speaker ? "playback" : "record",
             ONBOARD_I2S_MCLK_GPIO, ONBOARD_I2S_BCLK_GPIO, ONBOARD_I2S_LRCLK_GPIO,
             ONBOARD_I2S_DOUT_GPIO, ONBOARD_I2S_DIN_GPIO, ONBOARD_PA_GPIO,
             gpio_get_level(ONBOARD_PA_GPIO));
    return ESP_OK;
}

static esp_err_t official_codec_configure(void)
{
    esp_codec_dev_sample_info_t fs = {
        .bits_per_sample = 16,
        .channel = 2,
        .channel_mask = 0,
        .sample_rate = AUDIO_SAMPLE_RATE,
        .mclk_multiple = 0,
    };
    ESP_RETURN_ON_ERROR(codec_result(s_codec_if->set_fs(s_codec_if, &fs), "ES8389 set_fs"),
                        TAG, "configure sample format");
    if (s_codec_config.use_mclk) {
        ESP_RETURN_ON_ERROR(codec_configure_whisplay_mclk(), TAG,
                            "configure 24.576 MHz Whisplay clock");
    }
    return ESP_OK;
}

static esp_err_t codec_configure_whisplay_mclk(void)
{
    // Exact 16 kHz / 24.576 MHz (1536 fs) coefficient row from the ES8389
    // table. WhisPlay Plus supplies this clock with its on-board X4 oscillator.
    static const uint8_t registers[][2] = {
        {0x02, 0x00}, {0x04, 0x05}, {0x05, 0x41}, {0x06, 0x04},
        {0x07, 0xc0}, {0x08, 0x01}, {0x09, 0xd1}, {0x0a, 0x90},
        {0x0f, 0xc0}, {0x11, 0x00}, {0x21, 0x1f}, {0x22, 0x7f},
        {0x26, 0xbf}, {0x30, 0xc0}, {0x41, 0xff}, {0x42, 0x7f},
        {0x43, 0x00}, {0xf0, 0x12}, {0xf1, 0x00}, {0x16, 0x12},
        {0x18, 0x31}, {0x19, 0x0e},
    };
    for (size_t i = 0; i < sizeof(registers) / sizeof(registers[0]); ++i) {
        ESP_RETURN_ON_ERROR(codec_write(s_direct_codec, registers[i][0], registers[i][1]),
                            TAG, "configure Whisplay 24.576 MHz clock");
    }
    return ESP_OK;
}

static esp_err_t official_codec_open(void)
{
    if (s_codec_if == NULL) {
        // es8389_codec_new() opens and initializes the hardware immediately.
        // Call it only after BCLK/WS are already running so the serial port is
        // stable while the codec starts from its on-HAT 24.576 MHz MCLK.
        s_codec_if = es8389_codec_new(&s_codec_config);
        ESP_RETURN_ON_FALSE(s_codec_if != NULL, ESP_FAIL, TAG,
                            "create official ES8389 codec");
        s_codec_open = true;
    } else if (!s_codec_open) {
        ESP_RETURN_ON_ERROR(codec_result(s_codec_if->open(s_codec_if, &s_codec_config,
                                                           sizeof(s_codec_config)), "ES8389 open"),
                            TAG, "open codec");
        s_codec_open = true;
    }
    ESP_RETURN_ON_ERROR(official_codec_configure(), TAG, "configure codec");
    // Keep this sequence identical to the active Whisplay production tester.
    // Use 0 dB for the hardware-test tone.  This maps to 0xBF in the ES8389
    // DAC volume registers and removes playback level as a test variable.
    ESP_RETURN_ON_ERROR(codec_result(s_codec_if->set_vol(s_codec_if, 0.0f),
                                     "ES8389 set playback volume"),
                        TAG, "configure playback volume");
    if (s_codec_if->mute != NULL) {
        ESP_RETURN_ON_ERROR(codec_result(s_codec_if->mute(s_codec_if, false), "ES8389 unmute"),
                            TAG, "unmute codec");
    }
    ESP_RETURN_ON_ERROR(codec_result(s_codec_if->enable(s_codec_if, true), "ES8389 enable"),
                        TAG, "enable codec");
    // Keep every DAC block out of reset.  Register 0x4D bit 1 is RST_DAC_DSM;
    // leaving the Linux driver's revision-specific value 0x02 here prevents
    // this device from reaching the documented Normal state (F1=0x0D).
    ESP_RETURN_ON_ERROR(codec_write(s_direct_codec, 0x4d, 0x00), TAG,
                        "release ES8389 DAC reset state");
    ESP_RETURN_ON_ERROR(codec_write(s_direct_codec, 0xf0, 0x13), TAG,
                        "restore ES8389 DAC reference state");
    ESP_RETURN_ON_ERROR(codec_write(s_direct_codec, 0x69, 0x20), TAG,
                        "select ES8389 line output drivers");
    // esp_codec_dev's ES8389 mute callback currently operates on ADC_FORMAT
    // (0x20), not DAC_FORMAT (0x40).  Explicitly clear the DAC mute bits so
    // this speaker test cannot remain digitally muted.
    uint8_t dac_format = 0;
    ESP_RETURN_ON_ERROR(codec_read(s_direct_codec, 0x40, &dac_format), TAG,
                        "read ES8389 DAC format");
    ESP_RETURN_ON_ERROR(codec_write(s_direct_codec, 0x40, dac_format & 0xfc), TAG,
                        "unmute ES8389 DAC");
    uint8_t dac_reset = 0;
    uint8_t chip_misc = 0;
    uint8_t dac_vol_l = 0;
    uint8_t dac_vol_r = 0;
    ESP_RETURN_ON_ERROR(codec_read(s_direct_codec, 0x4d, &dac_reset), TAG,
                        "read ES8389 DAC reset state");
    ESP_RETURN_ON_ERROR(codec_read(s_direct_codec, 0xf0, &chip_misc), TAG,
                        "read ES8389 DAC reference state");
    ESP_RETURN_ON_ERROR(codec_read(s_direct_codec, 0x46, &dac_vol_l), TAG,
                        "read ES8389 left DAC volume");
    ESP_RETURN_ON_ERROR(codec_read(s_direct_codec, 0x47, &dac_vol_r), TAG,
                        "read ES8389 right DAC volume");
    ESP_RETURN_ON_ERROR(codec_read(s_direct_codec, 0x40, &dac_format), TAG,
                        "verify ES8389 DAC format");
    ESP_LOGI(TAG, "ES8389 playback: DAC=0dB 40=%02X 46=%02X 47=%02X 4D=%02X F0=%02X",
             dac_format, dac_vol_l, dac_vol_r, dac_reset, chip_misc);
    vTaskDelay(pdMS_TO_TICKS(100));
    static const uint8_t state_registers[] = {0x00, 0x03, 0x10, 0x44, 0x61, 0x63, 0x64, 0x69, 0xf1};
    uint8_t state_values[sizeof(state_registers)] = {0};
    for (size_t i = 0; i < sizeof(state_registers); ++i) {
        ESP_RETURN_ON_ERROR(codec_read(s_direct_codec, state_registers[i], &state_values[i]),
                            TAG, "read ES8389 playback state");
    }
    ESP_LOGI(TAG,
             "ES8389 state: 00=%02X 03=%02X 10=%02X 44=%02X 61=%02X 63=%02X 64=%02X 69=%02X F1=%02X",
             state_values[0], state_values[1], state_values[2], state_values[3],
             state_values[4], state_values[5], state_values[6], state_values[7], state_values[8]);
    ESP_LOGI(TAG, "ES8389 production playback path enabled");
    return ESP_OK;
}

static esp_err_t official_codec_close(void)
{
    if (s_codec_if == NULL || !s_codec_open) {
        return ESP_OK;
    }
    return codec_result(s_codec_if->enable(s_codec_if, false), "ES8389 disable");
}

static esp_err_t official_codec_record(void)
{
    ESP_RETURN_ON_FALSE(s_codec_if != NULL && s_codec_open, ESP_ERR_INVALID_STATE, TAG,
                        "ES8389 is not open");
    ESP_RETURN_ON_ERROR(official_codec_configure(), TAG, "configure codec for recording");
    ESP_RETURN_ON_ERROR(codec_result(s_codec_if->enable(s_codec_if, false),
                                     "ES8389 stop before record"),
                        TAG, "stop playback before recording");
    ESP_RETURN_ON_ERROR(codec_result(s_codec_if->set_mic_gain(s_codec_if, 36.0f),
                                     "ES8389 set mic gain"),
                        TAG, "configure microphone gain");
    ESP_RETURN_ON_ERROR(codec_result(s_codec_if->enable(s_codec_if, true),
                                     "ES8389 enable record"),
                        TAG, "enable recording");
    ESP_LOGI(TAG, "ES8389 production record path enabled (gain=36 dB)");
    return ESP_OK;
}

static esp_err_t codec_read(i2c_master_dev_handle_t codec, uint8_t reg, uint8_t *value)
{
    return i2c_master_transmit_receive(codec, &reg, 1, value, 1, 100);
}

static void audio_writer_task(void *argument)
{
    audio_writer_context_t *context = argument;
    int16_t frames[192 * 2];
    uint32_t phase = 0;
    while (context->running) {
        for (int i = 0; i < 192; ++i) {
            const float radians = 6.28318530718f * TONE_FREQUENCY_HZ * phase /
                                  AUDIO_SAMPLE_RATE;
            const int16_t sample = context->tone_channel
                ? (int16_t)(24000.0f * sinf(radians)) : 0;
            // The known-good ESP32 Whisplay example sends the same waveform
            // on both channels.  The HAT's mono speaker routing expects this;
            // channel-isolated test data can be silent depending on SP1/SP2.
            frames[i * 2] = sample;
            frames[i * 2 + 1] = sample;
            ++phase;
        }
        size_t bytes_written = 0;
        const esp_err_t err = i2s_channel_write(context->audio->tx, frames, sizeof(frames),
                                                 &bytes_written, portMAX_DELAY);
        if (err != ESP_OK || bytes_written != sizeof(frames)) {
            ESP_LOGW(TAG, "I2S writer stopped: err=%s bytes=%u",
                     esp_err_to_name(err), (unsigned)bytes_written);
            context->failed = true;
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    context->stopped = true;
    vTaskDelete(NULL);
}

static void audio_reader_task(void *argument)
{
    audio_reader_context_t *context = argument;
    while (context->running) {
        if (!context->audio->active) {
            vTaskDelay(pdMS_TO_TICKS(2));
            continue;
        }
        const uint8_t level = whisplay_audio_read_mic_level(context->audio);
        if (level > context->peak) context->peak = level;
        if (context->audio->last_mic_raw_peak > context->raw_peak) {
            context->raw_peak = context->audio->last_mic_raw_peak;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    context->stopped = true;
    vTaskDelete(NULL);
}

static void log_i2s_pin_activity(void)
{
    const int pins[3] = {
        CONFIG_WHISPLAY_I2S_BCLK_GPIO,
        CONFIG_WHISPLAY_I2S_LRCLK_GPIO,
        CONFIG_WHISPLAY_I2S_DOUT_GPIO,
    };
    const char *names[3] = {"BCLK", "LRCLK", "DOUT"};
    uint32_t edges[3] = {0};
    int previous[3];
    for (int channel = 0; channel < 3; ++channel) {
        gpio_input_enable(pins[channel]);
        previous[channel] = gpio_get_level(pins[channel]);
    }
    for (int sample = 0; sample < 200000; ++sample) {
        for (int channel = 0; channel < 3; ++channel) {
            const int level = gpio_get_level(pins[channel]);
            if (level != previous[channel]) {
                ++edges[channel];
                previous[channel] = level;
            }
        }
    }
    ESP_LOGI(TAG, "I2S physical activity: %s=%u %s=%u %s=%u edges",
             names[0], (unsigned)edges[0], names[1], (unsigned)edges[1],
             names[2], (unsigned)edges[2]);
}

static void finish_tone_window(whisplay_audio_t *audio)
{
    if (audio->mic_metric_window_frames == 0) {
        return;
    }
    for (int channel = 0; channel < 2; ++channel) {
        const float q1 = audio->mic_metric_goertzel_q1[channel];
        const float q2 = audio->mic_metric_goertzel_q2[channel];
        const float tone_power = q1 * q1 + q2 * q2 -
                                 TONE_GOERTZEL_COEFFICIENT * q1 * q2;
        unsigned int score = 0;
        if (audio->mic_metric_energy[channel] > 0) {
            score = (unsigned int)(tone_power * 1000.0f /
                ((float)audio->mic_metric_energy[channel] *
                 audio->mic_metric_window_frames));
        }
        if (score > 1000) score = 1000;
        if (score > audio->mic_metric_tone_best[channel]) {
            audio->mic_metric_tone_best[channel] = score;
        }
        audio->mic_metric_goertzel_q1[channel] = 0;
        audio->mic_metric_goertzel_q2[channel] = 0;
        audio->mic_metric_energy[channel] = 0;
    }
    ++audio->mic_metric_windows;
    audio->mic_metric_window_frames = 0;
}

static void release_i2s_channels(whisplay_audio_t *audio)
{
    audio->active = false;
    if (audio->rx != NULL) {
        (void)i2s_channel_disable(audio->rx);
        i2s_del_channel(audio->rx);
        audio->rx = NULL;
    }
    if (audio->tx != NULL) {
        (void)i2s_channel_disable(audio->tx);
        i2s_del_channel(audio->tx);
        audio->tx = NULL;
    }
}

static esp_err_t start_whisplay_microphone(whisplay_audio_t *audio)
{
    release_i2s_channels(audio);
    ESP_RETURN_ON_ERROR(create_i2s_rx(audio, CONFIG_WHISPLAY_I2S_DIN_GPIO), TAG,
                        "create Whisplay microphone I2S");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(audio->rx), TAG,
                        "start Whisplay microphone clocks");
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_err_t result = official_codec_open();
    if (result == ESP_OK) result = official_codec_record();
    if (result != ESP_OK) {
        release_i2s_channels(audio);
        return result;
    }
    audio->last_mic_level = 0;
    audio->last_mic_raw_peak = 0;
    audio->active = true;
    ESP_LOGI(TAG, "audio mode active: WhisPlay ES8389 (live microphone)");
    return ESP_OK;
}

static esp_err_t start_onboard_microphone(whisplay_audio_t *audio)
{
    release_i2s_channels(audio);
    ESP_RETURN_ON_ERROR(create_onboard_i2s_rx(audio), TAG,
                        "create onboard microphone I2S");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(audio->rx), TAG,
                        "start onboard microphone clocks");
    vTaskDelay(pdMS_TO_TICKS(100));
    const esp_err_t result = onboard_es8311_enable(false);
    if (result != ESP_OK) {
        release_i2s_channels(audio);
        return result;
    }
    audio->last_mic_level = 0;
    audio->last_mic_raw_peak = 0;
    audio->active = true;
    ESP_LOGI(TAG, "audio mode active: onboard ES8311 (live microphone)");
    return ESP_OK;
}

esp_err_t whisplay_audio_init(i2c_master_bus_handle_t bus, whisplay_audio_t *audio)
{
    memset(audio, 0, sizeof(*audio));
    s_i2c_bus = bus;
    const uint16_t codec_address = ES8389_ADDRESS;
    const i2c_device_config_t codec_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = codec_address,
        .scl_speed_hz = 100000,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(bus, &codec_config, &audio->codec), TAG, "add ES8389");
    s_direct_codec = audio->codec;
    // F3 is still accepted when this ES8389 revision was left in isolation by
    // a reset. Wake it before using ordinary probe/read transactions.
    ESP_RETURN_ON_ERROR(codec_write(audio->codec, 0xf3, 0x00), TAG, "wake isolated ES8389");
    vTaskDelay(pdMS_TO_TICKS(8));
    ESP_RETURN_ON_ERROR(codec_write(audio->codec, 0x00, 0x7e), TAG, "reset waking ES8389");
    ESP_RETURN_ON_ERROR(codec_write(audio->codec, 0xf3, 0x38), TAG, "complete ES8389 wake");
    vTaskDelay(pdMS_TO_TICKS(8));
    ESP_RETURN_ON_ERROR(codec_read(audio->codec, 0xff, &audio->codec_version), TAG,
                        "read ES8389 version");

    audio_codec_i2c_cfg_t ctrl_config = {
        .port = 0,
        .addr = ES8389_CODEC_DEFAULT_ADDR,
        .bus_handle = bus,
    };
    s_codec_ctrl = audio_codec_new_i2c_ctrl(&ctrl_config);
    ESP_RETURN_ON_FALSE(s_codec_ctrl != NULL, ESP_FAIL, TAG, "create ES8389 I2C control");
    s_codec_config = (es8389_codec_cfg_t) {
        .ctrl_if = s_codec_ctrl,
        .gpio_if = NULL,
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_BOTH,
        .pa_pin = -1,
        .pa_reverted = false,
        .master_mode = false,
        // WhisPlay Plus supplies a dedicated 24.576 MHz clock from its X4
        // oscillator.  Use it so the codec state machine does not depend on
        // the low 512 kHz serial BCLK generated by the ESP32-P4.
        .use_mclk = true,
        .digital_mic = false,
        .invert_mclk = false,
        .invert_sclk = false,
        .no_dac_ref = true,
        .mclk_div = 0,
    };
    audio->ready = true;
    audio->onboard_codec = false;
    ESP_RETURN_ON_ERROR(start_whisplay_microphone(audio), TAG,
                        "start default Whisplay audio mode");
    ESP_LOGI(TAG, "ES8389/I2S ready, version=0x%02x pins BCLK=%d LRCLK=%d DOUT=%d DIN=%d",
             audio->codec_version, CONFIG_WHISPLAY_I2S_BCLK_GPIO, CONFIG_WHISPLAY_I2S_LRCLK_GPIO,
             CONFIG_WHISPLAY_I2S_DOUT_GPIO, CONFIG_WHISPLAY_I2S_DIN_GPIO);
    return ESP_OK;
}

esp_err_t whisplay_audio_set_onboard_codec(whisplay_audio_t *audio, bool onboard)
{
    if (!audio->ready) return ESP_ERR_INVALID_STATE;
    if (audio->onboard_codec == onboard) return ESP_OK;

    const bool previous_onboard = audio->onboard_codec;
    release_i2s_channels(audio);
    // Stop only the codec that owns the current mode. Calling enable(false)
    // repeatedly on the already-idle codec unbalances esp_codec_dev's enable
    // reference count and prevents its PA transition on the next playback.
    if (previous_onboard && s_es8311_if != NULL) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(codec_result(s_es8311_if->enable(s_es8311_if, false),
                                                   "ES8311 disable on mode switch"));
    } else if (!previous_onboard && s_codec_if != NULL && s_codec_open) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(official_codec_close());
    }

    audio->onboard_codec = onboard;
    audio->last_mic_level = 0;
    audio->last_mic_raw_peak = 0;
    if (onboard) {
        return start_onboard_microphone(audio);
    }
    return start_whisplay_microphone(audio);
}

uint8_t whisplay_audio_read_mic_level(whisplay_audio_t *audio)
{
    if (!audio->ready) {
        return 0;
    }
    if (!audio->active) {
        return audio->last_mic_level;
    }
    int16_t words[256 * 2];
    size_t bytes_read = 0;
    const esp_err_t read_result = i2s_channel_read(audio->rx, words, sizeof(words),
                                                   &bytes_read, pdMS_TO_TICKS(100));
    if (read_result != ESP_OK && bytes_read == 0) {
        static unsigned int read_warning_count;
        if (read_warning_count++ < 3) {
            ESP_LOGW(TAG, "I2S RX read failed: %s bytes=%u",
                     esp_err_to_name(read_result), (unsigned)bytes_read);
        }
        return 0;
    }
    const size_t count = bytes_read / sizeof(words[0]);
    int16_t block_min[2] = {INT16_MAX, INT16_MAX};
    int16_t block_max[2] = {INT16_MIN, INT16_MIN};
    for (size_t i = 0; i < count; ++i) {
        const int channel = i & 1;
        if (words[i] < block_min[channel]) block_min[channel] = words[i];
        if (words[i] > block_max[channel]) block_max[channel] = words[i];
    }
    // Use AC peak-to-peak amplitude rather than absolute sample magnitude.
    // This rejects codec startup DC transients and the fixed padding value in
    // the unused slot of a mono ADC stream.
    int peak = 0;
    for (int channel = 0; channel < 2; ++channel) {
        const int channel_peak = ((int)block_max[channel] - block_min[channel]) / 2;
        if (channel_peak > peak) peak = channel_peak;
    }
    // Channel 0 zero-crossing frequency is a useful hardware-only check that
    // the known speaker tone reached the microphone acoustically.
    for (size_t i = 0; i + 1 < count; i += 2) {
        for (int channel = 0; channel < 2; ++channel) {
            const int16_t value = words[i + channel];
            if (audio->mic_metric_frames == 0) {
                audio->mic_metric_dc[channel] = (int32_t)value << 8;
            } else {
                audio->mic_metric_dc[channel] +=
                    (((int32_t)value << 8) - audio->mic_metric_dc[channel]) >> 8;
            }
            const int16_t centered = value - (audio->mic_metric_dc[channel] >> 8);
            const float goertzel_q0 = TONE_GOERTZEL_COEFFICIENT *
                                      audio->mic_metric_goertzel_q1[channel] -
                                      audio->mic_metric_goertzel_q2[channel] + centered;
            audio->mic_metric_goertzel_q2[channel] = audio->mic_metric_goertzel_q1[channel];
            audio->mic_metric_goertzel_q1[channel] = goertzel_q0;
            audio->mic_metric_energy[channel] += (int32_t)centered * (int32_t)centered;
            if (audio->mic_metric_frames > 0 &&
                ((audio->mic_metric_previous[channel] < 0 && centered >= 0) ||
                 (audio->mic_metric_previous[channel] >= 0 && centered < 0))) {
                ++audio->mic_metric_crossings[channel];
            }
            if (value < audio->mic_metric_min[channel]) audio->mic_metric_min[channel] = value;
            if (value > audio->mic_metric_max[channel]) audio->mic_metric_max[channel] = value;
            audio->mic_metric_previous[channel] = centered;
        }
        ++audio->mic_metric_frames;
        if (++audio->mic_metric_window_frames >= TONE_WINDOW_FRAMES) {
            finish_tone_window(audio);
        }
    }
    audio->last_mic_raw_peak = peak > UINT16_MAX ? UINT16_MAX : (uint16_t)peak;
    int level = peak * 100 / 6000;
    audio->last_mic_level = level > 100 ? 100 : (uint8_t)level;
    return audio->last_mic_level;
}

static uint16_t measure_mic_peak(whisplay_audio_t *audio, int blocks)
{
    uint16_t peak = 0;
    audio->active = true;
    for (int i = 0; i < blocks; ++i) {
        whisplay_audio_read_mic_level(audio);
        if (audio->last_mic_raw_peak > peak) peak = audio->last_mic_raw_peak;
    }
    audio->active = false;
    return peak;
}

static void whisplay_audio_diagnostic_scan(whisplay_audio_t *audio)
{
    if (!audio->ready) {
        return;
    }
    bool tx_enabled = false;
    bool rx_enabled = false;
    bool codec_awake = false;
    audio_writer_context_t writer = {
        .audio = audio,
        .running = false,
        // Begin with tone so the TX DMA cannot be pre-filled with silence
        // before the same-pad loopback diagnostic starts.
        .tone_channel = 1,
        .stopped = false,
        .failed = false,
    };
    audio_reader_context_t reader = {
        .audio = audio,
        .running = false,
        .stopped = false,
        .peak = 0,
        .raw_peak = 0,
    };
    // Replace the quiet-idle TX handle with a native full-duplex pair. Keeping
    // TX and RX on the same peripheral makes them share the exact same clock
    // state and avoids I2S1 slave synchronization losses on ESP32-P4.
    if (audio->tx != NULL) {
        i2s_del_channel(audio->tx);
        audio->tx = NULL;
    }

    // First run the known-good reference path in isolation: TX-only I2S,
    // 16 kHz, 16-bit stereo, the same 1 kHz waveform on L and R.  Do not let
    // RX/full-duplex setup or diagnostic register scans affect this window.
    if (create_i2s_tx(audio) == ESP_OK && i2s_channel_enable(audio->tx) == ESP_OK) {
        tx_enabled = true;
        writer.running = true;
        writer.stopped = false;
        writer.tone_channel = 1;
        if (xTaskCreate(audio_writer_task, "reference_tone", 4096, &writer, 6, NULL) == pdPASS) {
            vTaskDelay(pdMS_TO_TICKS(100));
            if (official_codec_open() == ESP_OK) {
                codec_awake = true;
                codec_write(audio->codec, 0x69, 0xa0);
                codec_write(audio->codec, 0x4d, 0x00);
                codec_write(audio->codec, 0x40, 0x60);
                ESP_LOGI(TAG, "reference speaker test: TX-only stereo 1 kHz");
                vTaskDelay(pdMS_TO_TICKS(3000));
                official_codec_close();
                codec_awake = false;
            }
            writer.running = false;
            for (int i = 0; i < 100 && !writer.stopped; ++i) {
                vTaskDelay(pdMS_TO_TICKS(2));
            }
        } else {
            writer.running = false;
            ESP_LOGW(TAG, "failed to start reference tone writer");
        }
        ESP_ERROR_CHECK_WITHOUT_ABORT(i2s_channel_disable(audio->tx));
        i2s_del_channel(audio->tx);
        audio->tx = NULL;
        tx_enabled = false;
    } else {
        ESP_LOGW(TAG, "failed to start TX-only reference speaker test");
        if (audio->tx != NULL) {
            i2s_del_channel(audio->tx);
            audio->tx = NULL;
        }
    }

    writer.stopped = false;
    writer.failed = false;
    writer.tone_channel = 1;
    if (create_i2s_pair(audio, CONFIG_WHISPLAY_I2S_DOUT_GPIO) != ESP_OK) {
        ESP_LOGW(TAG, "failed to create I2S loopback pair");
        return;
    }
    // Preload a complete 1 kHz period before starting the shared clocks.  If
    // TX starts with an empty DMA queue the first RX descriptors contain only
    // underrun zeros, which made the physical DOUT check falsely report MISS.
    static const int16_t preload_period[16] = {
        0, 9184, 16971, 22173, 24000, 22173, 16971, 9184,
        0, -9184, -16971, -22173, -24000, -22173, -16971, -9184,
    };
    int16_t preload_frames[240 * 2];
    for (size_t i = 0; i < 240; ++i) {
        preload_frames[i * 2] = preload_period[i % 16];
        preload_frames[i * 2 + 1] = preload_period[i % 16];
    }
    size_t preload_bytes = 0;
    const esp_err_t preload_result = i2s_channel_preload_data(
        audio->tx, preload_frames, sizeof(preload_frames), &preload_bytes);
    ESP_LOGI(TAG, "I2S preload: err=%s bytes=%u", esp_err_to_name(preload_result),
             (unsigned)preload_bytes);
    // Arm RX before TX starts the shared clocks.
    if (i2s_channel_enable(audio->rx) != ESP_OK) {
        ESP_LOGW(TAG, "failed to enable I2S RX");
        goto shutdown;
    }
    rx_enabled = true;
    if (i2s_channel_enable(audio->tx) != ESP_OK) {
        ESP_LOGW(TAG, "failed to enable I2S TX");
        goto shutdown;
    }
    tx_enabled = true;
    writer.running = true;
    if (xTaskCreate(audio_writer_task, "audio_writer", 4096, &writer, 6, NULL) != pdPASS) {
        ESP_LOGW(TAG, "failed to start I2S writer task");
        writer.running = false;
        goto shutdown;
    }
    writer.tone_channel = 1;
    vTaskDelay(pdMS_TO_TICKS(20));
    int16_t loop_words[256 * 2];
    int loop_l_min = INT16_MAX, loop_l_max = INT16_MIN;
    int loop_r_min = INT16_MAX, loop_r_max = INT16_MIN;
    unsigned int loop_l_crossings = 0, loop_r_crossings = 0, loop_frames = 0;
    int16_t loop_l_previous = 0, loop_r_previous = 0;
    for (int block = 0; block < 24; ++block) {
        size_t bytes_read = 0;
        if (i2s_channel_read(audio->rx, loop_words, sizeof(loop_words), &bytes_read,
                             pdMS_TO_TICKS(20)) != ESP_OK) continue;
        const size_t words_read = bytes_read / sizeof(loop_words[0]);
        for (size_t i = 0; i + 1 < words_read; i += 2) {
            const int16_t left = loop_words[i];
            const int16_t right = loop_words[i + 1];
            if (left < loop_l_min) loop_l_min = left;
            if (left > loop_l_max) loop_l_max = left;
            if (right < loop_r_min) loop_r_min = right;
            if (right > loop_r_max) loop_r_max = right;
            if (loop_frames > 0 && ((loop_l_previous < 0 && left >= 0) ||
                                    (loop_l_previous >= 0 && left < 0))) ++loop_l_crossings;
            if (loop_frames > 0 && ((loop_r_previous < 0 && right >= 0) ||
                                    (loop_r_previous >= 0 && right < 0))) ++loop_r_crossings;
            loop_l_previous = left;
            loop_r_previous = right;
            ++loop_frames;
        }
    }
    const unsigned int loop_l_hz = loop_frames
        ? (unsigned int)((uint64_t)loop_l_crossings * AUDIO_SAMPLE_RATE / (2U * loop_frames)) : 0;
    const unsigned int loop_r_hz = loop_frames
        ? (unsigned int)((uint64_t)loop_r_crossings * AUDIO_SAMPLE_RATE / (2U * loop_frames)) : 0;
    ESP_LOGI(TAG, "ESP TX pad loopback: frames=%u L=%d..%d/%uHz R=%d..%d/%uHz %s",
             loop_frames, loop_l_min, loop_l_max, loop_l_hz,
             loop_r_min, loop_r_max, loop_r_hz,
             loop_l_max >= 23000 && loop_l_min <= -23000 &&
             loop_l_hz >= 990 && loop_l_hz <= 1010 ? "PASS" : "MISS");
    writer.running = false;
    for (int i = 0; i < 100 && !writer.stopped; ++i) vTaskDelay(pdMS_TO_TICKS(2));
    ESP_ERROR_CHECK_WITHOUT_ABORT(i2s_channel_disable(audio->rx));
    ESP_ERROR_CHECK_WITHOUT_ABORT(i2s_channel_disable(audio->tx));
    i2s_del_channel(audio->rx);
    i2s_del_channel(audio->tx);
    audio->rx = NULL;
    audio->tx = NULL;
    rx_enabled = false;
    tx_enabled = false;
    if (create_i2s_pair(audio, CONFIG_WHISPLAY_I2S_DIN_GPIO) != ESP_OK ||
        i2s_channel_enable(audio->rx) != ESP_OK ||
        i2s_channel_enable(audio->tx) != ESP_OK) {
        ESP_LOGW(TAG, "failed to switch I2S pair to codec input");
        goto shutdown;
    }
    rx_enabled = true;
    tx_enabled = true;
    writer.tone_channel = 0;
    writer.stopped = false;
    writer.running = true;
    if (xTaskCreate(audio_writer_task, "audio_writer", 4096, &writer, 6, NULL) != pdPASS) {
        ESP_LOGW(TAG, "failed to restart I2S writer task");
        writer.running = false;
        goto shutdown;
    }
    vTaskDelay(pdMS_TO_TICKS(100));
    if (official_codec_open() != ESP_OK) {
        ESP_LOGW(TAG, "failed to wake ES8389");
        goto shutdown;
    }
    codec_awake = true;
    uint8_t state = 0, state2 = 0;
    for (int i = 0; i < 25; ++i) {
        codec_read(audio->codec, 0xf1, &state);
        codec_read(audio->codec, 0xf4, &state2);
        if (state == 0x0d) {
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    ESP_LOGI(TAG, "codec state after wake: F1=0x%02x F4=0x%02x", state, state2);

    uint8_t adc_format = 0, dac_format = 0, clock_state = 0;
    uint8_t hpsw = 0, dac_reset = 0, dac_mix = 0, dac_gain = 0;
    codec_read(audio->codec, 0xf1, &state);
    codec_read(audio->codec, 0x20, &adc_format);
    codec_read(audio->codec, 0x40, &dac_format);
    codec_read(audio->codec, 0x30, &clock_state);
    codec_read(audio->codec, 0x69, &hpsw);
    codec_read(audio->codec, 0x4d, &dac_reset);
    codec_read(audio->codec, 0x44, &dac_mix);
    codec_read(audio->codec, 0x46, &dac_gain);
    ESP_LOGI(TAG, "codec active: state=%02x ADC=%02x DAC=%02x SYS30=%02x HPSW=%02x RST=%02x MIX=%02x GAIN=%02x",
             state, adc_format, dac_format, clock_state, hpsw, dac_reset, dac_mix, dac_gain);

    // Analogue-path isolation test: route the live ADC signal into both DACs.
    // This bypasses ESP->codec serial data entirely. A rising peak/feedback as
    // the mixer approaches 0 dB proves the DAC, output driver, speaker switch,
    // and physical speaker are all functional.
    writer.tone_channel = 0;
    codec_write(audio->codec, 0x69, 0xa0);
    codec_write(audio->codec, 0x4d, 0x02);
    codec_write(audio->codec, 0x40, 0x60);
    codec_write(audio->codec, 0x44, 0x00);
    uint16_t monitor_peak[5] = {measure_mic_peak(audio, 24), 0, 0, 0, 0};
    static const uint8_t monitor_gain[] = {0x40, 0x80, 0xbe};
    codec_write(audio->codec, 0x44, 0x03);
    for (int i = 0; i < 3; ++i) {
        codec_write(audio->codec, 0x48, monitor_gain[i]);
        vTaskDelay(pdMS_TO_TICKS(50));
        monitor_peak[i + 1] = measure_mic_peak(audio, 24);
    }
    // Revision 6.0 documents bit 5 as EN_DACVREFGEN, while legacy vendor
    // drivers leave it clear. Test the documented value against the legacy
    // value on the actual board, with all other analogue routing unchanged.
    codec_write(audio->codec, 0x61, 0xf9);
    vTaskDelay(pdMS_TO_TICKS(50));
    monitor_peak[4] = measure_mic_peak(audio, 24);
    const uint8_t selected_bias = monitor_peak[4] > monitor_peak[3] * 2U + 100U
        ? 0xf9 : 0xd9;
    codec_write(audio->codec, 0x61, selected_bias);
    codec_write(audio->codec, 0x44, 0x00);
    codec_write(audio->codec, 0x48, 0xbe);
    ESP_LOGI(TAG, "ADC->speaker monitor: off=%u D9/40=%u D9/80=%u D9/BE=%u F9/BE=%u bias=%02x %s",
             monitor_peak[0], monitor_peak[1], monitor_peak[2], monitor_peak[3],
             monitor_peak[4], selected_bias,
             (monitor_peak[3] > monitor_peak[0] * 2U + 100U ||
              monitor_peak[4] > monitor_peak[0] * 2U + 100U) ? "PASS" : "MISS");

    // Measure I2S/electrical feed-through with the DAC digitally muted.  The
    // same PCM then plays after unmute, so a higher second reading proves that
    // the analogue speaker-to-microphone path, rather than mere shared-clock
    // coupling, produced the test result.
    uint16_t muted_tone_peak = 0;
    // Revision 0x01 follows the 2026 register definition where 0x61 bit 5 is
    // EN_DACVREFGEN.  Legacy ES8389 drivers write 0xd9 (bit 5 clear), which
    // leaves the state machine ON but the DAC analogue reference disabled.
    codec_write(audio->codec, 0x61, 0xd9);
    codec_write(audio->codec, 0x69, 0x00);
    writer.tone_channel = 1;

    // First prove that the ES8389 is decoding the serial PCM.  Register 0x31
    // mixes DAC1/2 into ADC1/2 inside the digital core, before the analogue
    // output stage.  This cleanly separates an I2S format/polarity problem
    // from a silent headphone/line-output path.  Try every plausible 16-bit
    // stereo framing and retain the one with the strongest internal loopback.
    static const uint8_t dac_formats[] = {0x60, 0x70, 0x64, 0x74};
    uint16_t digital_loop_peak[8] = {0};
    int best_format = 0;
    int best_sclk_invert = 0;
    uint8_t clock_source = 0;
    codec_read(audio->codec, 0x02, &clock_source);
    codec_write(audio->codec, 0x31, 0xc0);
    for (int invert = 0; invert < 2; ++invert) {
        codec_write(audio->codec, 0x02, (clock_source & ~0x01u) | invert);
        for (int i = 0; i < 4; ++i) {
            const int result_index = invert * 4 + i;
            codec_write(audio->codec, 0x40, dac_formats[i]);
            vTaskDelay(pdMS_TO_TICKS(40));
            digital_loop_peak[result_index] = measure_mic_peak(audio, 24);
            const int best_index = best_sclk_invert * 4 + best_format;
            if (digital_loop_peak[result_index] > digital_loop_peak[best_index]) {
                best_format = i;
                best_sclk_invert = invert;
            }
        }
    }
    codec_write(audio->codec, 0x31, 0x00);
    if (digital_loop_peak[best_sclk_invert * 4 + best_format] <= 4000) {
        // Never select a framing mode from sub-noise readings. The host and
        // codec are both configured for Philips I2S, normal LRCLK polarity.
        best_format = 0;
        best_sclk_invert = 0;
    }
    codec_write(audio->codec, 0x02, (clock_source & ~0x01u) | best_sclk_invert);
    codec_write(audio->codec, 0x40, dac_formats[best_format]);
    ESP_LOGI(TAG, "DAC digital loop: edge0=%u/%u/%u/%u edge1=%u/%u/%u/%u selected=0x%02x sclk_inv=%d %s",
             digital_loop_peak[0], digital_loop_peak[1], digital_loop_peak[2],
             digital_loop_peak[3], digital_loop_peak[4], digital_loop_peak[5],
             digital_loop_peak[6], digital_loop_peak[7], dac_formats[best_format],
             best_sclk_invert,
             digital_loop_peak[best_sclk_invert * 4 + best_format] > 4000 ? "PASS" : "MISS");

    codec_write(audio->codec, 0x40, dac_formats[best_format] | 0x03);
    muted_tone_peak = measure_mic_peak(audio, 40);
    static const uint8_t output_variants[][2] = {
        {0xa0, 0x00}, {0xa0, 0x02}, {0xa3, 0x00}, {0xa3, 0x02},
    };
    uint16_t variant_peak[4] = {0};
    int best_variant = 0;
    codec_write(audio->codec, 0x40, dac_formats[best_format]);
    for (int i = 0; i < 4; ++i) {
        codec_write(audio->codec, 0x69, output_variants[i][0]);
        codec_write(audio->codec, 0x4d, output_variants[i][1]);
        vTaskDelay(pdMS_TO_TICKS(70));
        variant_peak[i] = measure_mic_peak(audio, 40);
        if (variant_peak[i] > variant_peak[best_variant]) best_variant = i;
    }
    if (variant_peak[best_variant] <= muted_tone_peak * 2U + 100U) {
        // Do not choose from noise.  Whisplay's Linux driver explicitly sets
        // HPSW[1:0]=3 and, for revision 0x01, retains DAC_RESET=0x02.
        best_variant = 3; // HPSW=0xA3, DAC_RESET=0x02
    }
    writer.tone_channel = 0;
    codec_write(audio->codec, 0x69, output_variants[best_variant][0]);
    codec_write(audio->codec, 0x4d, output_variants[best_variant][1]);
    ESP_LOGI(TAG, "DAC A/B baseline=%u variants A0/00=%u A0/02=%u A3/00=%u A3/02=%u selected=%d",
             muted_tone_peak, variant_peak[0], variant_peak[1], variant_peak[2],
             variant_peak[3], best_variant);

    audio->last_mic_level = 0;
    audio->last_mic_raw_peak = 0;
    uint8_t mic_peak = 0;
    uint16_t mic_raw_peak = 0;
    audio->mic_metric_frames = 0;
    audio->mic_metric_window_frames = 0;
    audio->mic_metric_windows = 0;
    audio->mic_metric_crossings[0] = audio->mic_metric_crossings[1] = 0;
    audio->mic_metric_previous[0] = audio->mic_metric_previous[1] = 0;
    audio->mic_metric_dc[0] = audio->mic_metric_dc[1] = 0;
    audio->mic_metric_goertzel_q1[0] = audio->mic_metric_goertzel_q1[1] = 0;
    audio->mic_metric_goertzel_q2[0] = audio->mic_metric_goertzel_q2[1] = 0;
    audio->mic_metric_energy[0] = audio->mic_metric_energy[1] = 0;
    audio->mic_metric_tone_best[0] = audio->mic_metric_tone_best[1] = 0;
    audio->mic_metric_min[0] = audio->mic_metric_min[1] = INT16_MAX;
    audio->mic_metric_max[0] = audio->mic_metric_max[1] = INT16_MIN;

    reader.peak = 0;
    reader.raw_peak = 0;
    audio->active = true;
    reader.running = true;
    if (xTaskCreate(audio_reader_task, "audio_reader", 4096, &reader, tskIDLE_PRIORITY, NULL) != pdPASS) {
        ESP_LOGW(TAG, "failed to start I2S reader task");
        reader.running = false;
        goto shutdown;
    }
    // Capture the board microphone during the clearly audible two-second tone;
    // its 1 kHz score is the hardware acoustic-loopback pass criterion.
    ESP_LOGI(TAG, "speaker test: 1 kHz LEFT channel");
    writer.tone_channel = 1;
    vTaskDelay(pdMS_TO_TICKS(40));
    log_i2s_pin_activity();
    vTaskDelay(pdMS_TO_TICKS(1200));
    ESP_LOGI(TAG, "speaker test: 1 kHz RIGHT channel");
    writer.tone_channel = 2;
    vTaskDelay(pdMS_TO_TICKS(1200));
    writer.tone_channel = 0;
    reader.running = false;
    for (int i = 0; i < 50 && !reader.stopped; ++i) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    audio->active = false;
    mic_peak = reader.peak;
    mic_raw_peak = reader.raw_peak;
    audio->last_mic_level = mic_peak;
    audio->last_mic_raw_peak = mic_raw_peak;
    finish_tone_window(audio);
    const unsigned int left_hz = audio->mic_metric_frames > 0
        ? (unsigned int)((uint64_t)audio->mic_metric_crossings[0] * AUDIO_SAMPLE_RATE /
                         (2 * audio->mic_metric_frames)) : 0;
    const unsigned int right_hz = audio->mic_metric_frames > 0
        ? (unsigned int)((uint64_t)audio->mic_metric_crossings[1] * AUDIO_SAMPLE_RATE /
                         (2 * audio->mic_metric_frames)) : 0;
    ESP_LOGI(TAG, "acoustic loopback: frames=%u L=%d..%d/%uHz tone=%u/1000 R=%d..%d/%uHz tone=%u/1000 peak=%u level=%u%%",
             (unsigned)audio->mic_metric_frames,
             audio->mic_metric_min[0], audio->mic_metric_max[0], left_hz,
             audio->mic_metric_tone_best[0],
             audio->mic_metric_min[1], audio->mic_metric_max[1], right_hz,
             audio->mic_metric_tone_best[1], mic_raw_peak, mic_peak);
    ESP_LOGI(TAG, "speaker A/B: muted=%u active=%u analogue_delta=%s",
             muted_tone_peak, mic_raw_peak,
             mic_raw_peak > muted_tone_peak * 2U + 100U ? "PASS" : "MISS");

    vTaskDelay(pdMS_TO_TICKS(80));
    if (official_codec_close() != ESP_OK) {
        ESP_LOGW(TAG, "failed to disconnect ES8389 speaker");
    } else {
        codec_awake = false;
    }

shutdown:
    audio->active = false;
    if (codec_awake && official_codec_close() != ESP_OK) {
        ESP_LOGW(TAG, "failed to power down ES8389");
    }
    writer.running = false;
    reader.running = false;
    for (int i = 0; i < 50 && !writer.stopped; ++i) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    for (int i = 0; i < 50 && !reader.stopped; ++i) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    if (writer.failed) {
        ESP_LOGW(TAG, "continuous I2S writer reported a failure");
    }
    if (rx_enabled && i2s_channel_disable(audio->rx) != ESP_OK) {
        ESP_LOGW(TAG, "failed to disable I2S RX");
    }
    if (audio->rx != NULL) {
        i2s_del_channel(audio->rx);
        audio->rx = NULL;
    }
    if (tx_enabled && i2s_channel_disable(audio->tx) != ESP_OK) {
        ESP_LOGW(TAG, "failed to disable I2S TX");
    }
    for (int i = 0; i < 50 && !writer.stopped; ++i) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    for (int i = 0; i < 50 && !reader.stopped; ++i) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    ESP_LOGI(TAG, "audio test complete, mic raw peak=%u level=%u%%, codec powered down",
             audio->last_mic_raw_peak, audio->last_mic_level);
}

void whisplay_audio_play_tone(whisplay_audio_t *audio)
{
    if (!audio->ready) return;

    const bool onboard = audio->onboard_codec;
    release_i2s_channels(audio);
    if (onboard && s_es8311_if != NULL) {
        // Recording mode leaves ES8311 enabled with NS4150B PA forced low.
        // Fully stop that codec instance before enabling playback; otherwise
        // the driver's enable reference count treats playback as already on
        // and never raises PA_CTRL again.
        ESP_ERROR_CHECK_WITHOUT_ABORT(codec_result(s_es8311_if->enable(s_es8311_if, false),
                                                   "ES8311 stop record before playback"));
    } else if (!onboard && s_codec_if != NULL && s_codec_open) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(official_codec_close());
    }

    const esp_err_t create_result = onboard
        ? create_onboard_i2s_tx(audio) : create_i2s_tx(audio);
    if (create_result != ESP_OK || i2s_channel_enable(audio->tx) != ESP_OK) {
        ESP_LOGE(TAG, "%s audio test: failed to start TX-only I2S",
                 onboard ? "onboard" : "Whisplay");
        release_i2s_channels(audio);
        ESP_ERROR_CHECK_WITHOUT_ABORT(onboard ? start_onboard_microphone(audio)
                                                : start_whisplay_microphone(audio));
        return;
    }
    vTaskDelay(pdMS_TO_TICKS(100));
    const esp_err_t codec_result_value = onboard
        ? onboard_es8311_enable(true) : official_codec_open();
    if (codec_result_value != ESP_OK) {
        ESP_LOGE(TAG, "%s audio test: failed to enable codec playback",
                 onboard ? "onboard" : "Whisplay");
        release_i2s_channels(audio);
        ESP_ERROR_CHECK_WITHOUT_ABORT(onboard ? start_onboard_microphone(audio)
                                                : start_whisplay_microphone(audio));
        return;
    }

    audio_writer_context_t writer = {
        .audio = audio,
        .running = true,
        .tone_channel = 1,
    };
    if (xTaskCreate(audio_writer_task, "production_tone", 4096, &writer, 5, NULL) != pdPASS) {
        ESP_LOGE(TAG, "audio test: failed to create tone task");
        writer.running = false;
    } else {
        ESP_LOGI(TAG, "%s speaker test: stereo 1 kHz for 3 seconds",
                 onboard ? "ES8311" : "ES8389");
        vTaskDelay(pdMS_TO_TICKS(3000));
        writer.running = false;
        for (int i = 0; i < 100 && !writer.stopped; ++i) vTaskDelay(pdMS_TO_TICKS(2));
    }

    if (onboard && s_es8311_if != NULL) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(codec_result(s_es8311_if->enable(s_es8311_if, false),
                                                   "ES8311 disable"));
    } else if (!onboard) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(official_codec_close());
    }
    release_i2s_channels(audio);

    const esp_err_t mic_result = onboard ? start_onboard_microphone(audio)
                                         : start_whisplay_microphone(audio);
    if (mic_result != ESP_OK) {
        ESP_LOGE(TAG, "%s audio test: failed to restore live microphone",
                 onboard ? "onboard" : "Whisplay");
        return;
    }
    uint16_t peak = 0;
    for (int i = 0; i < 20; ++i) {
        whisplay_audio_read_mic_level(audio);
        if (audio->last_mic_raw_peak > peak) peak = audio->last_mic_raw_peak;
    }
    audio->last_mic_raw_peak = peak;
    ESP_LOGI(TAG, "%s test complete: microphone restored, raw peak=%u level=%u%%",
             onboard ? "ES8311" : "ES8389", peak, audio->last_mic_level);
}
