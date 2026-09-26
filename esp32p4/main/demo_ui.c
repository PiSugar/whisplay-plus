#include "demo_ui.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define W 480
#define H 640

typedef struct {
    uint8_t r, g, b;
} color_t;

#define C(R, G, B) ((color_t){R, G, B})

static const color_t BACKGROUND = C(8, 13, 24);
static const color_t PANEL = C(18, 26, 42);
static const color_t PANEL_DARK = C(11, 18, 31);
static const color_t TEXT = C(236, 241, 230);
static const color_t MUTED = C(119, 139, 150);
static const color_t CYAN = C(35, 214, 214);
static const color_t MAGENTA = C(236, 62, 143);
static const color_t AMBER = C(247, 176, 54);
static const color_t LIME = C(157, 213, 69);
static const color_t RED = C(239, 79, 67);
static const color_t BLUE = C(67, 127, 239);

static void pixel(uint8_t *fb, int x, int y, color_t color)
{
    if ((unsigned)x >= W || (unsigned)y >= H) {
        return;
    }
    uint8_t *p = &fb[(y * W + x) * 3];
    // The ST7701P DPI path consumes each RGB888 pixel in B, G, R byte order
    // in memory. Keeping color_t in the natural RGB order makes the palette
    // readable, while storing the bytes in panel order prevents red/blue
    // swaps (for example BLUE appearing orange on screen).
    p[0] = color.b;
    p[1] = color.g;
    p[2] = color.r;
}

static void blend_pixel(uint8_t *fb, int x, int y, color_t color, uint8_t alpha)
{
    if ((unsigned)x >= W || (unsigned)y >= H) {
        return;
    }
    uint8_t *p = &fb[(y * W + x) * 3];
    const unsigned inverse = 255U - alpha;
    p[0] = (uint8_t)((p[0] * inverse + color.b * alpha) / 255U);
    p[1] = (uint8_t)((p[1] * inverse + color.g * alpha) / 255U);
    p[2] = (uint8_t)((p[2] * inverse + color.r * alpha) / 255U);
}

static void rect(uint8_t *fb, int x, int y, int w, int h, color_t color)
{
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > W) { w = W - x; }
    if (y + h > H) { h = H - y; }
    if (w <= 0 || h <= 0) { return; }
    for (int row = y; row < y + h; ++row) {
        for (int col = x; col < x + w; ++col) {
            pixel(fb, col, row, color);
        }
    }
}

static void line(uint8_t *fb, int x0, int y0, int x1, int y1, color_t color)
{
    int dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    int dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    int error = dx + dy;
    for (;;) {
        pixel(fb, x0, y0, color);
        if (x0 == x1 && y0 == y1) { break; }
        const int twice = 2 * error;
        if (twice >= dy) { error += dy; x0 += sx; }
        if (twice <= dx) { error += dx; y0 += sy; }
    }
}

static void circle(uint8_t *fb, int cx, int cy, int radius, color_t color, bool filled)
{
    int x = radius, y = 0, error = 1 - radius;
    while (x >= y) {
        if (filled) {
            line(fb, cx - x, cy + y, cx + x, cy + y, color);
            line(fb, cx - x, cy - y, cx + x, cy - y, color);
            line(fb, cx - y, cy + x, cx + y, cy + x, color);
            line(fb, cx - y, cy - x, cx + y, cy - x, color);
        } else {
            pixel(fb, cx + x, cy + y, color); pixel(fb, cx + y, cy + x, color);
            pixel(fb, cx - y, cy + x, color); pixel(fb, cx - x, cy + y, color);
            pixel(fb, cx - x, cy - y, color); pixel(fb, cx - y, cy - x, color);
            pixel(fb, cx + y, cy - x, color); pixel(fb, cx + x, cy - y, color);
        }
        ++y;
        if (error < 0) {
            error += 2 * y + 1;
        } else {
            --x;
            error += 2 * (y - x + 1);
        }
    }
}

static void fading_ring(uint8_t *fb, int cx, int cy, int radius,
                        color_t color, uint8_t alpha)
{
    int x = radius, y = 0, error = 1 - radius;
    while (x >= y) {
        blend_pixel(fb, cx + x, cy + y, color, alpha);
        blend_pixel(fb, cx + y, cy + x, color, alpha);
        blend_pixel(fb, cx - y, cy + x, color, alpha);
        blend_pixel(fb, cx - x, cy + y, color, alpha);
        blend_pixel(fb, cx - x, cy - y, color, alpha);
        blend_pixel(fb, cx - y, cy - x, color, alpha);
        blend_pixel(fb, cx + y, cy - x, color, alpha);
        blend_pixel(fb, cx + x, cy - y, color, alpha);
        ++y;
        if (error < 0) {
            error += 2 * y + 1;
        } else {
            --x;
            error += 2 * (y - x + 1);
        }
    }
}

static const uint8_t *glyph(char input)
{
    static const uint8_t blank[7] = {0};
    static const uint8_t letters[26][7] = {
        {14,17,17,31,17,17,17}, {30,17,17,30,17,17,30}, {14,17,16,16,16,17,14},
        {30,17,17,17,17,17,30}, {31,16,16,30,16,16,31}, {31,16,16,30,16,16,16},
        {14,17,16,23,17,17,15}, {17,17,17,31,17,17,17}, {31,4,4,4,4,4,31},
        {1,1,1,1,17,17,14}, {17,18,20,24,20,18,17}, {16,16,16,16,16,16,31},
        {17,27,21,21,17,17,17}, {17,25,21,19,17,17,17}, {14,17,17,17,17,17,14},
        {30,17,17,30,16,16,16}, {14,17,17,17,21,18,13}, {30,17,17,30,20,18,17},
        {15,16,16,14,1,1,30}, {31,4,4,4,4,4,4}, {17,17,17,17,17,17,14},
        {17,17,17,17,17,10,4}, {17,17,17,21,21,21,10}, {17,17,10,4,10,17,17},
        {17,17,10,4,4,4,4}, {31,1,2,4,8,16,31},
    };
    static const uint8_t digits[10][7] = {
        {14,17,19,21,25,17,14}, {4,12,4,4,4,4,14}, {14,17,1,2,4,8,31},
        {30,1,1,14,1,1,30}, {2,6,10,18,31,2,2}, {31,16,16,30,1,1,30},
        {14,16,16,30,17,17,14}, {31,1,2,4,8,8,8}, {14,17,17,14,17,17,14},
        {14,17,17,15,1,1,14},
    };
    static const uint8_t colon[7] = {0,4,4,0,4,4,0};
    static const uint8_t dot[7] = {0,0,0,0,0,6,6};
    static const uint8_t dash[7] = {0,0,0,31,0,0,0};
    static const uint8_t slash[7] = {1,2,2,4,8,8,16};
    static const uint8_t plus[7] = {0,4,4,31,4,4,0};
    static const uint8_t percent[7] = {17,2,4,8,17,0,0};
    static const uint8_t equal[7] = {0,31,0,31,0,0,0};
    static const uint8_t under[7] = {0,0,0,0,0,0,31};
    const char c = (char)toupper((unsigned char)input);
    if (c >= 'A' && c <= 'Z') { return letters[c - 'A']; }
    if (c >= '0' && c <= '9') { return digits[c - '0']; }
    switch (c) {
    case ':': return colon; case '.': return dot; case '-': return dash;
    case '/': return slash; case '+': return plus; case '%': return percent;
    case '=': return equal; case '_': return under; default: return blank;
    }
}

static void text(uint8_t *fb, int x, int y, const char *value, int scale, color_t color)
{
    for (; *value; ++value, x += 6 * scale) {
        const uint8_t *rows = glyph(*value);
        for (int row = 0; row < 7; ++row) {
            for (int col = 0; col < 5; ++col) {
                if (rows[row] & (1 << (4 - col))) {
                    rect(fb, x + col * scale, y + row * scale, scale, scale, color);
                }
            }
        }
    }
}

static void panel(uint8_t *fb, int x, int y, int w, int h, color_t accent, const char *title)
{
    rect(fb, x + 4, y + 4, w, h, C(3, 7, 14));
    rect(fb, x, y, w, h, PANEL);
    rect(fb, x, y, 6, h, accent);
    rect(fb, x + 6, y, w - 6, 2, accent);
    text(fb, x + 16, y + 12, title, 2, TEXT);
}

static void status(uint8_t *fb, int x, int y, bool ok)
{
    rect(fb, x, y, 57, 17, ok ? LIME : RED);
    text(fb, x + 5, y + 2, ok ? "ONLINE" : "MISS", 1, BACKGROUND);
}

static void status_panel(uint8_t *fb, int x, int y, int w, int h,
                         color_t accent, const char *title, bool ok)
{
    rect(fb, x + 4, y + 4, w, h, C(3, 7, 14));
    rect(fb, x, y, w, h, PANEL);
    rect(fb, x, y, 6, h, accent);
    rect(fb, x + 6, y, w - 6, 2, accent);

    const int badge_x = x + w - 72;
    const int title_x = x + 16;
    const int title_width_2x = (int)strlen(title) * 12 - 2;
    const int title_scale = title_width_2x <= badge_x - title_x - 8 ? 2 : 1;
    const int title_y = y + (title_scale == 2 ? 12 : 17);
    text(fb, title_x, title_y, title, title_scale, TEXT);
    status(fb, badge_x, y + 12, ok);
}

static int scaled(float value, float maximum, int width)
{
    if (value > maximum) { value = maximum; }
    if (value < -maximum) { value = -maximum; }
    return (int)(value * width / maximum);
}

static void vector_bar(uint8_t *fb, int x, int y, int width, float value,
                       float maximum, color_t color, const char *name)
{
    text(fb, x, y - 2, name, 1, MUTED);
    const int start = x + 18;
    const int center = start + width / 2;
    rect(fb, start, y, width, 7, PANEL_DARK);
    rect(fb, center, y - 2, 1, 11, MUTED);
    const int amount = scaled(value, maximum, width / 2 - 2);
    if (amount >= 0) { rect(fb, center + 1, y + 1, amount, 5, color); }
    else { rect(fb, center + amount, y + 1, -amount, 5, color); }
}

static void checklist(uint8_t *fb, int x, int y, const char *name, bool ok, color_t color)
{
    rect(fb, x, y, 12, 12, ok ? color : PANEL_DARK);
    if (ok) {
        line(fb, x + 2, y + 6, x + 5, y + 9, BACKGROUND);
        line(fb, x + 5, y + 9, x + 10, y + 2, BACKGROUND);
    }
    text(fb, x + 18, y + 2, name, 1, ok ? TEXT : MUTED);
}

void demo_ui_render(uint8_t *fb, const demo_state_t *s)
{
    rect(fb, 0, 0, W, H, BACKGROUND);
    for (int y = 0; y < H; y += 4) {
        rect(fb, 0, y, W, 1, C(10, 17, 29));
    }

    rect(fb, 0, 0, W, 58, C(15, 23, 38));
    rect(fb, 0, 54, W, 4, AMBER);
    text(fb, 15, 11, "WHISPLAY P4", 3, TEXT);
    text(fb, 17, 38, "HARDWARE LAB / METRO DIAGNOSTIC", 1, AMBER);
    char buffer[64];
    snprintf(buffer, sizeof(buffer), "%02lu:%02lu", (unsigned long)(s->uptime_s / 60),
             (unsigned long)(s->uptime_s % 60));
    text(fb, 409, 17, buffer, 1, MUTED);

    status_panel(fb, 14, 70, 218, 116, CYAN, "DISPLAY", s->display_ok);
    text(fb, 31, 109, "480 X 640", 2, CYAN);
    text(fb, 31, 132, "RGB888 / 2-LANE DSI", 1, MUTED);
    for (int i = 0; i < 7; ++i) {
        rect(fb, 31 + i * 24, 151, 20, 18,
             (color_t[]){CYAN, MAGENTA, AMBER, LIME, RED, BLUE, TEXT}[i]);
    }

    status_panel(fb, 242, 70, 224, 116, MAGENTA, "TOUCH", s->touch_ok);
    snprintf(buffer, sizeof(buffer), "%u / 5 CONTACTS", s->touch_count);
    text(fb, 259, 109, buffer, 1, s->touch_count ? MAGENTA : MUTED);
    rect(fb, 259, 126, 188, 43, PANEL_DARK);
    static const color_t touch_colors[5] = {MAGENTA, CYAN, AMBER, LIME, BLUE};
    for (uint8_t i = 0; i < s->touch_count; ++i) {
        const int tx = 263 + s->touches[i].x * 180 / W;
        const int ty = 130 + s->touches[i].y * 35 / H;
        circle(fb, tx, ty, 5, touch_colors[i], true);
    }
    snprintf(buffer, sizeof(buffer), "EVENTS %lu", (unsigned long)s->touch_events);
    text(fb, 259, 173, buffer, 1, MUTED);

    status_panel(fb, 14, 198, 286, 138, AMBER, "MOTION / QMI8658B", s->imu_ok);
    vector_bar(fb, 31, 241, 116, s->ax_g, 2.0f, RED, "AX");
    vector_bar(fb, 31, 256, 116, s->ay_g, 2.0f, LIME, "AY");
    vector_bar(fb, 31, 271, 116, s->az_g, 2.0f, BLUE, "AZ");
    vector_bar(fb, 166, 241, 102, s->gx_dps, 300.0f, RED, "GX");
    vector_bar(fb, 166, 256, 102, s->gy_dps, 300.0f, LIME, "GY");
    vector_bar(fb, 166, 271, 102, s->gz_dps, 300.0f, BLUE, "GZ");
    snprintf(buffer, sizeof(buffer), "A %+0.2F %+0.2F %+0.2F G", s->ax_g, s->ay_g, s->az_g);
    text(fb, 31, 297, buffer, 1, TEXT);
    snprintf(buffer, sizeof(buffer), "T %0.1F C", s->temperature_c);
    text(fb, 31, 315, buffer, 1, AMBER);

    status_panel(fb, 310, 198, 156, 138, LIME, "COMPASS", s->mag_ok);
    const int ccx = 388, ccy = 272, radius = 42;
    circle(fb, ccx, ccy, radius, s->mag_ok ? LIME : MUTED, false);
    circle(fb, ccx, ccy, 2, TEXT, true);
    for (int i = 0; i < 4; ++i) {
        const float a = i * 1.5707963f;
        line(fb, ccx + (int)(cosf(a) * 35), ccy + (int)(sinf(a) * 35),
             ccx + (int)(cosf(a) * 42), ccy + (int)(sinf(a) * 42), MUTED);
    }
    if (s->mag_ok) {
        const float angle = (s->heading_deg - 90.0f) * 0.0174532925f;
        line(fb, ccx, ccy, ccx + (int)(cosf(angle) * 34), ccy + (int)(sinf(angle) * 34), RED);
    }
    snprintf(buffer, sizeof(buffer), "%03.0F DEG", s->heading_deg);
    text(fb, 354, 321, buffer, 1, s->mag_ok ? TEXT : MUTED);

    status_panel(fb, 14, 346, 286, 126, RED, "WHISPLAY SOUND", s->audio_ok);
    rect(fb, 31, 379, 110, 20, s->audio_onboard ? PANEL_DARK : RED);
    rect(fb, 145, 379, 121, 20, s->audio_onboard ? BLUE : PANEL_DARK);
    text(fb, 39, 385, "HAT ES8389", 1, s->audio_onboard ? MUTED : BACKGROUND);
    text(fb, 151, 385, "BOARD ES8311", 1, s->audio_onboard ? BACKGROUND : MUTED);
    text(fb, 31, 405, s->audio_recording ? "MIC REC" : "MIC LIVE", 1,
         s->audio_recording ? RED : MUTED);
    rect(fb, 58, 404, 183, 12, PANEL_DARK);
    rect(fb, 60, 406, s->mic_level * 179 / 100, 8,
         s->mic_level > 75 ? RED : (s->mic_level > 35 ? AMBER : LIME));
    snprintf(buffer, sizeof(buffer), "%u%%", s->mic_level);
    text(fb, 247, 406, buffer, 1, TEXT);
    const bool record_hot = s->audio_record_pressed || s->audio_recording;
    const bool play_hot = s->audio_play_pressed || s->audio_playing;
    rect(fb, 31, 430, 112, 27,
         s->audio_ok ? (record_hot ? PANEL_DARK : RED) : PANEL_DARK);
    rect(fb, 149, 430, 117, 27,
         s->audio_ok && s->audio_clip_ready ? (play_hot ? PANEL_DARK : BLUE) : PANEL_DARK);
    const int audio_progress = s->audio_progress > 100 ? 100 : s->audio_progress;
    if (s->audio_recording) {
        rect(fb, 31, 430, 112 * audio_progress / 100, 27, RED);
        snprintf(buffer, sizeof(buffer), "REC %u%%", audio_progress);
        text(fb, 50, 437, buffer, 1, TEXT);
    } else {
        text(fb, 50, 437, "RECORD 10S", 1,
             s->audio_ok ? (record_hot ? TEXT : BACKGROUND) : MUTED);
    }
    if (s->audio_playing) {
        rect(fb, 149, 430, 117 * audio_progress / 100, 27, BLUE);
        snprintf(buffer, sizeof(buffer), "PLAY %u%%", audio_progress);
        text(fb, 181, 437, buffer, 1, TEXT);
    } else {
        text(fb, 178, 437, "PLAY CLIP", 1,
             s->audio_ok && s->audio_clip_ready ? (play_hot ? TEXT : BACKGROUND) : MUTED);
    }

    panel(fb, 310, 346, 156, 126, BLUE, "I / O");
    circle(fb, 338, 395, 14, s->button_pressed ? AMBER : MUTED, s->button_pressed);
    text(fb, 361, 389, s->button_pressed ? "PRESSED" : "BUTTON", 1, TEXT);
    static const char *rgb_names[] = {"OFF", "RED", "GREEN", "BLUE"};
    static const color_t rgb_colors[] = {MUTED, RED, LIME, BLUE};
    rect(fb, 326, 420, 124, 34, s->rgb_pressed ? BLUE : PANEL_DARK);
    circle(fb, 343, 437, 9, rgb_colors[s->rgb_step & 3], true);
    text(fb, 361, 433, s->rgb_pressed ? "CLICK!" : rgb_names[s->rgb_step & 3], 1,
         s->rgb_pressed ? TEXT : rgb_colors[s->rgb_step & 3]);
    text(fb, 326, 460, s->rgb_pressed ? "INPUT RECEIVED" : "TAP OR HAT KEY", 1,
         s->rgb_pressed ? BLUE : MUTED);

    panel(fb, 14, 482, 452, 144, CYAN, "SYSTEM CHECK");
    checklist(fb, 31, 516, "DSI DISPLAY", s->display_ok, CYAN);
    checklist(fb, 31, 536, "GT911 MULTITOUCH", s->touch_ok, MAGENTA);
    checklist(fb, 31, 556, "ES8389 AUDIO", s->audio_ok, RED);
    checklist(fb, 31, 576, "QMI8658B IMU", s->imu_ok, AMBER);
    checklist(fb, 31, 596, "MQC5883P MAG", s->mag_ok, LIME);
    snprintf(buffer, sizeof(buffer), "TOUCH %lu", (unsigned long)s->touch_events);
    text(fb, 265, 520, buffer, 1, TEXT);
    snprintf(buffer, sizeof(buffer), "BUTTON %lu", (unsigned long)s->button_events);
    text(fb, 265, 540, buffer, 1, TEXT);
    snprintf(buffer, sizeof(buffer), "REC %lu PLAY %lu", (unsigned long)s->record_events,
             (unsigned long)s->playback_events);
    text(fb, 265, 560, buffer, 1, TEXT);
    text(fb, 265, 584, s->mag_ok ? "ALL SENSORS READY" : "MAG SENSOR MISSING", 1,
         s->mag_ok ? LIME : RED);
    text(fb, 265, 604, "MOVE / TOUCH / SPEAK", 1, CYAN);

    // Draw touch feedback last so it remains visible above every tile. Three
    // staggered rings create a short Metro-style water ripple at the exact
    // mapped screen coordinate reported to hit testing.
    if (s->ripple_active) {
        const int radius = 5 + s->ripple_age * 8;
        const uint8_t alpha = (uint8_t)(225 - s->ripple_age * 31);
        if (radius > 14) {
            fading_ring(fb, s->ripple_x, s->ripple_y, radius - 13,
                        C(232, 247, 255), alpha / 2);
        }
        if (radius > 7) {
            fading_ring(fb, s->ripple_x, s->ripple_y, radius - 6,
                        C(91, 224, 231), alpha * 3 / 4);
        }
        fading_ring(fb, s->ripple_x, s->ripple_y, radius, CYAN, alpha);
        if (s->ripple_age < 3) {
            circle(fb, s->ripple_x, s->ripple_y, 2, TEXT, true);
        }
    }
}

bool demo_ui_hit_audio_record(uint16_t x, uint16_t y)
{
    return x >= 31 && x < 144 && y >= 426 && y < 460;
}

bool demo_ui_hit_audio_play(uint16_t x, uint16_t y)
{
    return x >= 149 && x < 266 && y >= 426 && y < 460;
}

bool demo_ui_hit_audio_mode(uint16_t x, uint16_t y)
{
    return x >= 31 && x < 266 && y >= 375 && y < 403;
}

bool demo_ui_hit_rgb(uint16_t x, uint16_t y)
{
    return x >= 310 && x < 466 && y >= 346 && y < 472;
}
