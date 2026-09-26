#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_err.h"

typedef struct {
    i2c_master_dev_handle_t codec;
    i2s_chan_handle_t tx;
    i2s_chan_handle_t rx;
    bool ready;
    bool active;
    uint8_t codec_version;
    uint8_t last_mic_level;
    uint16_t last_mic_raw_peak;
    uint32_t mic_metric_frames;
    uint16_t mic_metric_window_frames;
    uint16_t mic_metric_windows;
    uint32_t mic_metric_crossings[2];
    int16_t mic_metric_previous[2];
    int32_t mic_metric_dc[2];
    float mic_metric_goertzel_q1[2];
    float mic_metric_goertzel_q2[2];
    uint64_t mic_metric_energy[2];
    uint16_t mic_metric_tone_best[2];
    int16_t mic_metric_min[2];
    int16_t mic_metric_max[2];
    bool onboard_codec;
} whisplay_audio_t;

esp_err_t whisplay_audio_init(i2c_master_bus_handle_t bus, whisplay_audio_t *audio);
uint8_t whisplay_audio_read_mic_level(whisplay_audio_t *audio);
void whisplay_audio_play_tone(whisplay_audio_t *audio);
esp_err_t whisplay_audio_set_onboard_codec(whisplay_audio_t *audio, bool onboard);
