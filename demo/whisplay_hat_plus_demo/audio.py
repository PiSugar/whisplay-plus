from __future__ import annotations

import math
import struct
import subprocess
import time
import wave
from pathlib import Path


RATE = 48000
TONE = Path("/tmp/whisplay-hat-plus-tone.wav")
RECORDING = Path("/tmp/whisplay-hat-plus-recording.wav")


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


def _prop(block: str, key: str) -> str:
    marker = f'{key} = "'
    start = block.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = block.find('"', start)
    return block[start:end] if end >= 0 else ""


def find_device(kind: str) -> tuple[str, str]:
    output = run_text(["pactl", "list", kind])
    fallback = ("", "not found")
    for block in output.split("\n\n"):
        name = ""
        for line in block.splitlines():
            if line.strip().startswith("Name:"):
                name = line.split("Name:", 1)[1].strip()
                break
        if not name or name.endswith(".monitor"):
            continue
        card = _prop(block, "alsa.card_name") or _prop(block, "api.alsa.card.name")
        label = card or name
        if not fallback[0]:
            fallback = (name, label)
        if card == "Whisplay Sound" or "whisplay" in block.lower():
            return name, label
    return fallback


def set_100_percent(source: str, sink: str) -> bool:
    ok = True
    if sink:
        ok = run_quiet(["pactl", "set-sink-mute", sink, "0"]) and ok
        ok = run_quiet(["pactl", "set-sink-volume", sink, "100%"]) and ok
    if source:
        ok = run_quiet(["pactl", "set-source-mute", source, "0"]) and ok
        ok = run_quiet(["pactl", "set-source-volume", source, "100%"]) and ok
    return ok


def make_tone() -> None:
    with wave.open(str(TONE), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        frames = bytearray()
        for i in range(int(RATE * 1.2)):
            t = i / RATE
            amp = min(1.0, i / 2400) * min(1.0, (int(RATE * 1.2) - i) / 2400)
            frames += struct.pack("<h", int(math.sin(2 * math.pi * 660.0 * t) * 14000 * amp))
        wav.writeframes(frames)


def play_tone(sink: str):
    make_tone()
    cmd = ["paplay", f"--device={sink}", str(TONE)] if sink else ["paplay", str(TONE)]
    return subprocess.Popen(cmd)


def record(source: str, seconds: float = 4.0):
    if RECORDING.exists():
        RECORDING.unlink()
    cmd = ["parecord", f"--device={source}", "--file-format=wav", str(RECORDING)]
    if not source:
        cmd.pop(1)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        time.sleep(0.1)
    proc.terminate()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
    return RECORDING


def wav_peak(path: Path = RECORDING) -> float:
    if not path.exists() or path.stat().st_size <= 44:
        return 0.0
    with wave.open(str(path), "rb") as wav:
        data = wav.readframes(wav.getnframes())
    data = data[: len(data) - (len(data) % 2)]
    if not data:
        return 0.0
    samples = struct.unpack("<" + "h" * (len(data) // 2), data)
    return min(1.0, max(abs(v) for v in samples) / 32768.0)
