# Archiver

> A Windows desktop application for batch-downloading media from **X (Twitter)**, **Douyin (抖音)**, and **Bilibili**.

[![Version](https://img.shields.io/badge/version-4.0.0-blue)](https://github.com/GH-Acho177/media-downloader/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)](https://github.com/GH-Acho177/media-downloader/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)

---

## Overview

Archiver lets you maintain a local archive of content from multiple social media accounts. Organise accounts under named creators, run scheduled syncs in the background, or manually trigger downloads on demand — all from a single dark/light themed desktop interface.

---

## Features

| Category | Details |
|----------|---------|
| **Download modes** | **Update** — new posts only; **Full** — complete history with optional date range; **Auto** — scheduled background sync (default every 30 min) |
| **Platforms** | X (Twitter) via gallery-dl · Douyin via f2 · Bilibili via yt-dlp |
| **Account management** | Group accounts under named creators; supports multiple platforms per creator |
| **Single URL download** | Paste any supported URL to download a single post immediately |
| **Integrity checking** | Detects corrupt and truncated downloads (MP4 box-structure validation); re-downloads failed files automatically |
| **Concurrency** | Parallel downloads across platforms with configurable worker count |
| **Rate limiting** | Progressive inter-user sleep (5 s → 30 s) to avoid IP-level throttling |
| **Authentication** | Per-platform Netscape cookies.txt import |
| **Telegram bot** | Send a post or profile URL from your phone; downloads start on the PC instantly |
| **UI** | Dark / light theme · English / Chinese · DPI-aware · System tray support |

---

## Installation

Download the latest installer from the [Releases](https://github.com/GH-Acho177/media-downloader/releases/latest) page and run it. No additional dependencies required.

---

## Getting Started

### 1. Authenticate

Each platform requires a browser cookie file.

1. Install **[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** in Chrome or Edge.
2. Log in to the target platform in your browser.
3. Export cookies and save the `.txt` file locally.
4. In Archiver: **Settings → Authentication → Import cookies.txt**

### 2. Add Accounts

Accounts are grouped under **Creators** in the Accounts panel.

| Platform | Accepted Input |
|----------|----------------|
| X (Twitter) | Username — e.g. `username` |
| Douyin | Profile page URL or bare `sec_uid` |
| Bilibili | Space page URL or bare UID |

For Douyin and Bilibili, paste the profile URL directly — the app extracts the identifier automatically.

### 3. Download

- **Update** — fetches posts published since the last run.
- **Full** — downloads the complete post history (optionally bounded by a date range).
- **Auto** — runs Update silently on a timer; toggle from the dashboard.

---

## Telegram Bot

The Telegram bot lets you trigger downloads from your phone without touching the PC.

### Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) on Telegram and copy the token.
2. In Archiver: **Settings → Telegram Bot → paste the token → Save & Start**.
3. Send your first message to the bot — your Telegram user ID is whitelisted automatically.

### Sending a Post URL

Send any post link (X, Douyin, or Bilibili) and the download starts on the PC immediately.

Supported formats:
- Direct URLs: `https://x.com/user/status/…`, `https://www.bilibili.com/video/…`
- Douyin short links: `https://v.douyin.com/XXXXX/`
- Douyin share blurbs: the full copied text (e.g. `6.92 复制打开抖音… https://v.douyin.com/…`) — the URL is extracted automatically
- Short links with UTM parameters (e.g. `https://b23.tv/…?utm_…`) — resolved before routing

### Adding an Account via Bot

Send a **profile URL** instead of a post URL and the bot starts a guided flow:

```
You  →  https://v.douyin.com/XXXXX/          (profile share link)
Bot  ←  📋 抖音 account: 可可
         Create a new creator for this account? (yes / no)

You  →  yes
Bot  ←  ✓ Created creator '可可' and added the account.

— or —

You  →  no
Bot  ←  Choose a creator:
         1. Creator A
         2. Creator B

You  →  2
Bot  ←  ✓ Added to 'Creator B'.
```

Send `/cancel` at any point to abort the flow.

The bot resolves short links and fetches the account's display name automatically for all three platforms.

---

## Download Structure

```
downloads/
├── {Creator Name}/
│   └── {media files…}
└── Unassigned/
    └── {media files…}
```

---

## Running from Source

**Install dependencies**

```bash
pip install sv_ttk f2 aiohttp aiofiles pystray pillow
```

`gallery-dl` and `yt-dlp` are bundled in the installer. When running from source, place their executables in `packaging/` (see [Building](#building)).

**Launch**

```bash
python app.py
```

---

## Building

**1. Install build dependencies**

```bash
pip install pyinstaller sv_ttk f2 aiohttp aiofiles pystray pillow
```

**2. Place third-party binaries in `packaging/`**

| File | Source |
|------|--------|
| `gallery-dl.exe` | [github.com/mikf/gallery-dl/releases](https://github.com/mikf/gallery-dl/releases) |
| `yt-dlp.exe` | [github.com/yt-dlp/yt-dlp/releases](https://github.com/yt-dlp/yt-dlp/releases) |

**3. Build**

```bash
pyinstaller packaging/Archiver.spec
```

Output: `dist\Archiver\` (portable folder)

**4. Package installer** *(optional)*

Compile `packaging/installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php) to produce a single-file setup executable.

---

## Project Structure

```
app.py                    # Application entry point
src/
  config.py               # Constants, platform config, UI strings, theme colours
  creator_store.py        # Creator / account persistence (config/creators.json)
  utils.py                # Shared utilities
helpers/
  f2_user.py              # Douyin batch downloader
  f2_one.py               # Douyin single-post downloader
  tg_bot.py               # Telegram bot (stdlib urllib, no SDK)
assets/
  icon.ico
packaging/
  Archiver.spec           # PyInstaller spec
  installer.iss           # Inno Setup script
  gallery-dl.exe          # (not tracked — download separately)
  yt-dlp.exe              # (not tracked — download separately)
```

---

## Changelog

### v4.0.0
- Telegram bot account-sharing flow: send a profile URL from your phone to add it to a creator group
- Bot detects platform from URL, resolves short links (v.douyin.com, b23.tv), and prompts to create a new creator or assign to an existing one
- Display names fetched automatically for all platforms — Douyin via f2, Bilibili via API, X via gallery-dl
- Supports all shared URL formats including share blurbs (Chinese text + short link) and UTM-tagged links

### v3.2.0
- Telegram bot now handles account/profile URLs with a guided conversation flow
- Sending a profile URL asks whether to create a new creator or add to an existing one
- If adding to existing, bot lists current creators and waits for an index reply
- Short URLs (v.douyin.com, b23.tv) are resolved in a background thread before routing
- Share blurbs (Chinese text + URL) are correctly parsed to extract just the URL
- `/cancel` aborts any in-progress conversation

### v3.1.9
- **Telegram Bot** — send a Douyin, X, or Bilibili URL from your phone to a Telegram bot and the download starts immediately on the PC; no extra software required, uses stdlib `urllib` only
- Token and whitelist stored in `config/settings.json`; first message auto-whitelists the sender
- Bot settings card added to the Settings panel (token entry, show/hide, Save & Start / Stop)

### v3.1.8
- Fixed UI becoming non-interactive (gray/frozen window) during heavy download output
- Log writes are now batched and flushed to the widget every 50 ms instead of scheduling one tkinter callback per output line — eliminates event-queue flooding that starved user input

### v3.1.7
- Corrupt-file detection now validates MP4 box structure, catching truncated downloads that partially play rather than just 0-byte files
- Corrupt Douyin files are immediately re-downloaded via targeted single-post fetch instead of waiting for the next Full mode run
- Leftover `.part` / `.tmp` partial-download stubs are cleaned up automatically

### v3.1.6
- Live log output with line-by-line streaming
- Progressive inter-user sleep to reduce rate-limit exposure
- Fixed filename sanitisation for X (Twitter) downloads

### v3.1.4
- Auto-fetch account display names from the platform
- Unassigned accounts listed first in the sidebar
- Date prefixes on downloaded filenames
- Configurable auto-sync interval setting

### v3.1.0
- Dark title bar
- Animated theme transitions
- ttk scrollbar in the log panel
- Post URL button for single-post downloads
