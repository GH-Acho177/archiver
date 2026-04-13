APP_VERSION = "2.1.2"

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
        "color":        "#fe2c55",
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

GDL                 = "gallery-dl"
SETTINGS_FILE       = "config/settings.json"
DOUYIN_LAST_RUN     = "config/douyin_last_run.json"
UPDATE_HISTORY_FILE = "config/update_history.json"
DOWNLOAD_PATH_FILE  = "config/download_path.txt"
LANG_FILE           = "config/lang.txt"
ACCENT              = "#0067c0"

STRINGS = {
    "en": {
        # Navigation
        "nav.dashboard":  "Dashboard",
        "nav.users":      "Users",
        "nav.settings":   "Settings",
        "nav.about":      "About",
        # Dashboard
        "label.platform": "Platform",
        "label.mode":     "Mode",
        "mode.update":    "Update",
        "mode.full":      "Full",
        "label.last":     "Last",
        "label.days_all": " days  (0=all)",
        "btn.start":      "▶  Start",
        "btn.stop":       "■  Stop",
        "btn.clear_log":  "Clear log",
        "btn.downloads":  "📁 Downloads",
        "btn.post_url":   "⬇ Post URL",
        "btn.updates":    "📢 Updates",
        "log.title":      "Log",
        # Users
        "users.list":     "Users",
        "btn.add":        "Add",
        "btn.remove":     "Remove",
        # Settings
        "settings.parallel_opts": "Parallel Download",
        "settings.douyin_opts":   "Douyin — Download Options",
        "settings.auth_hint":   "Log in to each site in your browser, then export cookies using\nthe «Get cookies.txt LOCALLY» extension and import the file below.",
        "settings.dl_location": "Download Location",
        "settings.database":    "Database",
        "settings.db_hint":     "Delete all download archive records (forces re-download).",
        "settings.appearance":  "Appearance",
        "label.theme":          "Theme:",
        "label.language":       "Language:",
        "btn.switch_light":     "Switch to Light",
        "btn.switch_dark":      "Switch to Dark",
        "btn.browse":           "Browse…",
        "btn.reset_db":         "Reset DB",
        "settings.sleep_req":   "Sleep between requests (s):",
        "settings.sleep_user":  "Sleep between users (s):",
        "settings.workers":     "Parallel workers:",
        # About
        "btn.github":         "View on GitHub",
    },
    "zh": {
        # Navigation
        "nav.dashboard":  "仪表盘",
        "nav.users":      "用户",
        "nav.settings":   "设置",
        "nav.about":      "关于",
        # Dashboard
        "label.platform": "平台",
        "label.mode":     "模式",
        "mode.update":    "更新",
        "mode.full":      "全部",
        "label.last":     "最近",
        "label.days_all": " 天  (0=全部)",
        "btn.start":      "▶  开始",
        "btn.stop":       "■  停止",
        "btn.clear_log":  "清空日志",
        "btn.downloads":  "📁 下载目录",
        "btn.post_url":   "⬇ 链接下载",
        "btn.updates":    "📢 更新记录",
        "log.title":      "日志",
        # Users
        "users.list":     "用户列表",
        "btn.add":        "添加",
        "btn.remove":     "删除",
        # Settings
        "settings.parallel_opts": "并行下载",
        "settings.douyin_opts":   "抖音 — 下载选项",
        "settings.auth_hint":   "在浏览器中登录各平台，使用「Get cookies.txt LOCALLY」插件\n导出 cookies 文件后，在下方导入。",
        "settings.dl_location": "下载位置",
        "settings.database":    "数据库",
        "settings.db_hint":     "删除所有下载记录（强制重新下载）。",
        "settings.appearance":  "外观",
        "label.theme":          "主题：",
        "label.language":       "语言：",
        "btn.switch_light":     "切换浅色",
        "btn.switch_dark":      "切换深色",
        "btn.browse":           "浏览…",
        "btn.reset_db":         "重置数据库",
        "settings.sleep_req":   "请求间隔（秒）：",
        "settings.sleep_user":  "用户间隔（秒）：",
        "settings.workers":     "并发数：",
        # About
        "btn.github":         "在 GitHub 查看",
    },
}

_MEDIA_EXTS = frozenset({
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv", ".m4v",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
})

THEME_COLORS = {
    "dark": {
        # JetBrains Darcula palette
        "bg":       "#2b2b2b",
        "panel":    "#3c3f41",
        "hover":    "#4b4b4b",
        "border":   "#515151",
        "text":     "#bbbbbb",
        "text_dim": "#6b6b6b",
        "accent":   "#4a9bca",
        # Legacy aliases used by pills, user list, log
        "list_bg":  "#3c3f41",
        "list_fg":  "#bbbbbb",
        "list_sel": "#214283",
        "log_bg":   "#2b2b2b",
        "log_fg":   "#bbbbbb",
        "pill_bg":  "#3c3f41",
    },
    "light": {
        # JetBrains Light palette
        "bg":       "#f2f2f2",
        "panel":    "#eaeaea",
        "hover":    "#d5d5d5",
        "border":   "#c4c4c4",
        "text":     "#1a1a1a",
        "text_dim": "#787878",
        "accent":   "#1e6eb5",
        # Legacy aliases
        "list_bg":  "#ffffff",
        "list_fg":  "#1a1a1a",
        "list_sel": "#2675bf",
        "log_bg":   "#f2f2f2",
        "log_fg":   "#1a1a1a",
        "pill_bg":  "#eaeaea",
    },
}

FONTS = {
    "title":   ("Segoe UI Variable Display Semib", 19),
    "heading": ("Segoe UI Variable Text Semibold", 13),
    "body":    ("Segoe UI Variable Text", 12),
    "small":   ("Segoe UI Variable Small", 11),
    "mono":    ("JetBrains Mono", 10),
}

# Log line color classification
_LOG_TAGS = {
    "error":   ("[error]", "[skip]", "[ERROR]"),
    "warning": ("[warning]", "WARNING", "⚠"),
    "success": ("✓", "→ Up to date"),
    "dim":     ("─────",),
    "info":    ("Platform :", "Mode     :", "Users :", "Interval :", "Aweme ID :", "Resolved :"),
}
