# Archiver

Archiver is a Windows desktop application for continuously backing up creator media from **X**, **Douyin**, **Bilibili**, and **Xiaohongshu**. It combines account tracking, incremental and complete synchronization, archive maintenance, remote-availability checks, Telegram control, and a local media viewer in one application.

[![Release](https://img.shields.io/github/v/release/GH-Acho177/archiver?display_name=tag)](https://github.com/GH-Acho177/archiver/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows-0078d4)](https://github.com/GH-Acho177/archiver/releases)
[![Python](https://img.shields.io/badge/python-3.12-3776ab)](https://www.python.org/)

## What Archiver does

- Tracks accounts across four platforms and organizes them into creator groups.
- Runs fast recent-post **Update** syncs or completeness-focused **Full** syncs.
- Uses account- and post-level concurrency while applying platform-specific throttling.
- Scans local files instead of relying only on downloader archive records.
- Reports local posts, posts available remotely, matched posts, and downloads per account.
- Detects missing, remotely unavailable, and corrupt local media.
- Groups multi-image and mixed-media posts as a single post.
- Finds duplicate files within creator groups and isolates cross-account duplicates.
- Downloads individual post URLs and missing posts discovered during verification.
- Provides per-account progress cards, scoped logs, summaries, retry controls, and history.
- Supports remote status, sync control, account management, and URL submission through Telegram.
- Includes a local TikTok-style Viewer with Home, Liked, Saved, Deleted, and account grids.

## Main workflow

The desktop application has four destinations:

| Page | Purpose |
|---|---|
| **Sync** | Start or stop synchronization, choose Update or Full mode, run maintenance, download a URL, inspect progress, retry failed accounts, and review logs/history. |
| **Browse** | Review the local archive, react to posts, browse collections, and open account grids. |
| **Account** | Add, search, group, move, inspect, verify, rename, or remove tracked accounts. |
| **Setting** | Configure download location, workers, pacing, scheduling, cookies, Viewer playback, language, theme, Telegram, and databases. |

Archiver always opens on **Sync**. Keyboard navigation uses `Ctrl+1` through `Ctrl+4`; `Ctrl+F` focuses the active search field and `Ctrl+S` saves Settings.

## Synchronization modes

### Update

Update is optimized for frequent runs. It begins with the newest posts and stops after reaching content already present locally. This makes it suitable for catching recent posts before they are removed without enumerating an account's entire history every time.

### Full

Full mode works toward archive completeness. It enumerates the remote history available to the platform client, compares it with local post IDs, downloads missing posts, and verifies whether local posts are still available remotely. Optional day limits can restrict the remote range.

Platform APIs can rate-limit, hide, paginate inconsistently, or return incomplete lists. Archiver reports incomplete verification instead of treating an uncertain result as a deletion.

### Maintenance

Maintenance is group-selectable and local-first. It scans files across account folders in each selected creator group:

- Same-content duplicates in one account folder are reduced to the best-titled copy.
- Same-content files found across different account folders are moved out of the originals and placed in the group's `duplicated` folder.
- Sync is blocked while a `duplicated` folder still contains unresolved files, preventing automatic redownload loops.

## Archive Viewer

Browse presents posts in a randomized feed. Accounts are sampled evenly, unseen and less-recently viewed posts receive more weight, and newer releases receive an additional boost. Reopening Browse creates a fresh sequence.

Viewer features include:

- Multi-file posts with horizontal media navigation.
- Full-monitor playback and responsive portrait/landscape sizing.
- Per-account, Liked, Saved, and Deleted grids.
- Newest-first and random account playback.
- Likes, saves, view history, last-seen weighting, and keyboard controls.
- Shared theme, default volume, and loop settings.

Useful shortcuts:

| Key | Action |
|---|---|
| `↑` / `↓` | Previous or next post |
| `←` / `→` | Previous/next image, or seek video timeline |
| Hold `→` | Temporarily speed up video playback |
| `Space` | Pause or resume video |
| `L` | Like |
| `S` | Save |
| `D` | Mark for deletion |
| `F` or double-click | Enter or leave fullscreen |
| `Esc` | Leave fullscreen |

### Delayed deletion

Marking a post for deletion schedules every media file belonging to that post for deletion after five minutes. Removing the mark during that window cancels the job. The queue is durable across restarts, and choosing **Quit** from the tray completes all pending jobs immediately. Closing the window with `X` only sends Archiver to the tray.

Deleted posts remain as non-playable records in the **Deleted** collection. The Viewer database is stored at `config/viewer.db`.

## Installation

Download the latest Windows installer from [GitHub Releases](https://github.com/GH-Acho177/archiver/releases/latest).

On first launch:

1. Open **Setting** and choose a writable download directory.
2. Import cookies for the platforms you use.
3. Open **Account**, add a profile link, and assign or create its creator group.
4. Return to **Sync** and run Update.

## Authentication

Export Netscape-format `cookies.txt` files from a browser session already logged into each platform. One option is [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc).

Import each file from **Setting → Cookies**. Cookies and browser profiles are local runtime data and are excluded from Git.

Douyin account enumeration uses a dedicated Edge profile when required by the platform. Do not use that profile for ordinary browsing; Archiver manages it during scans.

## Adding accounts

Paste a profile URL into **Account → Add account**. Archiver resolves the platform, stable account identifier, display name, avatar, and local account folder. Duplicate accounts are rejected whether they are submitted from the desktop app or Telegram.

Supported inputs include:

| Platform | Examples |
|---|---|
| X | `https://x.com/username` or a username |
| Douyin | A profile/share link or `sec_uid` |
| Bilibili | `https://space.bilibili.com/UID` or a UID |
| Xiaohongshu | A profile link, share message, or `xhslink.cn` short link |

Renaming a creator group also renames its archive folder and rewrites stored path references. Removing an account removes its downloader records; empty creator groups are cleaned automatically.

## Telegram bot

Create a bot with [@BotFather](https://t.me/BotFather), then paste its token into **Setting → Telegram bot** and select **Save & Start**. The first user to contact a new bot becomes the allowed user.

Commands:

| Command | Description |
|---|---|
| `/status` | Show whether a sync is running and list active account progress. |
| `/sync` | Start an Update sync for all accounts. |
| `/sync full` | Start a Full sync for all accounts. |
| `/stop` | Request cancellation of the current sync. |
| `/accounts` | List tracked accounts with stable numbers. |
| `/deleteaccount NUMBER` | Start a confirmed account-removal flow. |
| `/cancel` | Cancel the current guided action. |

Sending a supported post URL queues a one-off download. Sending a profile link starts the guided account-add flow. Accounts may be added while a sync is running, but operations that would conflict with active archive mutation remain guarded.

## Download layout

```text
<download path>/
├── Creator name/
│   ├── Account A [platform]/
│   │   └── post media
│   ├── Account B [platform]/
│   │   └── post media
│   └── duplicated/
└── Unassigned/
```

File names include the post title when available and retain the stable post ID so local scanning, grouping, verification, and redownload operations remain possible when titles change.

## Run from source

Requirements:

- Windows 10 or newer
- Python 3.12
- Node.js and npm
- Microsoft Edge/WebView2 Runtime
- `gallery-dl`, `yt-dlp`, `ffmpeg`, and `ffprobe` on `PATH` or in `packaging/`

Using [uv](https://docs.astral.sh/uv/):

```powershell
uv sync --dev
cd ui
npm install
npm run build
cd ..
uv run python run_api.py
```

Using an existing Python 3.12 environment:

```powershell
python -m pip install -e .
cd ui
npm install
npm run build
cd ..
python run_api.py
```

The standalone Viewer entry point remains available for development:

```powershell
python run_viewer.py
```

## Build a Windows release

Place these external tools in `packaging/`:

| File | Project |
|---|---|
| `gallery-dl.exe` | [mikf/gallery-dl](https://github.com/mikf/gallery-dl/releases) |
| `yt-dlp.exe` | [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp/releases) |
| `ffmpeg.exe`, `ffprobe.exe` | [yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds/releases) |

Then build the frontend and application:

```powershell
cd ui
npm install
npm run build
cd ..
uv run pyinstaller packaging/Archiver.spec
```

The onedir build is written to `dist/Archiver/`. Compile `packaging/installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php) to create the installer.

The downloader binaries, runtime databases, cookies, browser profiles, downloads, and DigiViewer project are intentionally excluded from this repository.

## Project layout

```text
run_api.py                 Desktop entry point, tray integration, and API host
run_viewer.py              Standalone Viewer development entry point
src/
  api.py                   Sync engine, REST API, verification, bot orchestration
  config.py                Platform configuration and runtime paths
  creator_store.py         Creator groups and tracked-account persistence
helpers/
  douyin_browser.py        Edge-based Douyin account enumeration
  f2_user.py               Douyin account downloader
  f2_one.py                Douyin single-post downloader
  tg_bot.py                Lightweight Telegram long-polling client
  xiaohongshu_user.py      Xiaohongshu account support
ui/
  src/                     React and TypeScript Archiver interface
  dist/                    Production frontend bundled by PyInstaller
viewer/
  app.py                   Viewer API, index, state, and deletion queue
  static/                  Viewer interface
packaging/
  Archiver.spec            PyInstaller configuration
  installer.iss            Inno Setup configuration
```

## Local data and safety

Archiver writes user state beneath `config/`, downloads beneath the configured archive path, and logs beneath `logs/`. These locations are ignored by Git.

- File deletion is restricted to resolved paths inside the configured archive root.
- Destructive account and database actions require explicit confirmation.
- Remote-list uncertainty remains **Unchecked** instead of being misreported as deleted.
- Maintenance never treats unresolved remote metadata as proof that a local file should be removed.

## License

No license file is currently included. Unless the repository owner states otherwise, all rights are reserved.
