"""Launch Archiver Viewer as its own pywebview desktop application."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import ctypes
from pathlib import Path

import uvicorn
import webview
import pystray
from PIL import Image, ImageDraw


HOST = "127.0.0.1"


def _choose_port(preferred: int = 5174) -> int:
    """Use the familiar port when possible, otherwise ask Windows for one."""
    for candidate in (preferred, 0):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind((HOST, candidate))
            return int(probe.getsockname()[1])
        except OSError:
            continue
        finally:
            probe.close()
    raise RuntimeError("Windows did not provide an available local port")


PORT = _choose_port()
URL = f"http://{HOST}:{PORT}"
_window: "webview.Window | None" = None
_tray: "pystray.Icon | None" = None
_server: "uvicorn.Server | None" = None
_server_thread: "threading.Thread | None" = None
_fullscreen = False
_quitting = False
_hiding = False
_hwnd = 0


def _asset_path(name: str) -> Path:
    """Return an asset path in both source and PyInstaller builds."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


_VIEWER_ICON = _asset_path("Archiver_Viewer.ico")

# Prevent the live pywebview window from intermittently inheriting Python's
# taskbar identity before its HWND receives WM_SETICON.
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Acho.Archiver.Viewer"
        )
    except Exception:
        pass

# Keep Viewer browser state separate so Archiver and Viewer can run together.
if getattr(sys, "frozen", False):
    WEBVIEW_DATA = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Archiver Viewer" / ".webview2"
    )
else:
    WEBVIEW_DATA = Path(__file__).resolve().parent / ".webview2-viewer"
os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", str(WEBVIEW_DATA))


def _start_server() -> None:
    global _server
    _server = uvicorn.Server(uvicorn.Config(
        "viewer.app:app", host=HOST, port=PORT,
        reload=False, log_level="error",
    ))
    _server.run()


def _wait_for_server(timeout: float = 90.0) -> bool:
    """Wait for archive indexing and the Viewer API to become available."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{URL}/api/stats", timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


class ViewerApi:
    def toggle_fullscreen(self) -> bool:
        """Toggle the native window across the entire monitor."""
        global _fullscreen
        if _window is None:
            return False
        _window.toggle_fullscreen()
        _fullscreen = not _fullscreen
        return _fullscreen

    def set_theme(self, theme: str) -> bool:
        """Match the native Windows title bar and border to the web UI."""
        if sys.platform != "win32" or not _hwnd:
            return False
        dark = theme == "dark"
        colors = {
            34: 0x002B2B2B if dark else 0x00E8E8E8,  # border
            35: 0x002B2B2B if dark else 0x00E8E8E8,  # caption
            36: 0x00E1E1E1 if dark else 0x001A1A1A,  # caption text
        }
        ok = True
        for attribute, color in colors.items():
            value = ctypes.c_uint(color)
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                _hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
            ok = ok and result == 0
        immersive_dark = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            _hwnd, 20, ctypes.byref(immersive_dark), ctypes.sizeof(immersive_dark)
        )
        return ok

    def open_database_folder(self) -> bool:
        folder = Path.cwd() / "config"
        if not folder.is_dir():
            return False
        if sys.platform == "win32":
            os.startfile(str(folder))
            return True
        return False


def _tray_image() -> Image.Image:
    icon = _asset_path("Archiver_Viewer.png")
    if icon.is_file():
        return Image.open(icon).convert("RGBA")
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, 60, 60), fill=(74, 155, 202, 255))
    return image


def _show_viewer(*_args) -> None:
    if _window is not None:
        _window.show()
        _window.restore()


def _quit_viewer(icon=None, *_args) -> None:
    global _quitting
    _quitting = True
    if _server is not None:
        _server.should_exit = True
    if _window is not None:
        _window.destroy()
    if icon is not None:
        icon.stop()


def _pause_and_hide() -> None:
    global _hiding
    try:
        if _window is not None:
            try:
                _window.evaluate_js(
                    "window.viewerPauseAll && window.viewerPauseAll()"
                )
            except Exception:
                pass
            _window.hide()
    finally:
        _hiding = False


def _hide_on_close() -> bool:
    """Cancel native close and keep Viewer available in the system tray."""
    global _hiding
    if _quitting:
        return True
    if not _hiding:
        _hiding = True
        # The closing event runs on WebView2's GUI thread. Calling hide or
        # evaluate_js synchronously from it can deadlock native teardown.
        threading.Thread(target=_pause_and_hide, daemon=True).start()
    return False


def _on_viewer_started() -> None:
    global _hwnd
    from helpers.window_icon import apply_window_icon, find_process_window
    _hwnd = find_process_window("Archiver Viewer")
    apply_window_icon(_hwnd, _VIEWER_ICON)
    if sys.platform == "win32" and _hwnd:
        # Keep the taskbar/tray icon, but suppress the small icon at the
        # upper-left of the native title bar.
        get_style = ctypes.windll.user32.GetWindowLongW
        set_style = ctypes.windll.user32.SetWindowLongW
        ex_style = get_style(_hwnd, -20)  # GWL_EXSTYLE
        set_style(_hwnd, -20, ex_style | 0x00000001)  # WS_EX_DLGMODALFRAME
        ctypes.windll.user32.SetWindowPos(
            _hwnd, 0, 0, 0, 0, 0,
            0x0001 | 0x0002 | 0x0004 | 0x0020,  # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
        )


if __name__ == "__main__":
    _server_thread = threading.Thread(target=_start_server, daemon=True)
    _server_thread.start()
    print("[Viewer] Scanning the archive. Large libraries may take a moment...")
    if not _wait_for_server():
        sys.exit(
            f"Viewer failed to start within 90 seconds on local port {PORT}."
        )

    print(f"[Viewer] Ready at {URL}")
    _window = webview.create_window(
        "Archiver Viewer", URL,
        width=1100, height=760, min_size=(760, 540),
        background_color="#2b2b2b",
        js_api=ViewerApi(),
    )
    _window.events.closing += _hide_on_close
    _tray = pystray.Icon(
        "Archiver Viewer", _tray_image(), "Archiver Viewer",
        pystray.Menu(
            pystray.MenuItem("Show Viewer", _show_viewer, default=True),
            pystray.MenuItem("Quit", _quit_viewer),
        ),
    )
    threading.Thread(target=_tray.run, daemon=True).start()
    webview.start(
        _on_viewer_started,
        private_mode=False,
        storage_path=str(WEBVIEW_DATA),
        icon=str(_VIEWER_ICON),
    )
    if _tray is not None:
        _tray.stop()
    if _server is not None:
        _server.should_exit = True
    if _server_thread is not None:
        _server_thread.join(timeout=5)
