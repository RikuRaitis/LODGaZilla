"""Real Windows mouse input with a log, for the demo video's recording.

The app receives genuine input (``SendInput``), so it reacts exactly as for a user, and
every move, press, release and wheel step is logged with its ``time.perf_counter()``
time and physical screen position. The editing step draws the cursor and click effects
from this log. Runs in the driver's worker thread, never the GUI thread: a Qt drag runs
a modal loop that the input must keep feeding.
"""
from __future__ import annotations

import ctypes
import math
import random
import time
from ctypes import wintypes

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
VIRTUAL_KEYS = {"ctrl": 0x11, "shift": 0x10, "enter": 0x0D, "escape": 0x1B, "a": 0x41, "c": 0x43, "s": 0x53}
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x8000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_WHEEL = 0x0800
SM_CXSCREEN, SM_CYSCREEN = 0, 1
STEP_SECONDS = 1 / 120


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32 = ctypes.windll.user32


class Mouse:
    """Moves and clicks the real cursor, in physical screen pixels, and logs each event."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        self.x, self.y = float(point.x), float(point.y)
        self.width = user32.GetSystemMetrics(SM_CXSCREEN)
        self.height = user32.GetSystemMetrics(SM_CYSCREEN)

    def _send(self, flags: int, data: int = 0) -> None:
        absolute_x = round(self.x * 65535 / max(1, self.width - 1))
        absolute_y = round(self.y * 65535 / max(1, self.height - 1))
        event = INPUT(type=INPUT_MOUSE)
        event.mi = MOUSEINPUT(absolute_x, absolute_y, ctypes.c_ulong(data & 0xFFFFFFFF).value,
                              flags | MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, 0)
        user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))

    def _log(self, kind: str, **values) -> None:  # type: ignore[no-untyped-def]
        self.events.append({"t": time.perf_counter(), "kind": kind, "x": round(self.x, 1),
                            "y": round(self.y, 1), **values})

    def jump(self, x: float, y: float) -> None:
        """Place the cursor without a visible movement (between scenes)."""
        self.x, self.y = float(x), float(y)
        self._send(0)
        self._log("jump")

    def move(self, x: float, y: float, seconds: float | None = None) -> None:
        """Glide to (x, y) on a slightly curved path, easing in and out, as a hand does."""
        start_x, start_y = self.x, self.y
        distance = math.hypot(x - start_x, y - start_y)
        if distance < 1:
            return
        if seconds is None:
            seconds = min(1.1, 0.3 + distance / 1800)
        # A bend of a few percent of the distance, to one side.
        bend = random.uniform(0.04, 0.09) * distance * random.choice((-1, 1))
        normal_x, normal_y = -(y - start_y) / distance, (x - start_x) / distance
        control_x = (start_x + x) / 2 + normal_x * bend
        control_y = (start_y + y) / 2 + normal_y * bend
        steps = max(2, round(seconds / STEP_SECONDS))
        began = time.perf_counter()
        for step in range(1, steps + 1):
            linear = step / steps
            eased = linear * linear * (3 - 2 * linear)
            inverse = 1 - eased
            self.x = inverse * inverse * start_x + 2 * inverse * eased * control_x + eased * eased * x
            self.y = inverse * inverse * start_y + 2 * inverse * eased * control_y + eased * eased * y
            self._send(0)
            self._log("move")
            delay = began + step * STEP_SECONDS - time.perf_counter()
            if delay > 0:
                time.sleep(delay)

    def press(self, button: str = "left") -> None:
        self._send(MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN)
        self._log("down", button=button)

    def release(self, button: str = "left") -> None:
        self._send(MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP)
        self._log("up", button=button)

    def click(self, button: str = "left") -> None:
        self.press(button)
        time.sleep(0.08)
        self.release(button)

    def double_click(self, button: str = "left") -> None:
        self.click(button)
        time.sleep(0.09)
        self.click(button)

    def drag(self, x: float, y: float, seconds: float | None = None, hold: float = 0.35,
             button: str = "left", nudge: bool = True) -> None:
        """Press, start the drag with a small move, glide to (x, y), wait there and release.

        ``nudge`` first moves a few pixels slowly, past Qt's drag distance, as a file or
        series drag needs; a pan or rotate inside a view doesn't."""
        self.press(button)
        time.sleep(0.15)
        if nudge:
            start_x, start_y = self.x, self.y
            for step in range(1, 7):
                self.x, self.y = start_x + step * 3, start_y + step
                self._send(0)
                self._log("move")
                time.sleep(0.03)
        self.move(x, y, seconds)
        time.sleep(hold)
        self.release(button)

    def key(self, name: str, down: bool) -> None:
        """Press or release a key by name; logged so the video can show held modifiers."""
        event = INPUT(type=INPUT_KEYBOARD)
        event.ki = KEYBDINPUT(VIRTUAL_KEYS[name], 0, 0 if down else KEYEVENTF_KEYUP, 0, 0)
        user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
        self._log("key_down" if down else "key_up", key=name)

    def tap(self, *names: str) -> None:
        """Press keys together and release them, such as ``tap("ctrl", "s")``."""
        for name in names:
            self.key(name, True)
            time.sleep(0.03)
        for name in reversed(names):
            self.key(name, False)
            time.sleep(0.03)

    def type(self, text: str, seconds_per_character: float = 0.06) -> None:
        """Type text as Unicode key events, as a quick typist would."""
        for character in text:
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                event = INPUT(type=INPUT_KEYBOARD)
                event.ki = KEYBDINPUT(0, ord(character), flags, 0, 0)
                user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
            self._log("type", text=character)
            time.sleep(seconds_per_character)

    def wheel(self, notches: int, seconds: float) -> None:
        """Turn the wheel ``notches`` steps (positive away from the user) over ``seconds``."""
        count = abs(notches)
        for _ in range(count):
            self._send(MOUSEEVENTF_WHEEL, 120 if notches > 0 else -120)
            self._log("wheel", delta=1 if notches > 0 else -1)
            time.sleep(seconds / max(1, count))
