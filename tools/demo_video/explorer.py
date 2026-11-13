"""A Windows Explorer window showing one folder through a temporary drive letter, for the
Magic drop scene: the address bar shows only the drive, never the user's folders."""
from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT]
HWND_TOPMOST = -1
SWP_SHOWWINDOW = 0x0040
WM_CLOSE = 0x0010
SW_RESTORE = 9
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _explorer_windows() -> list[int]:
    found: list[int] = []

    def visit(hwnd, _param):  # type: ignore[no-untyped-def]
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value == "CabinetWClass" and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(visit), 0)
    return found


def free_drive_letter() -> str:
    used = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in "WVUTSRQPONML":
        if not used & (1 << (ord(letter) - ord("A"))):
            return letter
    raise RuntimeError("No free drive letter")


class ExplorerWindow:
    """Open ``folder`` as a drive in Explorer, at a physical screen rectangle, above all windows."""

    def __init__(self, folder: Path) -> None:
        self.letter = free_drive_letter()
        subprocess.run(["subst", f"{self.letter}:", str(folder)], check=True)
        before = set(_explorer_windows())
        subprocess.Popen(["explorer.exe", f"{self.letter}:\\"])
        deadline = time.monotonic() + 15
        self.hwnd = 0
        while time.monotonic() < deadline and not self.hwnd:
            time.sleep(0.2)
            new = [hwnd for hwnd in _explorer_windows() if hwnd not in before]
            self.hwnd = new[0] if new else 0
        if not self.hwnd:
            self.close()
            raise RuntimeError("Explorer did not open")

    def place(self, x: int, y: int, width: int, height: int) -> None:
        """Explorer restores its own size once it has opened, so place it until it stays."""
        for _ in range(10):
            user32.ShowWindow(self.hwnd, SW_RESTORE)
            if not user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, width, height, SWP_SHOWWINDOW):
                raise ctypes.WinError(ctypes.get_last_error())
            user32.SetForegroundWindow(self.hwnd)
            time.sleep(0.6)
            if self.rect() == (x, y, x + width, y + height):
                return
        raise RuntimeError(f"Explorer stays at {self.rect()}")

    def rect(self) -> tuple[int, int, int, int]:
        box = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(box))
        return box.left, box.top, box.right, box.bottom

    def close_window(self) -> None:
        """Close the window but keep the drive: files dropped from it may be read later."""
        if getattr(self, "hwnd", 0):
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self.hwnd = 0
            time.sleep(0.5)

    def close(self) -> None:
        self.close_window()
        subprocess.run(["subst", f"{self.letter}:", "/d"], check=False)
