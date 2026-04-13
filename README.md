# Media Downloader

A Windows desktop app for batch-downloading media from **X (Twitter)**, **Douyin**, and **Bilibili**.

---

## Features

- Batch download from multiple accounts across platforms
- **Update mode** — fetch only new posts since the last run
- **Full mode** — download everything, with optional date range
- Single video download by pasting a post URL
- Parallel downloads
- Per-platform cookie authentication
- Dark / light theme, English / Chinese UI

---

## Requirements

```
pip install sv_ttk f2 aiohttp aiofiles pystray pillow
```

`gallery-dl` and `yt-dlp` are used as standalone executables (see [Building](#building)).

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

| Platform | Format | Example |
|----------|--------|---------|
| X (Twitter) | username | `elonmusk` |
| Douyin | `nickname\|sec_uid` | `SomeName\|MS4wLjABAAAA...` |
| Bilibili | `nickname\|uid` | `SomeName\|12345678` |

The sec_uid is the long string in the Douyin profile URL. The UID is the number in the Bilibili space URL.

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

**3. Run the build**
```
build.bat
```

Output: `dist\MediaDownloader\` (portable) and optionally `dist\MediaDownloader_Setup_2.0.0.exe` (requires [Inno Setup](https://jrsoftware.org/isinfo.php)).
