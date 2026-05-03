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
    if not _hwnd:
        return False
    wp = _WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
    ctypes.windll.user32.GetWindowPlacement(_hwnd, ctypes.byref(wp))
    return wp.showCmd == 3  # SW_MAXIMIZE


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
            # Title bar drag strip (exclude window-control button zone on right)
            if y - rc.top < _TITLE_H and rc.right - x > _WINCTRLS_W:
                return 2  # HTCAPTION — OS handles drag; double-click maximises
            return 1  # HTCLIENT

        if msg == 0x0024:  # WM_GETMINMAXINFO — respect taskbar on maximize
            rc = ctypes.wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0)
            mmi = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO))
            mmi.contents.ptMaxPosition.x = rc.left
            mmi.contents.ptMaxPosition.y = rc.top
            mmi.contents.ptMaxSize.x     = rc.right  - rc.left
            mmi.contents.ptMaxSize.y     = rc.bottom - rc.top
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
        if _hwnd:
            ctypes.windll.user32.ReleaseCapture()
            ctypes.windll.user32.PostMessageW(_hwnd, 0x00A1, 2, 0)  # WM_NCLBUTTONDOWN, HTCAPTION

    def minimize_window(self) -> None:
        if _window:
            _window.minimize()

    def toggle_maximize(self) -> None:
        if _window:
            if _is_maximized():
                _window.restore()
            else:
                _window.maximize()

    def close_window(self) -> None:
        if _window:
            _window.hide()

    def is_maximized(self) -> bool:
        return _is_maximized()


# ── Server ────────────────────────────────────────────────────────────────────

def _start_server() -> None:
    uvicorn.run("src.api:app", host="127.0.0.1", port=PORT,
                reload=False, log_level="error")


def _wait_for_server(timeout: float = 10.0) -> bool:
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
    ico = Path(__file__).parent / "assets" / "icon.ico"
    if ico.exists():
        return Image.open(ico).convert("RGBA")
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([4, 4, 60, 60], fill=(29, 155, 240, 255))
    return img


def _show() -> None:
    if _window:
        _window.show()


def _quit(icon: "pystray.Icon") -> None:
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
        sys.exit("API server failed to start within 10 s")

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
        frameless=True, js_api=JsApi(), background_color="#2b2b2b",
    )
    _window.events.loaded += _apply_thick_frame
    webview.start(_on_started, http_server=True)

    if _tray:
        _tray.stop()
