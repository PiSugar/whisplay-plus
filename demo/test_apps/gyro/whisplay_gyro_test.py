#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("SDL_ENABLE_SCREEN_KEYBOARD", "0")
os.environ.setdefault("SDL_IME_SHOW_UI", "0")

import pygame
from flask import Flask, jsonify, request

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from demo.whisplay_hat_plus_demo.gyro_map import GyroMap  # noqa: E402
except ModuleNotFoundError:
    # The Raspberry Pi image also ships this app as ~/whisplay-gyro-test.
    from gyro_map import GyroMap  # type: ignore[no-redef]  # noqa: E402

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus  # type: ignore

VENDOR_DIR = str(APP_DIR / "vendor")
if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)


BUS_ID = int(os.environ.get("WHISPLAY_I2C_BUS", "1"), 0)
QMI_ADDR = int(os.environ.get("QMI8658_ADDR", "0x6b"), 0)
MQC_ADDR = int(os.environ.get("MQC5883P_ADDR", "0x2c"), 0)
CALIBRATION_PATH = os.environ.get(
    "WHISPLAY_GYRO_CALIBRATION",
    os.path.expanduser("~/.config/whisplay-gyro-test/calibration.json"),
)
CALIBRATION_VERSION = 9
DEFAULT_CALIBRATION = {
    "version": 9,
    "mag_offsets": (0.0, 0.0, 0.0),
    "mag_scales": (1.0, 1.0, 1.0),
    "mag_heading_offset": 0.0,
    "mag_tumble_extent": {},
}
DEFAULT_CALIBRATION_PATH = PROJECT_ROOT / "demo" / "whisplay_hat_plus_demo" / "default_gyro_calibration.json"
if not DEFAULT_CALIBRATION_PATH.is_file():
    DEFAULT_CALIBRATION_PATH = APP_DIR / "default_gyro_calibration.json"
try:
    with DEFAULT_CALIBRATION_PATH.open("r", encoding="utf-8") as handle:
        DEFAULT_CALIBRATION = json.load(handle)
except (OSError, ValueError, TypeError):
    pass
PHONE_CAL_PORT = int(os.environ.get("WHISPLAY_PHONE_CAL_PORT", "8765"), 0)
PHONE_CAL_CERT_DIR = os.environ.get(
    "WHISPLAY_PHONE_CAL_CERT_DIR",
    os.path.expanduser("~/.config/whisplay-gyro-test/certs"),
)

BG = (12, 15, 20)
PANEL = (24, 31, 40)
PANEL_2 = (31, 40, 51)
LINE = (62, 76, 92)
TEXT = (238, 242, 247)
MUTED = (151, 162, 176)
GREEN = (57, 205, 151)
BLUE = (94, 166, 255)
YELLOW = (245, 187, 74)
RED = (255, 100, 106)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def i16le(data: list[int] | bytes) -> tuple[int, ...]:
    return struct.unpack("<" + "h" * (len(data) // 2), bytes(data))


def lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def phone_ssl_context(ip: str) -> ssl.SSLContext | None:
    cert_name = ip.replace(".", "-")
    cert_path = os.path.join(PHONE_CAL_CERT_DIR, f"phone-cal-{cert_name}.crt")
    key_path = os.path.join(PHONE_CAL_CERT_DIR, f"phone-cal-{cert_name}.key")
    if not os.path.exists(cert_path) or not os.path.exists(key_path):
        try:
            os.makedirs(PHONE_CAL_CERT_DIR, exist_ok=True)
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    key_path,
                    "-out",
                    cert_path,
                    "-days",
                    "3650",
                    "-subj",
                    "/CN=whisplay-gyro.local",
                    "-addext",
                    f"subjectAltName=IP:{ip},DNS:whisplay-gyro.local",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return "adhoc"
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        return context
    except Exception:
        return "adhoc"


def gf_mul(x: int, y: int) -> int:
    result = 0
    while y:
        if y & 1:
            result ^= x
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
        y >>= 1
    return result


def gf_pow(x: int, power: int) -> int:
    result = 1
    for _ in range(power):
        result = gf_mul(result, x)
    return result


def rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        next_poly = [0] * (len(poly) + 1)
        factor = gf_pow(2, i)
        for j, value in enumerate(poly):
            next_poly[j] ^= gf_mul(value, factor)
            next_poly[j + 1] ^= value
        poly = next_poly
    return poly


def rs_remainder(data: list[int], degree: int) -> list[int]:
    gen = rs_generator(degree)
    rem = [0] * degree
    for value in data:
        factor = value ^ rem.pop(0)
        rem.append(0)
        for i in range(degree):
            rem[i] ^= gf_mul(gen[i], factor)
    return rem


def qr_matrix(text: str) -> list[list[bool]]:
    # Version 3-L byte-mode QR. Capacity is 55 data codewords, enough for the LAN URL.
    version = 3
    size = 17 + version * 4
    data_codewords = 55
    ecc_codewords = 15
    bits = [0, 1, 0, 0]
    raw = text.encode("utf-8")
    bits += [(len(raw) >> i) & 1 for i in range(7, -1, -1)]
    for byte in raw:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, data_codewords * 8 - len(bits))
    while len(bits) % 8:
        bits.append(0)
    data = []
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits[i : i + 8]:
            value = (value << 1) | bit
        data.append(value)
    for pad in (0xEC, 0x11) * data_codewords:
        if len(data) >= data_codewords:
            break
        data.append(pad)
    codewords = data + rs_remainder(data, ecc_codewords)

    modules: list[list[bool | None]] = [[None for _ in range(size)] for _ in range(size)]
    reserved = [[False for _ in range(size)] for _ in range(size)]

    def set_mod(r: int, c: int, value: bool, reserve: bool = True) -> None:
        if 0 <= r < size and 0 <= c < size:
            modules[r][c] = value
            if reserve:
                reserved[r][c] = True

    def finder(r: int, c: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if 0 <= rr < size and 0 <= cc < size:
                    dark = 0 <= dr <= 6 and 0 <= dc <= 6 and (dr in (0, 6) or dc in (0, 6) or 2 <= dr <= 4 and 2 <= dc <= 4)
                    set_mod(rr, cc, dark)

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)
    for i in range(8, size - 8):
        set_mod(6, i, i % 2 == 0)
        set_mod(i, 6, i % 2 == 0)
    for center in (6, 22):
        for other in (6, 22):
            if (center, other) in ((6, 6), (6, 22), (22, 6)):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    dark = max(abs(dr), abs(dc)) in (0, 2)
                    set_mod(center + dr, other + dc, dark)
    set_mod(size - 8, 8, True)

    data_bits = []
    for byte in codewords:
        data_bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    bit_index = 0
    upward = True
    c = size - 1
    while c > 0:
        if c == 6:
            c -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, -1):
                cc = c + dc
                if reserved[r][cc]:
                    continue
                bit = data_bits[bit_index] if bit_index < len(data_bits) else 0
                if (r + cc) % 2 == 0:
                    bit ^= 1
                set_mod(r, cc, bool(bit), reserve=False)
                bit_index += 1
        upward = not upward
        c -= 2

    format_bits = "111011111000100"  # L error correction, mask 0.
    format_positions = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    for bit, (r, c) in zip(format_bits, format_positions):
        set_mod(r, c, bit == "1")
    format_positions_2 = [
        (size - 1, 8), (size - 2, 8), (size - 3, 8), (size - 4, 8), (size - 5, 8), (size - 6, 8), (size - 7, 8),
        (8, size - 8), (8, size - 7), (8, size - 6), (8, size - 5), (8, size - 4), (8, size - 3), (8, size - 2), (8, size - 1),
    ]
    for bit, (r, c) in zip(format_bits, format_positions_2):
        set_mod(r, c, bit == "1")
    return [[bool(value) for value in row] for row in modules]


class QMI8658B:
    def __init__(self, bus: SMBus, addr: int = QMI_ADDR):
        self.bus = bus
        self.addr = addr
        who = self.bus.read_byte_data(addr, 0x00)
        if who != 0x05:
            raise RuntimeError(f"WHO_AM_I=0x{who:02x}, expected 0x05")
        self.bus.write_byte_data(addr, 0x02, 0x60)
        self.bus.write_byte_data(addr, 0x03, 0x25)
        self.bus.write_byte_data(addr, 0x04, 0x45)
        self.bus.write_byte_data(addr, 0x08, 0x03)
        time.sleep(0.2)

    def read(self) -> tuple[float, float, float, float, float, float]:
        ax, ay, az, gx, gy, gz = i16le(self.bus.read_i2c_block_data(self.addr, 0x35, 12))
        # Convert IMU axes to the screen coordinate frame:
        # screen X right, Y up, Z out; QMI axes are +X, -Y, -Z in that frame.
        return (
            ax / 4096.0,
            -ay / 4096.0,
            -az / 4096.0,
            gx / 64.0,
            -gy / 64.0,
            -gz / 64.0,
        )


class MQC5883P:
    def __init__(self, bus: SMBus, addr: int):
        self.bus = bus
        self.addr = addr
        self.chip_id = self.bus.read_byte_data(addr, 0x00)
        if self.chip_id != 0x80:
            raise RuntimeError(f"CHIP_ID=0x{self.chip_id:02x}, expected 0x80")
        # Required vendor setup, then continuous 200 Hz conversion at +/-8 gauss.
        self.bus.write_byte_data(addr, 0x0D, 0x40)
        time.sleep(0.01)
        self.bus.write_byte_data(addr, 0x29, 0x06)
        time.sleep(0.01)
        self.bus.write_byte_data(addr, 0x0A, 0xCF)
        time.sleep(0.01)
        self.bus.write_byte_data(addr, 0x0B, 0x08)
        time.sleep(0.02)

    def read(self) -> tuple[float, float, float, float, int]:
        status = self.bus.read_byte_data(self.addr, 0x09)
        x, y, z = i16le(self.bus.read_i2c_block_data(self.addr, 0x01, 6))
        # Convert magnetometer axes to the screen coordinate frame:
        # raw magnetic X/Y/Z point to screen -Y/+X/+Z.
        sx, sy, sz = float(y), -float(x), float(z)
        heading = (math.degrees(math.atan2(sy, sx)) + 360.0) % 360.0
        return sx, sy, sz, heading, status


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


class HeadingFilter:
    def __init__(self, alpha: float):
        self.alpha = alpha
        self.value: float | None = None

    def update(self, heading: float) -> float:
        if self.value is None:
            self.value = heading % 360.0
        else:
            delta = ((heading - self.value + 540.0) % 360.0) - 180.0
            self.value = (self.value + delta * self.alpha) % 360.0
        return self.value

    def reset(self) -> None:
        self.value = None


class GyroCompassFusion:
    def __init__(self, gyro_sign: float = 1.0):
        self.gyro_sign = gyro_sign
        self.heading: float | None = None
        self.last_update = 0.0

    def update(self, mag_heading: float, gyro_z_dps: float, now: float) -> float:
        if self.heading is None or self.last_update <= 0.0:
            self.heading = mag_heading % 360.0
            self.last_update = now
            return self.heading

        dt = clamp(now - self.last_update, 0.0, 0.08)
        self.last_update = now
        predicted = (self.heading + gyro_z_dps * self.gyro_sign * dt) % 360.0
        error = ((mag_heading - predicted + 540.0) % 360.0) - 180.0
        rate = abs(gyro_z_dps)
        correction_tau = 0.45 if rate < 3.0 else 1.8
        correction = clamp(dt / correction_tau, 0.005, 0.12)
        self.heading = (predicted + error * correction) % 360.0
        return self.heading

    def reset(self) -> None:
        self.heading = None
        self.last_update = 0.0


PHONE_PAGE = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<style>
body{margin:0;background:#0c0f14;color:#eef2f7;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}
.wrap{padding:22px;max-width:520px;margin:auto}
h1{font-size:24px;margin:0 0 14px}
.card{background:#18202a;border:1px solid #3e4c5c;border-radius:12px;padding:16px;margin:12px 0}
button{width:100%;height:48px;border:0;border-radius:10px;background:#39cd97;color:#fff;font-size:17px;font-weight:700}
.muted{color:#97a2b0;font-size:14px;line-height:1.45}
.big{font-size:42px;font-weight:800;margin:8px 0}
.ok{color:#39cd97}.warn{color:#f5bb4a}.bad{color:#ff646a}
.compass{position:relative;width:min(76vw,300px);aspect-ratio:1;margin:18px auto 10px;border-radius:50%;background:#0f151d;border:2px solid #3e4c5c;box-shadow:inset 0 0 0 1px rgba(255,255,255,.04)}
.rose{position:absolute;inset:16px;border-radius:50%;transition:transform .08s linear}
.dir{position:absolute;font-weight:800;color:#eef2f7;transform:translate(-50%,-50%)}
.n{color:#39cd97}.e,.s,.w{color:#97a2b0}
.needle{position:absolute;left:50%;top:50%;width:4px;height:42%;background:#ff646a;border-radius:4px;transform:translate(-50%,-100%);transform-origin:50% 100%}
.needle:after{content:"";position:absolute;left:50%;top:-8px;transform:translateX(-50%);border-left:8px solid transparent;border-right:8px solid transparent;border-bottom:14px solid #ff646a}
.hub{position:absolute;left:50%;top:50%;width:18px;height:18px;background:#eef2f7;border-radius:50%;transform:translate(-50%,-50%)}
</style>
</head>
<body>
<div class="wrap">
<h1>Whisplay Compass Calibration</h1>
<div class="card">
<button id="start">Start Calibration</button>
<p class="muted">Hold the phone and Whisplay device together, screens facing the same direction. If iOS shows a certificate warning, open the advanced details and continue.</p>
<p id="secure" class="muted"></p>
</div>
<div class="card">
<div id="state" class="warn">Waiting</div>
<div class="compass">
  <div id="rose" class="rose">
    <div class="dir n" style="left:50%;top:7%">N</div>
    <div class="dir e" style="left:93%;top:50%">E</div>
    <div class="dir s" style="left:50%;top:93%">S</div>
    <div class="dir w" style="left:7%;top:50%">W</div>
  </div>
  <div class="needle"></div>
  <div class="hub"></div>
</div>
<div id="heading" class="big">--.-°</div>
<div id="detail" class="muted">No sensor data yet.</div>
</div>
</div>
<script>
let sensorActive=false, uploading=false, sent=0, dropped=0, lastHeading=null, lastAcceptedHeading=null, lastAcceptedTime=0;
let lastEventWallTime=0;
function bestHeading(e){
  if (typeof e.webkitCompassHeading === 'number') return e.webkitCompassHeading;
  if (typeof e.alpha === 'number') return (360 - e.alpha + 360) % 360;
  return null;
}
async function post(path, data){
  try {
    const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})});
    return await r.json();
  } catch(e) { return null; }
}
async function getStatus(){
  try {
    const r=await fetch('/status');
    return await r.json();
  } catch(e) { return null; }
}
function pauseFromServer(label){
  uploading=false;
  document.getElementById('state').textContent=label || 'Calibration complete / paused';
  document.getElementById('state').className='ok';
  document.getElementById('detail').textContent='Upload paused. Compass still live. Samples sent: '+sent+'  dropped: '+dropped;
}
async function start(){
  const s=document.getElementById('state');
  s.textContent='Connecting to Whisplay...'; s.className='warn';
  await post('/start',{});
  s.textContent='Connected. Requesting phone sensors...'; s.className='ok';
  try {
    if (window.DeviceOrientationEvent && typeof DeviceOrientationEvent.requestPermission === 'function') {
      const p=await DeviceOrientationEvent.requestPermission();
      if (p !== 'granted') { s.textContent='Permission denied'; s.className='bad'; return; }
    }
  } catch(e) {
    s.textContent='Sensor permission error'; s.className='bad';
    document.getElementById('detail').textContent=String(e);
  }
  sensorActive=true; uploading=true; sent=0;
  dropped=0; lastHeading=null; lastAcceptedHeading=null; lastAcceptedTime=0;
  s.textContent='Calibrating'; s.className='ok';
  setTimeout(() => {
    if (sent===0 && lastHeading===null) {
      s.textContent='No compass data from browser'; s.className='bad';
      document.getElementById('detail').textContent=window.isSecureContext ? 'Try another browser or enable motion/orientation permission.' : 'Browser may require HTTPS for motion/orientation sensors.';
    }
  }, 2500);
}
function onOrientation(e){
  if(!sensorActive) return;
  const h=bestHeading(e);
  if(h===null) {
    document.getElementById('detail').textContent='This browser did not provide compass heading.';
    return;
  }
  if(!Number.isFinite(h) || h<0 || h>=360) {
    dropped++;
    return;
  }
  const now=performance.now()/1000;
  if(lastAcceptedHeading!==null) {
    const dt=Math.max(0.001, now-lastAcceptedTime);
    const delta=Math.abs(((h-lastAcceptedHeading+540)%360)-180);
    if(dt<0.8 && delta/dt>220) {
      dropped++;
      document.getElementById('detail').textContent='Dropped jump: '+dropped+'  last jump '+delta.toFixed(0)+'°';
      return;
    }
  }
  lastAcceptedHeading=h;
  lastAcceptedTime=now;
  lastHeading=h;
  lastEventWallTime=Date.now()/1000;
  document.getElementById('heading').textContent=h.toFixed(1)+'°';
  document.getElementById('rose').style.transform='rotate('+(-h)+'deg)';
  const mode=uploading ? 'uploading' : 'paused';
  document.getElementById('detail').textContent='Compass live, '+mode+'. Samples sent: '+sent+'  dropped: '+dropped+'  beta: '+(e.beta||0).toFixed(1)+' gamma: '+(e.gamma||0).toFixed(1);
}
window.addEventListener('deviceorientation', onOrientation, true);
window.addEventListener('deviceorientationabsolute', onOrientation, true);
setInterval(() => {
  if(!uploading || lastHeading===null) return;
  post('/sample',{heading:lastHeading, t:lastEventWallTime, sentAt:Date.now()/1000}).then((res)=>{
    if(res && res.paused){ pauseFromServer(res.phase==='complete' ? 'Calibration complete / paused' : 'Calibration paused'); return; }
    sent++;
  });
}, 80);
setInterval(() => {
  if(!sensorActive) return;
  getStatus().then((res)=>{
    if(!res) return;
    if(res.phase==='complete') pauseFromServer('Calibration complete / paused');
    else if(res.phase==='cancelled') pauseFromServer('Calibration cancelled / paused');
  });
}, 500);
document.getElementById('start').onclick=start;
document.getElementById('secure').textContent='Secure context: '+(window.isSecureContext ? 'yes' : 'no');
</script>
</body>
</html>
"""


class PhoneCalibrationServer:
    def __init__(self, state: dict[str, object], lock: threading.Lock, ssl_context: object):
        self.state = state
        self.lock = lock
        self.ssl_context = ssl_context
        self.app = Flask("whisplay_phone_cal")
        self.thread: threading.Thread | None = None

        @self.app.route("/")
        def index():
            return PHONE_PAGE

        @self.app.route("/start", methods=["POST"])
        def start():
            with self.lock:
                self.state["active"] = True
                self.state["phase"] = "collecting"
                self.state["started"] = time.monotonic()
                self.state["updated"] = 0.0
                self.state["heading"] = None
                self.state["samples"] = 0
            return jsonify(ok=True)

        @self.app.route("/status")
        def status():
            with self.lock:
                return jsonify(
                    active=bool(self.state.get("active", False)),
                    phase=str(self.state.get("phase", "idle")),
                    samples=int(self.state.get("samples", 0)),
                    updated=float(self.state.get("updated", 0.0)),
                )

        @self.app.route("/sample", methods=["POST"])
        def sample():
            data = request.get_json(silent=True) or {}
            try:
                heading = float(data.get("heading"))
                phone_time = float(data.get("t", 0.0))
                sent_time = float(data.get("sentAt", phone_time))
            except Exception:
                return jsonify(ok=False), 400
            received = time.monotonic()
            with self.lock:
                phase = str(self.state.get("phase", "idle"))
                if phase in {"complete", "cancelled"}:
                    return jsonify(ok=True, paused=True, phase=phase)
                offset = self.state.get("phone_time_offset")
                sample_offset = received - sent_time if sent_time > 0.0 else None
                if sample_offset is not None:
                    if offset is None or sample_offset < float(offset):
                        offset = sample_offset
                    else:
                        offset = float(offset)
                    self.state["phone_time_offset"] = offset
                self.state["active"] = True
                self.state["heading"] = heading % 360.0
                self.state["updated"] = received
                self.state["phone_time"] = phone_time
                self.state["target_time"] = phone_time + float(offset) if phone_time > 0.0 and offset is not None else received
                self.state["delay_ms"] = max(0.0, (received - float(self.state["target_time"])) * 1000.0)
                self.state["samples"] = int(self.state.get("samples", 0)) + 1
            return jsonify(ok=True, paused=False, phase=phase)

    def start(self) -> None:
        if self.thread:
            return
        self.thread = threading.Thread(
            target=lambda: self.app.run(
                host="0.0.0.0",
                port=PHONE_CAL_PORT,
                debug=False,
                use_reloader=False,
                ssl_context=self.ssl_context,
            ),
            daemon=True,
        )
        self.thread.start()


class GyroTestApp:
    def __init__(self):
        pygame.init()
        pygame.key.stop_text_input()
        pygame.mouse.set_visible(False)
        pygame.display.set_caption("Whisplay Gyro Test")
        self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        self.width, self.height = self.screen.get_size()
        self.clock = pygame.time.Clock()
        self.title_font = pygame.font.SysFont("DejaVu Sans", 24, bold=True)
        self.body_font = pygame.font.SysFont("DejaVu Sans", 16)
        self.small_font = pygame.font.SysFont("DejaVu Sans", 13)
        self.big_font = pygame.font.SysFont("DejaVu Sans", 34, bold=True)
        self.qr_font = pygame.font.SysFont("DejaVu Sans", 10, bold=True)
        self.bus = SMBus(BUS_ID)
        self.qmi: QMI8658B | None = None
        self.qmc: MQC5883P | None = None
        self.qmi_error = ""
        self.qmc_error = ""
        self.qmi_filter = LowPass(0.22)
        self.qmc_filter = LowPass(1.0)
        self.heading_filter = HeadingFilter(0.28)
        gyro_heading_sign = float(os.environ.get("WHISPLAY_GYRO_HEADING_SIGN", "-1.0"))
        self.heading_fusion = GyroCompassFusion(gyro_heading_sign)
        self.latest_gyro_z_dps = 0.0
        self.latest_yaw_rate_dps = 0.0
        self.latest_accel = (0.0, 0.0, 1.0)
        self.baseline = (0.0, 0.0, 1.0)
        self.gyro_offsets = (0.0, 0.0, 0.0)
        self.mag_offsets = tuple(DEFAULT_CALIBRATION["mag_offsets"])
        self.mag_scales = tuple(DEFAULT_CALIBRATION["mag_scales"])
        self.mag_heading_offset = float(DEFAULT_CALIBRATION["mag_heading_offset"])
        self.mag_directions = [
            ("N", 0.0),
            ("E", 90.0),
            ("S", 180.0),
            ("W", 270.0),
        ]
        self.mag_direction_index = 0
        self.mag_direction_buttons: list[tuple[str, pygame.Rect]] = []
        self.modal_buttons: list[tuple[str, pygame.Rect, object]] = []
        self.mag_heading_samples: list[float] = []
        self.mag_vector_samples: list[tuple[float, float, float]] = []
        self.mag_accel_samples: list[tuple[float, float, float]] = []
        self.mag_direction_vectors: dict[str, tuple[float, float, float]] = {}
        self.mag_direction_accels: dict[str, tuple[float, float, float]] = {}
        self.mag_direction_errors: dict[str, float] = {}
        self.mag_rotation_samples: dict[str, list[tuple[float, float, float]]] = {}
        self.mag_rotation_extents: dict[str, dict[str, object]] = {}
        self.mag_rotation_mode = ""
        self.mag_rotation_duration = 18.0
        self.mag_tumble_extent: dict[str, object] = dict(DEFAULT_CALIBRATION["mag_tumble_extent"])
        self.mag_rotation_extents = {"phone": self.mag_tumble_extent}
        self.mag_tumble_min_duration = 24.0
        self.mag_tumble_max_duration = 60.0
        self.mag_tumble_required_span = 6000.0
        self.phone_lock = threading.Lock()
        self.phone_state: dict[str, object] = {
            "active": False,
            "phase": "idle",
            "heading": None,
            "updated": 0.0,
            "phone_time": 0.0,
            "target_time": 0.0,
            "phone_time_offset": None,
            "delay_ms": 0.0,
            "samples": 0,
            "started": 0.0,
        }
        self.phone_ip = lan_ip()
        self.phone_url = f"https://{self.phone_ip}:{PHONE_CAL_PORT}"
        self.phone_ssl_context = phone_ssl_context(self.phone_ip)
        self.phone_server = PhoneCalibrationServer(self.phone_state, self.phone_lock, self.phone_ssl_context)
        self.phone_server.start()
        self.phone_qr_surface = self.make_qr_surface(self.phone_url, 4)
        self.phone_cal_samples: list[tuple[tuple[float, float, float], tuple[float, float, float], float, float]] = []
        self.mag_history: list[tuple[float, tuple[float, float, float], tuple[float, float, float]]] = []
        self.phone_last_heading: float | None = None
        self.phone_last_sample_time = 0.0
        self.phone_last_update_used = 0.0
        self.phone_rejected_samples = 0
        self.phone_cal_min_duration = 16.0
        self.phone_cal_max_duration = 45.0
        self.phone_cal_required_span = 4500.0
        self.mag_calibrating = False
        self.mag_cal_started = 0.0
        self.mag_cal_phase = ""
        self.gyro_sum = [0.0, 0.0, 0.0]
        self.accel_sum = [0.0, 0.0, 0.0]
        self.gyro_samples = 0
        self.gyro_cal_duration = 2.0
        self.mag_stable_min_duration = 4.0
        self.mag_stable_window = 3.0
        self.mag_stable_threshold_deg = 1.0
        self.mag_stable_confirm_duration = 2.0
        self.mag_stable_max_duration = 15.0
        self.target_fps = 60
        self.mag_stable_since: float | None = None
        self.gyro_map = GyroMap()
        self.running = True
        self.buttons = self.layout_buttons()
        self.load_calibration()
        self.init_sensors()

    def init_sensors(self) -> None:
        try:
            self.qmi = QMI8658B(self.bus)
            self.qmi_error = ""
            self.baseline = self.qmi.read()[:3]
        except Exception as exc:
            self.qmi = None
            self.qmi_error = str(exc)
        try:
            self.qmc = MQC5883P(self.bus, MQC_ADDR)
            self.qmc_error = ""
        except Exception as exc:
            self.qmc = None
            self.qmc_error = str(exc)

    def layout_buttons(self) -> list[tuple[str, pygame.Rect, object]]:
        margin = 18
        gap = 8
        w = (self.width - margin * 2 - gap * 2) // 3
        y = self.height - 54
        return [
            ("Calibrate", pygame.Rect(margin, y, w, 36), self.calibrate),
            ("Map", pygame.Rect(margin + w + gap, y, w, 36), self.open_map),
            ("Quit", pygame.Rect(margin + (w + gap) * 2, y, w, 36), self.quit),
        ]

    def calibrate(self) -> None:
        if self.gyro_map.active:
            return
        if self.qmi:
            self.baseline = self.qmi.read()[:3]
        if not self.qmc:
            return
        if not self.mag_calibrating:
            self.start_mag_calibration()

    def open_map(self) -> None:
        if self.mag_calibrating:
            return
        self.gyro_map.open(self.latest_accel)

    def close_map(self) -> None:
        self.gyro_map.close()

    def done_calibration(self) -> None:
        if self.mag_calibrating and self.can_finish_calibration():
            self.finish_mag_calibration()

    def cancel_calibration(self) -> None:
        self.mag_calibrating = False
        self.mag_cal_phase = ""
        self.mag_heading_samples = []
        self.mag_vector_samples = []
        self.mag_accel_samples = []
        self.mag_direction_vectors = {}
        self.mag_direction_accels = {}
        self.mag_direction_errors = {}
        self.mag_rotation_samples = {}
        self.mag_rotation_extents = {}
        self.mag_rotation_mode = ""
        self.mag_tumble_extent = {}
        self.phone_cal_samples = []
        self.phone_last_heading = None
        self.phone_last_sample_time = 0.0
        self.phone_last_update_used = 0.0
        self.phone_rejected_samples = 0
        with self.phone_lock:
            self.phone_state["active"] = False
            self.phone_state["phase"] = "cancelled"
        self.mag_stable_since = None
        self.heading_filter.reset()
        self.heading_fusion.reset()
        self.qmi_filter.reset()
        self.qmc_filter.reset()

    def start_mag_calibration(self) -> None:
        self.mag_calibrating = True
        self.mag_cal_phase = "phone_wait"
        self.mag_cal_started = time.monotonic()
        self.gyro_sum = [0.0, 0.0, 0.0]
        self.accel_sum = [0.0, 0.0, 0.0]
        self.gyro_samples = 0
        self.mag_heading_samples = []
        self.mag_vector_samples = []
        self.mag_accel_samples = []
        self.mag_direction_vectors = {}
        self.mag_direction_accels = {}
        self.mag_direction_errors = {}
        self.mag_rotation_samples = {}
        self.mag_rotation_extents = {}
        self.mag_rotation_mode = ""
        self.mag_tumble_extent = {}
        self.mag_heading_offset = 0.0
        self.mag_offsets = (0.0, 0.0, 0.0)
        self.mag_scales = (1.0, 1.0, 1.0)
        self.phone_cal_samples = []
        self.phone_last_heading = None
        self.phone_last_sample_time = 0.0
        self.phone_last_update_used = 0.0
        self.phone_rejected_samples = 0
        with self.phone_lock:
            self.phone_state["active"] = False
            self.phone_state["phase"] = "waiting"
            self.phone_state["heading"] = None
            self.phone_state["updated"] = 0.0
            self.phone_state["phone_time"] = 0.0
            self.phone_state["target_time"] = 0.0
            self.phone_state["phone_time_offset"] = None
            self.phone_state["delay_ms"] = 0.0
            self.phone_state["samples"] = 0
            self.phone_state["started"] = 0.0
        self.heading_filter.reset()
        self.heading_fusion.reset()
        self.qmi_filter.reset()
        self.qmc_filter.reset()

    def start_mag_hold_calibration(self, index: int | None = None) -> None:
        if index is not None:
            self.mag_direction_index = index
        self.mag_cal_phase = "mag_hold"
        self.mag_cal_started = time.monotonic()
        self.mag_stable_since = None
        self.mag_heading_samples = []
        self.mag_vector_samples = []
        self.mag_accel_samples = []
        self.qmc_filter.reset()

    def start_mag_rotation_calibration(self, mode: str) -> None:
        self.mag_cal_phase = "mag_rotate"
        self.mag_rotation_mode = mode
        self.mag_cal_started = time.monotonic()
        self.mag_vector_samples = []
        self.mag_accel_samples = []
        self.qmc_filter.reset()

    def finish_mag_calibration(self) -> None:
        if self.mag_cal_phase == "gyro":
            self.finish_gyro_calibration()
        elif self.mag_cal_phase == "mag_tumble" and self.mag_vector_samples:
            self.finish_tumble_calibration()
        elif self.mag_cal_phase == "north_hold" and self.mag_heading_samples:
            self.finish_north_alignment()
        elif self.can_finish_calibration():
            self.save_calibration()
            self.mag_calibrating = False
            self.mag_cal_phase = ""
        self.qmc_filter.reset()

    def can_finish_calibration(self) -> bool:
        return bool(self.mag_tumble_extent) and self.mag_cal_phase == "mag_align"

    def start_north_alignment(self) -> None:
        self.mag_cal_phase = "north_hold"
        self.mag_cal_started = time.monotonic()
        self.mag_stable_since = None
        self.mag_heading_samples = []
        self.mag_vector_samples = []
        self.mag_accel_samples = []
        self.qmc_filter.reset()

    def finish_tumble_calibration(self) -> None:
        samples = list(self.mag_vector_samples)
        if len(samples) < 50:
            return
        self.apply_tumble_calibration(samples)
        self.mag_cal_phase = "mag_align"
        self.mag_cal_started = time.monotonic()
        self.mag_heading_samples = []
        self.mag_vector_samples = []
        self.mag_accel_samples = []
        self.heading_filter.reset()
        self.heading_fusion.reset()
        self.qmc_filter.reset()

    def finish_north_alignment(self) -> None:
        heading = self.circular_mean_deg(self.mag_heading_samples[-max(8, int(self.mag_stable_window * self.target_fps)):])
        self.mag_heading_offset = (-heading) % 360.0
        self.save_calibration()
        self.mag_calibrating = False
        self.mag_cal_phase = ""
        self.mag_heading_samples = []
        self.mag_vector_samples = []
        self.mag_accel_samples = []
        self.heading_filter.reset()
        self.heading_fusion.reset()
        self.qmc_filter.reset()

    def apply_tumble_calibration(self, samples: list[tuple[float, float, float]]) -> None:
        mins = [min(sample[i] for sample in samples) for i in range(3)]
        maxs = [max(sample[i] for sample in samples) for i in range(3)]
        spans = [maxs[i] - mins[i] for i in range(3)]
        radii = [max(1.0, span / 2.0) for span in spans]
        radius = max(1.0, sum(radii) / 3.0)
        self.mag_offsets = tuple((mins[i] + maxs[i]) / 2.0 for i in range(3))
        self.mag_scales = tuple(radius / radii[i] for i in range(3))
        self.mag_tumble_extent = {"min": mins, "max": maxs, "span": spans, "count": len(samples)}
        self.mag_rotation_extents = {"tumble": self.mag_tumble_extent}

    def tumble_progress(self) -> float:
        if len(self.mag_vector_samples) < 20:
            return 0.0
        extent = self.rotation_extent(self.mag_vector_samples)
        spans = [float(value) for value in extent.get("span", [0.0, 0.0, 0.0])]
        max_span = max(spans) if spans else 0.0
        min_span = min(spans) if spans else 0.0
        span_score = clamp(min_span / self.mag_tumble_required_span, 0.0, 1.0)
        balance_score = clamp(min_span / max(max_span, 1.0) / 0.55, 0.0, 1.0)
        time_score = clamp((time.monotonic() - self.mag_cal_started) / self.mag_tumble_min_duration, 0.0, 1.0)
        return min(span_score, balance_score, time_score)

    def tumble_ready(self) -> bool:
        elapsed = time.monotonic() - self.mag_cal_started
        if elapsed >= self.mag_tumble_max_duration and len(self.mag_vector_samples) >= 200:
            return True
        return self.tumble_progress() >= 1.0 and len(self.mag_vector_samples) >= int(self.mag_tumble_min_duration * 20)

    def phone_snapshot(self) -> dict[str, object]:
        with self.phone_lock:
            heading = self.phone_state.get("heading")
            updated = float(self.phone_state.get("updated", 0.0))
            target_time = float(self.phone_state.get("target_time", updated))
            delay_ms = float(self.phone_state.get("delay_ms", 0.0))
            active = bool(self.phone_state.get("active", False))
            samples = int(self.phone_state.get("samples", 0))
        return {
            "active": active,
            "heading": heading,
            "updated": updated,
            "target_time": target_time,
            "delay_ms": delay_ms,
            "fresh": time.monotonic() - updated < 1.0,
            "samples": samples,
        }

    def phone_cal_progress(self) -> float:
        if len(self.phone_cal_samples) < 20:
            return 0.0
        vectors = [sample[0] for sample in self.phone_cal_samples]
        extent = self.rotation_extent(vectors)
        spans = [float(value) for value in extent.get("span", [0.0, 0.0, 0.0])]
        max_span = max(spans) if spans else 0.0
        min_span = min(spans) if spans else 0.0
        span_score = clamp(min_span / self.phone_cal_required_span, 0.0, 1.0)
        balance_score = clamp(min_span / max(max_span, 1.0) / 0.45, 0.0, 1.0)
        time_score = clamp((time.monotonic() - self.mag_cal_started) / self.phone_cal_min_duration, 0.0, 1.0)
        return min(span_score, balance_score, time_score)

    def phone_cal_ready(self) -> bool:
        elapsed = time.monotonic() - self.mag_cal_started
        if elapsed >= self.phone_cal_max_duration and len(self.phone_cal_samples) >= 120:
            return True
        return self.phone_cal_progress() >= 1.0 and len(self.phone_cal_samples) >= int(self.phone_cal_min_duration * 8)

    def accept_phone_heading(self, heading: float, now: float) -> bool:
        if not math.isfinite(heading) or heading < 0.0 or heading >= 360.0:
            self.phone_rejected_samples += 1
            return False
        if self.phone_last_heading is None:
            self.phone_last_heading = heading
            self.phone_last_sample_time = now
            return True
        dt = max(0.001, now - self.phone_last_sample_time)
        delta = abs(((heading - self.phone_last_heading + 540.0) % 360.0) - 180.0)
        max_rate = 240.0
        max_delta = 70.0 if dt < 0.6 else 140.0
        if delta > max_delta or delta / dt > max_rate:
            self.phone_rejected_samples += 1
            return False
        self.phone_last_heading = heading
        self.phone_last_sample_time = now
        return True

    def record_mag_history(
        self,
        now: float,
        vector: tuple[float, float, float],
        accel: tuple[float, float, float],
    ) -> None:
        self.mag_history.append((now, vector, accel))
        cutoff = now - 3.0
        while len(self.mag_history) > 2 and self.mag_history[0][0] < cutoff:
            self.mag_history.pop(0)

    def mag_sample_at(
        self,
        target_time: float,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        if len(self.mag_history) < 2:
            return None
        if target_time <= self.mag_history[0][0] or target_time >= self.mag_history[-1][0]:
            return None
        for index in range(1, len(self.mag_history)):
            t1, v1, a1 = self.mag_history[index]
            if t1 < target_time:
                continue
            t0, v0, a0 = self.mag_history[index - 1]
            span = max(0.000001, t1 - t0)
            ratio = clamp((target_time - t0) / span, 0.0, 1.0)
            vector = tuple(v0[i] + (v1[i] - v0[i]) * ratio for i in range(3))
            accel = tuple(a0[i] + (a1[i] - a0[i]) * ratio for i in range(3))
            return vector, accel
        return None

    def finish_phone_calibration(self) -> None:
        samples = list(self.phone_cal_samples)
        if len(samples) < 20:
            return
        vectors = [sample[0] for sample in samples]
        mins = [min(vector[i] for vector in vectors) for i in range(3)]
        maxs = [max(vector[i] for vector in vectors) for i in range(3)]
        spans = [maxs[i] - mins[i] for i in range(3)]
        radii = [max(1.0, span / 2.0) for span in spans]
        radius = max(1.0, sum(radii) / 3.0)
        self.mag_offsets = tuple((mins[i] + maxs[i]) / 2.0 for i in range(3))
        self.mag_scales = tuple(radius / radii[i] for i in range(3))
        errors = []
        ox, oy, oz = self.mag_offsets
        sx, sy, sz = self.mag_scales
        for vector, accel, phone_heading, _ in samples[-min(len(samples), 900):]:
            mx, my, mz = vector
            device_heading = self.tilt_compensated_heading((mx - ox) * sx, (my - oy) * sy, (mz - oz) * sz, accel)
            errors.append((phone_heading - device_heading) % 360.0)
        first_offset = self.circular_mean_deg(errors)
        filtered_errors = []
        for error in errors:
            residual = abs(((error - first_offset + 540.0) % 360.0) - 180.0)
            if residual <= 35.0:
                filtered_errors.append(error)
        self.mag_heading_offset = self.circular_mean_deg(filtered_errors if len(filtered_errors) >= 12 else errors)
        self.mag_tumble_extent = {"min": mins, "max": maxs, "span": spans, "count": len(samples)}
        self.mag_rotation_extents = {"phone": self.mag_tumble_extent}
        self.save_calibration()
        self.mag_calibrating = False
        self.mag_cal_phase = ""
        with self.phone_lock:
            self.phone_state["active"] = False
            self.phone_state["phase"] = "complete"
        self.heading_filter.reset()
        self.heading_fusion.reset()
        self.qmc_filter.reset()

    def save_current_direction_calibration(self) -> None:
        if not self.mag_vector_samples:
            return
        label, target = self.mag_directions[self.mag_direction_index]
        sample_count = max(8, int(self.mag_stable_window * self.target_fps))
        values = self.mag_vector_samples[-sample_count:]
        accel_values = self.mag_accel_samples[-sample_count:] if self.mag_accel_samples else [self.latest_accel]
        count = len(values)
        vector = tuple(sum(sample[i] for sample in values) / count for i in range(3))
        accel = tuple(sum(sample[i] for sample in accel_values) / len(accel_values) for i in range(3))
        self.mag_direction_vectors[label] = vector
        self.mag_direction_accels[label] = accel
        ox, oy, _ = self.mag_offsets
        sx, sy, _ = self.mag_scales
        measured = self.tilt_compensated_heading((vector[0] - ox) * sx, (vector[1] - oy) * sy, vector[2], accel)
        self.mag_direction_errors[label] = (target - measured) % 360.0

    def finish_current_direction(self) -> None:
        if self.mag_cal_phase == "mag_hold":
            self.save_current_direction_calibration()
            if len(self.mag_direction_vectors) == len(self.mag_directions):
                self.apply_vector_calibration()
            self.mag_cal_phase = "mag_select"
            self.mag_cal_started = time.monotonic()
            self.mag_stable_since = None
            self.mag_heading_samples = []
            self.mag_vector_samples = []
            self.mag_accel_samples = []
        self.qmc_filter.reset()

    def finish_current_rotation(self) -> None:
        if not self.mag_vector_samples or not self.mag_rotation_mode:
            return
        mode = self.mag_rotation_mode
        samples = list(self.mag_vector_samples)
        self.mag_rotation_samples[mode] = samples
        self.mag_rotation_extents[mode] = self.rotation_extent(samples)
        if self.can_finish_calibration():
            self.apply_vector_calibration()
        self.mag_cal_phase = "mag_select"
        self.mag_rotation_mode = ""
        self.mag_cal_started = time.monotonic()
        self.mag_vector_samples = []
        self.mag_accel_samples = []
        self.qmc_filter.reset()

    def rotation_extent(self, samples: list[tuple[float, float, float]]) -> dict[str, object]:
        mins = [min(sample[i] for sample in samples) for i in range(3)]
        maxs = [max(sample[i] for sample in samples) for i in range(3)]
        spans = [maxs[i] - mins[i] for i in range(3)]
        return {"min": mins, "max": maxs, "span": spans, "count": len(samples)}

    def apply_vector_calibration(self) -> None:
        required = {label for label, _ in self.mag_directions}
        if set(self.mag_direction_vectors) != required:
            return
        north = self.mag_direction_vectors["N"]
        east = self.mag_direction_vectors["E"]
        south = self.mag_direction_vectors["S"]
        west = self.mag_direction_vectors["W"]
        rotation_vectors = [
            sample
            for samples in self.mag_rotation_samples.values()
            for sample in samples
        ]
        if rotation_vectors:
            vectors = rotation_vectors + list(self.mag_direction_vectors.values())
            mins = [min(vector[i] for vector in vectors) for i in range(3)]
            maxs = [max(vector[i] for vector in vectors) for i in range(3)]
            ox, oy, oz = tuple((mins[i] + maxs[i]) / 2.0 for i in range(3))
            radii = [max(1.0, (maxs[i] - mins[i]) / 2.0) for i in range(3)]
            radius = max(1.0, sum(radii) / 3.0)
            sx, sy, sz = tuple(radius / radii[i] for i in range(3))
        else:
            ox = (north[0] + south[0]) / 2.0
            oy = (east[1] + west[1]) / 2.0
            oz = sum(vector[2] for vector in self.mag_direction_vectors.values()) / 4.0
            x_radius = (abs(north[0] - ox) + abs(south[0] - ox)) / 2.0
            y_radius = (abs(east[1] - oy) + abs(west[1] - oy)) / 2.0
            radius = max(1.0, (x_radius + y_radius) / 2.0)
            sx = radius / x_radius if x_radius > 1.0 else 1.0
            sy = radius / y_radius if y_radius > 1.0 else 1.0
            sz = 1.0
        self.mag_offsets = (ox, oy, oz)
        self.mag_scales = (sx, sy, sz)
        errors = []
        for label, target in self.mag_directions:
            x, y, _ = self.mag_direction_vectors[label]
            accel = self.mag_direction_accels.get(label, self.latest_accel)
            z = self.mag_direction_vectors[label][2]
            heading = self.tilt_compensated_heading((x - ox) * sx, (y - oy) * sy, z - oz, accel)
            errors.append((target - heading) % 360.0)
        self.mag_direction_errors = {label: error for (label, _), error in zip(self.mag_directions, errors)}
        self.mag_heading_offset = self.circular_mean_deg(errors)
        self.heading_filter.reset()
        self.heading_fusion.reset()
        self.save_calibration()

    def load_calibration(self) -> None:
        try:
            with open(CALIBRATION_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if int(data.get("version", 0)) != CALIBRATION_VERSION:
                return
            offsets = tuple(float(value) for value in data.get("mag_offsets", self.mag_offsets))
            scales = tuple(float(value) for value in data.get("mag_scales", self.mag_scales))
            if len(offsets) == 3:
                self.mag_offsets = offsets
            if len(scales) == 3:
                self.mag_scales = scales
            self.mag_heading_offset = float(data.get("mag_heading_offset", self.mag_heading_offset)) % 360.0
            vectors = data.get("mag_direction_vectors", {})
            self.mag_direction_vectors = {
                label: tuple(float(value) for value in vector)
                for label, vector in vectors.items()
                if label in {direction for direction, _ in self.mag_directions} and len(vector) == 3
            }
            accels = data.get("mag_direction_accels", {})
            self.mag_direction_accels = {
                label: tuple(float(value) for value in accel)
                for label, accel in accels.items()
                if label in {direction for direction, _ in self.mag_directions} and len(accel) == 3
            }
            extents = data.get("mag_rotation_extents", {})
            self.mag_rotation_extents = {
                mode: extent
                for mode, extent in extents.items()
                if mode in {"flat", "upright", "tumble", "phone"} and isinstance(extent, dict)
            }
            tumble = data.get("mag_tumble_extent", self.mag_rotation_extents.get("tumble", {}))
            if isinstance(tumble, dict):
                self.mag_tumble_extent = tumble
        except Exception:
            return

    def save_calibration(self) -> None:
        try:
            os.makedirs(os.path.dirname(CALIBRATION_PATH), exist_ok=True)
            data = {
                "version": CALIBRATION_VERSION,
                "mag_offsets": list(self.mag_offsets),
                "mag_scales": list(self.mag_scales),
                "mag_heading_offset": self.mag_heading_offset,
                "mag_direction_vectors": {
                    label: list(vector) for label, vector in self.mag_direction_vectors.items()
                },
                "mag_direction_accels": {
                    label: list(accel) for label, accel in self.mag_direction_accels.items()
                },
                "mag_rotation_extents": self.mag_rotation_extents,
                "mag_tumble_extent": self.mag_tumble_extent,
            }
            tmp_path = f"{CALIBRATION_PATH}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp_path, CALIBRATION_PATH)
        except Exception:
            return

    def finish_gyro_calibration(self) -> None:
        if self.gyro_samples:
            self.gyro_offsets = tuple(value / self.gyro_samples for value in self.gyro_sum)
            self.baseline = tuple(value / self.gyro_samples for value in self.accel_sum)
        self.mag_cal_phase = "mag_tumble"
        self.mag_cal_started = time.monotonic()
        self.mag_vector_samples = []
        self.mag_accel_samples = []
        self.mag_heading_samples = []
        self.qmi_filter.reset()

    def quit(self) -> None:
        self.running = False

    def run(self) -> int:
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        if self.gyro_map.active:
                            self.close_map()
                        elif self.mag_calibrating:
                            self.cancel_calibration()
                        else:
                            self.running = False
                    elif event.key in (pygame.K_c, pygame.K_SPACE):
                        if not self.gyro_map.active:
                            self.calibrate()
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.gyro_map.active:
                        self.gyro_map.handle_click(event.pos)
                        continue
                    if self.mag_calibrating:
                        handled = False
                        for index, (_, rect) in enumerate(self.mag_direction_buttons):
                            if rect.collidepoint(event.pos):
                                if self.mag_cal_phase == "mag_select":
                                    self.start_mag_hold_calibration(index)
                                handled = True
                                break
                        if not handled:
                            for _, rect, action in self.modal_buttons:
                                if rect.collidepoint(event.pos):
                                    action()
                                    handled = True
                                    break
                        if handled:
                            continue
                    for _, rect, action in self.buttons:
                        if rect.collidepoint(event.pos):
                            action()
                            break

            self.width, self.height = self.screen.get_size()
            self.buttons = self.layout_buttons()
            imu = self.read_imu()
            mag = self.read_mag()
            self.update_map(imu, mag)
            self.draw(imu, mag)
            pygame.display.flip()
            self.clock.tick(self.target_fps)

        pygame.quit()
        return 0

    def read_imu(self) -> tuple[float, ...] | None:
        if not self.qmi:
            return None
        try:
            ax, ay, az, gx, gy, gz = self.qmi.read()
            if self.mag_calibrating and self.mag_cal_phase == "gyro":
                for i, value in enumerate((gx, gy, gz)):
                    self.gyro_sum[i] += value
                for i, value in enumerate((ax, ay, az)):
                    self.accel_sum[i] += value
                self.gyro_samples += 1
                if time.monotonic() - self.mag_cal_started >= self.gyro_cal_duration:
                    self.finish_gyro_calibration()
            ox, oy, oz = self.gyro_offsets
            corrected_gyro = (gx - ox, gy - oy, gz - oz)
            filtered = self.qmi_filter.update((ax, ay, az, *corrected_gyro))
            self.latest_accel = filtered[:3]
            self.latest_gyro_z_dps = corrected_gyro[2]
            self.latest_yaw_rate_dps = self.gyro_yaw_rate_dps(corrected_gyro, self.latest_accel)
            return filtered
        except Exception as exc:
            self.qmi_error = str(exc)
            return None

    def update_map(self, imu: tuple[float, ...] | None, mag: tuple[float, ...] | None) -> None:
        heading = float(mag[3]) if mag else None
        self.gyro_map.update(imu, heading)

    def read_mag(self) -> tuple[float, ...] | None:
        if not self.qmc:
            return None
        try:
            now = time.monotonic()
            mx, my, mz, _, status = self.qmc.read()
            self.record_mag_history(now, (mx, my, mz), self.latest_accel)
            ox, oy, oz = self.mag_offsets
            sx, sy, sz = self.mag_scales
            cx, cy, cz = (mx - ox) * sx, (my - oy) * sy, (mz - oz) * sz
            heading = self.tilt_compensated_heading(cx, cy, cz, self.latest_accel)
            if self.mag_calibrating and self.mag_cal_phase == "mag_hold":
                self.mag_heading_samples.append(heading)
                self.mag_vector_samples.append((mx, my, mz))
                self.mag_accel_samples.append(self.latest_accel)
                if self.update_heading_stability() or self.heading_wait_timed_out():
                    self.finish_current_direction()
            elif self.mag_calibrating and self.mag_cal_phase == "mag_rotate":
                self.mag_vector_samples.append((mx, my, mz))
                self.mag_accel_samples.append(self.latest_accel)
                if time.monotonic() - self.mag_cal_started >= self.mag_rotation_duration:
                    self.finish_current_rotation()
            elif self.mag_calibrating and self.mag_cal_phase == "mag_tumble":
                self.mag_vector_samples.append((mx, my, mz))
                self.mag_accel_samples.append(self.latest_accel)
                if self.tumble_ready():
                    self.finish_tumble_calibration()
            elif self.mag_calibrating and self.mag_cal_phase == "north_hold":
                self.mag_heading_samples.append(heading)
                self.mag_vector_samples.append((mx, my, mz))
                self.mag_accel_samples.append(self.latest_accel)
                if self.update_heading_stability() or self.heading_wait_timed_out():
                    self.finish_north_alignment()
            elif self.mag_calibrating and self.mag_cal_phase == "phone_wait":
                snapshot = self.phone_snapshot()
                if snapshot["active"] and snapshot["fresh"] and snapshot["heading"] is not None:
                    self.mag_cal_phase = "phone_collect"
                    self.mag_cal_started = time.monotonic()
                    self.phone_cal_samples = []
                    self.phone_last_update_used = 0.0
                    with self.phone_lock:
                        self.phone_state["phase"] = "collecting"
                    self.qmc_filter.reset()
            elif self.mag_calibrating and self.mag_cal_phase == "phone_collect":
                snapshot = self.phone_snapshot()
                if snapshot["fresh"] and snapshot["heading"] is not None and float(snapshot["updated"]) > self.phone_last_update_used:
                    self.phone_last_update_used = float(snapshot["updated"])
                    sample_time = float(snapshot["target_time"])
                    phone_heading = float(snapshot["heading"])
                    aligned_sample = self.mag_sample_at(sample_time)
                    if aligned_sample and self.accept_phone_heading(phone_heading, sample_time):
                        aligned_mag, aligned_accel = aligned_sample
                        self.phone_cal_samples.append((aligned_mag, aligned_accel, phone_heading, sample_time))
                    if self.phone_cal_ready():
                        self.finish_phone_calibration()
            heading = (heading + self.mag_heading_offset) % 360.0
            heading = self.heading_fusion.update(heading, self.latest_yaw_rate_dps, now)
            strength = math.sqrt(cx * cx + cy * cy + cz * cz)
            return cx, cy, cz, heading, status, strength
        except Exception as exc:
            self.qmc_error = str(exc)
            return None

    def draw(self, imu: tuple[float, ...] | None, mag: tuple[float, ...] | None) -> None:
        self.screen.fill(BG)
        self.modal_buttons = []
        self.text("Whisplay GYRO Test", 18, 14, self.title_font, TEXT)
        self.text("QMI8658B IMU / MQC5883P Magnetometer", 20, 44, self.small_font, MUTED)

        left = 18
        top = 76
        panel_w = self.width - 36
        self.panel(left, top, panel_w, 142)
        if imu:
            ax, ay, az, gx, gy, gz = imu
            self.text("QMI8658B", left + 14, top + 12, self.body_font, GREEN)
            self.row("ACC g", f"X {ax:+.2f}   Y {ay:+.2f}   Z {az:+.2f}", left + 14, top + 42)
            self.row("GYRO dps", f"X {gx:+.1f}   Y {gy:+.1f}   Z {gz:+.1f}", left + 14, top + 68)
            ox, oy, oz = self.gyro_offsets
            self.row("Bias", f"X {ox:+.1f} Y {oy:+.1f} Z {oz:+.1f}", left + 14, top + 94)
            self.draw_tilt(imu[:3], (self.width - 58, top + 112), 30)
        else:
            self.text("QMI8658B not ready", left + 14, top + 18, self.body_font, RED)
            self.text(self.fit(self.qmi_error, 38), left + 14, top + 48, self.small_font, MUTED)

        mag_top = top + 158
        self.panel(left, mag_top, panel_w, 142)
        self.mag_direction_buttons = []
        if mag:
            mx, my, mz, heading, status, strength = mag
            self.text(f"MQC5883P 0x{self.qmc.addr:02x}", left + 14, mag_top + 12, self.body_font, BLUE)
            self.row("MAG cal", f"X {mx:+.0f}   Y {my:+.0f}   Z {mz:+.0f}", left + 14, mag_top + 42)
            self.row("Heading", f"{heading:05.1f} deg   status 0x{int(status):02x}", left + 14, mag_top + 68)
            if self.mag_calibrating:
                if self.mag_cal_phase == "gyro":
                    elapsed = min(self.gyro_cal_duration, time.monotonic() - self.mag_cal_started)
                    self.row("Cal", f"hold still {elapsed:03.1f}/{self.gyro_cal_duration:.0f}s", left + 14, mag_top + 94)
                elif self.mag_cal_phase == "mag_select":
                    done = len(self.mag_direction_vectors)
                    rotations = len(self.mag_rotation_extents)
                    self.row("Cal", f"directions {done}/4  rotations {rotations}/2", left + 14, mag_top + 94)
                elif self.mag_cal_phase == "mag_tumble":
                    elapsed = min(self.mag_tumble_max_duration, time.monotonic() - self.mag_cal_started)
                    self.row("Cal", f"figure-8 {self.tumble_progress()*100:03.0f}%  {elapsed:03.1f}s samples {len(self.mag_vector_samples)}", left + 14, mag_top + 94)
                elif self.mag_cal_phase == "mag_align":
                    self.row("Cal", "point reference edge to true North", left + 14, mag_top + 94)
                elif self.mag_cal_phase == "north_hold":
                    elapsed = time.monotonic() - self.mag_cal_started
                    stable = self.heading_stability_deg()
                    confirm = self.stable_confirm_elapsed()
                    self.row("Cal", f"north hold {elapsed:03.1f}s jitter {stable:.1f} ok {confirm:.1f}s", left + 14, mag_top + 94)
                elif self.mag_cal_phase == "phone_wait":
                    snapshot = self.phone_snapshot()
                    self.row("Cal", f"scan QR, phone samples {snapshot['samples']}", left + 14, mag_top + 94)
                elif self.mag_cal_phase == "phone_collect":
                    elapsed = time.monotonic() - self.mag_cal_started
                    snapshot = self.phone_snapshot()
                    self.row("Cal", f"phone ref {self.phone_cal_progress()*100:03.0f}% {elapsed:03.1f}s pairs {len(self.phone_cal_samples)} drop {self.phone_rejected_samples} lag {float(snapshot['delay_ms']):.0f}ms", left + 14, mag_top + 94)
                elif self.mag_cal_phase == "mag_rotate":
                    elapsed = min(self.mag_rotation_duration, time.monotonic() - self.mag_cal_started)
                    mode = "flat" if self.mag_rotation_mode == "flat" else "upright"
                    self.row("Cal", f"{mode} rotate {elapsed:03.1f}/{self.mag_rotation_duration:.0f}s samples {len(self.mag_vector_samples)}", left + 14, mag_top + 94)
                else:
                    elapsed = time.monotonic() - self.mag_cal_started
                    label, _ = self.mag_directions[self.mag_direction_index]
                    stable = self.heading_stability_deg()
                    confirm = self.stable_confirm_elapsed()
                    self.row("Cal", f"face {label} {elapsed:03.1f}s jitter {stable:.1f} ok {confirm:.1f}s", left + 14, mag_top + 94)
            else:
                label, _ = self.mag_directions[self.mag_direction_index]
                ox, oy, _ = self.mag_offsets
                sx, sy, _ = self.mag_scales
                self.row("Offset", f"X {ox:+.0f} Y {oy:+.0f}  scale {sx:.2f}/{sy:.2f}", left + 14, mag_top + 94)
            self.text(f"|B| {strength:.0f}", self.width - 118, mag_top + 12, self.small_font, MUTED)
            self.draw_compass(heading, (self.width - 64, mag_top + 106), 34)
        else:
            self.text("MQC5883P not ready", left + 14, mag_top + 18, self.body_font, RED)
            self.text(self.fit(self.qmc_error, 38), left + 14, mag_top + 48, self.small_font, MUTED)

        for label, rect, _ in self.buttons:
            display_label = label
            color = GREEN if label == "Calibrate" else BLUE if label == "Map" else PANEL_2
            pygame.draw.rect(self.screen, color, rect, border_radius=8)
            pygame.draw.rect(self.screen, LINE, rect, 1, border_radius=8)
            self.center(display_label, rect.center, self.body_font, TEXT)
        if self.mag_calibrating:
            self.draw_calibration_modal()
        elif self.gyro_map.active:
            self.draw_map_modal()

    def make_qr_surface(self, text: str, scale: int) -> pygame.Surface:
        try:
            import qrcode

            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=1,
                border=4,
            )
            qr.add_data(text)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            quiet = 0
        except Exception:
            matrix = qr_matrix(text)
            quiet = 4
        size = (len(matrix) + quiet * 2) * scale
        surface = pygame.Surface((size, size))
        surface.fill((255, 255, 255))
        for r, row in enumerate(matrix):
            for c, value in enumerate(row):
                if value:
                    pygame.draw.rect(
                        surface,
                        (0, 0, 0),
                        ((c + quiet) * scale, (r + quiet) * scale, scale, scale),
                    )
        return surface

    def draw_map_modal(self) -> None:
        self.gyro_map.draw(self.screen, self.title_font, self.body_font, self.qr_font)
        return

        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((226, 230, 222, 232))
        self.screen.blit(overlay, (0, 0))

        radius = max(120, min((self.width - 48) // 2, (self.height - 150) // 2))
        size = radius * 2
        map_surface = pygame.Surface((size, size), pygame.SRCALPHA)
        map_surface.fill((232, 174, 58, 255))
        center = (radius, radius)
        heading_rad = math.radians(self.map_heading)
        cos_h, sin_h = math.cos(heading_rad), math.sin(heading_rad)
        pos_east, pos_north = self.map_position

        def project(east: float, north: float) -> tuple[int, int]:
            dx, dy = east - pos_east, north - pos_north
            return (
                round(radius + dx * cos_h - dy * sin_h),
                round(radius - dx * sin_h - dy * cos_h),
            )

        grid = 132.0
        east_cell = math.floor(pos_east / grid)
        north_cell = math.floor(pos_north / grid)
        building_colors = ((181, 111, 24), (194, 123, 27), (166, 94, 22), (205, 137, 33))
        for gx in range(east_cell - 3, east_cell + 4):
            for gy in range(north_cell - 3, north_cell + 4):
                inset_x = 19 + abs((gx * 7 + gy * 3) % 10)
                inset_y = 18 + abs((gx * 5 - gy * 2) % 11)
                x0, x1 = gx * grid + inset_x, (gx + 1) * grid - inset_x
                y0, y1 = gy * grid + inset_y, (gy + 1) * grid - inset_y
                polygon = [project(x0, y0), project(x1, y0), project(x1, y1), project(x0, y1)]
                color = building_colors[(gx * 3 + gy * 5) % len(building_colors)]
                pygame.draw.polygon(map_surface, color, polygon)
                pygame.draw.polygon(map_surface, (213, 149, 43), polygon, 2)
                if (gx + gy) % 3 == 0:
                    divider_a = project((x0 + x1) * 0.5, y0)
                    divider_b = project((x0 + x1) * 0.5, y1)
                    pygame.draw.line(map_surface, (143, 79, 20), divider_a, divider_b, 3)

        road = (250, 235, 188)
        road_edge = (218, 158, 48)
        span = radius * 2.5
        for gx in range(east_cell - 3, east_cell + 4):
            p1, p2 = project(gx * grid, pos_north - span), project(gx * grid, pos_north + span)
            width = 30 if gx % 2 == 0 else 22
            pygame.draw.line(map_surface, road_edge, p1, p2, width + 5)
            pygame.draw.line(map_surface, road, p1, p2, width)
        for gy in range(north_cell - 3, north_cell + 4):
            p1, p2 = project(pos_east - span, gy * grid), project(pos_east + span, gy * grid)
            width = 30 if gy % 2 else 22
            pygame.draw.line(map_surface, road_edge, p1, p2, width + 5)
            pygame.draw.line(map_surface, road, p1, p2, width)

        curve_offset = round((pos_north - (0.42 * pos_east + 58.0 * math.sin(pos_east / 155.0))) / 420.0) * 420.0
        highway_world = []
        for east in range(round(pos_east) - radius * 3, round(pos_east) + radius * 3 + 1, 18):
            north = 0.42 * east + 58.0 * math.sin(east / 155.0) + curve_offset
            highway_world.append(project(float(east), north))
        if len(highway_world) > 1:
            pygame.draw.lines(map_surface, (211, 145, 40), False, highway_world, 43)
            pygame.draw.lines(map_surface, (250, 235, 188), False, highway_world, 35)

        coast_x = round((pos_east - 165.0) / 620.0) * 620.0 + 165.0
        coast_world = []
        for north in range(round(pos_north) - radius * 3, round(pos_north) + radius * 3 + 1, 16):
            east = coast_x + 24.0 * math.sin(north / 82.0) + 11.0 * math.sin(north / 29.0)
            coast_world.append((east, float(north)))
        coast_screen = [project(east, north) for east, north in coast_world]
        water_polygon = coast_screen + [
            project(coast_x + span * 2.0, coast_world[-1][1]),
            project(coast_x + span * 2.0, coast_world[0][1]),
        ]
        pygame.draw.polygon(map_surface, (139, 188, 169), water_polygon)
        pygame.draw.lines(map_surface, (77, 75, 45), False, coast_screen, 13)
        pygame.draw.lines(map_surface, (229, 178, 59), False, coast_screen, 5)

        nearest_east = round(pos_east / grid) * grid
        nearest_north = round(pos_north / grid) * grid
        police_point = project(nearest_east - grid * 0.58, nearest_north + grid * 0.72)
        star = []
        for index in range(10):
            angle = math.radians(index * 36.0 - 90.0)
            star_radius = 15 if index % 2 == 0 else 8
            star.append((police_point[0] + math.cos(angle) * star_radius, police_point[1] + math.sin(angle) * star_radius))
        pygame.draw.polygon(map_surface, (40, 34, 23), star)
        inner_star = []
        for index in range(10):
            angle = math.radians(index * 36.0 - 90.0)
            star_radius = 11 if index % 2 == 0 else 6
            inner_star.append((police_point[0] + math.cos(angle) * star_radius, police_point[1] + math.sin(angle) * star_radius))
        pygame.draw.polygon(map_surface, (250, 213, 83), inner_star)

        label_surface = pygame.Surface((104, 35), pygame.SRCALPHA)
        line_1 = self.qr_font.render("VICE CITY", True, (42, 31, 18))
        line_2 = self.qr_font.render("POLICE DEPT", True, (42, 31, 18))
        label_surface.blit(line_1, (0, 1))
        label_surface.blit(line_2, (0, 16))
        label_surface = pygame.transform.rotate(label_surface, self.map_heading)
        label_center = project(nearest_east - grid * 0.05, nearest_north + grid * 0.74)
        map_surface.blit(label_surface, label_surface.get_rect(center=label_center))

        mask = pygame.Surface((size, size), pygame.SRCALPHA)
        pygame.draw.circle(mask, (255, 255, 255, 255), center, radius)
        map_surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        map_center = (self.width // 2, 32 + radius)
        map_rect = map_surface.get_rect(center=map_center)
        self.screen.blit(map_surface, map_rect)
        pygame.draw.circle(self.screen, (2, 3, 3), map_center, radius, 16)
        pygame.draw.circle(self.screen, (12, 13, 12), map_center, radius - 9, 2)

        north_x = map_center[0] - math.sin(heading_rad) * radius
        north_y = map_center[1] - math.cos(heading_rad) * radius
        pygame.draw.circle(self.screen, (2, 3, 3), (round(north_x), round(north_y)), 25)
        north_glyph = self.title_font.render("N", True, (250, 250, 246))
        self.screen.blit(north_glyph, north_glyph.get_rect(center=(north_x, north_y)))

        px, py = map_center
        pygame.draw.polygon(self.screen, (4, 5, 5), [(px, py - 24), (px - 17, py + 3), (px + 17, py + 3)])
        pygame.draw.circle(self.screen, (4, 5, 5), (px, py + 4), 17)
        pygame.draw.polygon(self.screen, (250, 250, 247), [(px, py - 16), (px - 10, py + 2), (px + 10, py + 2)])
        pygame.draw.circle(self.screen, (250, 250, 247), (px, py + 4), 10)

        close_rect = pygame.Rect(self.width // 2 - 60, self.height - 78, 120, 36)
        pygame.draw.rect(self.screen, PANEL_2, close_rect, border_radius=8)
        pygame.draw.rect(self.screen, LINE, close_rect, 1, border_radius=8)
        self.center("Close", close_rect.center, self.body_font, TEXT)
        self.map_buttons.append(("Close", close_rect, self.close_map))

    def draw_calibration_modal(self) -> None:
        overlay = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        self.screen.blit(overlay, (0, 0))

        modal_w = min(self.width - 36, 430)
        modal_h = min(self.height - 12, 332)
        rect = pygame.Rect((self.width - modal_w) // 2, (self.height - modal_h) // 2, modal_w, modal_h)
        pygame.draw.rect(self.screen, PANEL, rect, border_radius=8)
        pygame.draw.rect(self.screen, LINE, rect, 1, border_radius=8)

        x = rect.x + 18
        y = rect.y + 16
        self.text("Compass Calibration", x, y, self.body_font, TEXT)
        self.text("3D mag + north align", rect.right - 148, y + 2, self.small_font, MUTED)

        if self.mag_cal_phase == "gyro":
            elapsed = min(self.gyro_cal_duration, time.monotonic() - self.mag_cal_started)
            self.text("Keep the device still", x, y + 36, self.small_font, YELLOW)
            self.draw_progress_bar(x, y + 68, modal_w - 36, 16, elapsed / self.gyro_cal_duration)
            self.text(f"{elapsed:03.1f}/{self.gyro_cal_duration:.0f}s", x, y + 92, self.small_font, MUTED)
        elif self.mag_cal_phase == "phone_wait":
            snapshot = self.phone_snapshot()
            self.text("Scan with iPhone on same Wi-Fi", x, y + 34, self.small_font, YELLOW)
            qr_rect = self.phone_qr_surface.get_rect(center=(rect.centerx, y + 130))
            self.screen.blit(self.phone_qr_surface, qr_rect)
            self.center(f"https://{self.phone_ip}:8765", (rect.centerx, qr_rect.bottom + 15), self.body_font, TEXT)
            state = "Waiting for phone Start"
            if snapshot["active"]:
                state = "Phone connected, waiting for compass"
            self.center(state, (rect.centerx, rect.bottom - 58), self.small_font, MUTED)
        elif self.mag_cal_phase == "phone_collect":
            elapsed = min(self.phone_cal_max_duration, time.monotonic() - self.mag_cal_started)
            snapshot = self.phone_snapshot()
            self.text("Hold phone and device together, rotate slowly", x, y + 34, self.small_font, YELLOW)
            self.draw_phone_pair_hint(rect.centerx, y + 118)
            self.draw_phone_span_bars(x, rect.bottom - 120, modal_w - 36)
            self.draw_progress_bar(x, rect.bottom - 82, modal_w - 36, 16, self.phone_cal_progress())
            heading = snapshot["heading"]
            heading_text = "--.-" if heading is None else f"{float(heading):05.1f}"
            self.text(f"{self.phone_cal_progress()*100:03.0f}%  {elapsed:03.1f}/{self.phone_cal_max_duration:.0f}s  phone {heading_text}deg  pairs {len(self.phone_cal_samples)} drop {self.phone_rejected_samples} lag {float(snapshot['delay_ms']):.0f}ms", x, rect.bottom - 58, self.small_font, MUTED)
        elif self.mag_cal_phase == "mag_tumble":
            elapsed = min(self.mag_tumble_max_duration, time.monotonic() - self.mag_cal_started)
            self.text("Rotate the device around X / Y / Z", x, y + 34, self.small_font, YELLOW)
            self.draw_tumble_hint(rect.centerx, y + 116, modal_w - 48)
            self.draw_span_bars(x, rect.bottom - 120, modal_w - 36)
            self.draw_progress_bar(x, rect.bottom - 82, modal_w - 36, 16, self.tumble_progress())
            self.text(f"{self.tumble_progress()*100:03.0f}%  {elapsed:03.1f}/{self.mag_tumble_max_duration:.0f}s  samples {len(self.mag_vector_samples)}", x, rect.bottom - 58, self.small_font, MUTED)
        elif self.mag_cal_phase == "mag_align":
            self.text("Point reference edge to true North", x, y + 34, self.small_font, YELLOW)
            self.draw_north_align_hint(rect.centerx, y + 122)
            set_rect = pygame.Rect(rect.centerx - 70, rect.bottom - 92, 140, 36)
            pygame.draw.rect(self.screen, BLUE, set_rect, border_radius=8)
            pygame.draw.rect(self.screen, LINE, set_rect, 1, border_radius=8)
            self.center("Set North", set_rect.center, self.body_font, TEXT)
            self.modal_buttons.append(("Set North", set_rect, self.start_north_alignment))
        elif self.mag_cal_phase == "north_hold":
            elapsed = time.monotonic() - self.mag_cal_started
            stable = self.heading_stability_deg()
            stable_value = clamp(self.stable_confirm_elapsed() / self.mag_stable_confirm_duration, 0.0, 1.0)
            self.text("Hold still facing true North", x, y + 36, self.small_font, YELLOW)
            self.draw_north_align_hint(rect.centerx, y + 118)
            self.draw_progress_bar(x, rect.bottom - 82, modal_w - 36, 16, stable_value)
            self.text(f"stable {self.mag_stable_confirm_duration:.0f}s after {self.mag_stable_min_duration:.0f}s min  {elapsed:03.1f}/{self.mag_stable_max_duration:.0f}s  jitter {stable:.1f}deg", x, rect.bottom - 58, self.small_font, MUTED)
        elif self.mag_cal_phase == "mag_hold":
            label, _ = self.mag_directions[self.mag_direction_index]
            elapsed = time.monotonic() - self.mag_cal_started
            stable = self.heading_stability_deg()
            stable_value = clamp(self.stable_confirm_elapsed() / self.mag_stable_confirm_duration, 0.0, 1.0)
            self.text(f"Face {label} and hold still", x, y + 36, self.small_font, YELLOW)
            self.draw_progress_bar(x, y + 68, modal_w - 36, 16, stable_value)
            self.text(f"stable {self.mag_stable_confirm_duration:.0f}s after {self.mag_stable_min_duration:.0f}s min  {elapsed:03.1f}/{self.mag_stable_max_duration:.0f}s  jitter {stable:.1f}deg", x, y + 92, self.small_font, MUTED)
        elif self.mag_cal_phase == "mag_rotate":
            elapsed = min(self.mag_rotation_duration, time.monotonic() - self.mag_cal_started)
            mode_label = "Flat" if self.mag_rotation_mode == "flat" else "Upright"
            self.text(f"{mode_label}: rotate one full circle", x, y + 34, self.small_font, YELLOW)
            self.draw_rotation_hint(rect.centerx, y + 112, self.mag_rotation_mode)
            self.draw_progress_bar(x, rect.bottom - 82, modal_w - 36, 16, elapsed / self.mag_rotation_duration)
            self.text(f"{elapsed:03.1f}/{self.mag_rotation_duration:.0f}s  samples {len(self.mag_vector_samples)}", x, rect.bottom - 58, self.small_font, MUTED)
        else:
            self.text("Rotate first, then tap each direction", x, y + 34, self.small_font, YELLOW)
            self.draw_rotation_buttons(x, y + 62, modal_w - 36)
            direction_size = 34
            direction_spacing = min(38, max(28, (rect.bottom - y - 176) // 2))
            direction_center_y = rect.bottom - 74 - direction_spacing
            self.draw_compass_direction_buttons(rect.centerx, direction_center_y, direction_spacing, direction_size)
        cancel_rect = pygame.Rect(rect.x + 20, rect.bottom - 50, 96, 34)
        pygame.draw.rect(self.screen, PANEL_2, cancel_rect, border_radius=8)
        pygame.draw.rect(self.screen, LINE, cancel_rect, 1, border_radius=8)
        self.center("Cancel", cancel_rect.center, self.body_font, TEXT)
        self.modal_buttons.append(("Cancel", cancel_rect, self.cancel_calibration))

        done_rect = pygame.Rect(rect.right - 116, rect.bottom - 50, 96, 34)
        can_done = False
        done_color = BLUE if can_done else PANEL_2
        pygame.draw.rect(self.screen, done_color, done_rect, border_radius=8)
        pygame.draw.rect(self.screen, LINE, done_rect, 1, border_radius=8)
        self.center("Done", done_rect.center, self.body_font, TEXT if can_done else MUTED)
        self.modal_buttons.append(("Done", done_rect, self.done_calibration))

    def draw_progress_bar(self, x: int, y: int, w: int, h: int, value: float) -> None:
        value = clamp(value, 0.0, 1.0)
        pygame.draw.rect(self.screen, (18, 23, 30), (x, y, w, h), border_radius=5)
        pygame.draw.rect(self.screen, LINE, (x, y, w, h), 1, border_radius=5)
        pygame.draw.rect(self.screen, GREEN, (x + 2, y + 2, max(0, int((w - 4) * value)), h - 4), border_radius=4)

    def draw_rotation_buttons(self, x: int, y: int, w: int) -> None:
        gap = 10
        bw = (w - gap) // 2
        options = (("Flat Rotate", "flat"), ("Upright Rotate", "upright"))
        for index, (label, mode) in enumerate(options):
            rect = pygame.Rect(x + index * (bw + gap), y, bw, 36)
            color = GREEN if mode in self.mag_rotation_extents else PANEL_2
            pygame.draw.rect(self.screen, color, rect, border_radius=8)
            pygame.draw.rect(self.screen, LINE, rect, 1, border_radius=8)
            self.center(label, rect.center, self.small_font, TEXT)
            self.modal_buttons.append((label, rect, lambda mode=mode: self.start_mag_rotation_calibration(mode)))

    def draw_tumble_hint(self, center_x: int, center_y: int, width: int) -> None:
        spacing = max(88, min(124, width // 3))
        centers = (center_x - spacing, center_x, center_x + spacing)
        self.draw_axis_rotation_hint(centers[0], center_y, "YAW", "Z")
        self.draw_axis_rotation_hint(centers[1], center_y, "PITCH", "X")
        self.draw_axis_rotation_hint(centers[2], center_y, "ROLL", "Y")

    def draw_axis_rotation_hint(self, center_x: int, center_y: int, label: str, axis: str) -> None:
        self.center(label, (center_x, center_y - 58), self.small_font, MUTED)
        body = self.draw_device_body(center_x, center_y, 34, 46, 0.0)
        if axis == "Z":
            arc_rect = pygame.Rect(center_x - 36, center_y - 36, 72, 72)
            end_angle = math.radians(330)
            pygame.draw.arc(self.screen, YELLOW, arc_rect, math.radians(25), end_angle, 3)
            self.draw_arc_arrow_head(arc_rect, end_angle, YELLOW)
            pygame.draw.line(self.screen, MUTED, (center_x, center_y - 42), (center_x, center_y + 42), 1)
        elif axis == "X":
            arc_rect = pygame.Rect(center_x - 38, center_y - 16, 76, 50)
            end_angle = math.radians(520)
            pygame.draw.arc(self.screen, YELLOW, arc_rect, math.radians(190), end_angle, 3)
            self.draw_arc_arrow_head(arc_rect, end_angle, YELLOW)
            pygame.draw.line(self.screen, MUTED, (body.left - 10, body.centery), (body.right + 10, body.centery), 1)
        else:
            arc_rect = pygame.Rect(center_x - 24, center_y - 42, 48, 84)
            end_angle = math.radians(430)
            pygame.draw.arc(self.screen, YELLOW, arc_rect, math.radians(105), end_angle, 3)
            self.draw_arc_arrow_head(arc_rect, end_angle, YELLOW)
            pygame.draw.line(self.screen, MUTED, (body.centerx, body.top - 10), (body.centerx, body.bottom + 10), 1)

    def draw_north_align_hint(self, center_x: int, center_y: int) -> None:
        pygame.draw.line(self.screen, GREEN, (center_x, center_y + 58), (center_x, center_y - 58), 4)
        self.draw_arrow_head((center_x, center_y - 66), -90, GREEN)
        self.center("N", (center_x, center_y - 86), self.body_font, TEXT)
        body = self.draw_device_body(center_x, center_y + 18, 54, 72, 0.0)
        pygame.draw.line(self.screen, RED, body.midtop, (body.centerx, body.y - 22), 3)

    def draw_span_bars(self, x: int, y: int, w: int) -> None:
        extent = self.rotation_extent(self.mag_vector_samples) if self.mag_vector_samples else {"span": [0.0, 0.0, 0.0]}
        spans = [float(value) for value in extent.get("span", [0.0, 0.0, 0.0])]
        labels = ("X", "Y", "Z")
        bw = max(38, (w - 16) // 3)
        for i, label in enumerate(labels):
            bx = x + i * (bw + 8)
            value = clamp(spans[i] / self.mag_tumble_required_span, 0.0, 1.0)
            self.text(label, bx, y, self.small_font, MUTED)
            self.draw_progress_bar(bx + 16, y + 1, bw - 18, 10, value)

    def draw_phone_span_bars(self, x: int, y: int, w: int) -> None:
        vectors = [sample[0] for sample in self.phone_cal_samples]
        extent = self.rotation_extent(vectors) if vectors else {"span": [0.0, 0.0, 0.0]}
        spans = [float(value) for value in extent.get("span", [0.0, 0.0, 0.0])]
        labels = ("X", "Y", "Z")
        bw = max(38, (w - 16) // 3)
        for i, label in enumerate(labels):
            bx = x + i * (bw + 8)
            value = clamp(spans[i] / self.phone_cal_required_span, 0.0, 1.0)
            self.text(label, bx, y, self.small_font, MUTED)
            self.draw_progress_bar(bx + 16, y + 1, bw - 18, 10, value)

    def draw_phone_pair_hint(self, center_x: int, center_y: int) -> None:
        phone = pygame.Rect(0, 0, 58, 86)
        phone.center = (center_x - 38, center_y + 2)
        device = pygame.Rect(0, 0, 54, 72)
        device.center = (center_x + 38, center_y + 2)
        pygame.draw.rect(self.screen, (18, 23, 30), phone, border_radius=8)
        pygame.draw.rect(self.screen, GREEN, phone, 2, border_radius=8)
        pygame.draw.rect(self.screen, LINE, phone.inflate(-10, -14), 1, border_radius=4)
        self.center("P", phone.center, self.big_font, TEXT)
        self.draw_device_body(device.centerx, device.centery, device.w, device.h, 0.0)
        pygame.draw.line(self.screen, YELLOW, (center_x - 10, center_y - 52), (center_x + 10, center_y - 52), 3)
        self.draw_arrow_head((center_x + 18, center_y - 52), 0, YELLOW)
        arc_rect = pygame.Rect(center_x - 86, center_y - 50, 172, 100)
        end_angle = math.radians(515)
        pygame.draw.arc(self.screen, YELLOW, arc_rect, math.radians(210), end_angle, 3)
        self.draw_arc_arrow_head(arc_rect, end_angle, YELLOW)
        self.center("rotate together", (center_x, center_y + 64), self.small_font, MUTED)

    def draw_rotation_hint(self, center_x: int, center_y: int, mode: str) -> None:
        if mode == "upright":
            body = self.draw_device_body(center_x, center_y + 4, 54, 72, 0.0)
            pygame.draw.line(self.screen, MUTED, (body.centerx, body.bottom - 8), (body.centerx, body.bottom + 18), 2)
            arc_rect = pygame.Rect(center_x - 86, center_y + 4, 172, 52)
            end_angle = math.radians(520)
            pygame.draw.arc(self.screen, YELLOW, arc_rect, math.radians(195), end_angle, 4)
            self.draw_arc_arrow_head(arc_rect, end_angle, YELLOW)
            self.text("turn around vertical axis", center_x - 72, center_y + 62, self.small_font, MUTED)
        else:
            self.draw_device_body(center_x, center_y + 8, 54, 72, 0.0)
            arc_rect = pygame.Rect(center_x - 66, center_y - 58, 132, 132)
            end_angle = math.radians(340)
            pygame.draw.arc(self.screen, YELLOW, arc_rect, math.radians(20), end_angle, 4)
            self.draw_arc_arrow_head(arc_rect, end_angle, YELLOW)
            self.text("keep flat, rotate clockwise", center_x - 76, center_y + 62, self.small_font, MUTED)

    def draw_device_body(self, center_x: int, center_y: int, w: int, h: int, angle_deg: float = 0.0) -> pygame.Rect:
        rect = pygame.Rect(0, 0, w, h)
        rect.center = (center_x, center_y)
        pygame.draw.rect(self.screen, (18, 23, 30), rect, border_radius=7)
        pygame.draw.rect(self.screen, BLUE, rect, 2, border_radius=7)
        pygame.draw.rect(self.screen, LINE, rect.inflate(-10, -12), 1, border_radius=4)
        self.center("A", rect.center, self.big_font, TEXT)
        pygame.draw.circle(self.screen, MUTED, (rect.centerx, rect.bottom - 7), 2)
        return rect

    def draw_arrow_head(self, tip: tuple[int, int], angle_deg: float, color: tuple[int, int, int]) -> None:
        angle = math.radians(angle_deg)
        left = angle + math.radians(145)
        right = angle - math.radians(145)
        points = [
            tip,
            (tip[0] + math.cos(left) * 10, tip[1] + math.sin(left) * 10),
            (tip[0] + math.cos(right) * 10, tip[1] + math.sin(right) * 10),
        ]
        pygame.draw.polygon(self.screen, color, points)

    def draw_arc_arrow_head(self, rect: pygame.Rect, angle: float, color: tuple[int, int, int]) -> None:
        cx, cy = rect.center
        rx, ry = rect.width / 2.0, rect.height / 2.0
        tip = (round(cx + math.cos(angle) * rx), round(cy - math.sin(angle) * ry))
        tangent = math.degrees(math.atan2(-math.cos(angle) * ry, -math.sin(angle) * rx))
        self.draw_arrow_head(tip, tangent, color)

    def draw_direction_buttons(self, x: int, y: int, w: int, h: int) -> None:
        if w < 100:
            return
        gap = 6
        bw = max(30, (w - gap * (len(self.mag_directions) - 1)) // len(self.mag_directions))
        for index, (label, _) in enumerate(self.mag_directions):
            rect = pygame.Rect(x + index * (bw + gap), y, bw, h)
            self.mag_direction_buttons.append((label, rect))
            if label in self.mag_direction_vectors:
                color = GREEN
            elif self.mag_cal_phase == "mag_hold" and index == self.mag_direction_index:
                color = YELLOW
            elif index == self.mag_direction_index:
                color = BLUE
            else:
                color = PANEL_2
            pygame.draw.rect(self.screen, color, rect, border_radius=7)
            pygame.draw.rect(self.screen, LINE, rect, 1, border_radius=7)
            self.center(label, rect.center, self.small_font, TEXT)

    def draw_compass_direction_buttons(self, center_x: int, center_y: int, spacing: int, size: int) -> None:
        positions = {
            "N": (center_x, center_y - spacing),
            "E": (center_x + spacing, center_y),
            "S": (center_x, center_y + spacing),
            "W": (center_x - spacing, center_y),
        }
        self.mag_direction_buttons = []
        direction_index = {label: index for index, (label, _) in enumerate(self.mag_directions)}
        for label in ("N", "E", "S", "W"):
            index = direction_index[label]
            x, y = positions[label]
            rect = pygame.Rect(0, 0, size, size)
            rect.center = (x, y)
            self.mag_direction_buttons.append((label, rect))
            if label in self.mag_direction_vectors:
                color = GREEN
            elif self.mag_cal_phase == "mag_hold" and index == self.mag_direction_index:
                color = YELLOW
            else:
                color = PANEL_2
            pygame.draw.rect(self.screen, color, rect, border_radius=7)
            pygame.draw.rect(self.screen, LINE, rect, 1, border_radius=7)
            self.center(label, rect.center, self.body_font, TEXT)

    def draw_tilt(self, accel: tuple[float, float, float], center: tuple[int, int], radius: int) -> None:
        ax, ay, az = accel
        bx, by, bz = self.baseline
        x = clamp(-(ax - bx) * radius * 1.8, -radius, radius)
        y = clamp(-(ay - by) * radius * 1.8, -radius, radius)
        pygame.draw.circle(self.screen, (18, 23, 30), center, radius)
        pygame.draw.circle(self.screen, LINE, center, radius, 2)
        pygame.draw.line(self.screen, LINE, (center[0] - radius, center[1]), (center[0] + radius, center[1]), 1)
        pygame.draw.line(self.screen, LINE, (center[0], center[1] - radius), (center[0], center[1] + radius), 1)
        dot = (round(center[0] + x), round(center[1] + y))
        pygame.draw.circle(self.screen, YELLOW if az < bz else GREEN, dot, 9)

    def draw_compass(self, heading: float, center: tuple[int, int], radius: int) -> None:
        pygame.draw.circle(self.screen, (18, 23, 30), center, radius)
        pygame.draw.circle(self.screen, LINE, center, radius, 2)
        for label, angle in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            rad = math.radians(angle - heading - 90)
            pos = (center[0] + math.cos(rad) * (radius + 12), center[1] + math.sin(rad) * (radius + 12))
            self.center(label, pos, self.small_font, MUTED)
        end = (center[0], center[1] - radius + 6)
        pygame.draw.line(self.screen, RED, center, end, 4)
        pygame.draw.circle(self.screen, RED, end, 5)

    def panel(self, x: int, y: int, w: int, h: int) -> None:
        pygame.draw.rect(self.screen, PANEL, (x, y, w, h), border_radius=8)
        pygame.draw.rect(self.screen, LINE, (x, y, w, h), 1, border_radius=8)

    def row(self, label: str, value: str, x: int, y: int) -> None:
        self.text(label, x, y, self.small_font, MUTED)
        self.text(value, x + 76, y, self.small_font, TEXT)

    def circular_mean_deg(self, values: list[float]) -> float:
        if not values:
            return 0.0
        sin_sum = sum(math.sin(math.radians(value)) for value in values)
        cos_sum = sum(math.cos(math.radians(value)) for value in values)
        return (math.degrees(math.atan2(sin_sum, cos_sum)) + 360.0) % 360.0

    def gyro_yaw_rate_dps(
        self,
        gyro: tuple[float, float, float],
        accel: tuple[float, float, float],
    ) -> float:
        ax, ay, az = accel
        norm = math.sqrt(ax * ax + ay * ay + az * az)
        if norm < 0.2:
            return gyro[2]
        gx, gy, gz = gyro
        return gx * (ax / norm) + gy * (ay / norm) + gz * (az / norm)

    def tilt_compensated_heading(
        self,
        mx: float,
        my: float,
        mz: float,
        accel: tuple[float, float, float],
    ) -> float:
        ax, ay, az = accel
        norm = math.sqrt(ax * ax + ay * ay + az * az)
        if norm < 0.2:
            return (math.degrees(math.atan2(my, mx)) + 360.0) % 360.0
        ax, ay, az = ax / norm, ay / norm, az / norm
        mag_dot = mx * ax + my * ay + mz * az
        bx = mx - mag_dot * ax
        by = my - mag_dot * ay
        bz = mz - mag_dot * az
        b_norm = math.sqrt(bx * bx + by * by + bz * bz)
        rx, ry, rz = 1.0, 0.0, 0.0
        ref_dot = rx * ax + ry * ay + rz * az
        rx, ry, rz = rx - ref_dot * ax, ry - ref_dot * ay, rz - ref_dot * az
        r_norm = math.sqrt(rx * rx + ry * ry + rz * rz)
        if b_norm < 0.001 or r_norm < 0.001:
            return (math.degrees(math.atan2(my, mx)) + 360.0) % 360.0
        bx, by, bz = bx / b_norm, by / b_norm, bz / b_norm
        rx, ry, rz = rx / r_norm, ry / r_norm, rz / r_norm
        cross_x = ry * bz - rz * by
        cross_y = rz * bx - rx * bz
        cross_z = rx * by - ry * bx
        signed = math.degrees(math.atan2(cross_x * ax + cross_y * ay + cross_z * az, rx * bx + ry * by + rz * bz))
        return (signed + 360.0) % 360.0

    def heading_stability_deg(self) -> float:
        if len(self.mag_heading_samples) < 2:
            return 999.0
        sample_count = max(8, int(self.mag_stable_window * self.target_fps))
        values = self.mag_heading_samples[-sample_count:]
        sin_mean = sum(math.sin(math.radians(value)) for value in values) / len(values)
        cos_mean = sum(math.cos(math.radians(value)) for value in values) / len(values)
        resultant = clamp(math.sqrt(sin_mean * sin_mean + cos_mean * cos_mean), 0.000001, 1.0)
        return math.degrees(math.sqrt(-2.0 * math.log(resultant)))

    def stable_confirm_elapsed(self) -> float:
        if self.mag_stable_since is None:
            return 0.0
        return max(0.0, time.monotonic() - self.mag_stable_since)

    def update_heading_stability(self) -> bool:
        elapsed = time.monotonic() - self.mag_cal_started
        min_samples = max(8, int(self.mag_stable_min_duration * self.target_fps))
        stable_now = (
            elapsed >= self.mag_stable_min_duration
            and len(self.mag_heading_samples) >= min_samples
            and self.heading_stability_deg() <= self.mag_stable_threshold_deg
        )
        now = time.monotonic()
        if stable_now:
            if self.mag_stable_since is None:
                self.mag_stable_since = now
            return now - self.mag_stable_since >= self.mag_stable_confirm_duration
        self.mag_stable_since = None
        return False

    def heading_wait_timed_out(self) -> bool:
        elapsed = time.monotonic() - self.mag_cal_started
        min_samples = max(8, int(self.mag_stable_window * self.target_fps))
        return elapsed >= self.mag_stable_max_duration and len(self.mag_heading_samples) >= min_samples

    def text(self, value: str, x: int, y: int, font: pygame.font.Font, color: tuple[int, int, int]) -> None:
        self.screen.blit(font.render(value, True, color), (x, y))

    def center(self, value: str, pos: tuple[float, float], font: pygame.font.Font, color: tuple[int, int, int]) -> None:
        surface = font.render(value, True, color)
        self.screen.blit(surface, surface.get_rect(center=pos))

    def fit(self, value: str, chars: int) -> str:
        return value if len(value) <= chars else value[: chars - 3] + "..."


if __name__ == "__main__":
    try:
        raise SystemExit(GyroTestApp().run())
    except Exception as exc:
        pygame.quit()
        print(f"whisplay-gyro-test: {exc}", file=sys.stderr)
        raise
