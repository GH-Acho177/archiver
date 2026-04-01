APP_VERSION = "1.0.0"

# ── Platform config ────────────────────────────────────────────────────────────
PLATFORMS = {
    "x": {
        "label":        "X (Twitter)",
        "icon":         "𝕏",
        "color":        "#1d9bf0",
        "users_file":   "config/x_users.txt",
        "cookies_file": "config/x_cookies.txt",
        "url_fn":       lambda u: f"https://x.com/{u}/media",
        "downloader":   "gallery-dl",
        "entry_format": "id",
    },
    "douyin": {
        "label":        "抖音",
        "icon":         "抖",
        "color":        "#ff7a93",
        "users_file":   "config/douyin_users.txt",
        "cookies_file": "config/douyin_cookies.txt",
        "url_fn":       lambda u: f"https://www.douyin.com/user/{u.split('|')[-1]}",
        "downloader":   "f2",
        "entry_format": "name|id",
    },
    "bilibili": {
        "label":        "bilibili",
        "icon":         "哔",
        "color":        "#fb7299",
        "users_file":   "config/bilibili_users.txt",
        "cookies_file": "config/bilibili_cookies.txt",
        "url_fn":       lambda u: f"https://space.bilibili.com/{u.split('|')[-1]}/video",
        "downloader":   "yt-dlp",
        "entry_format": "name|id",
    },
}

DOUYIN_LAST_RUN     = "config/douyin_last_run.json"
UPDATE_HISTORY_FILE = "config/update_history.json"
GDL                 = "gallery-dl"
ACCENT              = "#0067c0"

_MEDIA_EXTS = frozenset({
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv", ".m4v",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
})

THEME_COLORS = {
    "dark":  {"list_bg": "#272727", "list_fg": "#ffffff", "list_sel": ACCENT,
              "log_bg":  "#1a1a1a", "log_fg":  "#d4d4d4",
              "pill_bg": "#2d2d2d", "hover":   "#383838"},
    "light": {"list_bg": "#ffffff", "list_fg": "#1a1a1a", "list_sel": ACCENT,
              "log_bg":  "#1a1a1a", "log_fg":  "#d4d4d4",
              "pill_bg": "#f0f0f0", "hover":   "#e0e0e0"},
}

FONTS = {
    "title":   ("Segoe UI Variable Display Semib", 17),
    "heading": ("Segoe UI Variable Text Semibold", 11),
    "body":    ("Segoe UI Variable Text", 10),
    "small":   ("Segoe UI Variable Small", 9),
    "mono":    ("Cascadia Mono", 9),
}

# Log line color classification
_LOG_TAGS = {
    "error":   ("[error]", "[skip]", "[ERROR]"),
    "warning": ("[warning]", "WARNING", "⚠"),
    "success": ("✓", "→ Up to date"),
    "dim":     ("─────",),
    "info":    ("Platform :", "Mode     :", "Users :", "Interval :", "Aweme ID :", "Resolved :"),
}
