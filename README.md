# Media Downloader

A Windows desktop app for batch-downloading media from **X (Twitter)**, **Douyin (抖音)**, and **Bilibili**.

Current version: **2.1.2** — [Download installer](https://github.com/GH-Acho177/media-downloader/releases/latest)

---

## Features

- Batch download from multiple accounts across all three platforms
- **Update mode** — fetch only new posts since the last run
- **Full mode** — download everything, with optional day-range limit
- Single video/post download by pasting a URL directly
- Parallel downloads with configurable worker count and sleep intervals
- Per-platform cookie authentication
- Update history viewer
- System tray support (minimise to tray)
- Configurable download location
- Dark / light theme, English / Chinese UI
- DPI-aware — scales correctly on high-DPI displays

---

## Requirements

```
pip install sv_ttk f2 aiohttp aiofiles pystray pillow
```

`gallery-dl` and `yt-dlp` are used as standalone executables and must be placed in `packaging/` before building (see [Building](#building)).

---

## Running from Source

```
python app.py
```

---

## Getting Cookies

1. Install **[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** in Chrome/Edge
2. Log in to the platform in your browser
3. Export cookies → save the `.txt` file
4. In the app: **Settings → Authentication → Import cookies.txt**

---

## Adding Users

| Platform | Accepted input |
|----------|----------------|
| X (Twitter) | username (e.g. `elonmusk`) |
| Douyin | profile page URL **or** bare sec_uid |
| Bilibili | space page URL **or** bare UID |

For Douyin and Bilibili you can simply copy the profile URL from your browser and paste it in — the app extracts the ID automatically.

---

## Project Structure

```
app.py                  # Application entry point
src/
  config.py             # Constants, platform config, UI strings, theme colours
  utils.py              # Shared utilities
helpers/
  f2_user.py            # Douyin batch downloader (via f2)
  f2_one.py             # Douyin single-post downloader
assets/
  icon.ico
packaging/
  MediaDownloader.spec  # PyInstaller spec
  installer.iss         # Inno Setup script
  gallery-dl.exe        # (not tracked — download separately)
  yt-dlp.exe            # (not tracked — download separately)
```

---

## Building

**1. Install dependencies**
```
pip install pyinstaller sv_ttk f2 aiohttp aiofiles pystray pillow
```

**2. Place binaries in `packaging/`**

| File | Source |
|------|--------|
| `gallery-dl.exe` | [github.com/mikf/gallery-dl/releases](https://github.com/mikf/gallery-dl/releases) |
| `yt-dlp.exe` | [github.com/yt-dlp/yt-dlp/releases](https://github.com/yt-dlp/yt-dlp/releases) |

**3. Build with PyInstaller**
```
pyinstaller packaging/MediaDownloader.spec
```

Output: `dist\MediaDownloader\` (portable folder)

**4. Optional installer** — compile `packaging/installer.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php) to produce `dist\MediaDownloader_Setup_2.1.2.exe`.
