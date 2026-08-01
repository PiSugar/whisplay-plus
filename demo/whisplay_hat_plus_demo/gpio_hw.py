from __future__ import annotations

import time


BOARD_TO_BCM = {
    7: 4,
    11: 17,
    15: 22,
    16: 23,
    18: 24,
    22: 25,
}


class GpioHardware:
    RED_PIN = 22
    GREEN_PIN = 18
    BLUE_PIN = 16
    BUTTON_PIN = 11

    def __init__(self):
        import gpiod

        self.gpiod = gpiod
        self.v2 = hasattr(gpiod, "LineSettings")
        self.chip_path = "/dev/gpiochip4"
        try:
            self.chip = gpiod.Chip(self.chip_path)
        except Exception:
            self.chip_path = "/dev/gpiochip0"
            self.chip = gpiod.Chip(self.chip_path)
        self.lines = {}
        for pin in (self.RED_PIN, self.GREEN_PIN, self.BLUE_PIN):
            self.lines[pin] = self._request_output(BOARD_TO_BCM[pin])
        self.button = self._request_input(BOARD_TO_BCM[self.BUTTON_PIN])
        self.button_pressed = False
        self.last_button_change = 0.0
        self.last_rgb = (0, 0, 0)
        self.set_rgb(0, 0, 0)

    def _request_output(self, offset: int):
        if self.v2:
            from gpiod.line import Direction, Value

            settings = self.gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE)
            return self.chip.request_lines(consumer="whisplay-hat-plus-demo", config={offset: settings}), offset
        line = self.chip.get_line(offset)
        line.request(consumer="whisplay-hat-plus-demo", type=self.gpiod.LINE_REQ_DIR_OUT, default_val=1)
        return line, None

    def _request_input(self, offset: int):
        if self.v2:
            from gpiod.line import Bias, Direction

            settings = self.gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.DISABLED)
            return self.chip.request_lines(consumer="whisplay-hat-plus-button", config={offset: settings}), offset
        line = self.chip.get_line(offset)
        try:
            line.request(
                consumer="whisplay-hat-plus-button",
                type=self.gpiod.LINE_REQ_DIR_IN,
                flags=self.gpiod.LINE_REQ_FLAG_BIAS_DISABLE,
            )
        except Exception:
            line.request(consumer="whisplay-hat-plus-button", type=self.gpiod.LINE_REQ_DIR_IN)
        return line, None

    def _set(self, handle, value: int) -> None:
        obj, offset = handle
        if offset is None:
            obj.set_value(value)
            return
        from gpiod.line import Value

        obj.set_value(offset, Value.ACTIVE if value else Value.INACTIVE)

    def _get(self, handle) -> int:
        obj, offset = handle
        if offset is None:
            return int(obj.get_value())
        from gpiod.line import Value

        return 1 if obj.get_value(offset) == Value.ACTIVE else 0

    def set_rgb(self, r: int, g: int, b: int) -> None:
        self.last_rgb = (r, g, b)
        self._set(self.lines[self.RED_PIN], 0 if r else 1)
        self._set(self.lines[self.GREEN_PIN], 0 if g else 1)
        self._set(self.lines[self.BLUE_PIN], 0 if b else 1)

    def poll_button(self) -> bool:
        pressed = bool(self._get(self.button))
        if pressed != self.button_pressed:
            self.button_pressed = pressed
            self.last_button_change = time.monotonic()
        return pressed

    def close(self) -> None:
        self.set_rgb(0, 0, 0)
        for handle in [*self.lines.values(), self.button]:
            obj, _ = handle
            release = getattr(obj, "release", None)
            if release:
                release()
