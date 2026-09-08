from __future__ import annotations

import subprocess
import time


BOARD_TO_BCM = {
    7: 4,
    11: 17,
    15: 22,
    16: 23,
    18: 24,
    22: 25,
}


class _WeakSinkLine:
    """Use input pull-down as the LOW state for a GPIO that cannot sink."""

    def __init__(self, request, offset: int):
        self.request = request
        self.offset = offset

    def set_value(self, value: int) -> None:
        import gpiod
        from gpiod.line import Bias, Direction, Value

        if value:
            settings = gpiod.LineSettings(
                direction=Direction.OUTPUT, output_value=Value.ACTIVE
            )
        else:
            settings = gpiod.LineSettings(
                direction=Direction.INPUT, bias=Bias.PULL_DOWN
            )
        self.request.reconfigure_lines({self.offset: settings})

    def get_value(self) -> int:
        from gpiod.line import Value

        return 1 if self.request.get_value(self.offset) == Value.ACTIVE else 0

    def release(self) -> None:
        self.set_value(1)
        self.request.release()


class _PinctrlLine:
    """Last-resort output for an RP1 line that misbehaves while requested."""

    def __init__(self, offset: int):
        self.offset = offset
        self.value = 1
        self.set_value(1)

    def set_value(self, value: int) -> None:
        self.value = int(bool(value))
        subprocess.run(
            ["pinctrl", "set", str(self.offset), "op", "dh" if self.value else "dl"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def get_value(self) -> int:
        return self.value

    def release(self) -> None:
        self.set_value(1)


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
        self.line_modes = {}
        for pin in (self.RED_PIN, self.GREEN_PIN, self.BLUE_PIN):
            offset = BOARD_TO_BCM[pin]
            handle = self._request_output(offset)
            handle, mode = self._apply_output_fallback(handle, offset)
            self.lines[pin] = handle
            self.line_modes[pin] = mode
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

    def _apply_output_fallback(self, handle, offset: int):
        """Mirror Whisplay's damaged-GPIO probe and weak-sink workaround.

        Some RP1 GPIOs accept an output-low setting but still read HIGH. Try
        input pull-down for those lines. If even that cannot pull the pad low,
        release the character-device request and use Raspberry Pi's pinctrl
        command, which remains functional on the affected boards.
        """
        if not self.v2:
            return handle, "gpiod"

        self._set(handle, 1)
        time.sleep(0.02)
        self._set(handle, 0)
        time.sleep(0.02)
        if self._get(handle) == 0:
            self._set(handle, 1)
            return handle, "gpiod"

        request, request_offset = handle
        import gpiod
        from gpiod.line import Bias, Direction, Value

        request.reconfigure_lines({
            request_offset: gpiod.LineSettings(
                direction=Direction.INPUT, bias=Bias.PULL_DOWN
            )
        })
        time.sleep(0.02)
        if self._get(handle) == 0:
            weak = _WeakSinkLine(request, request_offset)
            weak.set_value(1)
            return (weak, None), "pull-down"

        request.reconfigure_lines({
            request_offset: gpiod.LineSettings(
                direction=Direction.OUTPUT, output_value=Value.ACTIVE
            )
        })
        request.release()
        pinctrl = _PinctrlLine(offset)
        return (pinctrl, None), "pinctrl"

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
