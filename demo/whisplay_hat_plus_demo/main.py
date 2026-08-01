#!/usr/bin/env python3
from __future__ import annotations

import math
import os
import subprocess
import sys
import threading
import time

os.environ.setdefault("SDL_ENABLE_SCREEN_KEYBOARD", "0")
os.environ.setdefault("SDL_IME_SHOW_UI", "0")
os.environ.setdefault("SDL_TOUCH_MOUSE_EVENTS", "0")
os.environ.setdefault("SDL_MOUSE_TOUCH_EVENTS", "0")

import pygame

from . import audio
from .gpio_hw import GpioHardware

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus  # type: ignore

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "sensors"))
from whisplay_sensors import QMC6309, QMI8658B  # noqa: E402


BG = (12, 15, 20)
PANEL = (25, 31, 40)
PANEL_2 = (32, 40, 51)
LINE = (62, 76, 92)
TEXT = (238, 242, 247)
MUTED = (151, 162, 176)
GREEN = (57, 205, 151)
BLUE = (94, 166, 255)
YELLOW = (245, 187, 74)
RED = (255, 100, 106)


class LowPass:
    def __init__(self, alpha: float):
        self.alpha = alpha
        self.value: tuple[float, ...] | None = None

    def update(self, sample: tuple[float, ...]) -> tuple[float, ...]:
        if self.value is None:
            self.value = sample
        else:
            self.value = tuple(old + (new - old) * self.alpha for old, new in zip(self.value, sample))
        return self.value

    def reset(self) -> None:
        self.value = None


class HatPlusDemo:
    def __init__(self):
        pygame.init()
        pygame.key.stop_text_input()
        pygame.display.set_caption("Whisplay Hat+ Demo")
        self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.width, self.height = self.screen.get_size()
        self.clock = pygame.time.Clock()
        self.title_font = pygame.font.SysFont("DejaVu Sans", 24, bold=True)
        self.body_font = pygame.font.SysFont("DejaVu Sans", 16)
        self.small_font = pygame.font.SysFont("DejaVu Sans", 13)
        self.big_font = pygame.font.SysFont("DejaVu Sans", 32, bold=True)

        self.running = True
        self.status = "Ready"
        self.active_touches: dict[int, tuple[int, int, float]] = {}
        self.touch_points: list[tuple[int, int, float, int]] = []
        self.last_finger_event_at = 0.0
        self.buttons = []
        self.pressed_button = ""
        self.button_flash_until = 0.0

        self.bus = None
        self.qmi = None
        self.qmc = None
        self.imu_error = ""
        self.mag_error = ""
        self.imu_filter = LowPass(0.24)
        self.mag_filter = LowPass(0.2)
        self.gyro_offsets = (0.0, 0.0, 0.0)
        self.mag_offsets = (0.0, 0.0, 0.0)
        self.mag_min = [32767.0, 32767.0, 32767.0]
        self.mag_max = [-32768.0, -32768.0, -32768.0]
        self.calibrating = False
        self.cal_phase = ""
        self.cal_started = 0.0
        self.gyro_samples = 0
        self.gyro_sum = [0.0, 0.0, 0.0]
        self.gyro_cal_duration = 2.0
        self.mag_cal_duration = 12.0

        self.gpio = None
        self.gpio_error = ""
        self.led_index = 0
        self.button_test_active = False
        self.button_test_count = 0
        self.last_button_state = False
        self.last_button_event = "none"
        self.last_button_event_at = 0.0
        self.led_colors = [
            ("Red", (255, 0, 0)),
            ("Green", (0, 255, 0)),
            ("Blue", (0, 0, 255)),
            ("White", (255, 255, 255)),
            ("Off", (0, 0, 0)),
        ]

        self.source = ""
        self.source_label = ""
        self.sink = ""
        self.sink_label = ""
        self.player = None
        self.record_thread = None
        self.recording = False
        self.record_peak = 0.0

        self.init_hardware()

    def init_hardware(self) -> None:
        try:
            self.bus = SMBus(1)
            self.qmi = QMI8658B(self.bus)
            self.qmc = QMC6309(self.bus)
        except Exception as exc:
            self.imu_error = str(exc)
            self.mag_error = str(exc)
        try:
            self.gpio = GpioHardware()
        except Exception as exc:
            self.gpio_error = str(exc)
        self.refresh_audio()

    def refresh_audio(self) -> None:
        self.source, self.source_label = audio.find_device("sources")
        self.sink, self.sink_label = audio.find_device("sinks")
        ok = audio.set_100_percent(self.source, self.sink)
        self.status = "Audio set to 100%" if ok else "Audio device selected"

    def layout_buttons(self) -> list[tuple[str, pygame.Rect, object]]:
        margin = 14
        gap = 8
        h = 34
        w = max(52, (self.width - margin * 2 - gap * 5) // 6)
        y = self.height - margin - h
        actions = [
            ("Tone", self.play_tone),
            ("Record", self.record_audio),
            ("LED", self.cycle_led),
            ("Btn", self.start_button_test),
            ("Cal", self.toggle_calibration),
            ("Quit", self.quit),
        ]
        return [(label, pygame.Rect(margin + i * (w + gap), y, w, h), action) for i, (label, action) in enumerate(actions)]

    def play_tone(self) -> None:
        self.stop_player()
        audio.set_100_percent(self.source, self.sink)
        try:
            self.player = audio.play_tone(self.sink)
            self.status = "Playing speaker tone at 100%"
        except Exception as exc:
            self.status = f"Tone failed: {exc}"

    def record_audio(self) -> None:
        if self.recording:
            return
        audio.set_100_percent(self.source, self.sink)
        self.recording = True
        self.status = "Recording mic at 100%"
        self.record_thread = threading.Thread(target=self._record_worker, daemon=True)
        self.record_thread.start()

    def _record_worker(self) -> None:
        try:
            path = audio.record_with_peak(self.source, 4.0, self.set_record_peak)
            self.record_peak = audio.wav_peak(path)
            audio.set_100_percent(self.source, self.sink)
            self.stop_player()
            cmd = ["paplay", f"--device={self.sink}", str(path)] if self.sink else ["paplay", str(path)]
            self.player = subprocess.Popen(cmd)
            self.status = f"Recording peak {int(self.record_peak * 100)}%, playing back"
        except Exception as exc:
            self.status = f"Record failed: {exc}"
        finally:
            self.recording = False

    def set_record_peak(self, peak: float) -> None:
        self.record_peak = max(0.0, min(1.0, peak))

    def cycle_led(self) -> None:
        if not self.gpio:
            self.status = f"LED unavailable: {self.fit(self.gpio_error, 40)}"
            return
        self.led_index = (self.led_index + 1) % len(self.led_colors)
        name, rgb = self.led_colors[self.led_index]
        self.gpio.set_rgb(*rgb)
        self.status = f"LED {name} RGB {rgb}"

    def start_button_test(self) -> None:
        self.button_test_active = True
        self.button_test_count = 0
        self.last_button_state = self.gpio.poll_button() if self.gpio else False
        self.last_button_event = "waiting"
        self.last_button_event_at = time.monotonic()
        self.status = "Button test: press the hardware button"

    def toggle_calibration(self) -> None:
        if self.calibrating:
            self.finish_calibration()
        else:
            self.start_calibration()

    def start_calibration(self) -> None:
        self.calibrating = True
        self.cal_phase = "gyro"
        self.cal_started = time.monotonic()
        self.gyro_samples = 0
        self.gyro_sum = [0.0, 0.0, 0.0]
        self.mag_min = [32767.0, 32767.0, 32767.0]
        self.mag_max = [-32768.0, -32768.0, -32768.0]
        self.imu_filter.reset()
        self.mag_filter.reset()
        self.status = "Hold still for gyro bias"

    def finish_gyro_phase(self) -> None:
        if self.gyro_samples:
            self.gyro_offsets = tuple(v / self.gyro_samples for v in self.gyro_sum)
        self.cal_phase = "mag"
        self.cal_started = time.monotonic()
        self.status = "Rotate in all directions"

    def finish_calibration(self) -> None:
        if self.cal_phase == "gyro":
            self.finish_gyro_phase()
        spans = [self.mag_max[i] - self.mag_min[i] for i in range(3)]
        if spans[0] > 50 and spans[1] > 50:
            self.mag_offsets = tuple((self.mag_min[i] + self.mag_max[i]) / 2.0 for i in range(3))
        self.calibrating = False
        self.cal_phase = ""
        self.imu_filter.reset()
        self.mag_filter.reset()
        self.status = "Calibration saved"

    def stop_player(self) -> None:
        if self.player and self.player.poll() is None:
            self.player.terminate()
            try:
                self.player.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.player.kill()
        self.player = None

    def quit(self) -> None:
        self.running = False

    def run(self) -> int:
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.FINGERDOWN:
                    self.last_finger_event_at = time.monotonic()
                    pos = self.finger_pos(event)
                    self.active_touches[event.finger_id] = (*pos, time.monotonic())
                    self.touch_points.append((*pos, time.monotonic(), len(self.active_touches) % 6))
                    self.press_button_at(pos)
                elif event.type == pygame.FINGERMOTION:
                    self.last_finger_event_at = time.monotonic()
                    pos = self.finger_pos(event)
                    self.active_touches[event.finger_id] = (*pos, time.monotonic())
                    self.touch_points.append((*pos, time.monotonic(), len(self.active_touches) % 6))
                elif event.type == pygame.FINGERUP:
                    self.last_finger_event_at = time.monotonic()
                    self.active_touches.pop(event.finger_id, None)
                    self.pressed_button = ""
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        self.running = False
                    elif event.key in (pygame.K_c, pygame.K_SPACE):
                        self.toggle_calibration()
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.ignore_synthetic_mouse():
                        continue
                    self.active_touches[-1] = (event.pos[0], event.pos[1], time.monotonic())
                    self.touch_points.append((event.pos[0], event.pos[1], time.monotonic(), 0))
                    self.press_button_at(event.pos)
                elif event.type == pygame.MOUSEMOTION and event.buttons[0]:
                    if self.ignore_synthetic_mouse():
                        continue
                    self.active_touches[-1] = (event.pos[0], event.pos[1], time.monotonic())
                    self.touch_points.append((event.pos[0], event.pos[1], time.monotonic(), 0))
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    if self.ignore_synthetic_mouse():
                        continue
                    self.active_touches.pop(-1, None)
                    self.pressed_button = ""
            self.width, self.height = self.screen.get_size()
            self.buttons = self.layout_buttons()
            imu = self.read_imu()
            mag = self.read_mag()
            button_pressed = self.gpio.poll_button() if self.gpio else False
            self.update_button_test(button_pressed)
            self.draw(imu, mag, button_pressed)
            pygame.display.flip()
            self.clock.tick(30)
        self.close()
        return 0

    def update_button_test(self, pressed: bool) -> None:
        if not self.button_test_active:
            self.last_button_state = pressed
            return
        if pressed == self.last_button_state:
            return
        self.last_button_state = pressed
        self.last_button_event_at = time.monotonic()
        if pressed:
            self.button_test_count += 1
            self.last_button_event = "pressed"
            self.status = f"Button press detected #{self.button_test_count}"
        else:
            self.last_button_event = "released"

    def finger_pos(self, event) -> tuple[int, int]:
        return round(event.x * self.width), round(event.y * self.height)

    def ignore_synthetic_mouse(self) -> bool:
        return time.monotonic() - self.last_finger_event_at < 0.35

    def press_button_at(self, pos) -> None:
        for label, rect, action in self.buttons:
            if rect.collidepoint(pos):
                self.pressed_button = label
                self.button_flash_until = time.monotonic() + 0.18
                action()
                break

    def read_imu(self):
        if not self.qmi:
            return None
        try:
            reading = self.qmi.read()
            if self.calibrating and self.cal_phase == "gyro":
                for i, value in enumerate((reading.gx_dps, reading.gy_dps, reading.gz_dps)):
                    self.gyro_sum[i] += value
                self.gyro_samples += 1
                if time.monotonic() - self.cal_started >= self.gyro_cal_duration:
                    self.finish_gyro_phase()
            ox, oy, oz = self.gyro_offsets
            return self.imu_filter.update((
                reading.ax_g,
                reading.ay_g,
                reading.az_g,
                reading.gx_dps - ox,
                reading.gy_dps - oy,
                reading.gz_dps - oz,
                reading.temp_c,
            ))
        except Exception as exc:
            self.imu_error = str(exc)
            return None

    def read_mag(self):
        if not self.qmc:
            return None
        try:
            raw = self.qmc.read()
            if self.calibrating and self.cal_phase == "mag":
                for i, value in enumerate((raw.x, raw.y, raw.z)):
                    self.mag_min[i] = min(self.mag_min[i], value)
                    self.mag_max[i] = max(self.mag_max[i], value)
                if time.monotonic() - self.cal_started >= self.mag_cal_duration:
                    self.finish_calibration()
            reading = self.qmc.read(self.mag_offsets)
            return self.mag_filter.update((reading.x, reading.y, reading.z, reading.heading_deg, reading.status, reading.strength))
        except Exception as exc:
            self.mag_error = str(exc)
            return None

    def draw(self, imu, mag, button_pressed: bool) -> None:
        self.screen.fill(BG)
        self.text("Whisplay Hat+ Demo", 18, 12, self.title_font, TEXT)
        self.text(self.fit(self.status, 44), 20, 42, self.small_font, MUTED)

        top = 70
        gap = 10
        panel_h = 92
        col_w = (self.width - 46) // 2
        self.draw_display_panel(18, top, col_w, panel_h)
        self.draw_audio_panel(28 + col_w, top, col_w, panel_h)
        self.draw_gpio_panel(18, top + panel_h + gap, col_w, panel_h, button_pressed)
        self.draw_imu_panel(28 + col_w, top + panel_h + gap, col_w, panel_h, imu)
        self.draw_mag_panel(18, top + (panel_h + gap) * 2, self.width - 36, 120, mag)

        now = time.monotonic()
        self.touch_points = [p for p in self.touch_points if now - p[2] < 1.5]
        palette = [YELLOW, BLUE, GREEN, RED, (207, 139, 255), (255, 154, 102)]
        for x, y, ts, color_index in self.touch_points[-80:]:
            alpha = max(0.2, 1.0 - (now - ts) / 1.5)
            pygame.draw.circle(self.screen, palette[color_index % len(palette)], (x, y), int(14 * alpha), 2)
        for index, (touch_id, (x, y, _)) in enumerate(list(self.active_touches.items())[:5], start=1):
            color = palette[index % len(palette)]
            pygame.draw.circle(self.screen, color, (x, y), 22, 3)
            self.center(str(index), (x, y), self.body_font, color)

        for label, rect, _ in self.buttons:
            display = "Finish" if label == "Cal" and self.calibrating else label
            color = GREEN if label in ("Tone", "Record", "Btn", "Cal") else PANEL_2
            if label == "Quit":
                color = (80, 50, 55)
            pressed = label == self.pressed_button or (label == self.pressed_button and time.monotonic() < self.button_flash_until)
            if pressed:
                color = tuple(min(255, c + 38) for c in color)
                rect = rect.inflate(-4, -4)
            elif label == self.pressed_button and time.monotonic() >= self.button_flash_until:
                self.pressed_button = ""
            pygame.draw.rect(self.screen, color, rect, border_radius=8)
            pygame.draw.rect(self.screen, LINE, rect, 1, border_radius=8)
            self.center(display, rect.center, self.body_font, TEXT)

    def draw_display_panel(self, x, y, w, h) -> None:
        self.panel(x, y, w, h)
        self.text("Display / Touch", x + 12, y + 10, self.body_font, GREEN)
        self.text(f"{self.width}x{self.height} fullscreen", x + 12, y + 38, self.small_font, TEXT)
        self.text(f"active {len(self.active_touches)}/5  trail {len(self.touch_points)}", x + 12, y + 62, self.small_font, MUTED)

    def draw_audio_panel(self, x, y, w, h) -> None:
        self.panel(x, y, w, h)
        self.text("Sound", x + 12, y + 10, self.body_font, GREEN if self.source and self.sink else RED)
        self.text(self.fit(self.sink_label, 22), x + 12, y + 34, self.small_font, TEXT)
        self.draw_level_bar(x + 12, y + 57, w - 24, 14, self.record_peak, active=self.recording)
        label = "live mic" if self.recording else f"mic peak {int(self.record_peak * 100)}%"
        self.text(label, x + 12, y + 74, self.small_font, MUTED)

    def draw_gpio_panel(self, x, y, w, h, pressed: bool) -> None:
        self.panel(x, y, w, h)
        self.text("LED / Button", x + 12, y + 10, self.body_font, GREEN if self.gpio else RED)
        state = "button pressed" if pressed else "button released"
        self.text(state, x + 12, y + 34, self.small_font, TEXT)
        if self.gpio_error:
            detail = self.fit(self.gpio_error, 26)
        elif self.button_test_active:
            age = time.monotonic() - self.last_button_event_at
            detail = f"test {self.button_test_count} {self.last_button_event} {age:.1f}s"
        else:
            detail = f"RGB {self.gpio.last_rgb if self.gpio else (0, 0, 0)}"
        self.text(detail, x + 12, y + 58, self.small_font, MUTED)
        led_rgb = self.gpio.last_rgb if self.gpio else (0, 0, 0)
        pygame.draw.circle(self.screen, led_rgb, (x + w - 34, y + 48), 22)
        pygame.draw.circle(self.screen, LINE, (x + w - 34, y + 48), 22, 2)

    def draw_imu_panel(self, x, y, w, h, imu) -> None:
        self.panel(x, y, w, h)
        self.text("QMI8658B", x + 12, y + 10, self.body_font, GREEN if imu else RED)
        if imu:
            ax, ay, az, gx, gy, gz, temp = imu
            total = math.sqrt(ax * ax + ay * ay + az * az)
            self.text(f"acc {total:.2f}g  temp {temp:.1f}C", x + 12, y + 38, self.small_font, TEXT)
            self.text(f"gyro {gx:+.1f} {gy:+.1f} {gz:+.1f}", x + 12, y + 62, self.small_font, MUTED)
            self.draw_orientation_cube((ax, ay, az), (x + w - 48, y + 50), 36)
        else:
            self.text(self.fit(self.imu_error, 26), x + 12, y + 38, self.small_font, MUTED)

    def draw_mag_panel(self, x, y, w, h, mag) -> None:
        self.panel(x, y, w, h)
        self.text("QMC6309", x + 12, y + 10, self.body_font, GREEN if mag else RED)
        if mag:
            mx, my, mz, heading, status, strength = mag
            self.text(f"mag {mx:+.0f} {my:+.0f} {mz:+.0f}", x + 12, y + 38, self.small_font, TEXT)
            self.text(f"heading {heading:05.1f}  |B| {strength:.0f}  st 0x{int(status):02x}", x + 12, y + 62, self.small_font, MUTED)
            if self.calibrating:
                elapsed = time.monotonic() - self.cal_started
                if self.cal_phase == "gyro":
                    self.text(f"cal: hold still {elapsed:.1f}/{self.gyro_cal_duration:.0f}s", x + 12, y + 86, self.small_font, YELLOW)
                else:
                    self.text(f"cal: rotate {elapsed:.1f}/{self.mag_cal_duration:.0f}s", x + 12, y + 86, self.small_font, YELLOW)
            else:
                ox, oy, oz = self.mag_offsets
                self.text(f"offset {ox:+.0f} {oy:+.0f} {oz:+.0f}", x + 12, y + 86, self.small_font, MUTED)
            self.draw_compass(heading, (self.width - 64, y + 62), 34)
        else:
            self.text(self.fit(self.mag_error, 42), x + 12, y + 38, self.small_font, MUTED)

    def draw_compass(self, heading: float, center, radius: int) -> None:
        pygame.draw.circle(self.screen, (18, 23, 30), center, radius)
        pygame.draw.circle(self.screen, LINE, center, radius, 2)
        rad = math.radians(heading - 90)
        end = (center[0] + math.cos(rad) * (radius - 6), center[1] + math.sin(rad) * (radius - 6))
        pygame.draw.line(self.screen, RED, center, end, 4)
        pygame.draw.circle(self.screen, RED, end, 5)

    def draw_level_bar(self, x: int, y: int, w: int, h: int, value: float, active: bool) -> None:
        value = max(0.0, min(1.0, value))
        pygame.draw.rect(self.screen, (18, 23, 30), (x, y, w, h), border_radius=5)
        pygame.draw.rect(self.screen, LINE, (x, y, w, h), 1, border_radius=5)
        fill = GREEN if active else BLUE
        pygame.draw.rect(self.screen, fill, (x + 2, y + 2, max(0, int((w - 4) * value)), h - 4), border_radius=4)
        for frac in (0.25, 0.5, 0.75):
            px = x + int(w * frac)
            pygame.draw.line(self.screen, LINE, (px, y + 2), (px, y + h - 3), 1)

    def draw_orientation_cube(self, accel: tuple[float, float, float], center, size: int) -> None:
        ax, ay, az = accel
        roll = math.atan2(ay, az if abs(az) > 0.001 else 0.001)
        pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az))
        yaw = 0.35
        vertices = [
            (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
        ]
        points = []
        for x, y, z in vertices:
            cy, sy = math.cos(yaw), math.sin(yaw)
            x, z = x * cy + z * sy, -x * sy + z * cy
            cp, sp = math.cos(pitch), math.sin(pitch)
            y, z = y * cp - z * sp, y * sp + z * cp
            cr, sr = math.cos(roll), math.sin(roll)
            x, y = x * cr - y * sr, x * sr + y * cr
            depth = 3.6 + z
            scale = size / depth
            points.append((round(center[0] + x * scale), round(center[1] + y * scale)))
        front = [points[i] for i in (4, 5, 6, 7)]
        pygame.draw.polygon(self.screen, (34, 48, 64), front)
        for a, b in ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)):
            pygame.draw.line(self.screen, LINE, points[a], points[b], 2)
        pygame.draw.line(self.screen, GREEN, points[4], points[5], 3)
        pygame.draw.line(self.screen, BLUE, points[4], points[7], 3)

    def panel(self, x: int, y: int, w: int, h: int) -> None:
        pygame.draw.rect(self.screen, PANEL, (x, y, w, h), border_radius=8)
        pygame.draw.rect(self.screen, LINE, (x, y, w, h), 1, border_radius=8)

    def text(self, value: str, x: int, y: int, font, color) -> None:
        self.screen.blit(font.render(value, True, color), (x, y))

    def center(self, value: str, pos, font, color) -> None:
        surface = font.render(value, True, color)
        self.screen.blit(surface, surface.get_rect(center=pos))

    def fit(self, value: str, chars: int) -> str:
        return value if len(value) <= chars else value[: chars - 3] + "..."

    def close(self) -> None:
        self.stop_player()
        if self.gpio:
            self.gpio.close()
        if self.bus:
            close = getattr(self.bus, "close", None)
            if close:
                close()
        pygame.quit()


def main() -> int:
    return HatPlusDemo().run()


if __name__ == "__main__":
    raise SystemExit(main())
