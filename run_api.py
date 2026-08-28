import sys
import os
import threading
import time
import ctypes
import ctypes.wintypes
from pathlib import Path

import uvicorn
import webview
import pystray
from PIL import Image, ImageDraw

PORT = 5173
URL  = f"http://127.0.0.1:{PORT}"

# Give WebView2 a writable data folder. When frozen (installed), _internal\ is
# read-only, so use %LOCALAPPDATA%\Archiver instead.
if getattr(sys, "frozen", False):
    _wv2_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Archiver" / ".webview2"
else:
    _wv2_dir = Path(__file__).parent / ".webview2"
os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", str(_wv2_dir))

_window:            "webview.Window | None" = None
_tray:              "pystray.Icon | None"   = None
_hwnd:              int  = 0
_subclass_installed: bool = False
_fullscreen:         bool = False
_custom_maximized:   bool = False
_restore_bounds:     tuple[int, int, int, int] | None = None


def _asset_path(name: str) -> Path:
    """Return an asset path in both source and PyInstaller builds."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


_APP_ICON = _asset_path("Archiver.ico")

# Give the live Python/pywebview process its own taskbar identity instead of
# inheriting Python's default application identity and icon.
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Acho.Archiver"
        )
    except Exception:
        pass


# ── Win32 structures ──────────────────────────────────────────────────────────

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

class _MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved",     _POINT),
        ("ptMaxSize",      _POINT),
        ("ptMaxPosition",  _POINT),
        ("ptMinTrackSize", _POINT),
        ("ptMaxTrackSize", _POINT),
    ]

class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length",           ctypes.c_uint),
        ("flags",            ctypes.c_uint),
        ("showCmd",          ctypes.c_uint),
        ("ptMinPosition",    _POINT),
        ("ptMaxPosition",    _POINT),
        ("rcNormalPosition", ctypes.wintypes.RECT),
    ]

class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("left",   ctypes.c_int),
        ("right",  ctypes.c_int),
        ("top",    ctypes.c_int),
        ("bottom", ctypes.c_int),
    ]

class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize",    ctypes.wintypes.DWORD),
        ("rcMonitor", ctypes.wintypes.RECT),
        ("rcWork",    ctypes.wintypes.RECT),
        ("dwFlags",   ctypes.wintypes.DWORD),
    ]


# ── Frameless window helpers ──────────────────────────────────────────────────

_SUBCLASSPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong,
    ctypes.wintypes.HWND, ctypes.c_uint,
    ctypes.c_ulonglong,   ctypes.c_longlong,
    ctypes.c_ulonglong,   ctypes.c_ulonglong,
)
_subclass_cb: "_SUBCLASSPROC | None" = None  # kept alive to prevent GC

_BORDER      = 6   # resize grip width in pixels
_TITLE_H     = 32  # h-8 Tailwind title bar height (px)
_WINCTRLS_W  = 120 # 3 × w-10 window-control buttons (px)


def _is_maximized() -> bool:
    if _custom_maximized:
        return True
    if not _hwnd:
        return False
    wp = _WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
    ctypes.windll.user32.GetWindowPlacement(_hwnd, ctypes.byref(wp))
    return wp.showCmd == 3  # SW_MAXIMIZE


def _monitor_work_area(hwnd: int) -> tuple[int, int, int, int] | None:
    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    work = info.rcWork
    return work.left, work.top, work.right - work.left, work.bottom - work.top


def _install_subclass(hwnd: int) -> None:
    global _subclass_cb

    def _proc(hwnd, msg, wparam, lparam, uid, ref):
        if msg == 0x0083 and wparam:  # WM_NCCALCSIZE — remove caption strip
            wp = _WINDOWPLACEMENT()
            wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
            ctypes.windll.user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
            if wp.showCmd != 3:  # not maximized — client area = full window rect
                return 0

        if msg == 0x0084:  # WM_NCHITTEST — resize borders
            x = lparam & 0xFFFF
            y = (lparam >> 16) & 0xFFFF
            if x >= 0x8000: x -= 0x10000
            if y >= 0x8000: y -= 0x10000
            rc = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
            on_l = x - rc.left   < _BORDER
            on_r = rc.right - x  < _BORDER
            on_t = y - rc.top    < _BORDER
            on_b = rc.bottom - y < _BORDER
            if on_t and on_l: return 13  # HTTOPLEFT
            if on_t and on_r: return 14  # HTTOPRIGHT
            if on_b and on_l: return 16  # HTBOTTOMLEFT
            if on_b and on_r: return 17  # HTBOTTOMRIGHT
            if on_l: return 10  # HTLEFT
            if on_r: return 11  # HTRIGHT
            if on_t: return 12  # HTTOP
            if on_b: return 15  # HTBOTTOM
            return 1  # HTCLIENT — JS handles title-bar drag via start_drag()

        if msg == 0x0024:  # WM_GETMINMAXINFO — respect taskbar on maximize
            user32 = ctypes.windll.user32
            user32.MonitorFromWindow.restype = ctypes.c_void_p
            monitor = user32.MonitorFromWindow(hwnd, 2)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                work, bounds = info.rcWork, info.rcMonitor
                mmi = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO))
                mmi.contents.ptMaxPosition.x = work.left - bounds.left
                mmi.contents.ptMaxPosition.y = work.top - bounds.top
                mmi.contents.ptMaxSize.x = work.right - work.left
                mmi.contents.ptMaxSize.y = work.bottom - work.top
                mmi.contents.ptMaxTrackSize.x = mmi.contents.ptMaxSize.x
                mmi.contents.ptMaxTrackSize.y = mmi.contents.ptMaxSize.y
                return 0

        return ctypes.windll.comctl32.DefSubclassProc(hwnd, msg, wparam, lparam)

    _subclass_cb = _SUBCLASSPROC(_proc)
    ctypes.windll.comctl32.SetWindowSubclass(hwnd, _subclass_cb, 1, 0)


def _find_hwnd() -> int:
    """Locate the pywebview window by enumerating windows in this process."""
    pid  = ctypes.windll.kernel32.GetCurrentProcessId()
    found = ctypes.wintypes.HWND(0)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(hwnd, _):
        p = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid or not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        rc = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rc))
        if rc.right - rc.left > 200:          # skip tiny helper / tray windows
            found.value = hwnd
            return False
        return True

    ctypes.windll.user32.EnumWindows(_cb, 0)
    return found.value or ctypes.windll.user32.FindWindowW(None, "Archiver")


def _setup_frameless_window(hwnd: int) -> None:
    _install_subclass(hwnd)


def _apply_thick_frame() -> None:
    """Called on the UI thread (loaded event) — installs subclass then applies
    WS_THICKFRAME so DWM composites the frameless WebView2 window correctly."""
    global _hwnd, _subclass_installed
    if not _hwnd:
        _hwnd = _find_hwnd()
    if not _hwnd:
        return

    # SetWindowSubclass has thread affinity — must run on the window's UI thread.
    # _on_started fires on a background thread so we install the subclass here instead.
    if not _subclass_installed:
        _install_subclass(_hwnd)
        _subclass_installed = True

    GWL_STYLE     = -16
    WS_THICKFRAME = 0x00040000
    SWP_FLAGS     = 0x0027  # NOMOVE | NOSIZE | NOZORDER | FRAMECHANGED
    style = ctypes.windll.user32.GetWindowLongW(_hwnd, GWL_STYLE)
    if style & WS_THICKFRAME:
        return  # already applied (loaded fires per-navigation on some pywebview versions)
    ctypes.windll.user32.SetWindowLongW(_hwnd, GWL_STYLE, style | WS_THICKFRAME)
    ctypes.windll.user32.SetWindowPos(_hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
    margins = _MARGINS(-1, -1, -1, -1)
    ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(_hwnd, ctypes.byref(margins))
    # Windows 11: paint DWM caption the same colour as the app panel so the
    # 1-px top-border artifact is invisible (DWMWA_COLOR_NONE leaves it white).
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        _hwnd, 35,  # DWMWA_CAPTION_COLOR  (COLORREF = 0x00BBGGRR)
        ctypes.byref(ctypes.c_uint(0x00413F3C)),  # #3c3f41 — panel bg
        ctypes.sizeof(ctypes.c_uint),
    )


# ── JS ↔ Python bridge ────────────────────────────────────────────────────────

class JsApi:
    def start_drag(self) -> None:
        global _custom_maximized, _restore_bounds
        if _hwnd:
            # Match native Windows behavior: dragging a maximized window first
            # restores it beneath the pointer, then begins the caption drag.
            if _custom_maximized and _restore_bounds:
                cursor = _POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor))
                _, _, width, height = _restore_bounds
                x = cursor.x - width // 2
                y = cursor.y - _TITLE_H // 2
                ctypes.windll.user32.SetWindowPos(
                    _hwnd, None, x, y, width, height, 0x0024,
                )
                _custom_maximized = False
                _restore_bounds = None
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.PostMessageW(_hwnd, 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN, HTCAPTION

    def minimize_window(self) -> None:
        if _window:
            _window.minimize()

    def toggle_maximize(self) -> None:
        global _custom_maximized, _restore_bounds
        if _hwnd:
            user32 = ctypes.windll.user32
            if _custom_maximized:
                bounds = _restore_bounds
                _custom_maximized = False
                _restore_bounds = None
                if bounds:
                    user32.SetWindowPos(_hwnd, None, *bounds, 0x0024)
                return
            # Recover first if Windows/pywebview left the window in its native
            # maximized state, then apply explicit taskbar-safe work bounds.
            if _is_maximized():
                user32.ShowWindow(_hwnd, 9)  # SW_RESTORE
            current = ctypes.wintypes.RECT()
            if user32.GetWindowRect(_hwnd, ctypes.byref(current)):
                _restore_bounds = (
                    current.left, current.top,
                    current.right - current.left, current.bottom - current.top,
                )
            work = _monitor_work_area(_hwnd)
            if work:
                user32.SetWindowPos(_hwnd, None, *work, 0x0024)
                _custom_maximized = True
        elif _window:
            (_window.restore if _is_maximized() else _window.maximize)()

    def toggle_fullscreen(self) -> bool:
        """Toggle monitor fullscreen independently from maximize."""
        global _fullscreen
        if not _window:
            return False
        _window.toggle_fullscreen()
        _fullscreen = not _fullscreen
        return _fullscreen

    def close_window(self) -> None:
        if _window:
            try:
                # The Viewer runs in an iframe on the API origin. Notify it
                # before hiding the native window so tray mode is always quiet.
                _window.evaluate_js(
                    "document.querySelector('iframe[title=\"Archive Viewer\"]')"
                    "?.contentWindow?.postMessage({type:'archiver:pause-viewer'}, '*')"
                )
            except Exception:
                pass
            _window.hide()

    def is_maximized(self) -> bool:
        return _is_maximized()


# ── Server ────────────────────────────────────────────────────────────────────

def _start_server() -> None:
    uvicorn.run("src.api:app", host="127.0.0.1", port=PORT,
                reload=False, log_level="error")


def _wait_for_server(timeout: float = 90.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=1)
            return True
        except Exception:
            time.sleep(0.25)
    return False


# ── Tray ──────────────────────────────────────────────────────────────────────

def _make_icon() -> Image.Image:
    artwork = _asset_path("Archiver.png")
    if artwork.exists():
        return Image.open(artwork).convert("RGBA")
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([4, 4, 60, 60], fill=(29, 155, 240, 255))
    return img


def _show() -> None:
    if _window:
        _window.show()


def _quit(icon: "pystray.Icon") -> None:
    try:
        from viewer.app import flush_pending_deletions
        flush_pending_deletions()
    except Exception as exc:
        print(f"[Viewer] Could not finish pending deletions: {exc}")
    icon.stop()
    if _window:
        _window.destroy()


# ── Window started callback ───────────────────────────────────────────────────

def _on_started() -> None:
    global _hwnd
    if sys.platform != "win32" or not _window:
        return
    _hwnd = _find_hwnd()
    if not _hwnd:
        return
    from helpers.window_icon import apply_window_icon
    apply_window_icon(_hwnd, _APP_ICON)
    _setup_frameless_window(_hwnd)


# ── UI index preparation ──────────────────────────────────────────────────────

_UI_DIST = Path(__file__).resolve().parent / "ui" / "dist"


def _prepare_index() -> str:
    """Inject fetch/WebSocket interceptors into index.html so the React app's
    relative /api/* calls are forwarded to the FastAPI server on PORT, while
    pywebview's own HTTP server handles the static assets."""
    interceptor = (
        "<script>(function(){"
        f'var A="http://127.0.0.1:{PORT}",W="ws://127.0.0.1:{PORT}";'
        "var h=location.host;"
        "window.__apiBase=A;"
        "var _f=fetch.bind(window);"
        'window.fetch=function(u,o){if(typeof u==="string"&&u[0]==="/")u=A+u;return _f(u,o);};'
        "var _W=WebSocket;"
        "window.WebSocket=function(u,p){"
        'if(typeof u==="string"){'
        'if(u[0]==="/")u=W+u;'
        'else if(h&&u.startsWith("ws://"+h+"/"))u=W+u.slice(("ws://"+h).length);'
        "}"
        "return p?new _W(u,p):new _W(u);};"
        "Object.assign(window.WebSocket,_W);"
        "})();</script>"
    )
    html = (_UI_DIST / "index.html").read_text("utf-8")
    html = html.replace("<head>", "<head>" + interceptor, 1)
    out  = _UI_DIST / ".index_app.html"
    out.write_text(html, "utf-8")
    return str(out)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=_start_server, daemon=True).start()
    if not _wait_for_server():
        sys.exit("API server failed to start within 90 s")

    _tray = pystray.Icon(
        "Archiver", _make_icon(), "Archiver",
        pystray.Menu(
            pystray.MenuItem("Show", lambda *_: _show(), default=True),
            pystray.MenuItem("Quit", _quit),
        ),
    )
    threading.Thread(target=_tray.run, daemon=True).start()

    _window = webview.create_window(
        "Archiver", _prepare_index(), width=1100, height=720, min_size=(800, 560),
        frameless=True, easy_drag=False, js_api=JsApi(), background_color="#2b2b2b",
    )
    _window.events.loaded += _apply_thick_frame
    webview.start(
        _on_started,
        http_server=True,
        private_mode=False,
        storage_path=str(_wv2_dir),
        icon=str(_APP_ICON),
    )

    if _tray:
        _tray.stop()
