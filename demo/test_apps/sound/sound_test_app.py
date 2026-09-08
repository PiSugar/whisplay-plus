#!/usr/bin/env python3
from __future__ import annotations

import math
import os
import signal
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path

os.environ.setdefault("SDL_ENABLE_SCREEN_KEYBOARD", "0")
os.environ.setdefault("SDL_IME_SHOW_UI", "0")
import pygame


WIDTH = 360
HEIGHT = 390
BG = (13, 16, 21)
PANEL = (25, 31, 40)
PANEL_2 = (32, 40, 51)
TEXT = (238, 242, 247)
MUTED = (150, 160, 172)
ACCENT = (46, 196, 142)
BLUE = (78, 161, 255)
WARN = (255, 190, 83)
ERROR = (255, 100, 106)
LINE = (61, 75, 92)

RATE = 48000
CHANNELS = 1
SAMPLE_WIDTH = 2
RECORD_SECONDS = 5.0
RECORDING = Path("/tmp/whisplay-sound-test.wav")
TONE = Path("/tmp/whisplay-sound-tone.wav")
STEREO_THRESHOLD_DB = 3.0
STEREO_SILENCE_LEVEL = 0.015
STEREO_BALANCE_RANGE_DB = 6.0
STEREO_BALANCE_SMOOTH_SECONDS = 0.20


def run_text(args: list[str]) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=3)
    except Exception as exc:
        return str(exc)


def run_quiet(args: list[str]) -> bool:
    try:
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, check=True)
        return True
    except Exception:
        return False


def prop(block: str, key: str) -> str:
    marker = f'{key} = "'
    start = block.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = block.find('"', start)
    return block[start:end] if end >= 0 else ""


def audio_device(kind: str, direction: str, require_stereo: bool = False) -> tuple[str, str, str]:
    output = run_text(["pactl", "list", kind])
    blocks = [part for part in output.split("\n\n") if part.strip()]
    fallback: tuple[str, str, str] = ("", "not found", "")
    for block in blocks:
        lines = block.splitlines()
        name = ""
        for line in lines:
            if line.strip().startswith("Name:"):
                name = line.split("Name:", 1)[1].strip()
                break
        if not name or name.endswith(".monitor"):
            continue

        card = prop(block, "alsa.card_name") or prop(block, "api.alsa.card.name")
        codec = prop(block, "node.nick") or prop(block, "alsa.name")
        hw = prop(block, "api.alsa.path")
        sample_spec = next(
            (line.split("Sample Specification:", 1)[1].strip()
             for line in lines if "Sample Specification:" in line),
            "",
        )
        channel_map = next(
            (line.split("Channel Map:", 1)[1].strip()
             for line in lines if "Channel Map:" in line),
            "",
        )
        is_stereo = " 2ch " in f" {sample_spec} " or "," in channel_map
        if require_stereo and not is_stereo:
            continue
        label = f"{card} {direction}".strip() if card else name
        detail = " ".join(part for part in (codec, hw, channel_map) if part)
        candidate = (name, label, detail)
        if fallback[0] == "":
            fallback = candidate
        if card == "Whisplay Sound" or "whisplaysound" in block.lower():
            return candidate
    return fallback


def stereo_levels(chunk: bytes) -> tuple[float, float]:
    sample_count = len(chunk) // SAMPLE_WIDTH
    sample_count -= sample_count % 2
    if sample_count < 2:
        return 0.0, 0.0
    samples = struct.unpack(f"<{sample_count}h", chunk[: sample_count * SAMPLE_WIDTH])
    frames = sample_count // 2
    left_sq = sum(value * value for value in samples[0::2])
    right_sq = sum(value * value for value in samples[1::2])
    return (
        min(1.0, math.sqrt(left_sq / frames) / 32768.0),
        min(1.0, math.sqrt(right_sq / frames) / 32768.0),
    )


def stereo_direction(left: float, right: float) -> tuple[str, float]:
    if max(left, right) < STEREO_SILENCE_LEVEL:
        return "Waiting for sound", 0.0
    delta_db = 20.0 * math.log10((left + 1e-6) / (right + 1e-6))
    if delta_db >= STEREO_THRESHOLD_DB:
        return "LEFT", delta_db
    if delta_db <= -STEREO_THRESHOLD_DB:
        return "RIGHT", delta_db
    return "CENTER", delta_db


def duration_seconds(path: Path) -> float:
    if not path.exists() or path.stat().st_size < 44:
        return 0.0
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except Exception:
        return 0.0


def make_tone(path: Path) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        frames = bytearray()
        for i in range(int(RATE * 1.4)):
            t = i / RATE
            freq = 440.0 if t < 0.7 else 660.0
            envelope = min(1.0, i / 2400) * min(1.0, (int(RATE * 1.4) - i) / 2400)
            value = int(math.sin(2 * math.pi * freq * t) * 14000 * envelope)
            frames += struct.pack("<h", value)
        wav.writeframes(frames)


class Recorder:
    def __init__(self, source: str):
        self.source = source
        self.process: subprocess.Popen[bytes] | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.started_at = 0.0
        self.level = 0.0
        self.error = ""
        self.done = False

    @property
    def active(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self) -> None:
        self.stop()
        self.stop_event.clear()
        self.done = False
        self.error = ""
        self.started_at = time.time()
        self.thread = threading.Thread(target=self._record, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def _record(self) -> None:
        cmd = [
            "parecord",
            f"--device={self.source}",
            "--file-format=wav",
            str(RECORDING),
        ]
        if not self.source:
            cmd.pop(1)
        try:
            if RECORDING.exists():
                RECORDING.unlink()
            self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            while not self.stop_event.is_set() and time.time() - self.started_at < RECORD_SECONDS:
                self.level = self._file_peak(RECORDING)
                time.sleep(0.08)
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.stop()
            self.level = self._file_peak(RECORDING)
            self.done = True

    def _file_peak(self, path: Path) -> float:
        try:
            if not path.exists() or path.stat().st_size <= 4096:
                return 0.0
            with path.open("rb") as fh:
                fh.seek(max(44, path.stat().st_size - 8192))
                chunk = fh.read()
            chunk = chunk[: len(chunk) - (len(chunk) % 2)]
            if not chunk:
                return 0.0
            samples = struct.unpack("<" + "h" * (len(chunk) // 2), chunk)
            peak = max((abs(v) for v in samples), default=0)
            return min(1.0, peak / 32768.0)
        except Exception:
            return 0.0


class StereoMonitor:
    def __init__(self, source: str):
        self.source = source
        self.process: subprocess.Popen[bytes] | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.left = 0.0
        self.right = 0.0
        self.direction = "Stopped"
        self.delta_db = 0.0
        self.error = ""

    @property
    def active(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self) -> None:
        self.stop()
        self.stop_event.clear()
        self.error = ""
        self.direction = "Waiting for sound"
        if not self.source:
            self.error = "No stereo Whisplay input found"
            return
        self.thread = threading.Thread(target=self._capture, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

    def _capture(self) -> None:
        cmd = [
            "parecord",
            f"--device={self.source}",
            "--raw",
            "--format=s16le",
            f"--rate={RATE}",
            "--channels=2",
        ]
        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if not self.process.stdout:
                raise RuntimeError("cannot read stereo capture")
            while not self.stop_event.is_set():
                chunk = self.process.stdout.read(4096)
                if not chunk:
                    if self.process.poll() is not None:
                        raise RuntimeError(f"stereo capture exited ({self.process.returncode})")
                    continue
                left, right = stereo_levels(chunk)
                self.left += (left - self.left) * 0.35
                self.right += (right - self.right) * 0.35
                self.direction, self.delta_db = stereo_direction(self.left, self.right)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.error = str(exc)
                self.direction = "Error"
        finally:
            self.stop()


class SoundTestApp:
    def __init__(self):
        pygame.init()
        pygame.key.stop_text_input()
        pygame.display.set_caption("Whisplay Sound Test")
        self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.width, self.height = self.screen.get_size()
        self.clock = pygame.time.Clock()
        self.title_font = pygame.font.SysFont("DejaVu Sans", 24, bold=True)
        self.body_font = pygame.font.SysFont("DejaVu Sans", 17)
        self.small_font = pygame.font.SysFont("DejaVu Sans", 14)
        self.big_font = pygame.font.SysFont("DejaVu Sans", 38, bold=True)
        self.status = "Ready"
        self.source = ""
        self.source_label = ""
        self.source_detail = ""
        self.sink = ""
        self.sink_label = ""
        self.sink_detail = ""
        self.recorder: Recorder | None = None
        self.stereo_monitor: StereoMonitor | None = None
        self.stereo_source = ""
        self.stereo_label = ""
        self.stereo_detail = ""
        self.balance_position = 0.0
        self.balance_updated_at = time.monotonic()
        self.player: subprocess.Popen[bytes] | None = None
        self.refresh_devices()
        make_tone(TONE)
        self.buttons = self.layout_buttons()
        self.running = True

    def layout_buttons(self) -> list[tuple[str, pygame.Rect, object]]:
        margin = 18
        gap = 10
        button_w = max(120, (self.width - margin * 2 - gap) // 2)
        button_h = 38
        y = max(260, self.height - margin - button_h * 3 - gap * 2)
        left = margin
        right = margin + button_w + gap
        return [
            ("Record 5s", pygame.Rect(left, y, button_w, button_h), self.start_recording),
            ("L/R Mic", pygame.Rect(right, y, button_w, button_h), self.start_stereo_test),
            ("Stop", pygame.Rect(left, y + button_h + gap, button_w, button_h), self.stop_all),
            ("Play Rec", pygame.Rect(right, y + button_h + gap, button_w, button_h), self.play_recording),
            ("Tone Test", pygame.Rect(left, y + (button_h + gap) * 2, button_w, button_h), self.play_tone),
            ("Quit", pygame.Rect(right, y + (button_h + gap) * 2, button_w, button_h), self.quit),
        ]

    def refresh_devices(self) -> None:
        self.source, self.source_label, self.source_detail = audio_device("sources", "Input")
        self.stereo_source, self.stereo_label, self.stereo_detail = audio_device(
            "sources", "Stereo Input", require_stereo=True
        )
        self.sink, self.sink_label, self.sink_detail = audio_device("sinks", "Output")
        volume_ok = self.set_test_volumes()
        if self.source and self.sink:
            self.status = "Whisplay Sound card selected at 100%" if volume_ok else "Whisplay Sound card selected"
        else:
            self.status = "Audio device missing"

    def set_test_volumes(self) -> bool:
        ok = True
        if self.sink:
            ok = run_quiet(["pactl", "set-sink-mute", self.sink, "0"]) and ok
            ok = run_quiet(["pactl", "set-sink-volume", self.sink, "100%"]) and ok
        if self.source:
            ok = run_quiet(["pactl", "set-source-mute", self.source, "0"]) and ok
            ok = run_quiet(["pactl", "set-source-volume", self.source, "100%"]) and ok
        if self.stereo_source and self.stereo_source != self.source:
            ok = run_quiet(["pactl", "set-source-mute", self.stereo_source, "0"]) and ok
            ok = run_quiet(["pactl", "set-source-volume", self.stereo_source, "100%"]) and ok
        return ok

    def start_recording(self) -> None:
        self.stop_player()
        self.stop_stereo_test()
        self.set_test_volumes()
        self.recorder = Recorder(self.source)
        self.recorder.start()
        self.status = "Recording from Whisplay input at 100%"

    def start_stereo_test(self) -> None:
        if self.recorder:
            self.recorder.stop()
        self.stop_player()
        self.set_test_volumes()
        self.stereo_monitor = StereoMonitor(self.stereo_source)
        self.stereo_monitor.start()
        if self.stereo_monitor.error:
            self.status = self.stereo_monitor.error
        else:
            self.status = "Speak from the left or right side"

    def stop_stereo_test(self) -> None:
        if self.stereo_monitor:
            self.stereo_monitor.stop()

    def stop_all(self) -> None:
        if self.recorder:
            self.recorder.stop()
        self.stop_stereo_test()
        self.stop_player()
        self.status = "Stopped"

    def stop_player(self) -> None:
        if self.player and self.player.poll() is None:
            self.player.terminate()
            try:
                self.player.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.player.kill()
        self.player = None

    def play_recording(self) -> None:
        if not RECORDING.exists():
            self.status = "No recording yet"
            return
        self.play_file(RECORDING, "Playing recorded audio")

    def play_tone(self) -> None:
        self.play_file(TONE, "Playing tone on Whisplay output")

    def play_file(self, path: Path, message: str) -> None:
        self.stop_player()
        self.set_test_volumes()
        cmd = ["paplay", f"--device={self.sink}", str(path)] if self.sink else ["paplay", str(path)]
        try:
            self.player = subprocess.Popen(cmd)
            self.status = message
        except Exception as exc:
            self.status = str(exc)

    def quit(self) -> None:
        self.running = False

    def run(self) -> None:
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        self.running = False
                    elif event.key == pygame.K_r:
                        self.start_recording()
                    elif event.key == pygame.K_p:
                        self.play_recording()
                    elif event.key == pygame.K_t:
                        self.play_tone()
                    elif event.key == pygame.K_l:
                        self.start_stereo_test()
                    elif event.key == pygame.K_F5:
                        self.refresh_devices()
                elif event.type == pygame.VIDEORESIZE:
                    self.width, self.height = self.screen.get_size()
                    self.buttons = self.layout_buttons()
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    for _, rect, action in self.buttons:
                        if rect.collidepoint(event.pos):
                            action()
                            break
            self.update()
            self.draw()
            pygame.display.flip()
            self.clock.tick(30)
        self.stop_all()
        pygame.quit()

    def update(self) -> None:
        if self.recorder and self.recorder.done:
            if self.recorder.error:
                self.status = f"Record error: {self.recorder.error}"
            else:
                self.status = "Recording saved"
            self.recorder.done = False
        if self.player and self.player.poll() is not None:
            self.player = None
            if self.status.startswith("Playing"):
                self.status = "Playback finished"
        if self.stereo_monitor and self.stereo_monitor.error:
            self.status = f"Stereo error: {self.stereo_monitor.error}"

    def smooth_balance(self, target: float) -> float:
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self.balance_updated_at))
        self.balance_updated_at = now
        alpha = 1.0 - math.exp(-dt / STEREO_BALANCE_SMOOTH_SECONDS)
        self.balance_position += (target - self.balance_position) * alpha
        if abs(self.balance_position - target) < 0.001:
            self.balance_position = target
        return self.balance_position

    def draw(self) -> None:
        self.screen.fill(BG)
        self.width, self.height = self.screen.get_size()
        content_x = 18
        content_w = self.width - content_x * 2
        meter_w = max(80, content_w - 32)
        center_x = self.width // 2

        self.text("Whisplay Sound Test", 18, 12, self.title_font, TEXT)
        self.text("PipeWire capture / playback", 20, 42, self.small_font, MUTED)
        status_color = ERROR if "error" in self.status.lower() or "missing" in self.status.lower() else TEXT
        self.text(self.fit(self.status, 36), 20, 64, self.small_font, status_color)

        self.panel(content_x, 88, content_w, 66)
        self.text("In", 32, 101, self.small_font, MUTED)
        self.text(self.fit(self.source_label or "not found", 42), 66, 101, self.small_font, TEXT)
        self.text("Out", 32, 127, self.small_font, MUTED)
        self.text(self.fit(self.sink_label or "not found", 41), 66, 127, self.small_font, TEXT)

        self.panel(content_x, 166, content_w, 84)
        active = bool(self.recorder and self.recorder.active)
        level = self.recorder.level if self.recorder else 0.0
        elapsed = time.time() - self.recorder.started_at if active and self.recorder else 0.0
        remaining = max(0.0, RECORD_SECONDS - elapsed) if active else 0.0
        pct = min(1.0, elapsed / RECORD_SECONDS) if active else 0.0
        pygame.draw.rect(self.screen, LINE, (34, 210, meter_w, 18), border_radius=4)
        pygame.draw.rect(self.screen, ACCENT if active else BLUE, (34, 210, int(meter_w * level), 18), border_radius=4)
        pygame.draw.rect(self.screen, LINE, (34, 236, meter_w, 6), border_radius=3)
        pygame.draw.rect(self.screen, WARN, (34, 236, int(meter_w * pct), 6), border_radius=3)
        meter = f"{int(level * 100):02d}%"
        self.center(meter, (center_x, 188), self.big_font, ACCENT if active else MUTED)
        rec_dur = duration_seconds(RECORDING)
        rec_size = RECORDING.stat().st_size if RECORDING.exists() else 0
        tail = f"{remaining:0.1f}s left" if active else f"last {rec_dur:0.1f}s / {rec_size // 1024} KB"
        self.center(tail, (center_x, 247), self.small_font, MUTED)

        stereo_y = 262
        stereo_bottom = self.buttons[0][1].top - 10
        stereo_h = max(116, stereo_bottom - stereo_y)
        self.panel(content_x, stereo_y, content_w, stereo_h)
        monitor = self.stereo_monitor
        left = monitor.left if monitor else 0.0
        right = monitor.right if monitor else 0.0
        direction = monitor.direction if monitor else "Press L/R Mic"
        delta_db = monitor.delta_db if monitor else 0.0
        stereo_active = bool(monitor and monitor.active)
        self.text("Stereo source direction", 32, stereo_y + 12, self.body_font, ACCENT if stereo_active else TEXT)
        verdict_color = ACCENT if direction in ("LEFT", "RIGHT") else TEXT
        verdict = f"{direction}   L-R {delta_db:+.1f} dB" if stereo_active else direction
        self.center(verdict, (center_x, stereo_y + 44), self.body_font, verdict_color)

        # Spirit-level style balance indicator. Positive L-R dB moves the
        # bubble left; negative values move it right. +/-6 dB reaches an end
        # so modest left/right differences produce an obvious movement.
        track_x = content_x + 38
        track_w = content_w - 76
        track_y = stereo_y + 72
        track_h = 24
        track_center = track_x + track_w // 2
        pygame.draw.rect(self.screen, (15, 20, 27), (track_x, track_y, track_w, track_h), border_radius=track_h // 2)
        pygame.draw.rect(self.screen, LINE, (track_x, track_y, track_w, track_h), 2, border_radius=track_h // 2)
        for fraction in (-0.5, 0.0, 0.5):
            tick_x = round(track_center + fraction * track_w)
            tick_h = 17 if fraction == 0.0 else 10
            pygame.draw.line(
                self.screen,
                ACCENT if fraction == 0.0 else LINE,
                (tick_x, track_y + (track_h - tick_h) // 2),
                (tick_x, track_y + (track_h + tick_h) // 2),
                2,
            )
        self.text("L", content_x + 16, track_y + 1, self.body_font, BLUE)
        self.text("R", content_x + content_w - 30, track_y + 1, self.body_font, WARN)

        balance_target = max(-1.0, min(1.0, -delta_db / STEREO_BALANCE_RANGE_DB)) if stereo_active else 0.0
        balance = self.smooth_balance(balance_target)
        bubble_x = round(track_center + balance * (track_w / 2 - 13))
        bubble_color = BLUE if direction == "LEFT" else WARN if direction == "RIGHT" else ACCENT if stereo_active else MUTED
        pygame.draw.circle(self.screen, (8, 11, 16), (bubble_x, track_y + track_h // 2), 14)
        pygame.draw.circle(self.screen, bubble_color, (bubble_x, track_y + track_h // 2), 11)
        pygame.draw.circle(self.screen, (235, 248, 255), (bubble_x - 3, track_y + 8), 3)

        levels = f"L {int(left * 100):02d}%     R {int(right * 100):02d}%"
        self.center(levels, (center_x, stereo_y + 116), self.small_font, TEXT)
        if stereo_h >= 160:
            self.center(self.fit(self.stereo_label or "stereo input not found", 48), (center_x, stereo_y + 145), self.small_font, MUTED)

        for label, rect, _ in self.buttons:
            color = ACCENT if label in ("Record 5s", "L/R Mic") else PANEL_2
            if label == "Stop":
                color = (80, 50, 55)
            pygame.draw.rect(self.screen, color, rect, border_radius=8)
            pygame.draw.rect(self.screen, LINE, rect, width=1, border_radius=8)
            self.center(label, rect.center, self.body_font, TEXT)

    def panel(self, x: int, y: int, w: int, h: int) -> None:
        pygame.draw.rect(self.screen, PANEL, (x, y, w, h), border_radius=8)
        pygame.draw.rect(self.screen, LINE, (x, y, w, h), width=1, border_radius=8)

    def text(self, value: str, x: int, y: int, font: pygame.font.Font, color: tuple[int, int, int]) -> None:
        self.screen.blit(font.render(value, True, color), (x, y))

    def center(self, value: str, pos: tuple[int, int], font: pygame.font.Font, color: tuple[int, int, int]) -> None:
        surface = font.render(value, True, color)
        self.screen.blit(surface, surface.get_rect(center=pos))

    def fit(self, value: str, chars: int) -> str:
        return value if len(value) <= chars else value[: chars - 1] + "…"


if __name__ == "__main__":
    SoundTestApp().run()
