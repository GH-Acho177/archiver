import asyncio
import os
import subprocess
import threading
import sys
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
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
from src.utils import TextRedirector, _LineWriter, _del

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
        self.title(f"Media Downloader v{APP_VERSION}")
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

        self._platform_ids:   list[str]              = list(PLATFORMS.keys())
        self._platform_sel:   tk.StringVar            = tk.StringVar(value=self._platform_ids[0])
        self._platform_pills: dict[str, tk.Label]     = {}
        self._mode_btns:      dict[str, tk.Label]       = {}
        self._from_days_var = tk.StringVar(value="0")
        self._from_days_sb: ttk.Spinbox | None = None

        # Users tab state
        self._users_title:  tk.StringVar              = tk.StringVar()
        self._entry_hint:   tk.StringVar              = tk.StringVar()
        self._user_raw:     dict[str, list[str]]       = {}
        self._user_rows:    dict[str, list[tk.Frame]]  = {}
        self._sel_row:      int | None                 = None
        self._users_canvas:    tk.Canvas                = None   # type: ignore
        self._settings_canvas: tk.Canvas               = None   # type: ignore
        self._users_inner:  tk.Frame                   = None   # type: ignore
        self._canvas_win:   int                        = 0
        self._cookie_status: dict[str, tk.StringVar]  = {}

        self._log_widget: scrolledtext.ScrolledText | None = None

        Path("config").mkdir(exist_ok=True)
        Path("downloads").mkdir(exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
        self._migrate_legacy_files()
        self._build_ui()
        self._refresh_from_date(self._platform_ids[0])

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
            "MediaDownloader",
            icon=self._tray_img,
            title=f"Media Downloader v{APP_VERSION}",
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
        nav_keys = ["nav.dashboard", "nav.users", "nav.settings", "nav.about"]
        for i, (_, _, text_lbl, _) in enumerate(self._nav_frames):
            if i < len(nav_keys):
                text_lbl.configure(text=self._t(nav_keys[i]))
        # Mode buttons
        for val, btn in self._mode_btns.items():
            btn.configure(text=self._t(f"mode.{val}"))
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
        self._refresh_pills()
        self._refresh_user_rows_theme()
        if self._log_widget:
            self._log_widget.configure(bg=c["log_bg"], fg=c["log_fg"])
            self._configure_log_tags()
        if hasattr(self, '_theme_toggle_btn'):
            self._theme_toggle_btn.configure(
                text=self._t("btn.switch_light") if self._current_theme == "dark"
                else self._t("btn.switch_dark"))
        self._refresh_chrome_theme()

    # ── UI skeleton ────────────────────────────────────────────────────────────
    def _build_ui(self):
        c = THEME_COLORS[self._current_theme]

        self._toolbar_sep = tk.Frame(self, bg=c["border"], height=1)
        self._toolbar_sep.pack(fill=tk.X, side=tk.TOP)

        # ── Status bar — packed to BOTTOM before main so it isn't squeezed ───
        self._build_statusbar(c)

        # ── Main: sidebar (160 px) + stacked content panels ───────────────────
        self._main = tk.Frame(self, bg=c["bg"])
        self._main.pack(fill=tk.BOTH, expand=True, side=tk.TOP)

        self._sidebar = tk.Frame(self._main, bg=c["panel"], width=int(180 * self._sf))
        self._sidebar.pack(fill=tk.Y, side=tk.LEFT)
        self._sidebar.pack_propagate(False)

        self._sidebar_sep = tk.Frame(self._main, bg=c["border"], width=1)
        self._sidebar_sep.pack(fill=tk.Y, side=tk.LEFT)

        self._content = tk.Frame(self._main, bg=c["bg"])
        self._content.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        # Stacked panels — all placed to fill content; .lift() reveals active
        dash      = tk.Frame(self._content, bg=c["bg"])
        users_tab = tk.Frame(self._content, bg=c["bg"])
        sett_tab  = tk.Frame(self._content, bg=c["bg"])
        about_tab = tk.Frame(self._content, bg=c["bg"])
        self._panels = [dash, users_tab, sett_tab, about_tab]
        # Panels are shown/hidden via pack/pack_forget — only the active one
        # is in the layout so tkinter never renders the others.

        self._build_dashboard(dash)
        self._build_users(users_tab)
        self._build_settings(sett_tab)
        self._build_about_panel(about_tab)

        # Sidebar nav items
        self._nav_frames: list[tuple] = []
        tk.Frame(self._sidebar, bg=c["panel"], height=int(10 * self._sf)).pack(fill=tk.X)
        for idx, (icon, key) in enumerate([
                ("◈", "nav.dashboard"), ("☰", "nav.users"),
                ("⚙", "nav.settings"), ("ℹ", "nav.about")]):
            self._build_nav_item(self._sidebar, idx, icon, key)

        self._active_nav = -1
        self._nav_select(0)

    # ── Status bar ─────────────────────────────────────────────────────────────
    def _build_statusbar(self, c):
        self._statusbar_sep = tk.Frame(self, bg=c["border"], height=1)
        self._statusbar_sep.pack(fill=tk.X, side=tk.BOTTOM)
        self._statusbar = tk.Frame(self, bg=c["panel"], height=int(30 * self._sf))
        self._statusbar.pack(fill=tk.X, side=tk.BOTTOM)
        self._statusbar.pack_propagate(False)
        self.status_var = tk.StringVar(value="Ready")
        self._status_lbl = tk.Label(
            self._statusbar, textvariable=self.status_var,
            font=FONTS["small"], bg=c["panel"], fg=c["text_dim"], padx=8)
        self._status_lbl.pack(side=tk.LEFT, pady=3)
        self.progress = ttk.Progressbar(
            self._statusbar, mode="indeterminate", length=120)
        self.progress.pack(side=tk.RIGHT, padx=8, pady=3)

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
        if idx == 1:
            pid = self._selected_pid()
            self._users_title.set(PLATFORMS[pid]["label"])
            self._entry_hint.set(self._entry_hint_text(pid))
            self._load_users_for(pid)
            self.bind_all("<MouseWheel>",
                lambda e: self._users_canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        elif idx == 2:
            self.bind_all("<MouseWheel>",
                lambda e: self._settings_canvas.yview_scroll(-1 * (e.delta // 120), "units"))

    def _refresh_chrome_theme(self):
        """Re-apply JetBrains palette to all chrome (non-ttk) widgets."""
        c = THEME_COLORS[self._current_theme]
        self._toolbar_sep.configure(bg=c["border"])
        self._statusbar.configure(bg=c["panel"])
        self._statusbar_sep.configure(bg=c["border"])
        self._status_lbl.configure(bg=c["panel"], fg=c["text_dim"])
        self._main.configure(bg=c["bg"])
        self._sidebar.configure(bg=c["panel"])
        self._sidebar_sep.configure(bg=c["border"])
        self._content.configure(bg=c["bg"])
        for p in self._panels:
            p.configure(bg=c["bg"])
        # Refresh sidebar top padding frame
        for w in self._sidebar.winfo_children():
            if isinstance(w, tk.Frame) and not any(
                    w is row for _, _, _, row in self._nav_frames):
                w.configure(bg=c["panel"])
        self._nav_select(self._active_nav)
        if self._mode_btns:
            self._refresh_mode_btns()

    # ── Dashboard ──────────────────────────────────────────────────────────────
    def _build_dashboard(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, padx=18, pady=(16, 6))

        def _sep(row):
            ttk.Separator(row, orient=tk.VERTICAL).pack(
                side=tk.LEFT, fill=tk.Y, padx=24, pady=3)

        def _label(row, key):
            lbl = ttk.Label(row, text=self._t(key), font=FONTS["small"],
                            foreground="#888888")
            lbl.pack(side=tk.LEFT, padx=(0, 14))
            self._reg(lbl, key)

        # ── Row 1: Platform  ·  Mode ───────────────────────────────────────────
        row1 = ttk.Frame(top)
        row1.pack(fill=tk.X, pady=(0, 6))

        _label(row1, "label.platform")
        for pid in self._platform_ids:
            self._build_platform_pill(row1, pid)
        self._refresh_pills()

        _sep(row1)

        _label(row1, "label.mode")
        self.mode_var = tk.StringVar(value="update")
        for val in ("update", "full"):
            btn = tk.Label(row1, text=self._t(f"mode.{val}"), font=FONTS["body"],
                           cursor="hand2", padx=8, pady=3,
                           relief=tk.FLAT, bd=0, highlightthickness=1)
            btn.pack(side=tk.LEFT, padx=(0, 2))
            btn.bind("<Button-1>", lambda _e, v=val: self._set_mode(v))
            self._mode_btns[val] = btn
        self._refresh_mode_btns()

        # ── Row 2: From date  ·  Actions  ·  Utilities  ──────────────────────
        row2 = ttk.Frame(top)
        row2.pack(fill=tk.X, pady=(0, 6))

        # Recent N days (full mode only) — wrapped in a frame for easy show/hide
        self._from_days_frame = ttk.Frame(row2)
        self._from_days_frame.pack(side=tk.LEFT)
        self._reg(
            ttk.Label(self._from_days_frame, text=self._t("label.last"),
                      foreground="#888888", font=FONTS["small"]),
            "label.last").pack(side=tk.LEFT, padx=(0, 4))
        self._from_days_sb = ttk.Spinbox(self._from_days_frame,
                                         textvariable=self._from_days_var,
                                         from_=0, to=3650, width=4,
                                         font=FONTS["mono"])
        self._from_days_sb.pack(side=tk.LEFT)
        self._reg(
            ttk.Label(self._from_days_frame, text=self._t("label.days_all"),
                      foreground="#888888", font=FONTS["small"]),
            "label.days_all").pack(side=tk.LEFT)
        self._refresh_mode_btns()   # apply initial show/hide

        _sep(row2)

        # Start / Stop
        self.start_btn = ttk.Button(row2, text=self._t("btn.start"),
                                    style="Accent.TButton", command=self.start)
        self._reg(self.start_btn, "btn.start")
        self.start_btn.pack(side=tk.LEFT, padx=(0, 14))
        self.stop_btn = ttk.Button(row2, text=self._t("btn.stop"),
                                   style="Danger.TButton",
                                   command=self.stop, state=tk.DISABLED)
        self._reg(self.stop_btn, "btn.stop")
        self.stop_btn.pack(side=tk.LEFT)

        _sep(row2)

        # Utilities
        for key, cmd in [
            ("btn.clear_log",  self.clear_log),
            ("btn.downloads",  self._open_downloads),
            ("btn.post_url",   self._download_post_url),
            ("btn.updates",    self._open_announcements),
        ]:
            self._reg(
                ttk.Button(row2, text=self._t(key), command=cmd),
                key).pack(side=tk.LEFT, padx=(0, 14))

        # ── Log ───────────────────────────────────────────────────────────────
        log_f = ttk.LabelFrame(parent, text=self._t("log.title"), padding=4)
        self._reg(log_f, "log.title")
        log_f.pack(fill=tk.BOTH, expand=True, padx=18, pady=(10, 14))

        c = THEME_COLORS[self._current_theme]
        self._log_widget = scrolledtext.ScrolledText(
            log_f, state="disabled", wrap=tk.WORD,
            bg=c["log_bg"], fg=c["log_fg"], insertbackground=c["log_fg"],
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

    # ── Platform pills ─────────────────────────────────────────────────────────
    def _build_platform_pill(self, parent, pid: str):
        cfg  = PLATFORMS[pid]
        c    = THEME_COLORS[self._current_theme]
        pill = tk.Label(
            parent,
            text=f"  {cfg['label']}  ",
            font=FONTS["body"],
            cursor="hand2",
            padx=4, pady=3,
            relief=tk.FLAT, bd=0,
            highlightthickness=1,
            highlightbackground=c["border"],
        )
        pill.pack(side=tk.LEFT, padx=(0, 4))
        pill.bind("<Button-1>", lambda _e, p=pid: self._select_platform(p))
        pill.bind("<Enter>",    lambda _e, p=pid: self._pill_hover(p, True))
        pill.bind("<Leave>",    lambda _e, p=pid: self._pill_hover(p, False))
        self._platform_pills[pid] = pill

    def _pill_hover(self, pid: str, entering: bool):
        if pid == self._platform_sel.get():
            return
        c  = THEME_COLORS[self._current_theme]
        bg = c["hover"] if entering else c["pill_bg"]
        self._platform_pills[pid].configure(bg=bg, highlightbackground=c["border"])

    def _refresh_pills(self):
        c = THEME_COLORS[self._current_theme]
        for pid, pill in self._platform_pills.items():
            cfg      = PLATFORMS[pid]
            selected = pid == self._platform_sel.get()
            pill.configure(
                bg=cfg["color"]  if selected else c["pill_bg"],
                fg="#ffffff"     if selected else c["text"],
                font=(*FONTS["body"], "bold") if selected else FONTS["body"],
                highlightbackground=cfg["color"] if selected else c["border"],
            )

    def _select_platform(self, pid: str):
        self._platform_sel.set(pid)
        self._refresh_pills()
        self._on_platform_change()

    # ── Mode toggle ────────────────────────────────────────────────────────────
    def _set_mode(self, val: str):
        self.mode_var.set(val)
        self._refresh_mode_btns()

    def _refresh_mode_btns(self):
        c       = THEME_COLORS[self._current_theme]
        is_full = self.mode_var.get() == "full"
        for val, btn in self._mode_btns.items():
            sel = val == self.mode_var.get()
            btn.configure(
                bg=c["accent"]  if sel else c["panel"],
                fg="#ffffff"    if sel else c["text"],
                highlightbackground=c["accent"] if sel else c["border"],
            )
        if hasattr(self, "_from_days_frame"):
            if is_full:
                self._from_days_frame.pack(side=tk.LEFT)
            else:
                self._from_days_frame.pack_forget()

    # ── Tab change ─────────────────────────────────────────────────────────────
    def _on_tab_changed(self, _event=None):
        idx = self._nb.index(self._nb.select())
        self.unbind_all("<MouseWheel>")
        if idx == 1:
            pid = self._selected_pid()
            self._users_title.set(PLATFORMS[pid]["label"])
            self._entry_hint.set(self._entry_hint_text(pid))
            self._load_users_for(pid)
            self.bind_all("<MouseWheel>",
                lambda e: self._users_canvas.yview_scroll(-1 * (e.delta // 120), "units"))
        elif idx == 2:
            self.bind_all("<MouseWheel>",
                lambda e: self._settings_canvas.yview_scroll(-1 * (e.delta // 120), "units"))

    # ── Platform helpers ───────────────────────────────────────────────────────
    def _selected_pid(self) -> str:
        return self._platform_sel.get()

    def _entry_hint_text(self, pid: str) -> str:
        hints = {
            "x":        "Enter X username  (e.g. elonmusk)",
            "douyin":   "Paste a profile URL or sec_uid",
            "bilibili": "Enter UID or paste space URL",
        }
        return hints.get(pid, "Enter account identifier")

    def _on_platform_change(self, _event=None):
        pid = self._selected_pid()
        self._users_title.set(PLATFORMS[pid]["label"])
        self._entry_hint.set(self._entry_hint_text(pid))
        self._load_users_for(pid)
        self._refresh_from_date(pid)

    def _refresh_from_date(self, _pid: str):
        """Reset 'Last N days' spinbox to 0 (= from last run)."""
        self._from_days_var.set("0")

    # ── Users ──────────────────────────────────────────────────────────────────
    def _build_users(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        self._users_title.set(PLATFORMS[self._platform_ids[0]]["label"])
        ttk.Label(frame, textvariable=self._users_title,
                  font=FONTS["heading"]
                  ).grid(row=0, column=0, sticky="w", pady=(0, 6))

        inner = ttk.LabelFrame(frame, text=self._t("users.list"), padding=6)
        self._reg(inner, "users.list")
        inner.grid(row=1, column=0, sticky="nsew")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)

        c = THEME_COLORS[self._current_theme]

        self._users_canvas = tk.Canvas(inner, bg=c["list_bg"], highlightthickness=0)
        sb = ttk.Scrollbar(inner, command=self._users_canvas.yview)
        self._users_canvas.configure(yscrollcommand=sb.set)
        self._users_canvas.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        sb.grid(row=0, column=1, sticky="ns", pady=(0, 6))

        self._users_inner = tk.Frame(self._users_canvas, bg=c["list_bg"])
        self._canvas_win  = self._users_canvas.create_window(
            (0, 0), window=self._users_inner, anchor="nw")
        self._users_inner.bind(
            "<Configure>",
            lambda e: self._users_canvas.configure(
                scrollregion=self._users_canvas.bbox("all")))
        self._users_canvas.bind(
            "<Configure>",
            lambda e: self._users_canvas.itemconfig(self._canvas_win, width=e.width))

        entry_f = ttk.Frame(inner)
        entry_f.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self._users_entry = ttk.Entry(entry_f, font=FONTS["body"])
        self._users_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self._users_entry.bind("<Return>", lambda _: self._add_user())
        self._reg(ttk.Button(entry_f, text=self._t("btn.add"),    command=self._add_user),
                  "btn.add").pack(side=tk.LEFT, padx=(0, 4))
        self._reg(ttk.Button(entry_f, text=self._t("btn.remove"), command=self._remove_user),
                  "btn.remove").pack(side=tk.LEFT)

        self._entry_hint.set(self._entry_hint_text(self._platform_ids[0]))
        ttk.Label(inner, textvariable=self._entry_hint,
                  font=FONTS["small"]).grid(row=2, column=0, columnspan=2,
                                            sticky="w", pady=(2, 0))

        self._load_users_for(self._platform_ids[0])

    def _load_users_for(self, pid: str):
        cfg = PLATFORMS[pid]
        for w in self._users_inner.winfo_children():
            w.destroy()
        self._user_raw[pid]  = []
        self._user_rows[pid] = []
        self._sel_row        = None

        p = Path(cfg["users_file"])
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                display = line.split("|")[0] if cfg["entry_format"] == "name|id" else line
                self._user_raw[pid].append(line)
                self._append_user_row(pid, display)

    def _append_user_row(self, pid: str, display: str):
        c   = THEME_COLORS[self._current_theme]
        idx = len(self._user_rows.get(pid, []))

        row = tk.Frame(self._users_inner, bg=c["list_bg"], cursor="hand2")
        row.pack(fill=tk.X, pady=0)

        # 3px left accent bar — mirrors the sidebar selection style
        bar = tk.Frame(row, bg=c["list_bg"], width=3)
        bar.pack(side=tk.LEFT, fill=tk.Y)

        lbl = tk.Label(row, text=display, bg=c["list_bg"], fg=c["list_fg"],
                       font=FONTS["body"], anchor="w")
        lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 8), pady=5)

        for w in (row, bar, lbl):
            w.bind("<Button-1>", lambda _e, i=idx, p=pid:
                   self._select_user_row(i, p))

        self._user_rows.setdefault(pid, []).append(row)

    def _select_user_row(self, idx: int, pid: str):
        self._sel_row = idx
        c = THEME_COLORS[self._current_theme]
        for i, row in enumerate(self._user_rows.get(pid, [])):
            sel = i == idx
            bg  = c["hover"] if sel else c["list_bg"]
            row.configure(bg=bg)
            for w in row.winfo_children():
                if isinstance(w, tk.Frame):   # accent bar
                    w.configure(bg=c["accent"] if sel else c["list_bg"])
                elif isinstance(w, tk.Label):
                    w.configure(bg=bg, fg=c["list_fg"])

    def _refresh_user_rows_theme(self):
        if self._users_canvas is None:
            return
        c   = THEME_COLORS[self._current_theme]
        pid = self._selected_pid()
        self._users_canvas.configure(bg=c["list_bg"])
        self._users_inner.configure(bg=c["list_bg"])
        for i, row in enumerate(self._user_rows.get(pid, [])):
            sel = i == self._sel_row
            bg  = c["hover"] if sel else c["list_bg"]
            row.configure(bg=bg)
            for w in row.winfo_children():
                if isinstance(w, tk.Frame):
                    w.configure(bg=c["accent"] if sel else c["list_bg"])
                elif isinstance(w, tk.Label):
                    w.configure(bg=bg, fg=c["list_fg"])

    def _add_user(self):
        pid = self._selected_pid()
        cfg = PLATFORMS[pid]
        if cfg["entry_format"] == "name|id":
            self._add_user_named(pid)
        else:
            username = self._users_entry.get().strip().lstrip("@")
            if username and username not in self._user_raw.get(pid, []):
                self._user_raw.setdefault(pid, []).append(username)
                self._append_user_row(pid, username)
                self._save_users(pid)
            self._users_entry.delete(0, tk.END)

    def _add_user_named(self, pid: str):
        import re, http.client, ssl, urllib.parse, json as _json

        raw_input = self._users_entry.get().strip()
        self._users_entry.delete(0, tk.END)
        if not raw_input:
            return

        cfg = PLATFORMS[pid]
        orig_hint = self._entry_hint.get()

        if pid == "bilibili":
            # Accept: space.bilibili.com/{uid} URL or bare UID
            m = re.search(r"space\.bilibili\.com/(\d+)", raw_input)
            uid = m.group(1) if m else raw_input.strip()
            if not uid.isdigit():
                messagebox.showwarning("Invalid input",
                                       "Please enter a numeric UID or a bilibili space URL.")
                return

            self._entry_hint.set("Resolving username…")
            self._users_entry.configure(state="disabled")

            def fetch_bilibili():
                nickname = None
                error    = None
                try:
                    ctx  = ssl.create_default_context()
                    conn = http.client.HTTPSConnection("api.bilibili.com", timeout=10, context=ctx)
                    conn.request("GET", f"/x/web-interface/card?mid={uid}", headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                                      "Chrome/120.0.0.0 Safari/537.36",
                        "Referer":    "https://www.bilibili.com/",
                        "Accept":     "application/json",
                    })
                    resp = conn.getresponse()
                    data = _json.loads(resp.read())
                    conn.close()
                    if data.get("code") != 0:
                        error = f"UID {uid} not found (code {data.get('code')})."
                    else:
                        nickname = (data.get("data") or {}).get("card", {}).get("name") or None
                except Exception as exc:
                    error = str(exc)

                def apply():
                    self._users_entry.configure(state="normal")
                    self._entry_hint.set(orig_hint)
                    if error:
                        messagebox.showerror("Bilibili lookup failed", error)
                        return
                    display = nickname if nickname else uid
                    raw     = f"{display}|{uid}"
                    if raw not in self._user_raw.get(pid, []):
                        self._user_raw.setdefault(pid, []).append(raw)
                        self._append_user_row(pid, display)
                        self._save_users(pid)

                self.after(0, apply)

            threading.Thread(target=fetch_bilibili, daemon=True).start()
            return

        # ── Douyin ────────────────────────────────────────────────────────────
        # Accept: profile URL or bare sec_uid
        m = re.search(r"/user/([^/?#]+)", raw_input)
        sec_uid = m.group(1) if m else raw_input
        if not sec_uid:
            return

        self._entry_hint.set("Resolving username…")
        self._users_entry.configure(state="disabled")

        def fetch():
            nickname = None
            try:
                cookies: dict[str, str] = {}
                for line in Path(cfg["cookies_file"]).read_text(
                        encoding="utf-8").splitlines():
                    if line.startswith("#") or not line.strip():
                        continue
                    fields = line.split("\t")
                    if len(fields) >= 7:
                        cookies[fields[5].strip()] = fields[6].strip()

                cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
                params = urllib.parse.urlencode({
                    "sec_user_id":    sec_uid,
                    "aid":            "6383",
                    "cookie_enabled": "true",
                    "platform":       "PC",
                })
                hdrs = {
                    "Cookie":     cookie_header,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Referer":    "https://www.douyin.com/",
                    "Accept":     "application/json",
                }
                ctx  = ssl.create_default_context()
                conn = http.client.HTTPSConnection("www.douyin.com", timeout=10, context=ctx)
                conn.request("GET",
                             f"/aweme/v1/web/user/profile/other/?{params}",
                             headers=hdrs)
                resp = conn.getresponse()
                data = _json.loads(resp.read())
                conn.close()
                nickname = (data.get("user") or {}).get("nickname") or None
            except Exception:
                pass

            display = nickname if nickname else sec_uid
            raw     = f"{display}|{sec_uid}"

            def apply():
                self._users_entry.configure(state="normal")
                self._entry_hint.set(orig_hint)
                if raw not in self._user_raw.get(pid, []):
                    self._user_raw.setdefault(pid, []).append(raw)
                    self._append_user_row(pid, display)
                    self._save_users(pid)

            self.after(0, apply)

        threading.Thread(target=fetch, daemon=True).start()

    def _remove_user(self):
        pid = self._selected_pid()
        idx = self._sel_row
        if idx is None:
            return
        raws = self._user_raw.get(pid, [])
        if 0 <= idx < len(raws):
            raws.pop(idx)
            row = self._user_rows.get(pid, []).pop(idx)
            row.destroy()
            self._sel_row = None
            self._save_users(pid)

    def _save_users(self, pid: str | None = None):
        if pid is None:
            pid = self._selected_pid()
        path  = PLATFORMS[pid]["users_file"]
        lines = self._user_raw.get(pid, [])
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── Settings ───────────────────────────────────────────────────────────────
    def _build_settings(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0)
        sb     = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._settings_canvas = canvas

        outer = ttk.Frame(canvas, padding=(24, 18))
        win   = canvas.create_window((0, 0), window=outer, anchor="nw")

        def _on_resize(e):
            canvas.itemconfig(win, width=e.width)
        canvas.bind("<Configure>", _on_resize)

        def _on_frame_resize(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        outer.bind("<Configure>", _on_frame_resize)

        self._reg(
            ttk.Label(outer, text=self._t("settings.auth_hint"),
                      font=FONTS["small"], foreground="#888888"),
            "settings.auth_hint"
        ).pack(anchor="w", pady=(0, 10))

        for pid, cfg in PLATFORMS.items():
            af = ttk.LabelFrame(outer, text=f"Authentication  —  {cfg['label']}", padding=14)
            af.pack(fill=tk.X, pady=(0, 10))
            self._build_auth_section(af, pid, cfg)

        def _options_section(title_key, rows):
            f = ttk.LabelFrame(outer, text=self._t(title_key), padding=14)
            self._reg(f, title_key)
            f.pack(fill=tk.X, pady=(0, 10))
            for r, (label_key, attr, default, lo, hi) in enumerate(rows):
                lbl = ttk.Label(f, text=self._t(label_key))
                self._reg(lbl, label_key)
                lbl.grid(row=r, column=0, sticky="w", padx=6, pady=8)
                saved = self._load_setting(attr, default)
                sp = ttk.Spinbox(f, from_=lo, to=hi, increment=1, width=12, font=FONTS["body"])
                sp.set(saved)
                sp.grid(row=r, column=1, sticky="w", padx=14, pady=8)
                sp.bind("<<Increment>>", lambda _e, a=attr, w=sp: self._save_setting(a, w.get()))
                sp.bind("<<Decrement>>", lambda _e, a=attr, w=sp: self._save_setting(a, w.get()))
                sp.bind("<FocusOut>",    lambda _e, a=attr, w=sp: self._save_setting(a, w.get()))
                setattr(self, attr, sp)

        _options_section("settings.parallel_opts", [
            ("settings.workers", "parallel_workers", 1, 1, 10),
        ])

        # ── Download location ──────────────────────────────────────────────────
        dlf = ttk.LabelFrame(outer, text=self._t("settings.dl_location"), padding=14)
        self._reg(dlf, "settings.dl_location")
        dlf.pack(fill=tk.X, pady=(0, 10))

        path_row = ttk.Frame(dlf)
        path_row.pack(fill=tk.X)

        self._dl_path_var = tk.StringVar(value=str(self._get_download_dir()))
        ttk.Entry(path_row, textvariable=self._dl_path_var, font=FONTS["body"]).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        def _browse_dl():
            folder = filedialog.askdirectory(
                title="Select download folder",
                initialdir=str(self._get_download_dir()),
            )
            if folder:
                self._dl_path_var.set(folder)
                Path(DOWNLOAD_PATH_FILE).parent.mkdir(parents=True, exist_ok=True)
                Path(DOWNLOAD_PATH_FILE).write_text(folder, encoding="utf-8")

        self._reg(ttk.Button(path_row, text=self._t("btn.browse"), command=_browse_dl),
                  "btn.browse").pack(side=tk.LEFT)

        # ── Appearance ────────────────────────────────────────────────────────
        apf = ttk.LabelFrame(outer, text=self._t("settings.appearance"), padding=14)
        self._reg(apf, "settings.appearance")
        apf.pack(fill=tk.X, pady=(0, 10))

        self._reg(ttk.Label(apf, text=self._t("label.theme")),
                  "label.theme").grid(row=0, column=0, sticky="w", padx=6, pady=8)
        self._theme_toggle_btn = ttk.Button(
            apf,
            text=self._t("btn.switch_light") if self._current_theme == "dark"
                 else self._t("btn.switch_dark"),
            command=self._toggle_theme,
        )
        self._theme_toggle_btn.grid(row=0, column=1, sticky="w", padx=14, pady=8)

        self._reg(ttk.Label(apf, text=self._t("label.language")),
                  "label.language").grid(row=1, column=0, sticky="w", padx=6, pady=8)
        lang_cb = ttk.Combobox(apf, state="readonly", width=12,
                               font=FONTS["body"], values=["English", "中文"])
        lang_cb.set("English" if self._lang == "en" else "中文")
        lang_cb.grid(row=1, column=1, sticky="w", padx=14, pady=8)
        lang_cb.bind("<<ComboboxSelected>>",
                     lambda _e: self._set_lang("en" if lang_cb.get() == "English" else "zh"))

        dbf = ttk.LabelFrame(outer, text=self._t("settings.database"), padding=14)
        self._reg(dbf, "settings.database")
        dbf.pack(fill=tk.X)
        self._reg(
            ttk.Label(dbf, text=self._t("settings.db_hint"),
                      font=FONTS["small"], foreground="#888888"),
            "settings.db_hint").pack(anchor="w", pady=(0, 8))
        self._reg(ttk.Button(dbf, text=self._t("btn.reset_db"), style="Danger.TButton",
                             command=self._reset_db),
                  "btn.reset_db").pack(anchor="w")

    def _build_about_panel(self, parent):
        import webbrowser

        parent.pack_propagate(False)
        # Fill the whole panel with a ttk.Frame so the sv_ttk background is
        # uniform — avoids the colour mismatch between tk.Frame and ttk widgets.
        bg = ttk.Frame(parent)
        bg.pack(fill=tk.BOTH, expand=True)

        f = ttk.Frame(bg, padding=(40, 32))
        f.place(relx=0.5, rely=0.38, anchor="center")

        ttk.Label(f, text="Media Downloader", font=FONTS["title"]).pack()
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
        pid   = self._selected_pid()
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

        self._refresh_from_date(pid)
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
        folder = (self._get_download_dir() / self._selected_pid()).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(folder)])

    # ── DB viewer ──────────────────────────────────────────────────────────────
    # ── Post URL download ──────────────────────────────────────────────────────
    def _download_post_url(self):
        pid = self._selected_pid()
        cfg = PLATFORMS[pid]

        dlg = tk.Toplevel(self)
        dlg.title("Download Post URL")
        dlg.resizable(False, False)
        dlg.grab_set()
        self._centre_dialog(dlg, 680, 230)

        ttk.Label(dlg, text=f"Platform: {cfg['label']}",
                  font=FONTS["heading"]).grid(
            row=0, column=0, columnspan=2, padx=20, pady=(18, 8), sticky="w")
        ttk.Label(dlg, text="Post URL:").grid(row=1, column=0, padx=20, pady=6, sticky="w")
        url_var   = tk.StringVar()
        url_entry = ttk.Entry(dlg, textvariable=url_var, width=62, font=FONTS["body"])
        url_entry.grid(row=1, column=1, padx=(0, 20), pady=6, sticky="ew")
        dlg.columnconfigure(1, weight=1)
        url_entry.focus_set()

        btn_row = ttk.Frame(dlg)
        btn_row.grid(row=2, column=0, columnspan=2, pady=(12, 18))

        def start():
            url = url_var.get().strip()
            if not url:
                return
            if not Path(cfg["cookies_file"]).exists():
                messagebox.showwarning(
                    "No cookies",
                    f"Cookies not found for {cfg['label']}.\n"
                    f"Go to Settings → Authentication.")
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

        pid = self._selected_pid()
        cfg = PLATFORMS[pid]

        if not Path(cfg["cookies_file"]).exists():
            messagebox.showwarning(
                "Cannot start",
                f"No cookies found for {cfg['label']}.\n\n"
                "Go to Settings → Authentication to add cookies.")
            return

        all_users = self._user_raw.get(pid, [])
        if not all_users:
            messagebox.showwarning(
                "Cannot start",
                f"No users added for {cfg['label']}.\n\n"
                "Go to the Users tab to add some.")
            return

        import datetime as _dtv
        try:
            n = int(self._from_days_var.get())
            if n < 0:
                raise ValueError
        except (ValueError, TypeError):
            messagebox.showwarning("Invalid value",
                                   "Days must be a whole number ≥ 0.")
            return

        # 0 = no date filter; stop when archive hits an already-downloaded post
        from_date = (
            (_dtv.date.today() - _dtv.timedelta(days=n)).isoformat()
            if n > 0 else ""
        )

        selected  = self._pick_accounts(pid, all_users)
        if not selected:
            return

        self._pending_run_result = None
        self.running = True
        self.stop_flag.clear()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress.start(12)
        self.status_var.set("Running…")

        workers = int(self.parallel_workers.get())

        threading.Thread(
            target=self._worker,
            args=(pid,
                  self.mode_var.get() == "full",
                  2,  # sleep_req — gallery-dl default
                  5,  # sleep_user — gallery-dl default
                  selected,
                  from_date,
                  workers),
            daemon=True,
        ).start()

    def _pick_accounts(self, pid: str, all_users: list) -> list:
        """Show account picker dialog. Returns selected user entries or [] if cancelled."""
        cfg = PLATFORMS[pid]
        c   = THEME_COLORS[self._current_theme]

        dlg = tk.Toplevel(self)
        dlg.title(f"Select accounts — {cfg['label']}")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        self._centre_dialog(dlg, 900, min(72 * len(all_users) + 300, 1000))

        outer = ttk.Frame(dlg, padding=24)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text=cfg["label"], font=FONTS["heading"]).pack(
            anchor=tk.W, pady=(0, 4))
        ttk.Label(outer, text="Choose accounts for this run:",
                  font=FONTS["small"], foreground="#888888").pack(
            anchor=tk.W, pady=(0, 14))

        # Scrollable checkbox list — use theme background so ttk.Checkbutton blends in
        _list_bg = str(ttk.Style().lookup("TFrame", "background")) or c["bg"]

        list_frame = tk.Frame(outer, bg=_list_bg,
                              highlightthickness=1,
                              highlightbackground=c["border"])
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 14))

        canvas = tk.Canvas(list_frame, bg=_list_bg,
                           highlightthickness=0, width=750,
                           height=min(64 * len(all_users), 620))
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = tk.Frame(canvas, bg=_list_bg)
        _win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(_win_id, width=e.width))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)
        inner.bind_all("<MouseWheel>", _on_mousewheel)
        dlg.bind("<Destroy>", lambda _e: inner.unbind_all("<MouseWheel>"))

        _accent = c["accent"]
        _hover  = c["hover"]
        _fg     = c["text"]

        vars_ = []
        for user in all_users:
            v    = tk.BooleanVar(value=False)
            name = user.split("|")[0]

            row = tk.Frame(inner, bg=_list_bg, cursor="hand2")
            row.pack(fill=tk.X)

            # Accent bar on left — visible when selected
            bar = tk.Frame(row, bg=_list_bg, width=4)
            bar.pack(side=tk.LEFT, fill=tk.Y)

            lbl = tk.Label(row, text=name, font=FONTS["small"],
                           bg=_list_bg, fg=_fg, anchor="w")
            lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=14, pady=11)

            # Check mark on right — visible when selected
            chk = tk.Label(row, text="", font=FONTS["body"],
                           bg=_list_bg, fg=_accent, width=2, anchor="center")
            chk.pack(side=tk.RIGHT, padx=12, pady=11)

            def _toggle(_e, _v=v, _bar=bar, _chk=chk):
                _v.set(not _v.get())
                if _v.get():
                    _bar.configure(bg=_accent)
                    _chk.configure(text="✓")
                else:
                    _bar.configure(bg=str(_bar.master.cget("bg")))
                    _chk.configure(text="")

            def _enter(_e, _row=row, _bar=bar, _lbl=lbl, _chk=chk, _v=v):
                for w in (_row, _lbl, _chk): w.configure(bg=_hover)
                if not _v.get(): _bar.configure(bg=_hover)

            def _leave(_e, _row=row, _bar=bar, _lbl=lbl, _chk=chk, _v=v):
                for w in (_row, _lbl, _chk): w.configure(bg=_list_bg)
                if not _v.get(): _bar.configure(bg=_list_bg)

            for w in (row, lbl, chk):
                w.bind("<Button-1>", _toggle)
                w.bind("<Enter>",    _enter)
                w.bind("<Leave>",    _leave)

            vars_.append((v, user, bar, chk))

        def _set_all(checked: bool):
            for _v, _, _bar, _chk in vars_:
                _v.set(checked)
                if checked:
                    _bar.configure(bg=_accent)
                    _chk.configure(text="✓")
                else:
                    _bar.configure(bg=_list_bg)
                    _chk.configure(text="")

        # Select all / clear
        sel_row = ttk.Frame(outer)
        sel_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Button(sel_row, text="All",
                   command=lambda: _set_all(True)).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(sel_row, text="None",
                   command=lambda: _set_all(False)).pack(side=tk.LEFT)

        # Result container
        result: list = []

        def _confirm():
            chosen = [u for v, u, *_ in vars_ if v.get()]
            if not chosen:
                messagebox.showwarning("No accounts selected",
                                       "Select at least one account.",
                                       parent=dlg)
                return
            result.extend(chosen)
            dlg.destroy()

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Cancel",
                   command=dlg.destroy).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(btn_row, text="Start", style="Accent.TButton",
                   command=_confirm).pack(side=tk.RIGHT)

        dlg.bind("<Return>", lambda _e: _confirm())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        self.wait_window(dlg)
        return result

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
        self.progress.stop()
        self.status_var.set("Stopped" if self.stop_flag.is_set() else "Done")
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

    # ── Worker ─────────────────────────────────────────────────────────────────
    def _worker(self, pid: str, is_full: bool, sleep_req: int, sleep_user: int,
                users_override: list | None = None, from_date: str = "", workers: int = 1):
        import time
        _run_start = time.time()
        _run_key   = str(int(_run_start * 1000))
        old_stdout = sys.stdout
        sys.stdout = TextRedirector(self.log, self)

        try:
            cfg          = PLATFORMS[pid]
            cookies_file = cfg["cookies_file"]
            users_file   = cfg["users_file"]
            import datetime as _dt
            import re as _re
            _today    = _dt.date.today().isoformat()
            base_dir  = self._get_download_dir().resolve() / pid

            mode_label = "Full" if is_full else "Update"
            print(f"Platform : {cfg['label']}")
            print(f"Mode     : {mode_label}")
            if from_date:
                print(f"From     : {from_date}")
            print()

            if not Path(cookies_file).exists():
                print(f"[SKIP] {cookies_file} not found. "
                      f"Go to Settings → Authentication to add cookies.\n")
                return

            if not Path(users_file).exists() or \
                    not Path(users_file).read_text(encoding="utf-8").strip():
                print(f"[SKIP] No users in {users_file}.\n")
                return

            all_users = [u.strip() for u in
                         Path(users_file).read_text(encoding="utf-8").splitlines()
                         if u.strip()]
            users     = users_override if users_override is not None else all_users
            print(f"Users : {len(users)} / {len(all_users)}\n")

            suspended: list[str] = []
            _update_results: list[dict] = []
            downloader = cfg.get("downloader", "gallery-dl")

            def _build_run(extra_user=None, stopped=True):
                import datetime as _dt2, json as _json2  # noqa: F401 (used below)
                elapsed = time.time() - _run_start
                m, s    = divmod(int(elapsed), 60)
                users_  = list(_update_results)
                if extra_user:
                    users_ = users_ + [extra_user]
                return {
                    "run_key":  _run_key,
                    "date":     _dt2.date.today().isoformat(),
                    "time":     _dt2.datetime.now().strftime("%H:%M"),
                    "duration": f"{m}m {s}s" if m else f"{s}s",
                    "platform": pid,
                    "mode":     "Full" if is_full else "Update",
                    "stopped":  stopped,
                    "users":    users_,
                }

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

            if downloader == "f2":
                # ── Parallel f2 (Douyin) ──────────────────────────────────────
                import json as _json, datetime as _dt
                from concurrent.futures import ThreadPoolExecutor, as_completed

                cookie_str  = self._netscape_to_cookie_str(cookies_file)
                _config_dir = Path("config").resolve()
                _state_lock = threading.Lock()   # guards last-run file + result lists

                def _f2_task(user, idx):
                    display      = user.split("|")[0]
                    safe_display = _re.sub(r'[\\/:*?"<>|]', "_", display).strip()
                    sec_uid      = user.split("|")[-1]
                    today        = _dt.date.today().isoformat()
                    url          = cfg["url_fn"](user)
                    tag          = f"[{display}]"

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

                    print(f"\n{'─'*50}")
                    print(f"[{idx+1}/{len(users)}] {display}")
                    print(f"{'─'*50}")
                    print(f"  Interval : {interval}")

                    if self.stop_flag.is_set():
                        return None, False, user

                    _user_dir_f = base_dir / safe_display
                    _before     = set(_user_dir_f.glob("*")) if _user_dir_f.exists() else set()
                    _user_dir_f.mkdir(parents=True, exist_ok=True)
                    print(f"  Save to  : downloads/douyin/{safe_display}")

                    import f2_user as _f2_user
                    user_suspended = False

                    def _line_cb(line):
                        nonlocal user_suspended
                        if "[error]" in line and any(k in line for k in (
                                "could not be found", "UserUnavailable", "has been suspended")):
                            user_suspended = True

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

                    # ── Collect results ───────────────────────────────────────
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
                            print(f"  {tag} ⚠ Corrupt ({f.stat().st_size:,} B): {f.name} — deleted")
                            f.unlink(missing_ok=True)
                        hint = "will re-download now" if is_full else "run Full mode to re-download"
                        print(f"  {tag} → {corrupt_count} corrupt file(s) removed ({hint})")
                        new_count = max(0, new_count - corrupt_count)

                    if user_suspended:
                        print(f"  ⚠ {display} appears suspended/deleted — will be removed")
                        return None, True, user

                    # ── Persist last-run date ─────────────────────────────────
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

                    return {
                        "display": display,
                        "count":   new_count,
                        "corrupt": corrupt_count,
                        "files":   new_names,
                        "folder":  str(_user_dl_folder),
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
                            suspended.append(user_entry)
                        elif result:
                            _update_results.append(result)

            elif downloader == "yt-dlp":
                # ── yt-dlp (parallel when workers > 1) ───────────────────────
                from concurrent.futures import ThreadPoolExecutor, as_completed
                dateafter = from_date.replace("-", "") if from_date else None

                def _ytdlp_task(user, idx):
                    if self.stop_flag.is_set():
                        return None, False, user
                    display      = user.split("|")[0]
                    safe_display = _re.sub(r'[\\/:*?"<>|]', "_", display).strip()
                    tag          = f"[{display}]" if workers > 1 else ""
                    prefix       = f"  {tag} " if tag else "  "
                    print(f"\n{'─'*50}")
                    print(f"[{idx+1}/{len(users)}] {display}")
                    print(f"{'─'*50}")
                    url          = cfg["url_fn"](user)
                    archive_file = str(Path("config") / f"{pid}_downloaded.txt")
                    user_dir     = base_dir / safe_display
                    user_dir.mkdir(parents=True, exist_ok=True)
                    _before      = set(user_dir.glob("*"))
                    print(f"{prefix}Save to  : {user_dir}")
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
                    if workers == 1:
                        self._proc = proc
                    with self._procs_lock:
                        self._procs.append(proc)
                    user_suspended       = False
                    _completed_posts: list[str] = []
                    try:
                        for line in proc.stdout:
                            print(f"{prefix}{line}", end="")
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
                            print(f"{prefix}⚠ Corrupt/partial ({f.stat().st_size:,} B): {f.name} — deleted")
                            f.unlink(missing_ok=True)
                        hint = "will re-download now" if is_full else "run Full mode to re-download"
                        print(f"{prefix}→ {corrupt_count} corrupt file(s) removed ({hint})")
                        new_count = max(0, new_count - corrupt_count)

                    if user_suspended:
                        print(f"{prefix}⚠ {display} appears suspended/deleted — will be removed")
                        return None, True, user

                    return {
                        "display": display,
                        "count":   new_count,
                        "corrupt": corrupt_count,
                        "files":   new_names,
                        "folder":  str(_user_dl_folder),
                    }, False, user

                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = {pool.submit(_ytdlp_task, u, i): u
                            for i, u in enumerate(users)}
                    for i, fut in enumerate(as_completed(futs)):
                        result, is_susp, user = fut.result()
                        if is_susp:
                            suspended.append(user)
                        elif result:
                            _update_results.append(result)
                            _upsert_run(_build_run())
                        if workers == 1 and not self.stop_flag.is_set() and i < len(users) - 1:
                            print(f"\nWaiting {sleep_user}s…")
                            time.sleep(sleep_user)

            else:
                # ── Sequential gallery-dl (X / Twitter) ──────────────────────
                for i, user in enumerate(users):
                    if self.stop_flag.is_set():
                        break

                    display      = user.split("|")[0]
                    safe_display = _re.sub(r'[\\/:*?"<>|]', "_", display).strip()
                    print(f"\n{'─'*50}")
                    print(f"[{i+1}/{len(users)}] {display}")
                    print(f"{'─'*50}")

                    url          = cfg["url_fn"](user)
                    archive_file = str(Path("config") / f"{pid}_downloaded.db")
                    user_dir     = base_dir / safe_display
                    user_dir.mkdir(parents=True, exist_ok=True)
                    _gdl_before  = set(user_dir.glob("*"))
                    print(f"  Save to  : {user_dir}")
                    cmd = [
                        GDL,
                        "--cookies", cookies_file,
                        *(["--download-archive", archive_file] if not is_full else []),
                        *(["-o", f"extractor.date-min={from_date}T00:00:00"] if from_date else []),
                        "--sleep-request", str(sleep_req),
                        "-D", str(user_dir),
                        url,
                    ]
                    self._proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace",
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    user_suspended = False
                    for line in self._proc.stdout:
                        if "api.day.app" in line or "Bark notification" in line:
                            continue
                        print(line, end="")
                        if "[error]" in line and any(k in line for k in (
                                "could not be found", "UserUnavailable", "has been suspended")):
                            user_suspended = True
                        if not is_full and "# " in line:
                            self._proc.terminate()
                            print("\n  → Up to date.")
                            break
                    self._proc.wait()

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
                            print(f"  ⚠ Corrupt/partial ({f.stat().st_size:,} B): {f.name} — deleted")
                            f.unlink(missing_ok=True)
                        hint = "will re-download now" if is_full else "run Full mode to re-download"
                        print(f"  → {corrupt_count} corrupt file(s) removed ({hint})")
                        new_count = max(0, new_count - corrupt_count)

                    if user_suspended:
                        suspended.append(user)
                        print(f"  ⚠ {user} appears suspended/deleted — will be removed")
                    else:
                        _update_results.append({
                            "display": display,
                            "count":   new_count,
                            "corrupt": corrupt_count,
                            "files":   new_names,
                            "folder":  str(_user_dl_folder),
                        })

                    if not self.stop_flag.is_set() and i < len(users) - 1:
                        print(f"\nWaiting {sleep_user}s…")
                        time.sleep(sleep_user)

            if suspended:
                self._remove_suspended(suspended, users_file, pid)

            _elapsed   = time.time() - _run_start
            _mins, _secs = divmod(int(_elapsed), 60)
            _duration  = f"{_mins}m {_secs}s" if _mins else f"{_secs}s"
            _stopped   = self.stop_flag.is_set()

            if _update_results:
                run_result = _build_run(stopped=_stopped)
                self._pending_run_result = run_result
                _upsert_run(run_result)

            if not _stopped:
                print("\n✓ ALL DONE.")
            else:
                print("\n■ Stopped.")

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
            pid_key  = run.get("platform", "")
            platform = PLATFORMS.get(pid_key, {}).get("label", pid_key)
            mode     = run.get("mode", "")
            users    = run.get("users", [])
            total    = sum(u.get("count", 0) for u in users)
            total_corrupt = sum(u.get("corrupt", 0) for u in users)

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

            if platform:
                p_color = PLATFORMS.get(pid_key, {}).get("color", "#2d2d2d")
                tk.Label(tags, text=f" {platform} ", bg=p_color, fg="#000000",
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
                count   = u.get("count",   0)
                corrupt = u.get("corrupt", 0)
                files   = u.get("files",   [])
                folder  = u.get("folder",  "")
                display = u.get("display", "")

                row = tk.Frame(body, bg=BG)
                row.pack(fill=tk.X, padx=10, pady=1)

                badge_fg = "#4ec94e" if count > 0 else FG_DIM
                tk.Label(row, text=f"+{count}", bg=BG, fg=badge_fg,
                         font=(*FONTS["small"], "bold"),
                         width=4, anchor=tk.E).pack(side=tk.LEFT)

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

    def _remove_suspended(self, suspended: list[str], users_file: str, pid: str):
        suspended_set = set(suspended)
        path          = Path(users_file)
        remaining     = [u for u in path.read_text(encoding="utf-8").splitlines()
                         if u.strip() and u.strip() not in suspended_set]
        path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
        print(f"\n[INFO] Removed {len(suspended)} suspended account(s): "
              f"{', '.join(suspended)}")
        self.after(0, self._load_users_for, pid)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
