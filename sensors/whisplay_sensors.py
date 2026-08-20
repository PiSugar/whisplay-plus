#!/usr/bin/env python3
from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass


BUS_ID = 1
QMI8658B_ADDR = 0x6B
QMC6309_ADDR = 0x7C


def i16le(data: list[int] | bytes) -> tuple[int, ...]:
    return struct.unpack("<" + "h" * (len(data) // 2), bytes(data))


@dataclass(frozen=True)
class ImuReading:
    ax_g: float
    ay_g: float
    az_g: float
    gx_dps: float
    gy_dps: float
    gz_dps: float
    temp_c: float


@dataclass(frozen=True)
class MagReading:
    x: int
    y: int
    z: int
    heading_deg: float
    status: int
    strength: float


class QMI8658B:
    def __init__(self, bus, addr: int = QMI8658B_ADDR):
        self.bus = bus
        self.addr = addr
        self.who_am_i = self.bus.read_byte_data(addr, 0x00)
        self.revision = self.bus.read_byte_data(addr, 0x01)
        if self.who_am_i != 0x05:
            raise RuntimeError(f"QMI8658B WHO_AM_I=0x{self.who_am_i:02x}, expected 0x05")
        self.bus.write_byte_data(addr, 0x02, 0x60)
        self.bus.write_byte_data(addr, 0x03, 0x25)
        self.bus.write_byte_data(addr, 0x04, 0x45)
        self.bus.write_byte_data(addr, 0x08, 0x03)
        time.sleep(0.2)

    def read(self) -> ImuReading:
        ax, ay, az, gx, gy, gz = i16le(self.bus.read_i2c_block_data(self.addr, 0x35, 12))
        try:
            temp_raw = i16le(self.bus.read_i2c_block_data(self.addr, 0x33, 2))[0]
            temp_c = temp_raw / 256.0
        except Exception:
            temp_c = float("nan")
        return ImuReading(
            ax_g=ax / 4096.0,
            ay_g=ay / 4096.0,
            az_g=az / 4096.0,
            gx_dps=gx / 64.0,
            gy_dps=gy / 64.0,
            gz_dps=gz / 64.0,
            temp_c=temp_c,
        )


class QMC6309:
    def __init__(self, bus, addr: int = QMC6309_ADDR):
        self.bus = bus
        self.addr = addr
        self.chip_id = self.bus.read_byte_data(addr, 0x00)
        if self.chip_id != 0x90:
            raise RuntimeError(f"QMC6309 CHIP_ID=0x{self.chip_id:02x}, expected 0x90")
        self.bus.write_byte_data(addr, 0x0A, 0xC9)
        self.bus.write_byte_data(addr, 0x0B, 0x48)
        time.sleep(0.05)

    def read(self, offsets: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> MagReading:
        status = self.bus.read_byte_data(self.addr, 0x09)
        x, y, z = i16le(self.bus.read_i2c_block_data(self.addr, 0x01, 6))
        cx = x - offsets[0]
        cy = y - offsets[1]
        cz = z - offsets[2]
        heading = (math.degrees(math.atan2(cy, cx)) + 360.0) % 360.0
        strength = math.sqrt(cx * cx + cy * cy + cz * cz)
        return MagReading(int(cx), int(cy), int(cz), heading, status, strength)


def main() -> int:
    try:
        from smbus2 import SMBus
    except ImportError:
        from smbus import SMBus  # type: ignore

    bus = SMBus(BUS_ID)
    qmi = QMI8658B(bus)
    qmc = QMC6309(bus)
    print(f"QMI8658B addr=0x{QMI8658B_ADDR:02x} id=0x{qmi.who_am_i:02x} rev=0x{qmi.revision:02x}")
    print(f"QMC6309  addr=0x{QMC6309_ADDR:02x} id=0x{qmc.chip_id:02x}")
    for index in range(12):
        imu = qmi.read()
        mag = qmc.read()
        acc_total = math.sqrt(imu.ax_g * imu.ax_g + imu.ay_g * imu.ay_g + imu.az_g * imu.az_g)
        print(
            f"{index:02d} acc={acc_total:.2f}g "
            f"gyro=({imu.gx_dps:+.1f},{imu.gy_dps:+.1f},{imu.gz_dps:+.1f})dps "
            f"temp={imu.temp_c:.1f}C "
            f"mag=({mag.x:+d},{mag.y:+d},{mag.z:+d}) "
            f"heading={mag.heading_deg:05.1f} status=0x{mag.status:02x}"
        )
        time.sleep(0.2)
    bus.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
