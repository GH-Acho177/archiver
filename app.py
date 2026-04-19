import asyncio
import os
import subprocess
import threading
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog, simpledialog
import shutil
from pathlib import Path

import sv_ttk
import ctypes as _ctypes

# Enable system DPI awareness before any Tk window is created.
# Without this, Windows bitmap-scales the whole window on high-DPI displays,
# which makes text look jagged/blurry. Must run before tk.Tk().
try:
    _ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        _ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ── Path setup (must come before any local imports) ───────────────────────────
if getattr(sys, "frozen", False):
    _BASE_DIR    = Path(sys.executable).parent
    _MEIPASS     = Path(getattr(sys, "_MEIPASS", _BASE_DIR))
    _HELPERS_DIR = str(_MEIPASS / "helpers")
    os.environ["PATH"] = (str(_BASE_DIR) + os.pathsep +
                          str(_MEIPASS)   + os.pathsep +
                          os.environ.get("PATH", ""))
else:
    _BASE_DIR    = Path(__file__).resolve().parent
    _MEIPASS     = _BASE_DIR
    _HELPERS_DIR = str(_BASE_DIR / "helpers")

# Runtime data (config/, downloads/, logs/) resolves from exe/project root
os.chdir(_BASE_DIR)
if _HELPERS_DIR not in sys.path:
    sys.path.insert(0, _HELPERS_DIR)

from src.config import (
    APP_VERSION, PLATFORMS, SETTINGS_FILE, DOUYIN_LAST_RUN, UPDATE_HISTORY_FILE,
    DOWNLOAD_PATH_FILE, LANG_FILE, GDL, ACCENT, _MEDIA_EXTS,
    THEME_COLORS, FONTS, _LOG_TAGS, STRINGS,
)
from src.creator_store import CreatorStore
from src.utils import TextRedirector, _LineWriter, _TaskBuffer, _ThreadRouter, _del

try:
    import pystray
    from PIL import Image as _PILImage
    _TRAY_AVAILABLE = True
except ImportError:
    _TRAY_AVAILABLE = False

# ── Bundle font loading ────────────────────────────────────────────────────────
def _load_bundled_fonts(tk_root=None):
    """Load bundled .ttf files via GDI.  Call after tk.Tk() exists."""
    _FR_PRIVATE = 0x10
    _gdi        = _ctypes.windll.gdi32
    _fonts_dir  = _MEIPASS / "fonts"
    if not _fonts_dir.exists():
        return
    for _ttf in _fonts_dir.glob("*.ttf"):
        _gdi.AddFontResourceExW(str(_ttf), _FR_PRIVATE, 0)
    if tk_root is not None:
        tk_root.tk.eval("font families")

def _read_download_dir() -> Path:
    """Return the configured download root, falling back to 'downloads/'."""
    p = Path(DOWNLOAD_PATH_FILE)
    if p.exists():
        custom = p.read_text(encoding="utf-8").strip()
        if custom:
            return Path(custom)
    return Path("downloads")


# Sentinel — means "cursor is not over any valid drop target"
_DRAG_NONE = object()


class _ThinBar:
    """2 px accent strip; supports determinate fill and indeterminate animation."""

    _BAR_W = 0.22   # indeterminate block width as fraction of total
    _STEP  = 0.012  # fraction moved per frame
    _FPS   = 50     # ms per frame

    def __init__(self, parent, accent: str, bg: str):
        self._accent = accent
        self._bg     = bg
        self.canvas  = tk.Canvas(parent, height=2, bd=0,
                                 highlightthickness=0, bg=bg)
        self._rect   = self.canvas.create_rectangle(0, 0, 0, 2,
                                                    fill=accent, outline="")
        self._anim_id: "str | None" = None
        self._pos    = 0.0   # indeterminate head position (0–1)
        self._value  = 0.0   # determinate value (0–100)
        self._mode   = "determinate"
        self.canvas.bind("<Configure>", lambda _e: self._draw())

    # ── public API (mirrors ttk.Progressbar enough for our call sites) ─────────
    def pack(self, **kw):
        self.canvas.pack(**kw)

    def configure(self, **kw):
        if "mode" in kw:
            self._mode = kw["mode"]

    def __setitem__(self, key, val):
        if key == "value":
            self._value = float(val)
            if self._mode == "determinate":
                self._draw()

    def start(self, _interval=None):
        self._mode = "indeterminate"
        self._pos  = 0.0
        self._cancel()
        self._animate()

    def stop(self):
        self._cancel()
        self._mode  = "determinate"
        self._value = 0.0
        self._draw()

    # ── internals ──────────────────────────────────────────────────────────────
    def _draw(self):
        w = self.canvas.winfo_width()
        if w < 2:
            return
        if self._mode == "determinate":
            x2 = int(w * self._value / 100)
            self.canvas.coords(self._rect, 0, 0, x2, 2)
        # indeterminate drawn by _animate

    def _animate(self):
        w = self.canvas.winfo_width()
        if w < 2:
            self._anim_id = self.canvas.after(self._FPS, self._animate)
            return
        bw   = self._BAR_W
        head = self._pos
        tail = head - bw
        x1   = int(max(0, tail) * w)
        x2   = int(min(1, head) * w)
        self.canvas.coords(self._rect, x1, 0, x2, 2)
        self._pos += self._STEP
        if self._pos > 1 + bw:
            self._pos = 0.0
        self._anim_id = self.canvas.after(self._FPS, self._animate)

    def _cancel(self):
        if self._anim_id:
            self.canvas.after_cancel(self._anim_id)
            self._anim_id = None

# ── Main application ───────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()             # hide until fully built to avoid size flash
        _load_bundled_fonts(self)   # must be after Tk() but before any font use
        # Compute scale factor now that we have real DPI (SetProcessDpiAwareness
        # was called at module level so winfo_fpixels returns the actual value).
        _raw_dpi = self.winfo_fpixels('1i')
        self._sf: float = max(1.0, _raw_dpi / 96)   # 1.0 at 96 dpi, 1.5 at 144 dpi, etc.
        self.tk.call('tk', 'scaling', _raw_dpi / 72.0)
        self.title(f"Archiver v{APP_VERSION}")
        _ico = _MEIPASS / "assets" / "icon.ico"
        if _ico.exists():
            self.iconbitmap(str(_ico))
        self.geometry(f"{int(1080 * self._sf)}x{int(740 * self._sf)}")
        self.minsize(int(860 * self._sf), int(580 * self._sf))

        self._current_theme = "dark"
        sv_ttk.set_theme("dark")
        self._patch_styles()

        # i18n
        self._lang: str = "en"
        self._i18n: list = []   # [(widget, key), ...]
        try:
            saved = Path(LANG_FILE).read_text(encoding="utf-8").strip()
            if saved in STRINGS:
                self._lang = saved
        except Exception:
            pass

        self.running    = False
        self.stop_flag  = threading.Event()
        self._proc      = None
        self._procs:    list = []   # active parallel procs (f2)
        self._procs_lock = threading.Lock()

        self._mode_btns:    dict[str, tk.Label] = {}
        self._from_days_var = tk.StringVar(value="0")
        self._from_days_sb: ttk.Spinbox | None = None

        # Creator tab state
        self._creators_canvas: tk.Canvas | None        = None
        self._creators_inner:  tk.Frame | None         = None
        self._creators_win:    int                     = 0
        self._settings_canvas: tk.Canvas | None        = None
        self._cookie_status:   dict[str, tk.StringVar] = {}

        # Drag-and-drop state
        self._drag_entry_id:  "str | None"         = None
        self._drag_display:   str                  = ""
        self._drag_start_xy:  tuple                = (0, 0)
        self._drag_ghost:     "tk.Toplevel | None" = None
        self._drag_target_id: object               = _DRAG_NONE
        self._drag_headers:   list                 = []  # [(hdr_frame, creator_id_or_None)]
        self._drag_active_hdr: "tk.Frame | None"   = None
        self._drag_hdr_colors: dict                = {}  # hdr_frame -> saved bg

        self._log_widget: scrolledtext.ScrolledText | None = None

        # Scheduler state
        self._scheduler_stop:   threading.Event         = threading.Event()
        self._scheduler_thread: "threading.Thread|None" = None
        self._scheduler_next_at: float                  = 0.0

        Path("config").mkdir(exist_ok=True)
        Path("downloads").mkdir(exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
        self._migrate_legacy_files()
        self._store = CreatorStore()
        self._store.migrate_from_legacy(PLATFORMS)
        self._load_platform_icons()
        self._build_ui()
        self._refresh_from_date()

        if self._load_setting("auto_update_enabled", False):
            self._start_scheduler()

        self._tray_icon: "pystray.Icon | None" = None
        self._setup_tray()
        self.deiconify()            # show now that layout is complete

    # ── System tray ────────────────────────────────────────────────────────────
    def _setup_tray(self):
        if not _TRAY_AVAILABLE:
            self.protocol("WM_DELETE_WINDOW", self._quit_app)
            return

        # Load icon image once and reuse across tray icon instances.
        icon_path = _MEIPASS / "assets" / "icon.ico"
        if icon_path.exists():
            self._tray_img = _PILImage.open(icon_path).convert("RGBA").resize((64, 64))
        else:
            self._tray_img = _PILImage.new("RGBA", (64, 64), color=(0, 103, 192, 255))

        self._tray_icon = None
        self.protocol("WM_DELETE_WINDOW", self._quit_app)
        self.bind("<Unmap>", self._on_unmap)

    def _make_tray_icon(self) -> "pystray.Icon":
        """Create a fresh Icon instance (pystray icons cannot be reused after stop())."""
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._restore_from_tray, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit_app),
        )
        return pystray.Icon(
            "Archiver",
            icon=self._tray_img,
            title=f"Archiver v{APP_VERSION}",
            menu=menu,
        )

    def _on_unmap(self, event):
        # Only act on the iconic (minimized) state, not on withdraw() calls.
        if event.widget is self and self.state() == "iconic":
            self._hide_to_tray()

    def _hide_to_tray(self):
        # withdraw() removes the window AND its taskbar button completely.
        # The <Unmap> handler won't re-enter because state becomes "withdrawn".
        self.withdraw()
        self._tray_icon = self._make_tray_icon()
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _restore_from_tray(self, icon=None, item=None):
        """Called from the tray thread — stop the icon there, then restore the window."""
        if icon is not None:
            icon.stop()
        self.after(0, self._do_restore)

    def _do_restore(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit_app(self, icon=None, item=None):
        """Stop the tray icon (from its own thread if coming from menu) then exit."""
        if icon is not None:
            icon.stop()
        self.after(0, self.destroy)

    # ── Legacy migration ───────────────────────────────────────────────────────
    @staticmethod
    def _migrate_legacy_files():
        mapping = {
            "config/users.txt":   "config/x_users.txt",
            "config/cookies.txt": "config/x_cookies.txt",
        }
        for old, new in mapping.items():
            if Path(old).exists() and not Path(new).exists():
                Path(old).rename(new)
        App._flatten_date_folders()
        # Move any f2 DB files that landed in the script root into config/
        for _db in ("douyin_users.db", "douyin_videos.db"):
            _src = Path(_db)
            if _src.exists():
                _dst = Path("config") / _db
                if _dst.exists():
                    _src.unlink()   # already have one in config, discard duplicate
                else:
                    _src.rename(_dst)

    @staticmethod
    def _flatten_date_folders():
        """Move files out of YYYY-MM-DD subfolders into their parent account folder."""
        import re
        date_re = re.compile(r'^\d{4}-\d{2}-\d{2}$')
        base  = _read_download_dir()
        roots = [
            base / "douyin" / "post",
            *(base / pid for pid in PLATFORMS if pid != "douyin"),
        ]
        for root in roots:
            if not root.exists():
                continue
            for account_dir in root.iterdir():
                if not account_dir.is_dir():
                    continue
                for date_dir in list(account_dir.iterdir()):
                    if not date_dir.is_dir() or not date_re.match(date_dir.name):
                        continue
                    for f in list(date_dir.iterdir()):
                        if not f.is_file():
                            continue
                        dest = account_dir / f.name
                        if dest.exists():
                            stem, suffix, n = f.stem, f.suffix, 1
                            while dest.exists():
                                dest = account_dir / f"{stem}_{n}{suffix}"
                                n += 1
                        f.rename(dest)
                    try:
                        date_dir.rmdir()
                    except OSError:
                        pass  # leave non-empty dirs alone

    # ── Platform icon images ───────────────────────────────────────────────────
    def _load_platform_icons(self):
        """Load per-platform PNG icons from assets/, composited onto their
        icon_bg color so transparent cut-outs are filled before display."""
        self._platform_icons:    dict = {}
        self._platform_icons_lg: dict = {}
        try:
            from PIL import Image, ImageTk
        except ImportError:
            return
        size    = max(14, int(16 * self._sf))
        size_lg = max(24, int(28 * self._sf))
        for pid, cfg in PLATFORMS.items():
            path = _MEIPASS / "assets" / f"{pid}.png"
            if not path.exists():
                continue
            try:
                hex_bg = cfg.get("icon_bg", "#ffffff")
                r, g, b = int(hex_bg[1:3], 16), int(hex_bg[3:5], 16), int(hex_bg[5:7], 16)
                img = Image.open(path).convert("RGBA")
                canvas_sm = Image.new("RGBA", img.size, (r, g, b, 255))
                composited_sm = Image.alpha_composite(canvas_sm, img)
                self._platform_icons[pid] = ImageTk.PhotoImage(
                    composited_sm.resize((size, size), Image.LANCZOS).convert("RGB"))
                canvas_lg = Image.new("RGBA", img.size, (r, g, b, 255))
                composited_lg = Image.alpha_composite(canvas_lg, img)
                self._platform_icons_lg[pid] = ImageTk.PhotoImage(
                    composited_lg.resize((size_lg, size_lg), Image.LANCZOS).convert("RGB"))
            except Exception:
                pass

    # ── Download dir ───────────────────────────────────────────────────────────
    def _get_download_dir(self) -> Path:
        return _read_download_dir()

    # ── Persistent settings ────────────────────────────────────────────────────
    def _load_settings(self) -> dict:
        import json as _j
        try:
            if Path(SETTINGS_FILE).exists():
                return _j.loads(Path(SETTINGS_FILE).read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _load_setting(self, key: str, default):
        return self._load_settings().get(key, default)

    def _save_setting(self, key: str, value):
        import json as _j
        data = self._load_settings()
        data[key] = value
        Path(SETTINGS_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(SETTINGS_FILE).write_text(_j.dumps(data, indent=2), encoding="utf-8")

    # ── Dialog helper ──────────────────────────────────────────────────────────
    def _centre_dialog(self, dlg: tk.Toplevel, w: int, h: int):
        self.update_idletasks()
        rx, ry = self.winfo_rootx(), self.winfo_rooty()
        rw, rh = self.winfo_width(), self.winfo_height()
        dlg.geometry(f"{w}x{h}+{rx + (rw - w)//2}+{ry + (rh - h)//2}")
        self.unbind_all("<MouseWheel>")
        def _restore(_e=None):
            if self._active_nav == 1 and self._creators_canvas:
                self.bind_all("<MouseWheel>",
                    lambda e: self._creators_canvas.yview_scroll(-1*(e.delta//120), "units"))
            elif self._active_nav == 2 and self._settings_canvas:
                self.bind_all("<MouseWheel>",
                    lambda e: self._settings_canvas.yview_scroll(-1*(e.delta//120), "units"))
        dlg.bind("<Destroy>", _restore)

    # ── Styles ─────────────────────────────────────────────────────────────────
    def _patch_styles(self):
        s = ttk.Style()
        c = THEME_COLORS[self._current_theme]
        red = "#f75464" if self._current_theme == "dark" else "#c42b1c"

        s.configure("Danger.TButton", foreground=red)
        s.map("Danger.TButton",
              foreground=[("disabled", "#666666"), ("active", red)])

        light_blue = "#6ab4f5"
        s.configure("About.TButton", foreground=light_blue)
        s.map("About.TButton",
              foreground=[("active", "#90caff")])

        # Stop button — neutral secondary; dimmed when disabled
        s.configure("Secondary.TButton",
                    foreground=c["text_dim"], padding=(12, 6))
        s.map("Secondary.TButton",
              foreground=[("disabled", c["border"]), ("active", c["text"])])

        # Utility buttons (Clear log / Updates) — smaller, lower weight
        s.configure("Utility.TButton",
                    foreground=c["text_dim"], padding=(8, 4))
        s.map("Utility.TButton",
              foreground=[("active", c["text"])])

        s.configure("TLabelframe.Label", font=FONTS["heading"])
        s.configure("TLabel",      font=FONTS["body"])
        s.configure("TButton",     font=FONTS["body"], padding=(12, 6))
        s.configure("TCheckbutton", font=FONTS["body"])
        s.configure("TRadiobutton", font=FONTS["body"])
        s.configure("TSpinbox",    font=FONTS["body"], padding=(8, 6))
        s.configure("TEntry",      font=FONTS["body"], padding=(8, 6))
        s.configure("TCombobox",   font=FONTS["body"], padding=(8, 6))

        # Thin accent-coloured progress bar
        s.configure("Horizontal.TProgressbar",
                    troughcolor=c["border"],
                    background=c["accent"],
                    borderwidth=0,
                    thickness=4)

        # Slim scrollbar
        _sb = max(6, int(8 * self._sf))
        s.configure("TScrollbar", width=_sb, arrowsize=_sb)

        # Separators match border colour
        s.configure("TSeparator", background=c["border"])

    # ── i18n ───────────────────────────────────────────────────────────────────
    def _t(self, key: str) -> str:
        """Return translated string for the current language."""
        return STRINGS.get(self._lang, STRINGS["en"]).get(key, STRINGS["en"].get(key, key))

    def _reg(self, widget, key: str):
        """Register a widget for language updates; returns the widget."""
        self._i18n.append((widget, key))
        return widget

    def _apply_lang(self):
        """Push current language to all registered widgets."""
        for widget, key in self._i18n:
            try:
                widget.configure(text=self._t(key))
            except tk.TclError:
                pass
        # Nav text labels (order matches NAV_ITEMS)
        nav_keys = ["nav.dashboard", "nav.accounts", "nav.settings"]
        for i, (_, _, text_lbl, _) in enumerate(self._nav_frames):
            if i < len(nav_keys):
                text_lbl.configure(text=self._t(nav_keys[i]))
        # Mode buttons — keep dot prefix
        self._refresh_mode_btns()
        # Theme toggle button label
        if hasattr(self, "_theme_toggle_btn"):
            self._theme_toggle_btn.configure(
                text=self._t("btn.switch_light") if self._current_theme == "dark"
                else self._t("btn.switch_dark"))
        self._nav_select(self._active_nav)

    def _set_lang(self, lang: str):
        if lang == self._lang:
            return
        self._lang = lang
        try:
            Path(LANG_FILE).write_text(lang, encoding="utf-8")
        except Exception:
            pass
        self._apply_lang()

    def _toggle_theme(self):
        self._current_theme = "light" if self._current_theme == "dark" else "dark"
        sv_ttk.set_theme(self._current_theme)
        self._patch_styles()
        c = THEME_COLORS[self._current_theme]
        self._refresh_creator_list_theme()
        if self._log_widget:
            self._log_widget.configure(bg=c["log_bg_deep"], fg=c["log_fg"])
            self._configure_log_tags()
        if hasattr(self, "_log_header_lbl"):
            self._log_header_lbl.configure(bg=c["bg"], fg=c["text_dim"])
        if hasattr(self, '_theme_toggle_btn'):
            self._theme_toggle_btn.configure(
                text=self._t("btn.switch_light") if self._current_theme == "dark"
                else self._t("btn.switch_dark"))
        self._refresh_chrome_theme()

    # ── UI skeleton ────────────────────────────────────────────────────────────
    def _build_ui(self):
        c = THEME_COLORS[self._current_theme]

        # ── Top bar ───────────────────────────────────────────────────────────
        self._build_topbar(c)

        # ── Status bar — packed to BOTTOM before main so it isn't squeezed ───
        self._build_statusbar(c)

        # ── Main: sidebar (180 px) + stacked content panels ───────────────────
        self._main = tk.Frame(self, bg=c["bg"])
        self._main.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        self._sidebar = tk.Frame(self._main, bg=c["panel"], width=int(180 * self._sf))
        self._sidebar.pack(fill=tk.Y, side=tk.LEFT)
        self._sidebar.pack_propagate(False)

        self._sidebar_sep = tk.Frame(self._main, bg=c["border"], width=1)
        self._sidebar_sep.pack(fill=tk.Y, side=tk.LEFT)

        self._content = tk.Frame(self._main, bg=c["bg"])
        self._content.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        # Panels: Dashboard, Downloads, Accounts, Settings
        dash         = tk.Frame(self._content, bg=c["bg"])
        accounts_tab = tk.Frame(self._content, bg=c["bg"])
        sett_tab     = tk.Frame(self._content, bg=c["bg"])
        self._panels = [dash, accounts_tab, sett_tab]

        self._build_dashboard(dash)
        self._build_accounts(accounts_tab)
        self._build_settings(sett_tab)

        # Sidebar nav items
        self._nav_frames: list[tuple] = []
        tk.Frame(self._sidebar, bg=c["panel"], height=int(10 * self._sf)).pack(fill=tk.X)
        for idx, (icon, key) in enumerate([
                ("◈", "nav.dashboard"),
                ("☰", "nav.accounts"),
                ("⚙", "nav.settings")]):
            self._build_nav_item(self._sidebar, idx, icon, key)

        self._active_nav = -1
        self._nav_select(0)

    # ── Top bar ────────────────────────────────────────────────────────────────
    def _build_topbar(self, c):
        _sf = self._sf
        self._topbar = tk.Frame(self, bg=c["panel"], height=int(44 * _sf))
        self._topbar.pack(fill=tk.X, side=tk.TOP)
        self._topbar.pack_propagate(False)

        self._topbar_sep = tk.Frame(self, bg=c["border"], height=1)
        self._topbar_sep.pack(fill=tk.X, side=tk.TOP)

        # App name + version
        name_frame = tk.Frame(self._topbar, bg=c["panel"])
        name_frame.pack(side=tk.LEFT, padx=int(16 * _sf), pady=int(10 * _sf))

        self._topbar_name = tk.Label(
            name_frame, text="Archiver",
            font=FONTS["heading"], bg=c["panel"], fg=c["text"])
        self._topbar_name.pack(side=tk.LEFT)

        self._topbar_ver = tk.Label(
            name_frame, text=f"v{APP_VERSION}",
            font=FONTS["small"], bg=c["panel"], fg=c["text_dim"])
        self._topbar_ver.pack(side=tk.LEFT, padx=(6, 0))

        # Right-side controls
        right = tk.Frame(self._topbar, bg=c["panel"])
        right.pack(side=tk.RIGHT, padx=int(12 * _sf))

        self._topbar_theme_btn = ttk.Button(
            right, text="◐", width=3, command=self._toggle_theme)
        self._topbar_theme_btn.pack(side=tk.LEFT, padx=(0, 6))

        self._topbar_updates_btn = ttk.Button(
            right, text="📢", width=3, command=self._open_announcements)
        self._topbar_updates_btn.pack(side=tk.LEFT)

    # ── Status bar ─────────────────────────────────────────────────────────────
    def _build_statusbar(self, c):
        self._statusbar_sep = tk.Frame(self, bg=c["border"], height=1)
        self._statusbar_sep.pack(fill=tk.X, side=tk.BOTTOM)
        self._statusbar = tk.Frame(self, bg=c["panel"], height=int(30 * self._sf))
        self._statusbar.pack(fill=tk.X, side=tk.BOTTOM)
        self._statusbar.pack_propagate(False)
        self.status_var = tk.StringVar(value="Idle")
        self._status_lbl = tk.Label(
            self._statusbar, textvariable=self.status_var,
            font=FONTS["small"], bg=c["panel"], fg=c["text_dim"], padx=8)
        self._status_lbl.pack(side=tk.LEFT, pady=3)

        self._auto_next_var = tk.StringVar(value="")
        self._auto_countdown_lbl = tk.Label(
            self._statusbar, textvariable=self._auto_next_var,
            font=FONTS["small"], bg=c["panel"], fg=c["text_dim"], padx=8)
        self._auto_countdown_lbl.pack(side=tk.RIGHT, pady=3)


    # ── Sidebar nav ────────────────────────────────────────────────────────────
    def _build_nav_item(self, parent, idx: int, icon: str, key: str):
        c = THEME_COLORS[self._current_theme]

        _sf = self._sf
        row = tk.Frame(parent, bg=c["panel"], cursor="hand2", height=int(44 * _sf))
        row.pack(fill=tk.X, side=tk.TOP)
        row.pack_propagate(False)

        bar = tk.Frame(row, bg=c["panel"], width=max(3, int(4 * _sf)))
        bar.pack(side=tk.LEFT, fill=tk.Y)

        _ipad = int(6 * _sf)
        _ipad2 = max(2, int(3 * _sf))
        _vpad = int(8 * _sf)
        icon_lbl = tk.Label(row, text=icon, font=FONTS["body"],
                            bg=c["panel"], fg=c["text_dim"],
                            width=2, anchor="center")
        icon_lbl.pack(side=tk.LEFT, padx=(_ipad, _ipad2), pady=_vpad)

        text_lbl = tk.Label(row, text=self._t(key), font=FONTS["body"],
                            bg=c["panel"], fg=c["text"], anchor="w")
        text_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=_vpad)

        self._nav_frames.append((bar, icon_lbl, text_lbl, row))

        for w in (row, icon_lbl, text_lbl):
            w.bind("<Button-1>", lambda _e, i=idx: self._nav_select(i))
            w.bind("<Enter>",    lambda _e, i=idx: self._nav_hover(i, True))
            w.bind("<Leave>",    lambda _e, i=idx: self._nav_hover(i, False))

    def _nav_hover(self, idx: int, entering: bool):
        if idx == self._active_nav:
            return
        c  = THEME_COLORS[self._current_theme]
        bg = c["hover"] if entering else c["panel"]
        bar, icon_lbl, text_lbl, row = self._nav_frames[idx]
        for w in (row, icon_lbl, text_lbl):
            w.configure(bg=bg)

    def _nav_select(self, idx: int):
        c = THEME_COLORS[self._current_theme]
        self._active_nav = idx
        for i, (bar, icon_lbl, text_lbl, row) in enumerate(self._nav_frames):
            if i == idx:
                for w in (row, icon_lbl, text_lbl): w.configure(bg=c["hover"])
                bar.configure(bg=c["accent"])
                text_lbl.configure(font=(*FONTS["body"], "bold"), fg=c["text"])
                icon_lbl.configure(fg=c["accent"])
            else:
                for w in (row, bar, icon_lbl, text_lbl): w.configure(bg=c["panel"])
                text_lbl.configure(font=FONTS["body"], fg=c["text"])
                icon_lbl.configure(fg=c["text_dim"])
        for i, p in enumerate(self._panels):
            if i == idx:
                p.pack(fill=tk.BOTH, expand=True)
            else:
                p.pack_forget()
        self.unbind_all("<MouseWheel>")
        if idx == 1:  # Accounts
            self.after(50, self.focus_set)   # prevent bar entry from grabbing focus
            self._refresh_creator_list()
            if self._creators_canvas:
                self.bind_all("<MouseWheel>",
                    lambda e: self._creators_canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        elif idx == 2:  # Settings
            _INPUT_CLASSES = {"TSpinbox", "TEntry", "Entry", "Text", "Spinbox"}
            def _sett_wheel(e):
                if e.widget.winfo_class() in _INPUT_CLASSES:
                    return
                self._settings_canvas.yview_scroll(-1 * (e.delta // 120), "units")
            self.bind_all("<MouseWheel>", _sett_wheel)

    def _refresh_chrome_theme(self):
        """Re-apply JetBrains palette to all chrome (non-ttk) widgets."""
        c = THEME_COLORS[self._current_theme]
        # Top bar
        self._topbar.configure(bg=c["panel"])
        self._topbar_sep.configure(bg=c["border"])
        self._topbar_name.configure(bg=c["panel"], fg=c["text"])
        self._topbar_ver.configure(bg=c["panel"], fg=c["text_dim"])
        for w in self._topbar.winfo_children():
            if isinstance(w, tk.Frame):
                w.configure(bg=c["panel"])
        # Status bar
        self._statusbar.configure(bg=c["panel"])
        self._statusbar_sep.configure(bg=c["border"])
        self._status_lbl.configure(bg=c["panel"], fg=c["text_dim"])
        # Main layout
        self._main.configure(bg=c["bg"])
        self._sidebar.configure(bg=c["panel"])
        self._sidebar_sep.configure(bg=c["border"])
        self._content.configure(bg=c["bg"])
        for p in self._panels:
            p.configure(bg=c["bg"])
        # Dashboard log area
        if hasattr(self, "_log_area"):
            self._log_area.configure(bg=c["bg"])
        if hasattr(self, "_log_wrap"):
            self._log_wrap.configure(bg=c["log_border"])
        if hasattr(self, "_log_header_lbl"):
            self._log_header_lbl.configure(bg=c["bg"], fg=c["text_dim"])
        # Recursively remap every tk widget's bg/fg from old palette → new palette
        old_theme = "light" if self._current_theme == "dark" else "dark"
        old_c     = THEME_COLORS[old_theme]
        self._recolor_all(self, old_c, c)
        # Refresh sidebar top padding frame
        for w in self._sidebar.winfo_children():
            if isinstance(w, tk.Frame) and not any(
                    w is row for _, _, _, row in self._nav_frames):
                w.configure(bg=c["panel"])
        self._nav_select(self._active_nav)
        if self._mode_btns:
            self._refresh_mode_btns()

    def _recolor_all(self, widget, old_c: dict, new_c: dict):
        """Walk every widget; replace any bg/fg that matches an old-theme color."""
        old_to_new = {v: new_c[k] for k, v in old_c.items()}
        self._recolor_widget(widget, old_to_new)

    def _recolor_widget(self, widget, mapping: dict):
        for opt in ("background", "foreground"):
            try:
                val = str(widget.cget(opt))
                val = val.lower()
                if val in mapping:
                    widget.configure(**{opt: mapping[val]})
            except tk.TclError:
                pass
        for child in widget.winfo_children():
            self._recolor_widget(child, mapping)

    # ── Dashboard ──────────────────────────────────────────────────────────────
    def _get_last_sync(self) -> str:
        try:
            import json as _j
            if Path(UPDATE_HISTORY_FILE).exists():
                hist = _j.loads(Path(UPDATE_HISTORY_FILE).read_text(encoding="utf-8"))
                if hist:
                    last = hist[-1]
                    return f"{last.get('date', '')} {last.get('time', '')}".strip()
        except Exception:
            pass
        return "Never"

    def _build_dashboard(self, parent):
        c   = THEME_COLORS[self._current_theme]
        _sf = self._sf
        pad = int(16 * _sf)
        sfg = c["status_fg"]    # lightest — status strip
        cfg = c["text_dim"]     # mid — Mode/Auto settings labels

        top = tk.Frame(parent, bg=c["bg"])
        top.pack(fill=tk.X, padx=pad, pady=(int(16 * _sf), 0))

        # ── Row 1: Status strip ────────────────────────────────────────────────
        row1 = tk.Frame(top, bg=c["bg"])
        row1.pack(fill=tk.X, pady=(0, 14))

        def _seg(label, var):
            tk.Label(row1, text=label, font=FONTS["small"],
                     bg=c["bg"], fg=sfg).pack(side=tk.LEFT)
            tk.Label(row1, textvariable=var, font=FONTS["small"],
                     bg=c["bg"], fg=sfg).pack(side=tk.LEFT, padx=(3, 0))

        def _pipe():
            tk.Label(row1, text="  |  ", font=FONTS["small"],
                     bg=c["bg"], fg=sfg).pack(side=tk.LEFT)

        _seg("Status:", self.status_var)
        _pipe()
        self._last_sync_var = tk.StringVar(value=self._get_last_sync())
        _seg("Last Sync:", self._last_sync_var)
        _pipe()
        self._tracking_var = tk.StringVar(
            value=f"{len(self._store.all_entries())} accounts")
        _seg("Tracking:", self._tracking_var)

        # ── Row 2a: Primary actions ────────────────────────────────────────────
        row2a = tk.Frame(top, bg=c["bg"])
        row2a.pack(fill=tk.X, pady=(0, 12))

        self.start_btn = self._reg(
            ttk.Button(row2a, text=self._t("btn.start"),
                       style="Accent.TButton", command=self.start),
            "btn.start")
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.stop_btn = self._reg(
            ttk.Button(row2a, text=self._t("btn.stop"),
                       style="Secondary.TButton", command=self.stop,
                       state=tk.DISABLED),
            "btn.stop")
        self.stop_btn.pack(side=tk.LEFT)

        # ── Row 2b: Settings + utilities ──────────────────────────────────────
        self._row2b = tk.Frame(top, bg=c["bg"])
        self._row2b.pack(fill=tk.X)
        row2b = self._row2b

        # Mode — radio-dots (first)
        self._mode_lbl = tk.Label(row2b, text="Mode:", font=FONTS["body"],
                                  bg=c["bg"], fg=cfg)
        self._mode_lbl.pack(side=tk.LEFT, anchor="center", padx=(0, 6))

        self.mode_var = tk.StringVar(value="update")
        self._mode_btns: dict[str, tk.Label] = {}
        for val in ("update", "full"):
            lbl = tk.Label(row2b, font=FONTS["body"],
                           cursor="hand2", bd=0, highlightthickness=0,
                           bg=c["bg"])
            lbl.pack(side=tk.LEFT, anchor="center", padx=(0, 14))
            lbl.bind("<Button-1>", lambda _e, v=val: self._set_mode(v))
            self._mode_btns[val] = lbl

        # "Last N days" spinbox — inline after Full when selected
        self._from_days_frame = tk.Frame(row2b, bg=c["bg"])
        self._reg(
            tk.Label(self._from_days_frame, text=self._t("label.last"),
                     bg=c["bg"], fg=cfg, font=FONTS["body"]),
            "label.last").pack(side=tk.LEFT, anchor="center", padx=(0, 4))
        self._from_days_sb = ttk.Spinbox(
            self._from_days_frame, textvariable=self._from_days_var,
            from_=0, to=3650, width=4, font=FONTS["mono"])
        self._from_days_sb.pack(side=tk.LEFT, anchor="center")
        self._reg(
            tk.Label(self._from_days_frame, text=self._t("label.days_all"),
                     bg=c["bg"], fg=cfg, font=FONTS["body"]),
            "label.days_all").pack(side=tk.LEFT, anchor="center")

        # Auto toggle — after mode buttons
        TW, TH = 44, 24
        self._auto_pill = tk.Canvas(row2b, width=TW, height=TH,
                                    highlightthickness=0, bg=c["bg"],
                                    cursor="hand2")
        self._auto_pill.pack(side=tk.LEFT, anchor="center", padx=(20, 4), pady=(8, 0))
        self._auto_pill.bind("<Button-1>",
                             lambda _e: self._toggle_auto_from_dashboard())

        self._auto_lbl = tk.Label(row2b, text="Auto", font=FONTS["small"],
                                  bg=c["bg"], fg=cfg, cursor="hand2")
        self._auto_lbl.pack(side=tk.LEFT, anchor="center")
        self._auto_lbl.bind("<Button-1>",
                            lambda _e: self._toggle_auto_from_dashboard())

        self._refresh_mode_btns()
        self._refresh_auto_pill()

        # Utilities — right-aligned, inside the countdown
        for key, cmd in [
            ("btn.downloads", self._open_downloads),
            ("btn.clear_log", self.clear_log),
        ]:
            self._reg(
                ttk.Button(row2b, text=self._t(key), command=cmd,
                           style="Utility.TButton"),
                key).pack(side=tk.RIGHT, padx=(4, 0))

        # ── Separator ─────────────────────────────────────────────────────────
        tk.Frame(top, height=1, bg=c["border"]).pack(fill=tk.X, pady=(14, 0))

        # ── Progress bar — between controls and log ────────────────────────────
        self.progress = _ThinBar(parent, accent=c["accent"], bg=c["bg"])
        self.progress.pack(fill=tk.X, padx=pad, pady=(6, 0))

        # ── Log — dim label then bordered scrolledtext, no heavy header bar ───
        self._log_area = tk.Frame(parent, bg=c["bg"])
        self._log_area.pack(fill=tk.BOTH, expand=True,
                            padx=pad, pady=(int(10 * _sf), int(14 * _sf)))

        self._log_header_lbl = tk.Label(
            self._log_area, text=self._t("log.title"),
            bg=c["bg"], fg=cfg, font=FONTS["small"], anchor="w")
        self._reg(self._log_header_lbl, "log.title")
        self._log_header_lbl.pack(fill=tk.X, pady=(0, 4))

        self._log_wrap = tk.Frame(self._log_area, bg=c["log_border"], bd=0)
        self._log_wrap.pack(fill=tk.BOTH, expand=True)

        self._log_widget = scrolledtext.ScrolledText(
            self._log_wrap, state="disabled", wrap=tk.WORD,
            bg=c["log_bg_deep"], fg=c["log_fg"],
            insertbackground=c["log_fg"],
            font=FONTS["mono"], relief=tk.FLAT, borderwidth=0,
            selectbackground="#264f78",
        )
        self._log_widget.pack(fill=tk.BOTH, expand=True)
        self.log = self._log_widget
        self._configure_log_tags()

    def _configure_log_tags(self):
        self._log_widget.tag_config("error",   foreground="#ff6b6b")
        self._log_widget.tag_config("warning", foreground="#e8a030")
        self._log_widget.tag_config("success", foreground="#4ec94e")
        self._log_widget.tag_config("dim",     foreground="#555555")
        self._log_widget.tag_config("info",    foreground="#7aafff")

    # ── Downloads panel ────────────────────────────────────────────────────────
    def _build_downloads(self, parent):
        c   = THEME_COLORS[self._current_theme]
        _sf = self._sf

        # ── URL input section ─────────────────────────────────────────────────
        self._dl_input_frame = tk.Frame(parent, bg=c["panel"],
                                        highlightthickness=1,
                                        highlightbackground=c["border"])
        self._dl_input_frame.pack(fill=tk.X, padx=int(18 * _sf),
                                   pady=(int(16 * _sf), 0))
        input_frame = self._dl_input_frame

        inner = tk.Frame(input_frame, bg=c["panel"])
        inner.pack(fill=tk.X, padx=int(16 * _sf), pady=int(12 * _sf))

        url_row = tk.Frame(inner, bg=c["panel"])
        url_row.pack(fill=tk.X)
        self._dl_url_var = tk.StringVar()
        url_entry = ttk.Entry(url_row, textvariable=self._dl_url_var,
                              font=FONTS["body"])
        url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4,
                       padx=(0, int(10 * _sf)))

        self._dl_detected_var = tk.StringVar(value="")
        dl_btn = ttk.Button(url_row, text=self._t("dl.new"),
                            style="Accent.TButton",
                            command=self._dl_panel_start)
        self._reg(dl_btn, "dl.new")
        dl_btn.pack(side=tk.LEFT)

        hint_row = tk.Frame(inner, bg=c["panel"])
        hint_row.pack(fill=tk.X, pady=(int(4 * _sf), 0))
        self._dl_hint_var = tk.StringVar(value=self._t("dl.input_hint"))
        hint_lbl = tk.Label(hint_row, textvariable=self._dl_hint_var,
                            font=FONTS["small"], bg=c["panel"], fg=c["text_dim"])
        hint_lbl.pack(side=tk.LEFT)

        plat_lbl = tk.Label(hint_row, textvariable=self._dl_detected_var,
                            font=(*FONTS["small"], "bold"),
                            bg=c["panel"], fg=c["accent"])
        plat_lbl.pack(side=tk.LEFT, padx=(8, 0))

        def _on_url_change(*_):
            text = self._dl_url_var.get()
            pid  = self._detect_platform_from_url(text)
            if pid and text:
                self._dl_detected_var.set(PLATFORMS[pid]["label"])
                self._dl_hint_var.set("")
            else:
                self._dl_detected_var.set("")
                self._dl_hint_var.set(self._t("dl.input_hint"))

        self._dl_url_var.trace_add("write", _on_url_change)
        url_entry.bind("<Return>", lambda _: self._dl_panel_start())

        # ── Toolbar ───────────────────────────────────────────────────────────
        toolbar = tk.Frame(parent, bg=c["bg"])
        toolbar.pack(fill=tk.X, padx=int(18 * _sf), pady=(int(12 * _sf), int(4 * _sf)))

        self._reg(
            ttk.Button(toolbar, text=self._t("dl.refresh"),
                       command=self._refresh_downloads_list),
            "dl.refresh").pack(side=tk.LEFT, padx=(0, 6))
        self._reg(
            ttk.Button(toolbar, text=self._t("dl.open_folder"),
                       command=self._open_downloads),
            "dl.open_folder").pack(side=tk.LEFT)

        self._dl_count_var = tk.StringVar(value="")
        tk.Label(toolbar, textvariable=self._dl_count_var,
                 font=FONTS["small"], bg=c["bg"],
                 fg=c["text_dim"]).pack(side=tk.RIGHT)

        # ── Downloads table ───────────────────────────────────────────────────
        table_frame = tk.Frame(parent, bg=c["bg"])
        table_frame.pack(fill=tk.BOTH, expand=True,
                         padx=int(18 * _sf), pady=(0, int(14 * _sf)))

        cols = ("name", "platform", "size", "date")
        self._dl_tree = ttk.Treeview(
            table_frame, columns=cols, show="headings",
            selectmode="browse")

        col_conf = [
            ("name",     self._t("dl.col_name"),  400, tk.W),
            ("platform", self._t("dl.col_plat"),  100, tk.CENTER),
            ("size",     self._t("dl.col_size"),   80, tk.E),
            ("date",     self._t("dl.col_date"),  130, tk.CENTER),
        ]
        for cid, heading, width, anchor in col_conf:
            self._dl_tree.heading(cid, text=heading, anchor=anchor)
            self._dl_tree.column(cid, width=int(width * _sf),
                                 minwidth=int(50 * _sf), anchor=anchor)

        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL,
                            command=self._dl_tree.yview)
        self._dl_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._dl_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Right-click context menu
        self._dl_ctx = tk.Menu(self, tearoff=0)
        self._dl_ctx.add_command(label="Open in Explorer",
                                 command=self._dl_ctx_open)
        self._dl_ctx.add_command(label="Delete File",
                                 command=self._dl_ctx_delete)

        self._dl_tree.bind("<Button-3>", self._dl_show_ctx)

        self._dl_empty_lbl = tk.Label(
            table_frame, text=self._t("dl.empty"),
            font=FONTS["small"], bg=c["bg"], fg=c["text_dim"])

        self._refresh_downloads_list()

    def _detect_platform_from_url(self, url: str) -> "str | None":
        _MAP = [
            ("x.com",        "x"),  ("twitter.com", "x"),
            ("bilibili.com", "bilibili"), ("b23.tv", "bilibili"),
            ("douyin.com",   "douyin"),   ("iesdouyin.com", "douyin"),
        ]
        for domain, pid in _MAP:
            if domain in url:
                return pid
        return None

    def _dl_panel_start(self):
        url = self._dl_url_var.get().strip()
        if not url:
            return
        pid = self._detect_platform_from_url(url)
        if pid is None:
            self._dl_detected_var.set("⚠ Unknown platform")
            return
        cfg = PLATFORMS[pid]
        if not Path(cfg["cookies_file"]).exists():
            messagebox.showwarning(
                "No cookies",
                f"Cookies not found for {cfg['label']}.\nGo to Settings → Authentication.")
            return
        self._dl_url_var.set("")
        self._dl_detected_var.set("")
        self._run_single_post(pid, url)

    def _refresh_downloads_list(self):
        if not hasattr(self, "_dl_tree"):
            return
        import datetime as _dt
        tree = self._dl_tree
        tree.delete(*tree.get_children())

        base   = self._get_download_dir()
        _PLAT  = {"x": "X", "bilibili": "Bilibili", "douyin": "Douyin", "url": "—"}
        rows   = []

        if base.exists():
            for f in base.rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in _MEDIA_EXTS:
                    continue
                try:
                    parts = f.relative_to(base).parts
                    plat  = _PLAT.get(parts[0], parts[0]) if parts else "—"
                    size  = f.stat().st_size
                    mtime = f.stat().st_mtime
                    rows.append((f, plat, size, mtime))
                except Exception:
                    continue

        rows.sort(key=lambda r: r[3], reverse=True)

        for f, plat, size, mtime in rows[:500]:
            size_str = (f"{size // (1024*1024)} MB" if size >= 1024*1024
                        else f"{size // 1024} KB")
            date_str = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            iid = tree.insert("", tk.END, values=(f.name, plat, size_str, date_str))
            tree.item(iid, tags=(str(f),))

        count = len(rows)
        self._dl_count_var.set(f"{count} file{'s' if count != 1 else ''}")

        if count == 0:
            self._dl_empty_lbl.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self._dl_empty_lbl.place_forget()

    def _dl_show_ctx(self, event):
        item = self._dl_tree.identify_row(event.y)
        if not item:
            return
        self._dl_tree.selection_set(item)
        try:
            self._dl_ctx.tk_popup(event.x_root, event.y_root)
        finally:
            self._dl_ctx.grab_release()

    def _dl_ctx_open(self):
        sel = self._dl_tree.selection()
        if not sel:
            return
        tags = self._dl_tree.item(sel[0], "tags")
        if tags:
            subprocess.Popen(["explorer", "/select,", tags[0]])

    def _dl_ctx_delete(self):
        sel = self._dl_tree.selection()
        if not sel:
            return
        tags = self._dl_tree.item(sel[0], "tags")
        if not tags:
            return
        fp = Path(tags[0])
        from tkinter import messagebox as _mb
        if _mb.askyesno("Delete", f'Delete "{fp.name}"?', parent=self):
            try:
                fp.unlink()
            except Exception as e:
                _mb.showerror("Error", str(e))
            self._refresh_downloads_list()

    # ── Mode toggle ────────────────────────────────────────────────────────────
    def _set_mode(self, val: str):
        self.mode_var.set(val)
        self._refresh_mode_btns()

    def _refresh_mode_btns(self):
        c       = THEME_COLORS[self._current_theme]
        is_full = self.mode_var.get() == "full"
        labels  = {"update": self._t("mode.update"), "full": self._t("mode.full")}
        row_bg  = str(self._row2b.cget("background")) if hasattr(self, "_row2b") else c["bg"]
        for val, btn in self._mode_btns.items():
            sel = val == self.mode_var.get()
            dot = "●" if sel else "○"
            btn.configure(
                text=f"{dot} {labels[val]}",
                fg=c["text"] if sel else c["status_fg"],
                bg=row_bg,
            )
        for attr in ("_auto_lbl", "_mode_lbl"):
            if hasattr(self, attr):
                getattr(self, attr).configure(bg=row_bg, fg=c["text_dim"])
        if hasattr(self, "_auto_pill"):
            self._auto_pill.configure(bg=row_bg)
        if hasattr(self, "_from_days_frame"):
            self._from_days_frame.configure(bg=row_bg)
            for w in self._from_days_frame.winfo_children():
                if isinstance(w, tk.Label):
                    w.configure(bg=row_bg)
            if is_full:
                self._from_days_frame.pack(side=tk.LEFT)
            else:
                self._from_days_frame.pack_forget()

    def _refresh_from_date(self):
        self._from_days_var.set("0")

    # ── Accounts ────────────────────────────────────────────────────────────────
    def _build_accounts(self, parent):
        import re as _re, http.client, ssl, urllib.parse, json as _json
        c = THEME_COLORS[self._current_theme]

        # ── Top bar ───────────────────────────────────────────────────────────
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, padx=12, pady=(8, 4))
        self._reg(
            ttk.Label(top, text=self._t("accounts.heading"), font=FONTS["heading"]),
            "accounts.heading").pack(side=tk.LEFT)
        ttk.Button(top, text="Manage Creators", style="Secondary.TButton",
                   command=self._show_creator_manager).pack(side=tk.RIGHT)

        # ── Creator list ──────────────────────────────────────────────────────
        self._accounts_border = tk.Frame(parent, bg=c["border"])
        self._accounts_border.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 0))
        container = tk.Frame(self._accounts_border, bg=c["list_bg"])
        container.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self._creators_canvas = tk.Canvas(container, bg=c["list_bg"], highlightthickness=0)
        sb = ttk.Scrollbar(container, command=self._creators_canvas.yview)
        self._creators_canvas.configure(yscrollcommand=sb.set)
        self._creators_canvas.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

        self._creators_inner = tk.Frame(self._creators_canvas, bg=c["list_bg"])
        self._creators_win   = self._creators_canvas.create_window(
            (0, 0), window=self._creators_inner, anchor="nw")
        self._creators_inner.bind(
            "<Configure>",
            lambda e: self._creators_canvas.configure(
                scrollregion=self._creators_canvas.bbox("all")))
        self._creators_canvas.bind(
            "<Configure>",
            lambda e: self._creators_canvas.itemconfig(self._creators_win, width=e.width))

        # ── Bottom input bar ──────────────────────────────────────────────────
        _URL_DETECT = [
            (_re.compile(r"x\.com|twitter\.com"),       "x"),
            (_re.compile(r"bilibili\.com|b23\.tv"),      "bilibili"),
            (_re.compile(r"douyin\.com|iesdouyin\.com"), "douyin"),
        ]

        def _infer_pid(text: str) -> "str | None":
            for pat, pid in _URL_DETECT:
                if pat.search(text):
                    return pid
            if _re.match(r'^@?[A-Za-z0-9_]{1,50}$', text):
                return "x"
            return None

        _pid_state: list = [None]

        bar = tk.Frame(parent, bg=c["panel"], highlightthickness=1,
                       highlightbackground=c["border"])
        bar.pack(fill=tk.X, padx=12, pady=(4, 8))

        _entry_var = tk.StringVar()
        self._bar_entry = ttk.Entry(bar, textvariable=_entry_var, font=FONTS["body"])
        self._bar_entry.pack(side=tk.LEFT, fill=tk.X, expand=True,
                             padx=(8, 6), pady=6, ipady=3)

        _plat_var = tk.StringVar()
        tk.Label(bar, textvariable=_plat_var, font=FONTS["small"],
                 bg=c["panel"], fg=c["text_dim"]).pack(side=tk.LEFT, padx=(0, 6))

        def _on_entry_change(*_):
            pid = _infer_pid(_entry_var.get().strip())
            _pid_state[0] = pid
            _plat_var.set(PLATFORMS[pid]["label"] if pid else "")

        _entry_var.trace_add("write", _on_entry_change)

        def _do_add():
            raw = _entry_var.get().strip()
            pid = _pid_state[0]
            if not raw or pid is None:
                return
            cfg = PLATFORMS[pid]

            def _finish(handle: str, creator_name: str):
                creator = self._store.add_creator(creator_name)
                self._store.add_entry(pid, handle, creator.id)
                _entry_var.set("")
                self._refresh_creator_list()

            if pid == "x":
                handle = raw.lstrip("@")
                _finish(handle, handle)

            elif pid == "bilibili":
                m   = _re.search(r"space\.bilibili\.com/(\d+)", raw)
                uid = m.group(1) if m else raw
                if not uid.isdigit():
                    return
                self._bar_entry.configure(state="disabled")
                self._bar_add_btn.configure(state=tk.DISABLED)

                def _fetch_bl():
                    nick = None
                    try:
                        ctx  = ssl.create_default_context()
                        conn = http.client.HTTPSConnection(
                            "api.bilibili.com", timeout=10, context=ctx)
                        conn.request("GET", f"/x/web-interface/card?mid={uid}",
                                     headers={"User-Agent": "Mozilla/5.0",
                                              "Referer": "https://www.bilibili.com/",
                                              "Accept": "application/json"})
                        data = _json.loads(conn.getresponse().read())
                        conn.close()
                        if data.get("code") == 0:
                            nick = (data.get("data") or {}).get("card", {}).get("name")
                    except Exception:
                        pass
                    def _apply():
                        self._bar_entry.configure(state="normal")
                        self._bar_add_btn.configure(state=tk.NORMAL)
                        name = nick or uid
                        _finish(f"{name}|{uid}", name)
                    self.after(0, _apply)

                threading.Thread(target=_fetch_bl, daemon=True).start()

            elif pid == "douyin":
                m       = _re.search(r"/user/([^/?#]+)", raw)
                sec_uid = m.group(1) if m else raw
                self._bar_entry.configure(state="disabled")
                self._bar_add_btn.configure(state=tk.DISABLED)

                def _fetch_dy():
                    nick = None
                    try:
                        cookies: dict[str, str] = {}
                        cf = Path(cfg["cookies_file"])
                        if cf.exists():
                            for line in cf.read_text(encoding="utf-8").splitlines():
                                if line.startswith("#") or not line.strip():
                                    continue
                                fields = line.split("\t")
                                if len(fields) >= 7:
                                    cookies[fields[5].strip()] = fields[6].strip()
                        params = urllib.parse.urlencode({
                            "sec_user_id": sec_uid, "aid": "6383",
                            "cookie_enabled": "true", "platform": "PC",
                        })
                        ctx  = ssl.create_default_context()
                        conn = http.client.HTTPSConnection(
                            "www.douyin.com", timeout=10, context=ctx)
                        conn.request("GET",
                                     f"/aweme/v1/web/user/profile/other/?{params}",
                                     headers={
                                         "Cookie": "; ".join(
                                             f"{k}={v}" for k, v in cookies.items()),
                                         "User-Agent": "Mozilla/5.0",
                                         "Referer": "https://www.douyin.com/",
                                         "Accept": "application/json",
                                     })
                        data = _json.loads(conn.getresponse().read())
                        conn.close()
                        nick = (data.get("user") or {}).get("nickname") or None
                    except Exception:
                        pass
                    def _apply():
                        self._bar_entry.configure(state="normal")
                        self._bar_add_btn.configure(state=tk.NORMAL)
                        name = nick or sec_uid
                        _finish(f"{name}|{sec_uid}", name)
                    self.after(0, _apply)

                threading.Thread(target=_fetch_dy, daemon=True).start()

        self._bar_add_btn = ttk.Button(bar, text="Add", style="Accent.TButton",
                                       command=_do_add)
        self._bar_add_btn.pack(side=tk.RIGHT, padx=(0, 8), pady=6)

        self._bar_entry.bind("<Return>", lambda _: _do_add())
        bar.after(100, self.focus_set)

        self._refresh_creator_list()

    def _show_creator_manager(self):
        c   = THEME_COLORS[self._current_theme]
        dlg = tk.Toplevel(self)
        dlg.title("Manage Creators")
        dlg.configure(bg=c["bg"])
        dlg.resizable(False, False)
        self._centre_dialog(dlg, 620, 820)
        dlg.transient(self)
        dlg.grab_set()
        dlg.bind("<Button-1>", lambda e: dlg.focus_set())

        # ── Header ────────────────────────────────────────────────────────────
        hdr_f = tk.Frame(dlg, bg=c["bg"])
        hdr_f.pack(fill=tk.X, padx=20, pady=(16, 8))
        tk.Label(hdr_f, text="Creators", font=FONTS["heading"],
                 bg=c["bg"], fg=c["text"]).pack(side=tk.LEFT)
        ttk.Button(hdr_f, text="+ New Creator", style="Secondary.TButton",
                   command=lambda: _add_creator()).pack(side=tk.RIGHT)

        # ── Scrollable list ───────────────────────────────────────────────────
        border = tk.Frame(dlg, bg=c["border"])
        border.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 16))
        canvas = tk.Canvas(border, bg=c["list_bg"], highlightthickness=0)
        sb     = ttk.Scrollbar(border, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1, pady=1)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        inner = tk.Frame(canvas, bg=c["list_bg"])
        win   = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win, width=e.width))
        canvas.bind("<Enter>",
                    lambda e: dlg.bind_all("<MouseWheel>",
                        lambda ev: canvas.yview_scroll(-1*(ev.delta//120), "units")))
        canvas.bind("<Leave>", lambda e: dlg.unbind_all("<MouseWheel>"))

        def _rebuild():
            for w in inner.winfo_children():
                w.destroy()
            for idx, creator in enumerate(self._store.all_creators()):
                _build_row(idx, creator)

        def _build_row(idx: int, creator):
            if idx > 0:
                tk.Frame(inner, bg=c["border"], height=1).pack(fill=tk.X)

            group = tk.Frame(inner, bg=c["list_bg"])
            group.pack(fill=tk.X)

            # ── Creator header row ────────────────────────────────────────────
            row = tk.Frame(group, bg=c["panel"])
            row.pack(fill=tk.X)

            # name — click to edit inline
            name_var = tk.StringVar(value=creator.name)
            name_lbl = tk.Label(row, textvariable=name_var, bg=c["panel"],
                                fg=c["text"], font=(*FONTS["body"], "bold"),
                                anchor="w", cursor="xterm")
            name_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True,
                          padx=(14, 8), pady=8)

            def _start_edit(lbl=name_lbl, var=name_var, cid=creator.id, r=row):
                lbl.pack_forget()
                ent = ttk.Entry(r, textvariable=var, font=FONTS["body"])
                ent.pack(side=tk.LEFT, fill=tk.X, expand=True,
                         padx=(14, 8), pady=5, ipady=3)
                ent.focus_set()
                ent.select_range(0, tk.END)

                def _commit(ev=None):
                    new_name = var.get().strip()
                    if new_name:
                        self._store.rename_creator(cid, new_name)
                    ent.pack_forget()
                    cr = self._store.get_creator(cid)
                    var.set(cr.name if cr else var.get())
                    lbl.pack(side=tk.LEFT, fill=tk.X, expand=True,
                             padx=(14, 8), pady=8)
                    self._refresh_creator_list()

                ent.bind("<Return>",   _commit)
                ent.bind("<FocusOut>", _commit)
                ent.bind("<Escape>",   lambda e: _commit())

            name_lbl.bind("<Button-1>", lambda e: _start_edit())

            def _delete(cid=creator.id, name=creator.name):
                n   = len(self._store.get_entries_for_creator(cid))
                msg = (f'Delete "{name}"?\n\nIts {n} entr{"y" if n==1 else "ies"} '
                       f'will become unassigned.' if n else f'Delete "{name}"?')
                if messagebox.askyesno("Delete Creator", msg, parent=dlg):
                    self._store.remove_creator(cid)
                    _rebuild()
                    self._refresh_creator_list()

            ttk.Button(row, text="Delete", style="Secondary.TButton",
                       command=_delete).pack(side=tk.RIGHT, padx=(0, 14), pady=6)

            # ── Entries under creator ─────────────────────────────────────────
            entries = self._store.get_entries_for_creator(creator.id)
            for entry in entries:
                cfg     = PLATFORMS.get(entry.platform, {})
                display = entry.handle.split("|")[0] if "|" in entry.handle else entry.handle
                erow = tk.Frame(group, bg=c["list_bg"])
                erow.pack(fill=tk.X)
                tk.Frame(erow, bg=c["border"], width=1).pack(side=tk.LEFT, fill=tk.Y)
                plat_lbl = tk.Label(erow, text=cfg.get("label", entry.platform),
                                    bg=c["list_bg"], fg=c["text_dim"],
                                    font=FONTS["small"], width=8, anchor="w")
                plat_lbl.pack(side=tk.LEFT, padx=(10, 6), pady=5)
                tk.Label(erow, text=display, bg=c["list_bg"], fg=c["text"],
                         font=FONTS["body"], anchor="w"
                         ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            if not entries:
                tk.Label(group, text="No accounts", bg=c["list_bg"],
                         fg=c["text_dim"], font=FONTS["small"], anchor="w"
                         ).pack(fill=tk.X, padx=14, pady=4)

        def _add_creator():
            name = simpledialog.askstring("New Creator", "Creator name:",
                                          parent=dlg)
            if name and name.strip():
                self._store.add_creator(name.strip())
                _rebuild()
                self._refresh_creator_list()

        _rebuild()

    def _refresh_creator_list(self):
        if self._creators_inner is None:
            return
        for w in self._creators_inner.winfo_children():
            w.destroy()
        self._drag_headers    = []
        self._drag_hdr_colors = {}
        self._drag_active_hdr = None
        c        = THEME_COLORS[self._current_theme]
        creators = self._store.all_creators()
        entries  = self._store.all_entries()

        if not entries:
            tk.Label(self._creators_inner,
                     text=self._t("creator.empty_hint"),
                     bg=c["list_bg"], fg=c["text_dim"],
                     font=FONTS["small"]).pack(padx=12, pady=20)
            return

        # ── Creator groups ────────────────────────────────────────────────────
        for creator in creators:
            c_entries = self._store.get_entries_for_creator(creator.id)
            self._render_creator_section(creator, c_entries, self._creators_inner)

        # ── Unassigned entries ────────────────────────────────────────────────
        unassigned = self._store.get_unassigned_entries()
        if unassigned:
            self._render_unassigned_section(unassigned, self._creators_inner)

    # ── Hover helper ───────────────────────────────────────────────────────────
    def _bind_row_hover(self, root_frame: tk.Frame,
                        bg_widgets: list, on_enter, on_leave):
        """Bind enter/leave to root_frame and all its children.
        on_leave fires only when cursor genuinely leaves root_frame."""
        def _leave(event):
            w = self.winfo_containing(event.x_root, event.y_root)
            node = w
            while node is not None:
                if node is root_frame:
                    return   # still inside — child-to-child transition
                node = getattr(node, "master", None)
            on_leave()
        root_frame.bind("<Enter>", lambda e: on_enter())
        root_frame.bind("<Leave>", _leave)
        for child in bg_widgets:
            child.bind("<Enter>", lambda e: on_enter())
            child.bind("<Leave>", _leave)

    def _render_creator_section(self, creator, entries, parent):
        c = THEME_COLORS[self._current_theme]

        # Single-entry creators: bordered card, no group header
        if len(entries) == 1:
            group = tk.Frame(parent, bg=c["border"])
            group.pack(fill=tk.X, padx=8, pady=(16, 8))
            gf = tk.Frame(group, bg=c["list_bg"])
            gf.pack(fill=tk.X, padx=2, pady=2)
            self._render_entry_row(entries[0], creator.id, gf)
            return

        # Bordered group container
        group = tk.Frame(parent, bg=c["border"],
                         highlightthickness=0)
        group.pack(fill=tk.X, padx=8, pady=(16, 8))
        inner = tk.Frame(group, bg=c["panel"])
        inner.pack(fill=tk.X, padx=2, pady=2)

        hdr = tk.Frame(inner, bg=c["panel"])
        hdr.pack(fill=tk.X)

        # Thick accent bar
        tk.Frame(hdr, bg=c["accent"], width=4).pack(side=tk.LEFT, fill=tk.Y)

        name_lbl = tk.Label(hdr, text=creator.name,
                            bg=c["panel"], fg=c["text"],
                            font=FONTS["heading"], anchor="w")
        name_lbl.pack(side=tk.LEFT, padx=(10, 4), pady=6, fill=tk.X, expand=True)

        # ⋯ — invisible until hover
        more = tk.Label(hdr, text="⋯", cursor="hand2",
                        bg=c["panel"], fg=c["panel"],
                        font=FONTS["body"])
        more.pack(side=tk.RIGHT, padx=(0, 10))

        def _rename():
            self._rename_creator_dialog(creator.id)

        def _delete():
            if messagebox.askyesno(
                    "Delete Group",
                    f'Remove "{creator.name}"?\n\nIts entries become unassigned.',
                    parent=self):
                self._store.remove_creator(creator.id)
                self._refresh_creator_list()

        def _show_ctx(x, y):
            m = tk.Menu(self, tearoff=0)
            m.add_command(label="Rename", command=_rename)
            m.add_separator()
            m.add_command(label="Delete group", command=_delete)
            try:
                m.tk_popup(x, y)
            finally:
                m.grab_release()

        def _enter():
            for w in (hdr, name_lbl):
                w.configure(bg=c["hover"])
            more.configure(bg=c["hover"], fg=c["text_dim"])

        def _leave():
            for w in (hdr, name_lbl):
                w.configure(bg=c["panel"])
            more.configure(bg=c["panel"], fg=c["panel"])

        self._bind_row_hover(hdr, [name_lbl, more], _enter, _leave)
        for w in (hdr, name_lbl, more):
            w.bind("<Button-3>", lambda e: _show_ctx(e.x_root, e.y_root))
        more.bind("<Button-1>", lambda e: _show_ctx(e.x_root, e.y_root))

        self._drag_headers.append((hdr, creator.id))
        self._drag_hdr_colors[id(hdr)] = c["panel"]

        if entries:
            gf = tk.Frame(inner, bg=c["list_bg"])
            gf.pack(fill=tk.X, padx=0, pady=(0, 0))
            for entry in entries:
                self._render_entry_row(entry, creator.id, gf)
        else:
            # Empty group — dim placeholder, still a valid drag target
            tk.Label(inner,
                     text="No entries — drag one here",
                     bg=c["list_bg"], fg=c["text_dim"],
                     font=FONTS["small"], anchor="w"
                     ).pack(fill=tk.X, padx=(24, 0), pady=4)

    def _render_unassigned_section(self, entries, parent):
        c = THEME_COLORS[self._current_theme]

        hdr = tk.Frame(parent, bg=c["panel"])
        hdr.pack(fill=tk.X, padx=0, pady=(4, 0))
        tk.Frame(hdr, bg=c["border"], width=2).pack(side=tk.LEFT, fill=tk.Y)
        lbl = tk.Label(hdr, text=self._t("creator.unassigned"),
                       bg=c["panel"], fg=c["text_dim"],
                       font=(*FONTS["small"], "bold"), anchor="w")
        lbl.pack(side=tk.LEFT, padx=(8, 0), pady=3)

        self._drag_headers.append((hdr, None))
        self._drag_hdr_colors[id(hdr)] = c["panel"]

        gf = tk.Frame(parent, bg=c["list_bg"])
        gf.pack(fill=tk.X, padx=8, pady=(2, 4))
        for entry in entries:
            self._render_entry_row(entry, None, gf)

    def _render_entry_row(self, entry, current_creator_id, parent):
        c   = THEME_COLORS[self._current_theme]
        cfg = PLATFORMS.get(entry.platform, {})
        display = entry.handle.split("|")[0] if "|" in entry.handle else entry.handle

        card = tk.Frame(parent, bg=c["list_bg"], cursor="fleur",
                        highlightthickness=0)
        card.pack(fill=tk.X, padx=0, pady=0)

        # ── Layout: icon left (full height) | platform + name right ─────
        body = tk.Frame(card, bg=c["list_bg"], cursor="fleur")
        body.pack(fill=tk.X, padx=8, pady=8)

        img_icon = getattr(self, "_platform_icons_lg", {}).get(entry.platform)
        if img_icon:
            pill_bg = cfg.get("icon_bg", cfg.get("color", c["border"]))
            icon_w = tk.Label(body, image=img_icon, bg=pill_bg,
                              bd=0, highlightthickness=0, cursor="fleur",
                              padx=4, pady=4)
        else:
            pill_bg = cfg.get("color", c["border"])
            icon_w = tk.Label(body, text=f" {cfg.get('icon', entry.platform)} ",
                              bg=pill_bg, fg="#ffffff",
                              font=FONTS["heading"], cursor="fleur")
        icon_w.pack(side=tk.LEFT, padx=(0, 10), anchor="center")

        # Right column: platform label + name
        right = tk.Frame(body, bg=c["list_bg"], cursor="fleur")
        right.pack(side=tk.LEFT, fill=tk.X, expand=True)

        top = tk.Frame(right, bg=c["list_bg"], cursor="fleur")
        top.pack(fill=tk.X)

        plat_lbl = tk.Label(top, text=cfg.get("label", entry.platform),
                            bg=c["list_bg"], fg=c["text_dim"],
                            font=FONTS["small"], anchor="w", cursor="fleur")
        plat_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        more = tk.Label(top, text="⋯", cursor="hand2",
                        bg=c["list_bg"], fg=c["list_bg"], font=FONTS["small"])
        more.pack(side=tk.RIGHT)

        name_lbl = tk.Label(right, text=display,
                            bg=c["list_bg"], fg=c["text"],
                            font=FONTS["body"], anchor="w", cursor="fleur")
        name_lbl.pack(fill=tk.X)

        # Context menu
        def _remove():
            if messagebox.askyesno("Remove",
                                   f'Remove "{display}" from tracking?',
                                   parent=self):
                self._store.remove_entry(entry.id)
                self._refresh_creator_list()

        def _create_creator_for_entry():
            new_c = self._store.add_creator(display)
            self._store.assign_entry(entry.id, new_c.id)
            self._refresh_creator_list()

        def _show_ctx(x, y):
            m = tk.Menu(self, tearoff=0)
            creators = self._store.all_creators()
            if creators:
                sub = tk.Menu(m, tearoff=0)
                for cr in creators:
                    sub.add_command(
                        label=cr.name,
                        command=lambda cid=cr.id: (
                            self._store.assign_entry(entry.id, cid),
                            self._refresh_creator_list()))
                if current_creator_id is not None:
                    sub.add_separator()
                    sub.add_command(
                        label=self._t("creator.unassigned"),
                        command=lambda: (
                            self._store.assign_entry(entry.id, None),
                            self._refresh_creator_list()))
                m.add_cascade(label="Move to", menu=sub)
                m.add_separator()
            m.add_command(label=f'Create creator "{display}"',
                          command=_create_creator_for_entry)
            m.add_separator()
            m.add_command(label="Remove", command=_remove)
            try:
                m.tk_popup(x, y)
            finally:
                m.grab_release()

        # Hover
        bg_ws = [card, body, top, right, plat_lbl, name_lbl, more]

        def _enter():
            for w in bg_ws:
                w.configure(bg=c["hover"])
            more.configure(fg=c["text_dim"])

        def _leave():
            for w in bg_ws:
                w.configure(bg=c["list_bg"])
            more.configure(fg=c["list_bg"])

        self._bind_row_hover(card, [body, top, right, icon_w, plat_lbl, name_lbl, more],
                             _enter, _leave)
        for w in (card, body, top, right, icon_w, plat_lbl, name_lbl, more):
            w.bind("<Button-3>", lambda e: _show_ctx(e.x_root, e.y_root))
        more.bind("<Button-1>", lambda e: _show_ctx(e.x_root, e.y_root))

        # Drag bindings
        for w in (card, body, top, right, icon_w, plat_lbl, name_lbl):
            w.bind("<ButtonPress-1>",
                   lambda e, eid=entry.id, d=display: self._drag_press(e, eid, d))
            w.bind("<B1-Motion>",
                   lambda e, eid=entry.id, d=display: self._drag_motion(e, eid, d))
            w.bind("<ButtonRelease-1>",
                   lambda e, eid=entry.id: self._drag_release(e, eid))

    def _refresh_creator_list_theme(self):
        if self._creators_canvas is None:
            return
        c = THEME_COLORS[self._current_theme]
        self._creators_canvas.configure(bg=c["list_bg"])
        self._creators_inner.configure(bg=c["list_bg"])
        if hasattr(self, "_accounts_border"):
            self._accounts_border.configure(bg=c["border"])
        self._refresh_creator_list()

    # ── Drag-and-drop ──────────────────────────────────────────────────────────
    def _drag_press(self, event, entry_id: str, display: str):
        self._drag_entry_id  = entry_id
        self._drag_display   = display
        self._drag_start_xy  = (event.x_root, event.y_root)
        self._drag_ghost     = None
        self._drag_target_id = _DRAG_NONE

    def _drag_motion(self, event, entry_id: str, display: str):
        if self._drag_entry_id != entry_id:
            return
        dx = abs(event.x_root - self._drag_start_xy[0])
        dy = abs(event.y_root - self._drag_start_xy[1])
        if dx < 5 and dy < 5:
            return  # not yet a drag — wait for threshold

        if self._drag_ghost is None:
            self._drag_create_ghost(display)

        if self._drag_ghost:
            self._drag_ghost.geometry(
                f"+{event.x_root + 14}+{event.y_root + 6}")

        self._drag_update_target(event.x_root, event.y_root)

    def _drag_release(self, event, entry_id: str):
        if self._drag_entry_id != entry_id:
            return

        # Tear down ghost
        if self._drag_ghost:
            try:
                self._drag_ghost.destroy()
            except tk.TclError:
                pass
            self._drag_ghost = None

        self._drag_clear_highlight()

        target = self._drag_target_id
        self._drag_entry_id  = None
        self._drag_target_id = _DRAG_NONE

        if target is _DRAG_NONE:
            return  # never dragged far enough — treat as click, do nothing

        entry = self._store.get_entry(entry_id)
        if entry is None:
            return
        if entry.creator_id == target:
            return  # dropped on own section — no-op

        self._store.assign_entry(entry_id, target)
        self._refresh_creator_list()

    def _drag_create_ghost(self, display: str):
        c = THEME_COLORS[self._current_theme]
        ghost = tk.Toplevel(self)
        ghost.overrideredirect(True)
        ghost.attributes("-topmost", True)
        try:
            ghost.attributes("-alpha", 0.88)
        except tk.TclError:
            pass
        frame = tk.Frame(ghost, bg=c["accent"], padx=10, pady=5)
        frame.pack()
        tk.Label(frame, text=f"  {display}  ",
                 bg=c["accent"], fg="#ffffff",
                 font=FONTS["body"]).pack()
        self._drag_ghost = ghost

    def _drag_update_target(self, x_root: int, y_root: int):
        found_hdr = None
        found_cid = _DRAG_NONE

        widget = self.winfo_containing(x_root, y_root)
        while widget is not None and widget is not self:
            for hdr, cid in self._drag_headers:
                if widget is hdr:
                    found_hdr = hdr
                    found_cid = cid
                    break
            if found_hdr is not None:
                break
            widget = getattr(widget, "master", None)

        if found_hdr is self._drag_active_hdr:
            self._drag_target_id = found_cid
            return  # same header — nothing changed

        self._drag_clear_highlight()

        if found_hdr is not None:
            c = THEME_COLORS[self._current_theme]
            found_hdr.configure(bg=c["accent"])
            for child in found_hdr.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=c["accent"], fg="#ffffff")
                elif isinstance(child, tk.Frame):
                    child.configure(bg=c["accent"])
            self._drag_active_hdr = found_hdr

        self._drag_target_id = found_cid

    def _drag_clear_highlight(self):
        hdr = self._drag_active_hdr
        if hdr is None:
            return
        c   = THEME_COLORS[self._current_theme]
        orig = self._drag_hdr_colors.get(id(hdr), c["panel"])
        # Determine whether this is a named-creator header (accent bar) or unassigned
        is_unassigned = any(
            cid is None and hdr is h for h, cid in self._drag_headers)
        text_fg = c["text_dim"] if is_unassigned else c["text"]
        try:
            hdr.configure(bg=orig)
            for child in hdr.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=orig, fg=text_fg)
                elif isinstance(child, tk.Frame):
                    child.configure(bg=orig)
        except tk.TclError:
            pass
        self._drag_active_hdr = None

    def _hide_accounts_form(self):
        pass  # no longer used; kept for any stale call sites

    def _show_add_creator_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("New Creator")
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        self._centre_dialog(dlg, 320, 118)
        outer = ttk.Frame(dlg, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)
        var = tk.StringVar()
        entry = ttk.Entry(outer, textvariable=var, font=FONTS["body"])
        entry.pack(fill=tk.X, ipady=3, pady=(0, 10))
        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X)
        def _create():
            name = var.get().strip()
            if not name:
                return
            self._store.add_creator(name)
            dlg.destroy()
            self._refresh_creator_list()
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(
            side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_row, text="Create", style="Accent.TButton",
                   command=_create).pack(side=tk.RIGHT)
        entry.bind("<Return>", lambda _: _create())
        entry.bind("<Escape>", lambda _: dlg.destroy())
        entry.focus_set()

    def _rename_creator_dialog(self, creator_id: str):
        creator = self._store.get_creator(creator_id)
        if not creator:
            return
        dlg = tk.Toplevel(self)
        dlg.title("Rename Creator")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        self._centre_dialog(dlg, 520, 150)

        outer = ttk.Frame(dlg, padding=24)
        outer.pack(fill=tk.BOTH, expand=True)
        name_var = tk.StringVar(value=creator.name)
        entry = ttk.Entry(outer, textvariable=name_var, font=FONTS["body"])
        entry.pack(fill=tk.X, pady=(0, 12))
        dlg.after(1, lambda: (entry.focus_set(), entry.selection_clear(), entry.icursor(tk.END)))

        def _apply():
            n = name_var.get().strip()
            if n:
                self._store.rename_creator(creator_id, n)
                dlg.destroy()
                self._refresh_creator_list()

        entry.bind("<Return>", lambda _: _apply())
        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_row, text="Rename", command=_apply).pack(side=tk.RIGHT)

    def _assign_entry_dialog(self, entry_id: str, display: str):
        """Show a popup to move an entry to a creator (or unassign it)."""
        creators = self._store.all_creators()
        if not creators:
            messagebox.showinfo("No Creators",
                                'No creators exist yet. Create one first with "+ Add Creator".',
                                parent=self)
            return

        dlg = tk.Toplevel(self)
        dlg.title(f'Assign "{display}"')
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        self._centre_dialog(dlg, 380, min(100 + len(creators) * 36 + 60, 600))

        outer = ttk.Frame(dlg, padding=16)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(outer, text=f'Move  "{display}"  to:',
                  font=FONTS["body"]).pack(anchor="w", pady=(0, 8))

        canvas = tk.Canvas(outer, highlightthickness=0,
                           height=min(len(creators) * 36, 400))
        sb = ttk.Scrollbar(outer, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = tk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        def _pick(cid):
            self._store.assign_entry(entry_id, cid)
            dlg.destroy()
            self._refresh_creator_list()

        def _unassign():
            self._store.assign_entry(entry_id, None)
            dlg.destroy()
            self._refresh_creator_list()

        for creator in creators:
            ttk.Button(inner, text=creator.name,
                       command=lambda cid=creator.id: _pick(cid)
                       ).pack(fill=tk.X, pady=1)

        ttk.Separator(outer, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)
        ttk.Button(outer, text="Unassign (move to Unassigned)",
                   command=_unassign).pack(fill=tk.X)

    def _show_add_entry_form(self):
        pass  # replaced by the persistent bottom bar in _build_accounts
        return
        import re as _re, http.client, ssl, urllib.parse, json as _json
        c   = THEME_COLORS[self._current_theme]
        frm = None  # dead code below kept for reference

        # ── Row 1: handle + platform badge + creator picker + buttons ─────────
        row1 = tk.Frame(frm, bg=c["panel"])
        row1.pack(fill=tk.X, padx=10, pady=(8, 2))

        handle_var   = tk.StringVar()
        handle_entry = ttk.Entry(row1, textvariable=handle_var, font=FONTS["body"])
        handle_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3, padx=(0, 6))

        detected_var = tk.StringVar(value="")
        ttk.Label(row1, textvariable=detected_var,
                  font=(*FONTS["small"], "bold"),
                  foreground=c["accent"]).pack(side=tk.LEFT, padx=(0, 6))

        _UNASSIGNED = "— Unassigned —"
        creators    = self._store.all_creators()
        cb_values   = [_UNASSIGNED] + [cr.name for cr in creators]
        creator_var = tk.StringVar(value=_UNASSIGNED)
        creator_cb  = ttk.Combobox(row1, textvariable=creator_var,
                                   values=cb_values, state="readonly",
                                   font=FONTS["body"], width=14)
        creator_cb.pack(side=tk.LEFT, padx=(0, 6))

        add_btn = ttk.Button(row1, text="Add", style="Accent.TButton")
        add_btn.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row1, text="✕", width=2,
                   command=self._hide_accounts_form).pack(side=tk.LEFT)

        # ── Row 2: status hint ────────────────────────────────────────────────
        row2 = tk.Frame(frm, bg=c["panel"])
        row2.pack(fill=tk.X, padx=10, pady=(0, 8))
        status_var = tk.StringVar(
            value="Paste a URL from X, Bilibili, or Douyin — or type an X username")
        ttk.Label(row2, textvariable=status_var,
                  font=FONTS["small"], foreground="#888888").pack(side=tk.LEFT)

        # ── Platform inference ────────────────────────────────────────────────
        _URL_DETECT = [
            (_re.compile(r"x\.com|twitter\.com"),       "x"),
            (_re.compile(r"bilibili\.com|b23\.tv"),      "bilibili"),
            (_re.compile(r"douyin\.com|iesdouyin\.com"), "douyin"),
        ]
        _pid_state: list = [None]

        def _infer_pid(text: str) -> "str | None":
            for pat, pid in _URL_DETECT:
                if pat.search(text):
                    return pid
            if _re.match(r'^@?[A-Za-z0-9_]{1,50}$', text):
                return "x"
            return None

        def _on_change(*_):
            raw = handle_var.get().strip()
            pid = _infer_pid(raw)
            _pid_state[0] = pid
            detected_var.set(PLATFORMS[pid]["label"] if pid else "")
            if pid:
                status_var.set({
                    "x":        self._t("entry.hint_x"),
                    "bilibili": self._t("entry.hint_bilibili"),
                    "douyin":   self._t("entry.hint_douyin"),
                }.get(pid, ""))
            else:
                status_var.set(
                    "Paste a URL from X, Bilibili, or Douyin — or type an X username")

        handle_var.trace_add("write", _on_change)

        # ── Add logic ─────────────────────────────────────────────────────────
        def _resolve_cid() -> "str | None":
            sel = creator_var.get()
            if sel == _UNASSIGNED:
                return None
            for cr in creators:
                if cr.name == sel:
                    return cr.id
            return None

        def _finish(handle: str):
            self._store.add_entry(_pid_state[0], handle, _resolve_cid())
            self._hide_accounts_form()
            self._refresh_creator_list()

        def _add():
            raw = handle_var.get().strip()
            pid = _pid_state[0]
            if not raw:
                return
            if pid is None:
                status_var.set("⚠  Could not detect platform — paste a full URL.")
                return
            cfg = PLATFORMS[pid]

            if pid == "x":
                _finish(raw.lstrip("@"))

            elif pid == "bilibili":
                m   = _re.search(r"space\.bilibili\.com/(\d+)", raw)
                uid = m.group(1) if m else raw
                if not uid.isdigit():
                    status_var.set("⚠  Enter a UID or bilibili space URL.")
                    return
                status_var.set("Resolving name…")
                add_btn.configure(state=tk.DISABLED)
                handle_entry.configure(state="disabled")

                def _fetch_bl():
                    nick = None; error = None
                    try:
                        ctx  = ssl.create_default_context()
                        conn = http.client.HTTPSConnection(
                            "api.bilibili.com", timeout=10, context=ctx)
                        conn.request("GET", f"/x/web-interface/card?mid={uid}",
                                     headers={"User-Agent": "Mozilla/5.0",
                                              "Referer": "https://www.bilibili.com/",
                                              "Accept": "application/json"})
                        data = _json.loads(conn.getresponse().read())
                        conn.close()
                        if data.get("code") != 0:
                            error = f"UID not found (code {data.get('code')})."
                        else:
                            nick = (data.get("data") or {}).get("card", {}).get("name")
                    except Exception as exc:
                        error = str(exc)

                    def _apply():
                        handle_entry.configure(state="normal")
                        add_btn.configure(state=tk.NORMAL)
                        if error:
                            status_var.set(f"⚠  {error}")
                        else:
                            _finish(f"{nick or uid}|{uid}")
                    self.after(0, _apply)

                threading.Thread(target=_fetch_bl, daemon=True).start()

            elif pid == "douyin":
                m       = _re.search(r"/user/([^/?#]+)", raw)
                sec_uid = m.group(1) if m else raw
                status_var.set("Resolving name…")
                add_btn.configure(state=tk.DISABLED)
                handle_entry.configure(state="disabled")

                def _fetch_dy():
                    nick = None
                    try:
                        cookies: dict[str, str] = {}
                        cf = Path(cfg["cookies_file"])
                        if cf.exists():
                            for line in cf.read_text(encoding="utf-8").splitlines():
                                if line.startswith("#") or not line.strip():
                                    continue
                                fields = line.split("\t")
                                if len(fields) >= 7:
                                    cookies[fields[5].strip()] = fields[6].strip()
                        params = urllib.parse.urlencode({
                            "sec_user_id": sec_uid, "aid": "6383",
                            "cookie_enabled": "true", "platform": "PC",
                        })
                        ctx  = ssl.create_default_context()
                        conn = http.client.HTTPSConnection(
                            "www.douyin.com", timeout=10, context=ctx)
                        conn.request("GET",
                                     f"/aweme/v1/web/user/profile/other/?{params}",
                                     headers={
                                         "Cookie": "; ".join(
                                             f"{k}={v}" for k, v in cookies.items()),
                                         "User-Agent": "Mozilla/5.0",
                                         "Referer": "https://www.douyin.com/",
                                         "Accept": "application/json",
                                     })
                        data = _json.loads(conn.getresponse().read())
                        conn.close()
                        nick = (data.get("user") or {}).get("nickname") or None
                    except Exception:
                        pass

                    def _apply():
                        handle_entry.configure(state="normal")
                        add_btn.configure(state=tk.NORMAL)
                        _finish(f"{nick or sec_uid}|{sec_uid}")
                    self.after(0, _apply)

                threading.Thread(target=_fetch_dy, daemon=True).start()

        add_btn.configure(command=_add)
        handle_entry.bind("<Return>", lambda _: _add())
        handle_entry.bind("<Escape>", lambda _: self._hide_accounts_form())

        frm.pack(fill=tk.X, padx=12, pady=(0, 4))
        handle_entry.focus_set()

    # ── Settings ───────────────────────────────────────────────────────────────
    def _build_settings(self, parent):
        c      = THEME_COLORS[self._current_theme]
        canvas = tk.Canvas(parent, highlightthickness=0, bg=c["bg"])
        sb     = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._settings_canvas = canvas

        outer = tk.Frame(canvas, bg=c["bg"])
        win   = canvas.create_window((0, 0), window=outer, anchor="nw")

        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        outer.bind("<Configure>",  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        PAD     = 24   # horizontal page margin
        LBL_W   = 168  # fixed label column width
        ROW_PAD = 4    # vertical padding per row

        # ── Helpers ───────────────────────────────────────────────────────────

        def _card(title, hint=None):
            wrapper = tk.Frame(outer, bg=c["border"])
            wrapper.pack(fill=tk.X, padx=PAD, pady=(0, 8))
            body = tk.Frame(wrapper, bg=c["panel"])
            body.pack(fill=tk.X, padx=1, pady=1)
            # header
            tk.Label(body, text=title, font=FONTS["heading"],
                     bg=c["panel"], fg=c["text"]
                     ).pack(anchor="w", padx=16, pady=(10, 0))
            tk.Frame(body, bg=c["border"], height=1).pack(fill=tk.X, pady=(8, 0))
            if hint:
                tk.Label(body, text=hint, font=FONTS["small"],
                         bg=c["panel"], fg=c["text_dim"],
                         wraplength=540, justify="left",
                         ).pack(anchor="w", padx=16, pady=(6, 0))
            content = tk.Frame(body, bg=c["panel"])
            content.pack(fill=tk.X, padx=16, pady=(6, 10))
            content.columnconfigure(0, minsize=LBL_W, weight=0)
            content.columnconfigure(1, weight=0)   # inputs don't expand by default
            content.columnconfigure(2, weight=1)   # spacer
            content.columnconfigure(3, weight=0)   # action
            return content

        def _lbl(parent, text, key=None):
            w = tk.Label(parent, text=text, font=FONTS["body"],
                         bg=c["panel"], fg=c["text"], anchor="w")
            if key:
                self._reg(w, key)
            return w

        def _row(parent, row, label, ctrl, action=None, label_key=None):
            """col0=label, col1=control, col2=spacer, col3=action"""
            (label if isinstance(label, tk.Widget)
             else _lbl(parent, label, label_key)
             ).grid(row=row, column=0, sticky="w", pady=ROW_PAD)
            ctrl.grid(row=row, column=1, sticky="w", padx=(14, 0), pady=ROW_PAD)
            if action:
                action.grid(row=row, column=3, sticky="e", pady=ROW_PAD)

        def _sep(parent, row):
            tk.Frame(parent, bg=c["border"], height=1).grid(
                row=row, column=0, columnspan=4, sticky="ew", pady=(3, 3))

        def _spinbox(parent, attr, default, lo, hi, increment=1, w=6):
            saved = self._load_setting(attr, default)
            sp = ttk.Spinbox(parent, from_=lo, to=hi, increment=increment,
                             width=w, font=FONTS["body"])
            sp.set(saved)
            sp.bind("<<Increment>>", lambda _e, a=attr, s=sp: self._save_setting(a, s.get()))
            sp.bind("<<Decrement>>", lambda _e, a=attr, s=sp: self._save_setting(a, s.get()))
            sp.bind("<FocusOut>",    lambda _e, a=attr, s=sp: self._save_setting(a, s.get()))
            sp.bind("<MouseWheel>",  lambda e: "break")
            sp.bind("<FocusIn>",     lambda _e, s=sp: s.after(50, s.selection_clear))
            return sp

        # top margin
        tk.Frame(outer, bg=c["bg"], height=14).pack()

        # ── Authentication ────────────────────────────────────────────────────
        ac = _card("Authentication", hint=self._t("settings.auth_hint"))

        for i, (pid, cfg) in enumerate(PLATFORMS.items()):
            if i > 0:
                _sep(ac, i * 2 - 1)

            cookies_path = Path(cfg["cookies_file"])
            if cookies_path.exists():
                st_text  = f"✓  {cookies_path.name}  ({cookies_path.stat().st_size // 1024} KB)"
                st_color = "#4ec94e"
            else:
                st_text  = "✗  not found"
                st_color = "#ff6b6b"

            st_var = tk.StringVar(value=st_text)
            self._cookie_status[pid] = st_var
            st_lbl = tk.Label(ac, textvariable=st_var, font=FONTS["small"],
                              fg=st_color, bg=c["panel"], anchor="w")

            def _update_st(p=cookies_path, v=st_var, l=st_lbl):
                if p.exists():
                    v.set(f"✓  {p.name}  ({p.stat().st_size // 1024} KB)")
                    l.configure(fg="#4ec94e")
                else:
                    v.set("✗  not found")
                    l.configure(fg="#ff6b6b")

            plat_lbl = tk.Label(ac, text=cfg["label"], font=(*FONTS["body"], "bold"),
                                bg=c["panel"], fg=c["text"], anchor="w")
            import_btn = ttk.Button(ac, text="Import", style="Secondary.TButton",
                                    command=lambda p=pid, u=_update_st:
                                        (self._browse_cookies(p), u()))
            plat_lbl.grid(row=i * 2, column=0, sticky="w", pady=ROW_PAD)
            st_lbl.grid(   row=i * 2, column=1, columnspan=2, sticky="w",
                           padx=(14, 0), pady=ROW_PAD)
            import_btn.grid(row=i * 2, column=3, sticky="e", pady=ROW_PAD)

        # ── Download Settings ─────────────────────────────────────────────────
        dc = _card("Download Settings")

        workers_sp = _spinbox(dc, "parallel_workers", 1, 1, 10, w=6)
        setattr(self, "parallel_workers", workers_sp)
        _row(dc, 0, self._t("settings.workers"), workers_sp, label_key="settings.workers")

        _sep(dc, 1)

        # Download location — entry expands full width across cols 1-3
        self._dl_path_var = tk.StringVar(value=str(self._get_download_dir()))
        loc_lbl = _lbl(dc, self._t("settings.dl_location"), "settings.dl_location")
        loc_lbl.grid(row=2, column=0, sticky="w", pady=ROW_PAD)

        loc_entry = ttk.Entry(dc, textvariable=self._dl_path_var, font=FONTS["body"])
        loc_entry.grid(row=2, column=1, columnspan=2, sticky="ew",
                       padx=(14, 6), pady=ROW_PAD)
        dc.columnconfigure(2, weight=1)

        def _browse_dl():
            folder = filedialog.askdirectory(
                title="Select download folder",
                initialdir=str(self._get_download_dir()),
            )
            if folder:
                self._dl_path_var.set(folder)
                Path(DOWNLOAD_PATH_FILE).parent.mkdir(parents=True, exist_ok=True)
                Path(DOWNLOAD_PATH_FILE).write_text(folder, encoding="utf-8")

        self._reg(ttk.Button(dc, text=self._t("btn.browse"),
                             style="Secondary.TButton", command=_browse_dl),
                  "btn.browse").grid(row=2, column=3, sticky="e", pady=ROW_PAD)

        # ── Appearance ────────────────────────────────────────────────────────
        apc = _card(self._t("settings.appearance"))

        # Theme: label | current state (dim) | action button
        def _theme_state_str():
            return "Dark mode" if self._current_theme == "dark" else "Light mode"

        theme_state_var = tk.StringVar(value=_theme_state_str())
        theme_state_lbl = tk.Label(apc, textvariable=theme_state_var,
                                   font=FONTS["body"], bg=c["panel"], fg=c["text_dim"],
                                   anchor="w")
        self._theme_toggle_btn = ttk.Button(
            apc,
            text=self._t("btn.switch_light") if self._current_theme == "dark"
                 else self._t("btn.switch_dark"),
            style="Secondary.TButton",
            command=lambda: (self._toggle_theme(),
                             theme_state_var.set(_theme_state_str())),
        )
        _lbl(apc, self._t("label.theme"), "label.theme").grid(
            row=0, column=0, sticky="w", pady=ROW_PAD)
        theme_state_lbl.grid(row=0, column=1, sticky="w", padx=(14, 0), pady=ROW_PAD)
        self._theme_toggle_btn.grid(row=0, column=3, sticky="e", pady=ROW_PAD)

        lang_cb = ttk.Combobox(apc, state="readonly", width=14,
                               font=FONTS["body"], values=["English", "中文"])
        lang_cb.set("English" if self._lang == "en" else "中文")
        lang_cb.bind("<<ComboboxSelected>>",
                     lambda _e: self._set_lang("en" if lang_cb.get() == "English" else "zh"))
        lang_cb.bind("<MouseWheel>", lambda e: "break")
        lang_cb.bind("<<ComboboxSelected>>",
                     lambda _e: lang_cb.after(50, lang_cb.selection_clear), add="+")
        _row(apc, 1, self._t("label.language"), lang_cb, label_key="label.language")

        # ── Database ──────────────────────────────────────────────────────────
        dbc = _card(self._t("settings.database"))

        db_hint = _lbl(dbc, self._t("settings.db_hint"))
        db_hint.configure(font=FONTS["small"], fg=c["text_dim"])
        self._reg(db_hint, "settings.db_hint")
        db_hint.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        self._reg(
            ttk.Button(dbc, text=self._t("btn.reset_db"),
                       style="Danger.TButton", command=self._reset_db),
            "btn.reset_db").grid(row=1, column=0, sticky="w", pady=(0, 2))

        # ── About (dim footer) ────────────────────────────────────────────────
        import webbrowser as _wb
        about_f = tk.Frame(outer, bg=c["bg"])
        about_f.pack(fill=tk.X, padx=PAD, pady=(0, 24))
        tk.Label(about_f,
                 text=f"Archiver  v{APP_VERSION}  ·  gallery-dl · yt-dlp · f2",
                 font=FONTS["small"], bg=c["bg"], fg=c["text_dim"]).pack(side=tk.LEFT)
        self._reg(
            ttk.Button(about_f, text=self._t("btn.github"), style="Secondary.TButton",
                       command=lambda: _wb.open(
                           "https://github.com/GH-Acho177/media-downloader")),
            "btn.github").pack(side=tk.RIGHT)

    def _build_about_panel(self, parent):
        import webbrowser

        parent.pack_propagate(False)
        # Fill the whole panel with a ttk.Frame so the sv_ttk background is
        # uniform — avoids the colour mismatch between tk.Frame and ttk widgets.
        bg = ttk.Frame(parent)
        bg.pack(fill=tk.BOTH, expand=True)

        f = ttk.Frame(bg, padding=(40, 32))
        f.place(relx=0.5, rely=0.38, anchor="center")

        ttk.Label(f, text="Archiver", font=FONTS["title"]).pack()
        ttk.Label(f, text=f"v{APP_VERSION}",
                  font=FONTS["body"], foreground="#888888").pack(pady=(6, 4))

        ttk.Separator(f, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=20)

        ttk.Label(f, text="Download media from X, Douyin, and Bilibili.",
                  font=FONTS["body"], foreground="#888888").pack(pady=(0, 4))
        ttk.Label(f, text="Powered by gallery-dl, yt-dlp, and f2.",
                  font=FONTS["small"], foreground="#666666").pack(pady=(0, 24))

        self._reg(
            ttk.Button(f, text=self._t("btn.github"), style="About.TButton",
                       command=lambda: webbrowser.open(
                           "https://github.com/GH-Acho177/media-downloader")),
            "btn.github").pack()

    def _build_auth_section(self, parent, pid, cfg):
        cookies_path = Path(cfg["cookies_file"])
        exists       = cookies_path.exists()

        if exists:
            status_text  = f"✓  {cookies_path.name}  ({cookies_path.stat().st_size // 1024} KB)"
            status_color = "#4ec94e"
        else:
            status_text  = f"✗  {cookies_path.name} not found"
            status_color = "#ff6b6b"

        var = tk.StringVar(value=status_text)
        self._cookie_status[pid] = var

        status_lbl = tk.Label(parent, textvariable=var, font=FONTS["small"],
                              fg=status_color, bg=parent.winfo_toplevel().cget("bg"))
        status_lbl.pack(anchor="w", pady=(0, 6))

        def _update_status():
            p = Path(cfg["cookies_file"])
            if p.exists():
                var.set(f"✓  {p.name}  ({p.stat().st_size // 1024} KB)")
                status_lbl.configure(fg="#4ec94e")
            else:
                var.set(f"✗  {p.name} not found")
                status_lbl.configure(fg="#ff6b6b")

        btn_row = ttk.Frame(parent)
        btn_row.pack(anchor="w")
        ttk.Button(btn_row, text="📂  Import cookies.txt",
                   style="Accent.TButton",
                   command=lambda: (self._browse_cookies(pid), _update_status())
                   ).pack(side=tk.LEFT)

    def _reset_db(self):
        import json as _json
        # Pick platform via simple dialog
        pid = self._pick_platform_dialog("Reset DB — choose platform")
        if pid is None:
            return
        label = PLATFORMS[pid]["label"]
        if not messagebox.askyesno(
                "Reset DB",
                f"Clear ALL state for {label}?\n\n"
                "• Download archive DB\n"
                "• Update announcements\n"
                + ("• Douyin user/video DBs\n• Last-run dates\n"
                   if pid == "douyin" else "") +
                "\nThe next run will re-download everything.",
                icon="warning"):
            return

        # Download archive
        _del(Path(f"config/{pid}_downloaded.db"))

        # Update history — remove entries for this platform
        hist_path = Path(UPDATE_HISTORY_FILE)
        if hist_path.exists():
            try:
                data = _json.loads(hist_path.read_text(encoding="utf-8"))
                data = [r for r in data if r.get("platform") != pid]
                hist_path.write_text(_json.dumps(data, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
            except Exception:
                pass

        # Douyin-specific state
        if pid == "douyin":
            for name in ("douyin_users.db", "douyin_videos.db"):
                _del(Path("config") / name)
            lr_path = Path(DOUYIN_LAST_RUN)
            if lr_path.exists():
                try:
                    lr_path.write_text("{}", encoding="utf-8")
                except Exception:
                    pass

        self._refresh_from_date()
        messagebox.showinfo("Reset DB", f"All state for {label} cleared.")

    # ── Log helpers ────────────────────────────────────────────────────────────
    def log_write(self, text):
        self.log.configure(state="normal")
        self.log.insert(tk.END, text)
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete(1.0, tk.END)
        self.log.configure(state="disabled")

    def _open_downloads(self):
        folder = self._get_download_dir().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(folder)])

    # ── DB viewer ──────────────────────────────────────────────────────────────
    # ── Post URL download ──────────────────────────────────────────────────────
    def _download_post_url(self):
        dlg = tk.Toplevel(self)
        dlg.title("Download Post URL")
        dlg.resizable(False, False)
        dlg.grab_set()
        self._centre_dialog(dlg, 680, 200)

        ttk.Label(dlg, text="Post URL:").grid(row=0, column=0, padx=20, pady=(18, 6), sticky="w")
        url_var   = tk.StringVar()
        url_entry = ttk.Entry(dlg, textvariable=url_var, width=62, font=FONTS["body"])
        url_entry.grid(row=0, column=1, padx=(0, 20), pady=(18, 6), sticky="ew")
        dlg.columnconfigure(1, weight=1)
        url_entry.focus_set()

        hint_var = tk.StringVar(value="Paste a URL from X, Bilibili, or Douyin")
        ttk.Label(dlg, textvariable=hint_var, font=FONTS["small"]).grid(
            row=1, column=0, columnspan=2, padx=20, sticky="w")

        btn_row = ttk.Frame(dlg)
        btn_row.grid(row=2, column=0, columnspan=2, pady=(14, 18))

        _URL_PLATFORM = (
            ("x.com",         "x"),
            ("twitter.com",   "x"),
            ("bilibili.com",  "bilibili"),
            ("b23.tv",        "bilibili"),
            ("douyin.com",    "douyin"),
            ("iesdouyin.com", "douyin"),
        )

        def _detect_pid(url: str) -> "str | None":
            for domain, p in _URL_PLATFORM:
                if domain in url:
                    return p
            return None

        def start():
            url = url_var.get().strip()
            if not url:
                return
            pid = _detect_pid(url)
            if pid is None:
                hint_var.set("⚠ Could not detect platform from URL.")
                return
            cfg = PLATFORMS[pid]
            if not Path(cfg["cookies_file"]).exists():
                messagebox.showwarning(
                    "No cookies",
                    f"Cookies not found for {cfg['label']}.\n"
                    "Go to Settings → Authentication.")
                return
            dlg.destroy()
            self._run_single_post(pid, url)

        ttk.Button(btn_row, text="Download", style="Accent.TButton",
                   command=start).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT)
        dlg.bind("<Return>", lambda _: start())

    def _run_single_post(self, pid: str, url: str):
        if self.running:
            messagebox.showwarning("Busy", "A download is already running.")
            return

        self.running = True
        self.stop_flag.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.stop_btn.configure(style="Danger.TButton")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set("Downloading post…")

        def worker():
            old_stdout = sys.stdout
            sys.stdout = TextRedirector(self.log, self)
            try:
                cfg    = PLATFORMS[pid]
                outdir = str(self._get_download_dir() / "url")
                Path(outdir).mkdir(parents=True, exist_ok=True)
                # Ensure URL has a scheme
                _url = url if url.startswith(("http://", "https://")) else "https://" + url
                print(f"Post URL : {_url}\n")

                if pid == "douyin" or cfg.get("downloader") == "f2":
                    from urllib.parse import urlparse, parse_qs
                    import re as _re
                    _qs = parse_qs(urlparse(_url).query)
                    if "modal_id" in _qs:
                        aweme_id = _qs["modal_id"][0]
                    else:
                        _m = _re.search(r"/video/(\d+)", _url)
                        if not _m:
                            print("[ERROR] Cannot extract video ID from URL.")
                            return
                        aweme_id = _m.group(1)
                    print(f"Aweme ID : {aweme_id}\n")
                    existing = list((self._get_download_dir() / "douyin").glob(f"**/*{aweme_id}*"))
                    if existing:
                        print(f"→ Already downloaded: {existing[0].name}")
                        return
                    cookie_str = self._netscape_to_cookie_str(cfg["cookies_file"])
                    dl_outdir  = str(self._get_download_dir() / "url")
                    import f2_one as _f2_one
                    asyncio.run(_f2_one.download_one(
                        aweme_id, cookie_str, dl_outdir,
                        "{nickname}_{create}_{aweme_id}",
                    ))
                elif cfg.get("downloader") == "yt-dlp":
                    cmd = [
                        "yt-dlp",
                        "--cookies", cfg["cookies_file"],
                        "-o", "%(id)s_%(title)s.%(ext)s",
                        "-P", outdir,
                        _url,
                    ]
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    for line in self._proc.stdout:
                        print(line, end="")
                    self._proc.wait()
                else:
                    cmd = [
                        GDL,
                        "--cookies", cfg["cookies_file"],
                        "-D", outdir,
                        _url,
                    ]
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    for line in self._proc.stdout:
                        if "api.day.app" in line or "Bark notification" in line:
                            continue
                        print(line, end="")
                    self._proc.wait()

                if not self.stop_flag.is_set():
                    print("\n✓ Done.")
            except Exception as exc:
                print(f"\n[ERROR] {exc}")
            finally:
                sys.stdout = old_stdout
                self.after(0, self._on_done)

        threading.Thread(target=worker, daemon=True).start()

    # ── Run / stop ─────────────────────────────────────────────────────────────
    def start(self):
        if self.running:
            return

        if not self._store.all_entries():
            messagebox.showwarning(
                "Cannot start",
                "No entries added yet.\n\nGo to the Accounts tab and add entries.")
            return

        import datetime as _dtv
        try:
            n = int(self._from_days_var.get())
            if n < 0:
                raise ValueError
        except (ValueError, TypeError):
            messagebox.showwarning("Invalid value", "Days must be a whole number ≥ 0.")
            return

        from_date = (
            (_dtv.date.today() - _dtv.timedelta(days=n)).isoformat()
            if n > 0 else ""
        )

        selected_ids = self._pick_creators()
        if selected_ids is None:
            return

        self._pending_run_result = None
        self.running = True
        self.stop_flag.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.stop_btn.configure(style="Danger.TButton")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set("Running…")

        workers = int(self.parallel_workers.get())

        threading.Thread(
            target=self._worker,
            args=(selected_ids,
                  self.mode_var.get() == "full",
                  from_date,
                  workers),
            daemon=True,
        ).start()

    def _pick_platform_dialog(self, title: str = "Choose platform") -> "str | None":
        """Simple modal with one button per platform. Returns pid or None."""
        c   = THEME_COLORS[self._current_theme]
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        self._centre_dialog(dlg, 320, 140)

        chosen: list = [None]

        ttk.Label(dlg, text="Select platform:", font=FONTS["heading"]).pack(pady=(16, 10))
        btn_row = ttk.Frame(dlg)
        btn_row.pack()
        for pid, cfg in PLATFORMS.items():
            p = pid  # capture
            ttk.Button(btn_row, text=cfg["label"],
                       command=lambda p=p: (chosen.__setitem__(0, p), dlg.destroy())
                       ).pack(side=tk.LEFT, padx=6)
        ttk.Button(dlg, text="Cancel", command=dlg.destroy).pack(pady=10)

        dlg.wait_window()
        return chosen[0]

    def _pick_creators(self) -> "list[str] | None":
        """Show creator picker. Returns list of creator_ids (+ UNASSIGNED_ID if chosen),
        or None if cancelled."""
        from src.creator_store import UNASSIGNED_ID
        c        = THEME_COLORS[self._current_theme]
        creators = self._store.all_creators()
        unassigned = self._store.get_unassigned_entries()

        rows: list[tuple] = []  # (BooleanVar, id, name, [platform_ids])
        for cr in creators:
            entries = self._store.get_entries_for_creator(cr.id)
            if entries:
                pids = list(dict.fromkeys(e.platform for e in entries))  # unique, ordered
                rows.append((tk.BooleanVar(value=True), cr.id, cr.name, pids))
        if unassigned:
            pids = list(dict.fromkeys(e.platform for e in unassigned))
            rows.append((tk.BooleanVar(value=True), UNASSIGNED_ID,
                         self._t("creator.unassigned"), pids))

        if not rows:
            messagebox.showwarning("Nothing to download",
                                   "All entries are missing cookies or\n"
                                   "no entries exist.")
            return None

        dlg = tk.Toplevel(self)
        dlg.title("Select Creators")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        self._centre_dialog(dlg, 680, min(80 * len(rows) + 220, 900))

        outer = ttk.Frame(dlg, padding=20)
        outer.pack(fill=tk.BOTH, expand=True)

        # ── Top bar: search + All/None ─────────────────────────────────────────
        top_bar = tk.Frame(outer, bg=str(ttk.Style().lookup("TFrame", "background")) or c["bg"])
        top_bar.pack(fill=tk.X, pady=(0, 8))

        search_var = tk.StringVar()
        search_ent = ttk.Entry(top_bar, textvariable=search_var, font=FONTS["body"])
        search_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3, padx=(0, 8))

        # All / None — small utility buttons, right-aligned
        ttk.Button(top_bar, text="None", style="Utility.TButton",
                   command=lambda: _set_all(False)).pack(side=tk.RIGHT)
        ttk.Button(top_bar, text="All",  style="Utility.TButton",
                   command=lambda: _set_all(True)).pack(side=tk.RIGHT, padx=(0, 4))

        _list_bg = str(ttk.Style().lookup("TFrame", "background")) or c["bg"]
        lf = tk.Frame(outer, bg=_list_bg, highlightthickness=1,
                      highlightbackground=c["border"])
        lf.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        canvas = tk.Canvas(lf, bg=_list_bg, highlightthickness=0,
                           height=min(70 * len(rows), 600))
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = tk.Frame(canvas, bg=_list_bg)
        _wid  = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(_wid, width=e.width))

        # Mousewheel — bind_all while cursor is over the list
        def _on_wheel(e):
            canvas.yview_scroll(-1 * (e.delta // 120), "units")
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        dlg.bind("<Destroy>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        # Clicking the list area removes cursor from search entry
        def _blur_search(_e=None):
            dlg.focus_set()
        canvas.bind("<Button-1>", _blur_search)
        inner.bind("<Button-1>",  _blur_search)
        dlg.after(50, dlg.focus_set)   # also hide cursor on dialog open

        _accent = c["accent"]
        _hover  = c["hover"]
        _fg     = c["text"]
        # (bv, cid, bar, chk, row_frame, name)
        var_rows: list[tuple] = []
        _icon_refs: list = []   # keep PhotoImage refs alive for dialog lifetime

        for (bv, cid, name, pids) in rows:
            row = tk.Frame(inner, bg=_list_bg, cursor="hand2")
            row.pack(fill=tk.X)
            bar = tk.Frame(row, bg=_accent if bv.get() else _list_bg, width=4)
            bar.pack(side=tk.LEFT, fill=tk.Y)
            lbl = tk.Label(row, text=name, font=FONTS["body"],
                           bg=_list_bg, fg=_fg, anchor="w")
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=12, pady=10)

            # Platform icons — image if available, else short text badge
            icon_frame = tk.Frame(row, bg=_list_bg)
            icon_frame.pack(side=tk.LEFT, padx=8)
            for pid in pids:
                img = getattr(self, "_platform_icons", {}).get(pid)
                if img:
                    _icon_refs.append(img)
                    cfg_p = PLATFORMS[pid]
                    pill_bg = cfg_p.get("icon_bg", cfg_p.get("color", c["border"]))
                    tk.Label(icon_frame, image=img, bg=pill_bg,
                             relief=tk.FLAT, bd=0).pack(side=tk.LEFT, padx=2)
                else:
                    tk.Label(icon_frame,
                             text=PLATFORMS[pid]["icon"],
                             font=FONTS["small"], bg=_list_bg,
                             fg=c["text_dim"]).pack(side=tk.LEFT, padx=2)

            def _toggle(_e, _v=bv, _bar=bar):
                dlg.focus_set()
                _v.set(not _v.get())
                _bar.configure(bg=_accent if _v.get() else _list_bg)

            for w in (row, lbl, icon_frame):
                w.bind("<Button-1>", _toggle)
            for child in icon_frame.winfo_children():
                child.bind("<Button-1>", _toggle)
            var_rows.append((bv, cid, bar, None, row, name))

        # ── Search filter ──────────────────────────────────────────────────────
        def _filter(*_):
            q = search_var.get().strip().lower()
            for _bv, _cid, _bar, _chk, _row, _name in var_rows:
                if q in _name.lower():
                    _row.pack(fill=tk.X)
                else:
                    _row.pack_forget()
            canvas.configure(scrollregion=canvas.bbox("all"))

        search_var.trace_add("write", _filter)

        def _set_all(v: bool):
            q = search_var.get().strip().lower()
            for _bv, _, _bar, _chk, _row, _name in var_rows:
                if q and q not in _name.lower():
                    continue  # only affect visible rows
                _bv.set(v)
                _bar.configure(bg=_accent if v else _list_bg)

        result: list[list[str]] = [[]]

        def _confirm():
            chosen = [cid for bv, cid, *_ in var_rows if bv.get()]
            if not chosen:
                messagebox.showwarning("Nothing selected",
                                       "Select at least one creator.", parent=dlg)
                return
            result[0] = chosen
            dlg.destroy()

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Cancel", command=dlg.destroy).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_row, text="Start", style="Accent.TButton",
                   command=_confirm).pack(side=tk.RIGHT)

        dlg.bind("<Return>", lambda _e: _confirm())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        self.wait_window(dlg)
        return result[0] if result[0] else None

    def stop(self):
        self.stop_flag.set()
        self.status_var.set("Stopping…")
        self.log_write("\n[STOP requested — terminating current download]\n")
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        with self._procs_lock:
            for p in self._procs:
                if p.poll() is None:
                    p.terminate()

    def _on_done(self):
        self.running = False
        self._proc   = None
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.stop_btn.configure(style="Secondary.TButton")
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress["value"] = 0
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self.status_var.set("Idle")
        else:
            self.status_var.set("Stopped" if self.stop_flag.is_set() else "Idle")
        if hasattr(self, "_last_sync_var"):
            self._last_sync_var.set(self._get_last_sync())
        self._refresh_auto_pill()
        result = getattr(self, "_pending_run_result", None)
        if result:
            self._pending_run_result = None
            self._show_update_summary(result)

    # ── Cookie helpers ─────────────────────────────────────────────────────────
    def _browse_cookies(self, pid):
        cfg  = PLATFORMS[pid]
        dest = Path(cfg["cookies_file"])
        src  = filedialog.askopenfilename(
            title=f"Select cookies for {cfg['label']}",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not src:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        self._cookie_status[pid].set(
            f"✓  {dest.name} found  ({dest.stat().st_size // 1024} KB)")
        messagebox.showinfo("Cookies imported", f"Copied to {dest}")

    @staticmethod
    def _netscape_to_cookie_str(path: str) -> str:
        parts = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) >= 7:
                parts.append(f"{fields[5]}={fields[6]}")
        return "; ".join(parts)

    # ── Auto-update scheduler ──────────────────────────────────────────────────
    def _refresh_auto_pill(self):
        """Sync the Auto toggle to the current scheduler state."""
        if not hasattr(self, "_auto_pill"):
            return
        c   = THEME_COLORS[self._current_theme]
        on  = bool(self._scheduler_thread and self._scheduler_thread.is_alive())
        cv  = self._auto_pill
        TW, TH, TR = 44, 24, 12
        cv.delete("all")
        cv.configure(bg=c["bg"])
        track = c["accent"] if on else c["border"]
        cv.create_arc(0, 0, TH, TH, start=90, extent=180, fill=track, outline=track)
        cv.create_arc(TW - TH, 0, TW, TH, start=270, extent=180, fill=track, outline=track)
        cv.create_rectangle(TR, 0, TW - TR, TH, fill=track, outline=track)
        m = 3
        kx = TW - TH + m if on else m
        cv.create_oval(kx, m, kx + TH - 2 * m, TH - m, fill="#ffffff", outline="")

    def _toggle_auto_from_dashboard(self):
        on = bool(self._scheduler_thread and self._scheduler_thread.is_alive())
        enabled = not on
        self._save_setting("auto_update_enabled", enabled)
        if enabled:
            self._start_scheduler()
        else:
            self._stop_scheduler()
        self._refresh_auto_pill()

    def _start_scheduler(self):
        self._stop_scheduler()
        self._scheduler_stop = threading.Event()
        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            args=(self._scheduler_stop,),
            daemon=True,
        )
        self._scheduler_thread.start()
        self.progress.configure(mode="determinate")
        self.progress["value"] = 0
        self._refresh_auto_pill()
        self._tick_countdown()

    def _stop_scheduler(self):
        self._scheduler_stop.set()
        self._scheduler_thread = None
        self._scheduler_next_at = 0.0
        if hasattr(self, "_auto_next_var"):
            self._auto_next_var.set("")
        self.progress.configure(mode="determinate")
        self.progress["value"] = 0
        self._refresh_auto_pill()

    def _scheduler_loop(self, stop_event: threading.Event):
        import time as _time
        while not stop_event.is_set():
            interval = int(self._load_setting("auto_update_interval", 30)) * 60
            self._scheduler_next_at = _time.time() + interval
            if stop_event.wait(timeout=interval):
                break
            if not self.running:
                self.after(0, self._start_auto)

    def _start_auto(self):
        """Trigger an Update-mode run on all entries without any dialog."""
        if self.running or not self._store.all_entries():
            return
        from src.creator_store import UNASSIGNED_ID
        all_ids = [c.id for c in self._store.all_creators()]
        if self._store.get_unassigned_entries():
            all_ids.append(UNASSIGNED_ID)
        if not all_ids:
            return
        self.running = True
        self.stop_flag.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.stop_btn.configure(style="Danger.TButton")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status_var.set("Running…")
        workers = int(self.parallel_workers.get())
        threading.Thread(
            target=self._worker,
            args=(all_ids, False, "", workers, True),
            daemon=True,
        ).start()

    def _tick_countdown(self):
        """Drive the statusbar bar (determinate) and countdown while auto is idle."""
        if not (self._scheduler_thread and self._scheduler_thread.is_alive()):
            return
        import time as _time
        interval  = int(self._load_setting("auto_update_interval", 30)) * 60
        remaining = max(0.0, self._scheduler_next_at - _time.time())
        elapsed   = max(0.0, interval - remaining)
        pct = min(100.0, elapsed / interval * 100) if interval > 0 else 0
        m, s = divmod(int(remaining), 60)
        if hasattr(self, "_auto_next_var"):
            self._auto_next_var.set(f"{m}m {s:02d}s")
        if not self.running:
            self.progress.configure(mode="determinate")
            self.progress["value"] = pct
        self.after(1000, self._tick_countdown)

    # ── Worker ─────────────────────────────────────────────────────────────────
    def _worker(self, creator_ids: list, is_full: bool,
                from_date: str = "", workers: int = 1, is_auto: bool = False):
        from src.creator_store import UNASSIGNED_ID
        import time
        _run_start = time.time()
        _run_key   = str(int(_run_start * 1000))
        old_stdout  = sys.stdout
        _redirector = TextRedirector(self.log, self)
        _log_lock   = threading.Lock()
        sys.stdout  = _ThreadRouter(_redirector)   # always; routes per-thread in parallel

        try:
            import datetime as _dt
            import re as _re

            mode_label = "Full" if is_full else "Update"
            print(f"Mode     : {mode_label}")
            if from_date:
                print(f"From     : {from_date}")
            print(f"Creators : {len(creator_ids)}\n")

            sleep_req  = float(self._load_setting("sleep_req",  0))
            sleep_user = float(self._load_setting("sleep_user", 0))

            # Collect handles per platform from selected creators
            by_pid: dict[str, list[str]] = {}
            _handle_creator: dict[tuple, str] = {}   # (pid, handle) → safe creator folder
            for cid in creator_ids:
                if cid == UNASSIGNED_ID:
                    src_entries = self._store.get_unassigned_entries()
                    _cname = "Unassigned"
                else:
                    _c = self._store.get_creator(cid)
                    _cname = _c.name if _c else "Unassigned"
                _safe_cname = _re.sub(r'[\\/:*?"<>|]', "_", _cname).strip()
                for e in src_entries:
                    by_pid.setdefault(e.platform, []).append(e.handle)
                    _handle_creator[(e.platform, e.handle)] = _safe_cname

            _all_update_results: list[dict] = []
            _all_suspended: dict[str, list[str]] = {}

            def _upsert_run(result):
                import json as _json2
                try:
                    hist = []
                    if Path(UPDATE_HISTORY_FILE).exists():
                        hist = _json2.loads(
                            Path(UPDATE_HISTORY_FILE).read_text(encoding="utf-8"))
                    for _i, _e in enumerate(hist):
                        if _e.get("run_key") == result["run_key"]:
                            hist[_i] = result
                            break
                    else:
                        hist.append(result)
                    Path(UPDATE_HISTORY_FILE).write_text(
                        _json2.dumps(hist, indent=2), encoding="utf-8")
                except Exception:
                    pass

            _parallel_plats = len(by_pid) > 1
            _results_lock   = threading.Lock()

            def _run_platform(pid, users):
                """Download all users for one platform; return (results, suspended)."""
                if self.stop_flag.is_set():
                    return [], []

                cfg          = PLATFORMS[pid]
                cookies_file = cfg["cookies_file"]
                _dl_root     = self._get_download_dir().resolve()
                downloader   = cfg.get("downloader", "gallery-dl")

                # True whenever this task runs concurrently with others
                _is_par = workers > 1 or _parallel_plats

                _local_results:   list[dict] = []
                _local_suspended: list[str]  = []

                print(f"\n{'═'*60}")
                print(f"▶  {cfg['label']}  ({len(users)} accounts)")
                print(f"{'═'*60}\n")

                if not Path(cookies_file).exists():
                    print(f"[SKIP] No cookies for {cfg['label']}.")
                    return [], []

                if downloader == "f2":
                    # ── f2 (Douyin) ───────────────────────────────────────────
                    import json as _json, datetime as _dt
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    cookie_str  = self._netscape_to_cookie_str(cookies_file)
                    _state_lock = threading.Lock()

                    def _f2_task(user, idx):
                        display      = user.split("|")[0]
                        safe_display = _re.sub(r'[\\/:*?"<>|]', "_", display).strip()
                        sec_uid      = user.split("|")[-1]
                        today        = _dt.date.today().isoformat()
                        url          = cfg["url_fn"](user)
                        tag          = f"[{display}]"

                        if _is_par:
                            _tbuf = _TaskBuffer()
                            _out  = _tbuf.write_raw
                            _outp = lambda t: _tbuf.write_raw(f"  {tag} {t}\n")
                        else:
                            _out  = lambda t: print(t)
                            _outp = lambda t: print(f"  {tag} {t}")

                        if from_date:
                            interval = f"{from_date}|{today}"
                        elif is_full:
                            interval = "all"
                        else:
                            with _state_lock:
                                lr: dict = {}
                                if Path(DOUYIN_LAST_RUN).exists():
                                    try:
                                        lr = _json.loads(
                                            Path(DOUYIN_LAST_RUN).read_text(encoding="utf-8"))
                                    except Exception:
                                        pass
                            last_date = lr.get(sec_uid)
                            interval  = f"{last_date}|{today}" if last_date else "all"

                        _out(f"\n{'─'*50}")
                        _out(f"[{idx+1}/{len(users)}] {display}")
                        _out(f"{'─'*50}")
                        _out(f"  Interval : {interval}")

                        if self.stop_flag.is_set():
                            if _is_par:
                                _tbuf.flush_to(_redirector, _log_lock)
                            return None, False, user

                        _creator_dir = _handle_creator.get((pid, user), "Unassigned")
                        _user_dir_f  = _dl_root / _creator_dir / safe_display
                        _before      = set(_user_dir_f.glob("*")) if _user_dir_f.exists() else set()
                        _user_dir_f.mkdir(parents=True, exist_ok=True)
                        _out(f"  Save to  : {_creator_dir}/{safe_display}")

                        import f2_user as _f2_user
                        user_suspended = False

                        def _line_cb(line):
                            nonlocal user_suspended
                            if "[error]" in line and any(k in line for k in (
                                    "could not be found", "UserUnavailable", "has been suspended")):
                                user_suspended = True

                        if _is_par:
                            _f2_writer = _tbuf.make_prefixed_writer(
                                prefix=f"  {tag} ",
                                skip_fn=lambda l: "api.day.app" in l or "Bark notification" in l,
                                line_cb=_line_cb,
                            )
                            sys.stdout.set_target(_f2_writer)
                            try:
                                asyncio.run(_f2_user.download_user(
                                    url, cookie_str, str(_user_dir_f), interval,
                                    "{create}_{aweme_id}",
                                    stop_check=self.stop_flag.is_set,
                                ))
                            finally:
                                sys.stdout.clear_target()
                        else:
                            _outer = sys.stdout
                            sys.stdout = _LineWriter(
                                _outer,
                                prefix=f"  {tag} ",
                                skip_fn=lambda l: "api.day.app" in l or "Bark notification" in l,
                                line_cb=_line_cb,
                            )
                            try:
                                asyncio.run(_f2_user.download_user(
                                    url, cookie_str, str(_user_dir_f), interval,
                                    "{create}_{aweme_id}",
                                    stop_check=self.stop_flag.is_set,
                                ))
                            finally:
                                sys.stdout.flush()
                                sys.stdout = _outer

                        new_count       = 0
                        new_names: list[str] = []
                        corrupt_count   = 0
                        _user_dl_folder = _user_dir_f.resolve()

                        if _user_dir_f.exists():
                            _after    = set(_user_dir_f.glob("*"))
                            new_files = [p for p in _after
                                         if p.is_file() and p not in _before]
                            new_count = len(new_files)
                            new_names = [f.name for f in new_files]

                        corrupt = self._scan_corrupt(_user_dl_folder)
                        if corrupt:
                            corrupt_count = len(corrupt)
                            for f in corrupt:
                                _outp(f"⚠ Corrupt ({f.stat().st_size:,} B): {f.name} — deleted")
                                f.unlink(missing_ok=True)
                            hint = "will re-download now" if is_full else "run Full mode to re-download"
                            _outp(f"→ {corrupt_count} corrupt file(s) removed ({hint})")
                            new_count = max(0, new_count - corrupt_count)

                        if user_suspended:
                            _out(f"  ⚠ {display} appears suspended/deleted — will be removed")
                            if _is_par:
                                _tbuf.flush_to(_redirector, _log_lock)
                            return None, True, user

                        if not self.stop_flag.is_set():
                            with _state_lock:
                                lr2: dict = {}
                                if Path(DOUYIN_LAST_RUN).exists():
                                    try:
                                        lr2 = _json.loads(
                                            Path(DOUYIN_LAST_RUN).read_text(encoding="utf-8"))
                                    except Exception:
                                        pass
                                lr2[sec_uid] = today
                                Path(DOUYIN_LAST_RUN).write_text(
                                    _json.dumps(lr2, indent=2), encoding="utf-8")

                        if _is_par:
                            _tbuf.flush_to(_redirector, _log_lock)

                        return {
                            "platform": pid,
                            "display":  display,
                            "count":    new_count,
                            "corrupt":  corrupt_count,
                            "files":    new_names,
                            "folder":   str(_user_dl_folder),
                        }, False, user

                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {pool.submit(_f2_task, u, i): u
                                   for i, u in enumerate(users)}
                        for fut in as_completed(futures):
                            try:
                                result, is_susp, user_entry = fut.result()
                            except Exception as exc:
                                print(f"\n[ERROR] {exc}")
                                continue
                            if is_susp:
                                _local_suspended.append(user_entry)
                            elif result:
                                _local_results.append(result)

                elif downloader == "yt-dlp":
                    # ── yt-dlp (Bilibili) ─────────────────────────────────────
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    dateafter = from_date.replace("-", "") if from_date else None

                    def _ytdlp_task(user, idx):
                        if self.stop_flag.is_set():
                            return None, False, user
                        display      = user.split("|")[0]
                        safe_display = _re.sub(r'[\\/:*?"<>|]', "_", display).strip()
                        prefix       = "  "

                        if _is_par:
                            _tbuf = _TaskBuffer()
                            _pr   = lambda t, end="\n": _tbuf.write_raw(f"{t}{end}")
                        else:
                            _pr   = lambda t, end="\n": print(t, end=end)

                        _pr(f"\n{'─'*50}")
                        _pr(f"[{idx+1}/{len(users)}] {display}")
                        _pr(f"{'─'*50}")
                        url          = cfg["url_fn"](user)
                        archive_file = str(Path("config") / f"{pid}_downloaded.txt")
                        _creator_dir = _handle_creator.get((pid, user), "Unassigned")
                        user_dir     = _dl_root / _creator_dir / safe_display
                        user_dir.mkdir(parents=True, exist_ok=True)
                        _before      = set(user_dir.glob("*"))
                        _pr(f"{prefix}Save to  : {_creator_dir}/{safe_display}")
                        cmd = [
                            "yt-dlp",
                            "--cookies", cookies_file,
                            *(["--download-archive", archive_file] if not is_full else []),
                            *(["--dateafter", dateafter] if dateafter else []),
                            "--sleep-requests", str(sleep_req),
                            "-o", "%(id)s_%(title)s.%(ext)s",
                            "-P", str(user_dir),
                            url,
                        ]
                        proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        if not _is_par:
                            self._proc = proc
                        with self._procs_lock:
                            self._procs.append(proc)
                        user_suspended       = False
                        _completed_posts: list[str] = []
                        try:
                            for line in proc.stdout:
                                _pr(f"{prefix}{line}", end="")
                                if "ERROR" in line and any(k in line for k in (
                                        "not found", "unavailable", "suspended", "deleted")):
                                    user_suspended = True
                                if '[Merger] Merging formats into "' in line:
                                    _fname = line.split('"')[1] if '"' in line else ""
                                    if _fname:
                                        _completed_posts.append(Path(_fname).name)
                                if self.stop_flag.is_set():
                                    proc.terminate()
                                    break
                            proc.wait()
                        finally:
                            with self._procs_lock:
                                if proc in self._procs:
                                    self._procs.remove(proc)

                        _user_dl_folder = user_dir.resolve()
                        if _completed_posts:
                            new_names = _completed_posts
                            new_count = len(new_names)
                        else:
                            _after     = set(user_dir.glob("*")) if user_dir.exists() else set()
                            _new_files = {f for f in (_after - _before)
                                          if not _re.search(r'\.f\d+\.[a-z0-9]+$',
                                                            f.name, _re.IGNORECASE)}
                            new_count  = len(_new_files)
                            new_names  = [f.name for f in _new_files]

                        corrupt = self._scan_corrupt(_user_dl_folder)
                        corrupt_count = 0
                        if corrupt:
                            corrupt_count = len(corrupt)
                            for f in corrupt:
                                _pr(f"{prefix}⚠ Corrupt/partial ({f.stat().st_size:,} B): {f.name} — deleted")
                                f.unlink(missing_ok=True)
                            hint = "will re-download now" if is_full else "run Full mode to re-download"
                            _pr(f"{prefix}→ {corrupt_count} corrupt file(s) removed ({hint})")
                            new_count = max(0, new_count - corrupt_count)

                        if user_suspended:
                            _pr(f"{prefix}⚠ {display} appears suspended/deleted — will be removed")
                            if _is_par:
                                _tbuf.flush_to(_redirector, _log_lock)
                            return None, True, user

                        if _is_par:
                            _tbuf.flush_to(_redirector, _log_lock)

                        return {
                            "platform": pid,
                            "display":  display,
                            "count":    new_count,
                            "corrupt":  corrupt_count,
                            "files":    new_names,
                            "folder":   str(_user_dl_folder),
                        }, False, user

                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futs = {pool.submit(_ytdlp_task, u, i): u
                                for i, u in enumerate(users)}
                        for i, fut in enumerate(as_completed(futs)):
                            result, is_susp, user = fut.result()
                            if is_susp:
                                _local_suspended.append(user)
                            elif result:
                                _local_results.append(result)
                            if not _is_par and not self.stop_flag.is_set() and i < len(users) - 1:
                                print(f"\nWaiting {sleep_user}s…")
                                time.sleep(sleep_user)

                else:
                    # ── gallery-dl (X / Twitter) — sequential per user ────────
                    for i, user in enumerate(users):
                        if self.stop_flag.is_set():
                            break

                        display      = user.split("|")[0]
                        safe_display = _re.sub(r'[\\/:*?"<>|]', "_", display).strip()

                        if _is_par:
                            _tbuf = _TaskBuffer()
                            _pr   = lambda t, end="\n": _tbuf.write_raw(f"{t}{end}")
                        else:
                            _pr   = lambda t, end="\n": print(t, end=end)

                        _pr(f"\n{'─'*50}")
                        _pr(f"[{i+1}/{len(users)}] {display}")
                        _pr(f"{'─'*50}")

                        url          = cfg["url_fn"](user)
                        archive_file = str(Path("config") / f"{pid}_downloaded.db")
                        _creator_dir = _handle_creator.get((pid, user), "Unassigned")
                        user_dir     = _dl_root / _creator_dir / safe_display
                        user_dir.mkdir(parents=True, exist_ok=True)
                        _gdl_before  = set(user_dir.glob("*"))
                        _pr(f"  Save to  : {_creator_dir}/{safe_display}")
                        cmd = [
                            GDL,
                            "--cookies", cookies_file,
                            *(["--download-archive", archive_file] if not is_full else []),
                            *(["-o", f"extractor.date-min={from_date}T00:00:00"] if from_date else []),
                            "--sleep-request", str(sleep_req),
                            "-D", str(user_dir),
                            url,
                        ]
                        gdl_proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        if not _is_par:
                            self._proc = gdl_proc
                        with self._procs_lock:
                            self._procs.append(gdl_proc)
                        user_suspended = False
                        for line in gdl_proc.stdout:
                            if "api.day.app" in line or "Bark notification" in line:
                                continue
                            _pr(line, end="")
                            if "[error]" in line and any(k in line for k in (
                                    "could not be found", "UserUnavailable", "has been suspended")):
                                user_suspended = True
                            if not is_full and "# " in line:
                                gdl_proc.terminate()
                                _pr("\n  → Up to date.")
                                break
                        gdl_proc.wait()
                        with self._procs_lock:
                            if gdl_proc in self._procs:
                                self._procs.remove(gdl_proc)

                        _gdl_after  = set(user_dir.glob("*")) if user_dir.exists() else set()
                        _new_files  = _gdl_after - _gdl_before
                        new_count   = len(_new_files)
                        new_names   = [f.name for f in _new_files]
                        _user_dl_folder = user_dir.resolve()

                        corrupt = self._scan_corrupt(_user_dl_folder)
                        corrupt_count = 0
                        if corrupt:
                            corrupt_count = len(corrupt)
                            for f in corrupt:
                                _pr(f"  ⚠ Corrupt/partial ({f.stat().st_size:,} B): {f.name} — deleted")
                                f.unlink(missing_ok=True)
                            hint = "will re-download now" if is_full else "run Full mode to re-download"
                            _pr(f"  → {corrupt_count} corrupt file(s) removed ({hint})")
                            new_count = max(0, new_count - corrupt_count)

                        if user_suspended:
                            _local_suspended.append(user)
                            _pr(f"  ⚠ {user} appears suspended/deleted — will be removed")
                        else:
                            _local_results.append({
                                "platform": pid,
                                "display":  display,
                                "count":    new_count,
                                "corrupt":  corrupt_count,
                                "files":    new_names,
                                "folder":   str(_user_dl_folder),
                            })

                        if _is_par:
                            _tbuf.flush_to(_redirector, _log_lock)
                        elif not self.stop_flag.is_set() and i < len(users) - 1:
                            print(f"\nWaiting {sleep_user}s…")
                            time.sleep(sleep_user)

                return _local_results, _local_suspended

            # ── Dispatch: parallel across platforms, sequential for one ────────
            if _parallel_plats:
                from concurrent.futures import ThreadPoolExecutor as _PlatPool, \
                                               as_completed      as _plat_ac
                with _PlatPool(max_workers=len(by_pid)) as _ppool:
                    _pfuts = {_ppool.submit(_run_platform, pid, users): pid
                              for pid, users in by_pid.items()}
                    for _pfut in _plat_ac(_pfuts):
                        _ppid = _pfuts[_pfut]
                        try:
                            _presults, _psusp = _pfut.result()
                        except Exception as exc:
                            print(f"\n[ERROR] {_ppid}: {exc}")
                            continue
                        with _results_lock:
                            _all_update_results.extend(_presults)
                            if _psusp:
                                _all_suspended.setdefault(_ppid, []).extend(_psusp)
            else:
                _pid, _users = next(iter(by_pid.items()))
                _presults, _psusp = _run_platform(_pid, _users)
                _all_update_results.extend(_presults)
                if _psusp:
                    _all_suspended.setdefault(_pid, []).extend(_psusp)

            for susp_pid, susp_handles in _all_suspended.items():
                self._remove_suspended(susp_handles, susp_pid)

            _elapsed     = time.time() - _run_start
            _mins, _secs = divmod(int(_elapsed), 60)
            _stopped     = self.stop_flag.is_set()

            if not _stopped:
                print("\n✓ ALL DONE.")
            else:
                print("\n■ Stopped.")

            # Save one combined history entry covering all platforms
            if _all_update_results:
                import datetime as _dt_end
                _el = time.time() - _run_start
                _m, _s = divmod(int(_el), 60)
                combined = {
                    "run_key":  _run_key,
                    "date":     _dt_end.date.today().isoformat(),
                    "time":     _dt_end.datetime.now().strftime("%H:%M"),
                    "duration": f"{_m}m {_s}s" if _m else f"{_s}s",
                    "mode":     "Full" if is_full else "Update",
                    "stopped":  _stopped,
                    "users":    _all_update_results,
                }
                total_new = sum(u.get("count", 0) for u in _all_update_results)
                if not is_auto or total_new > 0:
                    _upsert_run(combined)
                if not is_auto:
                    self._pending_run_result = combined

        except Exception as exc:
            print(f"\n[ERROR] {exc}")
        finally:
            sys.stdout = old_stdout
            self.after(0, self._on_done)

    # ── Integrity scan ─────────────────────────────────────────────────────────
    @staticmethod
    def _scan_corrupt(folder: Path) -> list:
        """Return media files that are 0 bytes (guaranteed corrupt/partial)."""
        bad = []
        if not folder.exists():
            return bad
        for f in folder.iterdir():
            if f.is_file() and f.suffix.lower() in _MEDIA_EXTS and f.stat().st_size == 0:
                bad.append(f)
        return bad

    # ── Update history ─────────────────────────────────────────────────────────
    def _open_announcements(self):
        """Show the full update history (📢 Updates button)."""
        try:
            import json as _json
            history = []
            if Path(UPDATE_HISTORY_FILE).exists():
                history = _json.loads(
                    Path(UPDATE_HISTORY_FILE).read_text(encoding="utf-8"))
        except Exception:
            history = []

        if not history:
            messagebox.showinfo("Update History",
                                "No update runs recorded yet.\n\n"
                                "Run in Update mode to start tracking results.")
            return
        self._show_history_dialog(list(reversed(history)), "Update History")

    def _show_update_summary(self, result: dict):
        """Show the post-run summary dialog for a single run."""
        self._show_history_dialog([result], "Update Complete")

    def _show_history_dialog(self, runs: list, title: str):
        dlg = tk.Toplevel(self)
        dlg.withdraw()
        dlg.title(title)
        dlg.resizable(True, True)
        dlg.transient(self)
        dlg.grab_set()

        self._centre_dialog(dlg, 980, 800)

        BG      = "#1e1e1e"
        BG_HDR  = "#252525"
        FG      = "#d4d4d4"
        FG_DIM  = "#666666"
        FG_DATE = "#999999"
        SEP     = "#333333"

        outer = ttk.Frame(dlg, padding=(12, 12, 12, 8))
        outer.pack(fill=tk.BOTH, expand=True)

        canvas    = tk.Canvas(outer, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)

        inner = tk.Frame(canvas, bg=BG)
        win   = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",  lambda _e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda  e: canvas.itemconfig(win, width=e.width))
        inner.bind_all("<MouseWheel>", _on_mousewheel)
        dlg.bind("<Destroy>",      lambda _e: inner.unbind_all("<MouseWheel>"))

        def _open_folder(path: str):
            import subprocess as _sp
            target = Path(path)
            if not target.exists():
                target = target.parent
            _sp.Popen(["explorer", str(target)])

        def _delete_run(run_ref, card_widget):
            runs.remove(run_ref)
            card_widget.destroy()
            try:
                import json as _j
                history = []
                if Path(UPDATE_HISTORY_FILE).exists():
                    history = _j.loads(
                        Path(UPDATE_HISTORY_FILE).read_text(encoding="utf-8"))
                history = [r for r in history if r != run_ref]
                Path(UPDATE_HISTORY_FILE).write_text(
                    _j.dumps(history, indent=2), encoding="utf-8")
            except Exception:
                pass

        PAGE = 10

        def _render_card(run):
            date     = run.get("date", "")
            time_    = run.get("time", "")
            duration = run.get("duration", "")
            stopped  = run.get("stopped", False)
            mode     = run.get("mode", "")
            users    = run.get("users", [])
            total    = sum(u.get("count", 0) for u in users)
            total_corrupt = sum(u.get("corrupt", 0) for u in users)

            # Collect distinct platforms present in this run
            seen_pids: list[str] = []
            for u in users:
                p = u.get("platform", "")
                if p and p not in seen_pids:
                    seen_pids.append(p)

            card = tk.Frame(inner, bg=BG, highlightthickness=1,
                            highlightbackground=SEP)
            card.pack(fill=tk.X, padx=8, pady=(8, 0))

            hdr = tk.Frame(card, bg=BG_HDR)
            hdr.pack(fill=tk.X)

            _hdr_text = f"  {date}  {time_}"
            if duration:
                _hdr_text += f"  ·  {duration}"
            tk.Label(hdr, text=_hdr_text,
                     bg=BG_HDR, fg=FG_DATE,
                     font=FONTS["small"]).pack(side=tk.LEFT, pady=5)

            del_btn = tk.Label(hdr, text=" ✕ ", bg=BG_HDR, fg="#666666",
                               font=FONTS["small"], cursor="hand2")
            del_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=4)
            del_btn.bind("<Button-1>", lambda _e, r=run, c=card: _delete_run(r, c))
            del_btn.bind("<Enter>",    lambda _e, w=del_btn: w.config(fg="#cc4444"))
            del_btn.bind("<Leave>",    lambda _e, w=del_btn: w.config(fg="#666666"))

            tags = tk.Frame(hdr, bg=BG_HDR)
            tags.pack(side=tk.RIGHT, padx=8, pady=4)

            for pid_key in seen_pids:
                _pcfg   = PLATFORMS.get(pid_key, {})
                _icon   = self._platform_icons.get(pid_key)
                if _icon:
                    _ibg = _pcfg.get("icon_bg", _pcfg.get("color", "#2d2d2d"))
                    tk.Label(tags, image=_icon, bg=_ibg,
                             bd=0, highlightthickness=0,
                             padx=2, pady=2).pack(side=tk.LEFT, padx=2)
                else:
                    _plabel = _pcfg.get("label", pid_key)
                    _pcolor = _pcfg.get("color", "#2d2d2d")
                    tk.Label(tags, text=f" {_plabel} ", bg=_pcolor, fg="#000000",
                             font=FONTS["small"]).pack(side=tk.LEFT, padx=2)

            if mode:
                tk.Label(tags, text=f" {mode} ", bg="#2d2d2d", fg="#555555",
                         font=FONTS["small"]).pack(side=tk.LEFT, padx=2)

            if stopped:
                tk.Label(tags, text=" ■ stopped ", bg="#3a2020", fg="#cc4444",
                         font=FONTS["small"]).pack(side=tk.LEFT, padx=2)

            count_color = "#4ec94e" if total > 0 else FG_DIM
            tk.Label(tags, text=f" +{total} ", bg="#2d2d2d", fg=count_color,
                     font=(*FONTS["small"], "bold")).pack(side=tk.LEFT, padx=2)
            if total_corrupt:
                tk.Label(tags, text=f" ⚠{total_corrupt} ", bg="#2d2d2d",
                         fg="#e8a030", font=FONTS["small"]).pack(side=tk.LEFT, padx=2)

            body = tk.Frame(card, bg=BG)
            body.pack(fill=tk.X, padx=0, pady=(2, 4))

            for u in users:
                count    = u.get("count",    0)
                corrupt  = u.get("corrupt",  0)
                files    = u.get("files",    [])
                folder   = u.get("folder",   "")
                display  = u.get("display",  "")
                u_pid  = u.get("platform", "")
                u_pcfg = PLATFORMS.get(u_pid, {})

                row = tk.Frame(body, bg=BG)
                row.pack(fill=tk.X, padx=10, pady=1)

                badge_fg = "#4ec94e" if count > 0 else FG_DIM
                tk.Label(row, text=f"+{count}", bg=BG, fg=badge_fg,
                         font=(*FONTS["small"], "bold"),
                         width=4, anchor=tk.E).pack(side=tk.LEFT)

                if u_pid and len(seen_pids) > 1:
                    _uicon = self._platform_icons.get(u_pid)
                    if _uicon:
                        _uibg = u_pcfg.get("icon_bg", u_pcfg.get("color", "#444444"))
                        tk.Label(row, image=_uicon, bg=_uibg,
                                 bd=0, highlightthickness=0,
                                 padx=2, pady=1).pack(side=tk.LEFT, padx=(4, 0))
                    else:
                        _uplabel = u_pcfg.get("label", u_pid)
                        _upcolor = u_pcfg.get("color", "#444444")
                        tk.Label(row, text=f" {_uplabel} ", bg=_upcolor, fg="#000000",
                                 font=FONTS["small"]).pack(side=tk.LEFT, padx=(4, 0))

                tk.Label(row, text=f"  {display}", bg=BG, fg=FG,
                         font=FONTS["body"]).pack(side=tk.LEFT)

                if corrupt:
                    tk.Label(row, text=f"  ⚠ {corrupt}",
                             bg=BG, fg="#e8a030",
                             font=FONTS["small"]).pack(side=tk.LEFT)

                if folder:
                    lnk = tk.Label(row, text="open folder",
                                   bg=BG, fg=ACCENT,
                                   font=FONTS["small"], cursor="hand2")
                    lnk.pack(side=tk.RIGHT, padx=(0, 2))
                    lnk.bind("<Button-1>",
                             lambda _e, p=folder: _open_folder(p))

                if files:
                    n = len(files)
                    label_text = lambda open_: (
                        f"  ▼ {n} file{'s' if n != 1 else ''}"
                        if open_ else
                        f"  ▶ {n} file{'s' if n != 1 else ''}"
                    )
                    files_frame = tk.Frame(body, bg=BG)
                    shown      = [False]
                    built      = [False]
                    toggle_lbl = tk.Label(row, text=label_text(False),
                                         bg=BG, fg=FG_DIM,
                                         font=FONTS["small"], cursor="hand2")
                    toggle_lbl.pack(side=tk.LEFT, padx=(4, 0))

                    def _make_toggle(ff, tl, lt, s, b, anchor_row, fnames):
                        def _toggle(_e):
                            s[0] = not s[0]
                            tl.config(text=lt(s[0]))
                            if s[0]:
                                if not b[0]:
                                    for fname in fnames:
                                        tk.Label(ff, text=f"      {fname}",
                                                 bg=BG, fg=FG_DIM,
                                                 font=FONTS["mono"],
                                                 anchor=tk.W).pack(
                                            fill=tk.X, padx=10)
                                    b[0] = True
                                ff.pack(fill=tk.X, after=anchor_row)
                            else:
                                ff.pack_forget()
                        return _toggle

                    toggle_lbl.bind("<Button-1>",
                        _make_toggle(files_frame, toggle_lbl,
                                     label_text, shown, built, row, files))

        # ── Paginated rendering ────────────────────────────────────────────────
        offset = [0]
        more_btn_frame = [None]

        def _render_next():
            start = offset[0]
            batch = runs[start:start + PAGE]
            for run in batch:
                _render_card(run)
            offset[0] += len(batch)

            # Remove old "Show more" button if present
            if more_btn_frame[0]:
                more_btn_frame[0].destroy()
                more_btn_frame[0] = None

            remaining = len(runs) - offset[0]
            if remaining > 0:
                f = tk.Frame(inner, bg=BG)
                f.pack(fill=tk.X, pady=(12, 0))
                lbl = tk.Label(
                    f,
                    text=f"Show {min(PAGE, remaining)} more  ({remaining} remaining) ▼",
                    bg=BG, fg=ACCENT, font=FONTS["small"], cursor="hand2",
                )
                lbl.pack()
                lbl.bind("<Button-1>", lambda _e: _render_next())
                more_btn_frame[0] = f

            tk.Frame(inner, bg=BG, height=8).pack()

        _render_next()

        dlg.deiconify()

    def _remove_suspended(self, suspended: list[str], pid: str):
        for handle in suspended:
            self._store.remove_entry_by_handle(pid, handle)
        print(f"\n[INFO] Cleared {len(suspended)} suspended handle(s) from creators: "
              f"{', '.join(suspended)}")
        self.after(0, self._refresh_creator_list)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
