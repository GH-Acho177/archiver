"""Small Win32 helpers for assigning PNG-derived application icons."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import sys
import time
from pathlib import Path


_ICON_HANDLES: list[int] = []


def find_process_window(title: str, timeout: float = 5.0) -> int:
    if sys.platform != "win32":
        return 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = ctypes.wintypes.HWND(0)
        process_id = os.getpid()

        @ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
        )
        def callback(hwnd, _lparam):
            nonlocal found
            pid = ctypes.wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value != process_id or not ctypes.windll.user32.IsWindowVisible(hwnd):
                return True
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value == title:
                found = hwnd
                return False
            return True

        ctypes.windll.user32.EnumWindows(callback, 0)
        if found:
            return int(found)
        time.sleep(0.1)
    return 0


def apply_window_icon(hwnd: int, icon_path: Path) -> bool:
    if sys.platform != "win32" or not hwnd or not icon_path.is_file():
        return False
    load_image = ctypes.windll.user32.LoadImageW
    load_image.restype = ctypes.c_void_p
    handle = load_image(None, str(icon_path.resolve()), 1, 0, 0, 0x10)
    if not handle:
        return False
    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, handle)  # ICON_BIG
    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, handle)  # ICON_SMALL
    _ICON_HANDLES.append(int(handle))
    return True
