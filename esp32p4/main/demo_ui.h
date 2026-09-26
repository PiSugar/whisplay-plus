#pragma once

#include <stdbool.h>
#include <stdint.h>

#define DEMO_MAX_TOUCHES 5

typedef struct {
    uint16_t x;
    uint16_t y;
    uint16_t raw_x;
    uint16_t raw_y;
    uint8_t id;
} demo_touch_t;

typedef struct {
    bool display_ok;
    bool touch_ok;
    bool audio_ok;
    bool imu_ok;
    bool mag_ok;
    uint8_t touch_count;
    demo_touch_t touches[DEMO_MAX_TOUCHES];
    bool button_pressed;
    bool audio_testing;
    bool audio_record_pressed;
    bool audio_play_pressed;
    bool audio_recording;
    bool audio_playing;
    bool audio_clip_ready;
    uint8_t audio_progress;
    bool audio_onboard;
    bool rgb_pressed;
    bool ripple_active;
    uint16_t ripple_x;
    uint16_t ripple_y;
    uint8_t ripple_age;
    uint8_t rgb_step;
    float ax_g;
    float ay_g;
    float az_g;
    float gx_dps;
    float gy_dps;
    float gz_dps;
    float temperature_c;
    int16_t mag_x;
    int16_t mag_y;
    int16_t mag_z;
    float heading_deg;
    uint8_t mag_status;
    uint8_t mic_level;
    uint32_t uptime_s;
    uint32_t touch_events;
    uint32_t button_events;
    uint32_t tone_events;
    uint32_t record_events;
    uint32_t playback_events;
} demo_state_t;

void demo_ui_render(uint8_t *framebuffer, const demo_state_t *state);
bool demo_ui_hit_audio_record(uint16_t x, uint16_t y);
bool demo_ui_hit_audio_play(uint16_t x, uint16_t y);
bool demo_ui_hit_audio_mode(uint16_t x, uint16_t y);
bool demo_ui_hit_rgb(uint16_t x, uint16_t y);
