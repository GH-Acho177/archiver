# Archiver

**Batch-download and archive media from X (Twitter), Douyin, and Bilibili — on Windows.**

[![Version](https://img.shields.io/badge/version-5.0.2-blue)](https://github.com/GH-Acho177/media-downloader/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)](https://github.com/GH-Acho177/media-downloader/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)

---

## Features

| Feature | Description |
|---------|-------------|
| **Three platforms** | X (Twitter), Douyin, Bilibili |
| **Sync modes** | Update (new only), Full (complete history), Auto (scheduled) |
| **Creator groups** | Organise accounts across platforms under named creators |
| **URL download** | Paste any post URL for an immediate one-off download |
| **File browser** | In-app file list with double-click to open |
| **Post index** | Track which downloaded posts have since been deleted (ghost check) |
| **History log** | Per-run breakdown; double-click any file to open it |
| **Telegram bot** | Send a link from your phone — it downloads on the PC |
| **Theme & language** | Dark / light · English / Chinese |

---

## Installation

Download the latest installer from the [Releases](https://github.com/GH-Acho177/media-downloader/releases/latest) page and run it. No additional setup required.

---

## Getting Started

### Authentication

Each platform requires a browser cookie file.

1. Install **[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** in Chrome or Edge.
2. Log in to the platform in your browser.
3. Export cookies and save the `.txt` file.
4. In Archiver → **Settings → Authentication → Import cookies.txt**

### Adding Accounts

Go to the **Accounts** panel and add accounts under any Creator group.

| Platform | Accepted input |
|----------|----------------|
| X (Twitter) | Profile URL or bare username |
| Douyin | Profile URL or bare `sec_uid` |
| Bilibili | Space URL or bare UID |

### Downloading

| Mode | Behaviour |
|------|-----------|
| **Update** | Fetches posts since the last run |
| **Full** | Downloads complete history (optional date range) |
| **Auto** | Runs Update on a timer in the background |

---

## Telegram Bot

Trigger downloads from your phone without touching the PC.

**Setup**

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
2. In Archiver → **Settings → Telegram Bot → paste token → Save & Start**.
3. Send any message to the bot — your user ID is whitelisted on first contact.

**Sending a post URL**

Send any post link (X, Douyin, or Bilibili). Supported formats:

- `https://x.com/user/status/…`
- `https://www.bilibili.com/video/…`
- `https://v.douyin.com/XXXXX/` — short links resolved automatically
- Douyin share blurbs (`6.92 复制打开抖音… https://v.douyin.com/…`) — URL extracted automatically
- `https://b23.tv/…` — short links with UTM params resolved before routing

**Adding an account via bot**

Send a profile URL and the bot starts a guided flow:

```
You  →  https://v.douyin.com/XXXXX/
Bot  ←  📋 Douyin account: <display name>
        Create a new creator for this account? (yes / no)
You  →  yes
Bot  ←  ✓ Created creator '<display name>' and added the account.

         — or —

You  →  no
Bot  ←  Choose a creator:
        1. Creator A
        2. Creator B
You  →  2
Bot  ←  ✓ Added to 'Creator B'.
```

Send `/cancel` at any time to abort.

---

## Download Structure

```
downloads/
├── {Creator Name}/
│   └── {media files}
└── Unassigned/
    └── {media files}
```

---

## Running from Source

**1. Python dependencies**

```bash
pip install fastapi "uvicorn[standard]" pywebview pystray pillow f2 aiohttp aiofiles
```

`gallery-dl` and `yt-dlp` must be on `PATH` or placed in `packaging/`.

**2. Frontend**

```bash
cd ui && npm install && npm run build && cd ..
```

**3. Run**

```bash
python run_api.py
```

---

## Building a Release

**1. Dependencies**

```bash
pip install pyinstaller fastapi "uvicorn[standard]" pywebview pystray pillow f2 aiohttp aiofiles
```

**2. Frontend** *(the PyInstaller spec bundles `ui/dist`)*

```bash
cd ui && npm run build && cd ..
```

**3. Third-party binaries** — place in `packaging/`

| Binary | Source |
|--------|--------|
| `gallery-dl.exe` | [mikf/gallery-dl](https://github.com/mikf/gallery-dl/releases) |
| `yt-dlp.exe` | [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp/releases) |

**4. PyInstaller**

```bash
pyinstaller packaging/Archiver.spec
# output: dist\Archiver\
```

**5. Installer** *(optional)*

Compile `packaging/installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php).

---

## Project Structure

```
run_api.py              # Entry point — FastAPI server + pywebview window
src/
  api.py                # All REST endpoints
  config.py             # Platform config, constants, theme colours, i18n strings
  creator_store.py      # Creator / account persistence
helpers/
  f2_user.py            # Douyin batch downloader
  f2_one.py             # Douyin single-post downloader
  tg_bot.py             # Telegram bot (stdlib only, no SDK)
ui/
  src/                  # React + TypeScript source
  dist/                 # Built frontend — served by FastAPI
assets/
  icon.ico
packaging/
  Archiver.spec         # PyInstaller spec
  installer.iss         # Inno Setup script
```

---

## Changelog

### v5.0.2
- Wire `sleep_req` setting into Douyin (f2) page fetches — defaults to 1s between API pages to avoid rate limiting

### v5.0.1
- Auto-detect and remove corrupt/truncated media files during sync; re-download on next run (Douyin archive entries also purged)
- Simple log mode: collapses download progress into single `↓ filename (size)` lines, hides extractor/merger/already-downloaded noise

### v5.0.0
- **UI rewrite** — replaced Tkinter/sv_ttk with React + TypeScript, served by FastAPI (uvicorn) and embedded via pywebview. Entry point is now `run_api.py`.
- Double-click any file in the Posts dialog or History log to open it with the default app
- Hover highlight on all clickable file rows
- History log filters out runs and accounts with zero new downloads (UI + API level)
- Post index stores absolute paths; endpoint falls back to a root search if the path no longer resolves
- Removed `app.py`, `src/utils.py`, `fonts/`, `sv_ttk` dependency

### v4.0.6
- Avatar tiles in the Accounts panel open a Posts dialog showing all downloaded files, with deleted posts highlighted in red
- Ghost check merged into the Posts dialog — no separate window
- Fixed X avatar fetching (switched from deprecated guest-token API to gallery-dl JSON output)

### v4.0.5
- Fixed X profile URL incorrectly stored as display name when adding an account via URL

### v4.0.4
- Installer prompts for install path (defaults to Program Files)

### v4.0.3
- History hides accounts with zero new downloads
- Telegram bot Stop no longer clears the saved token

### v4.0.2
- Douyin `max_connections` and `max_tasks` raised from 5 → 10
- Douyin `page_counts` raised from 20 → 50 to reduce listing round-trips

### v4.0.1
- Bot queues URLs received during an active download and processes them when it finishes
- Bot replies with the download result after each bot-triggered job

### v4.0.0
- Telegram bot account-sharing flow: send a profile URL to add it to a creator group
- Resolves short links (v.douyin.com, b23.tv) and fetches display names for all platforms
- Supports share blurbs, short links, and UTM-tagged URLs

### v3.2.0
- Bot guided flow for profile URLs: create new creator or assign to existing
- `/cancel` aborts any in-progress conversation

### v3.1.9
- Telegram bot — stdlib-only implementation, no SDK required
- Token and whitelist stored in `config/settings.json`; first message auto-whitelists sender

### v3.1.8
- Fixed frozen/unresponsive UI during heavy download output; log writes now batch-flushed every 50 ms

### v3.1.7
- Corrupt-file detection validates MP4 box structure
- Corrupt Douyin files re-downloaded immediately via single-post fetch
- Leftover `.part` / `.tmp` stubs cleaned up automatically

### v3.1.6
- Line-by-line live log output (unbuffered subprocess stdout)
- Progressive inter-user sleep to reduce rate-limit exposure
- Fixed X filename sanitisation (`{date_url}` replaces unreliable `{date}`)

### v3.1.4
- Display names auto-fetched from platform on account add
- Configurable auto-sync interval

### v3.1.0
- Dark title bar (DwmSetWindowAttribute)
- Animated theme transitions
- Post URL one-off download button on dashboard

### v3.0.0
- Renamed to **Archiver**
- Download structure changed to `downloads/{Creator}/{account}/`
- Parallel cross-platform downloads via per-platform thread pools
- Combined per-run history record across all platforms

### v2.1.2
- DPI-aware scaling throughout
- Persistent settings via `config/settings.json`
- Moved `config.py` / `utils.py` into `src/` package

### v2.0.0
- Configurable download path
- Auto-create runtime directories on startup
- Subprocess console windows hidden in release builds

### v1.0.0
- Initial release — Tkinter GUI for X, Douyin, and Bilibili
- Update mode, Full mode, parallel downloads, cookie import, dark/light theme, EN/ZH
