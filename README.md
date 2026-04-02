# Media Downloader

A Windows desktop app for batch-downloading media from **X (Twitter)**, **Douyin**, and **Bilibili**, built with Tkinter + Sun Valley theme.

---

## Features

- Batch download media from multiple accounts across platforms
- **Update mode** — only fetch new posts since the last run
- **Full mode** — download everything (with optional date range)
- Single video download by pasting a URL
- Per-platform cookie authentication
- Live log output with start / stop controls
- Dark and light theme

---

## Requirements

| Tool | Purpose |
|------|---------|
| Python 3.10+ | Runtime |
| `sv_ttk` | Windows 11-style UI theme |
| `f2` | Douyin downloads |
| `gallery-dl` | X (Twitter) downloads |
| `yt-dlp` | Bilibili downloads |

```
pip install sv_ttk f2 aiohttp aiofiles
```

`gallery-dl` and `yt-dlp` are used as standalone executables — see [Building from Source](#building-from-source).

---

## Running from Source

```
python app.py
```

---

## Platform Support

### X (Twitter)
- Downloads media from user `/media` pages via `gallery-dl`
- Skips already-downloaded posts using a SQLite archive (`config/x_downloaded.db`)
- Add users by plain username (e.g. `elonmusk`)
- **Import Following**: fetches your follow list so you can pick accounts to add

### Douyin
- Downloads posts via `f2`
- **Update mode** stores the last-run date per user — only new posts are fetched on the next run
- **Full mode** downloads everything, with an optional "last N days" range
- Add users in `nickname|sec_uid` format (the app displays the nickname)
- **Download Post URL**: paste any Douyin video URL to download a single video

### Bilibili
- Downloads videos via `yt-dlp`
- Add users in `nickname|uid` format (UID is the number in the profile URL)

---

## Tabs

**Dashboard**
- Platform selector, mode toggle (Update / Full), date range control
- Start / Stop controls
- Live log output

**Users**
- Checklist of accounts per platform — only checked users are downloaded
- Add / remove users with auto-save

**Settings**
- Import a `cookies.txt` file per platform
- Cookie status indicator

---

## Getting Cookies

Cookies must be exported from your browser as a **Netscape-format `.txt` file**.

1. Install the **[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** extension in Chrome/Edge (or equivalent for Firefox)
2. Log in to the platform in your browser
3. Click the extension icon on that site → **Export** → save the file
4. In the app go to **Settings → Authentication** → click **Import cookies.txt**

| Platform | Log in at |
|----------|-----------|
| X (Twitter) | `x.com` |
| Douyin | `douyin.com` |
| Bilibili | `bilibili.com` |

Cookies expire when you log out or after inactivity. Re-export and re-import if downloads start failing with auth errors.

---

## Adding Users

**X** — plain username:
```
elonmusk
```

**Douyin** — `nickname|sec_uid` (sec_uid is the long string in the profile URL):
```
https://www.douyin.com/user/MS4wLjABAAAA...
                              ↑ this part
```
```
SomeName|MS4wLjABAAAA...
```

**Bilibili** — `nickname|uid` (UID is the number in the profile URL):
```
https://space.bilibili.com/12345678/video
                            ↑ this part
```
```
SomeName|12345678
```

---

## Download Layout

```
downloads/
  x/
    username/
      post_id.jpg
      post_id.mp4
  douyin/
    nickname/
      create_awemeid.mp4
    total/                 ← single-URL downloads
      nickname_create_awemeid.mp4
  bilibili/
    nickname/
      video_title.mp4
```

---

## Config Files

```
config/
  x_users.txt              one username per line
  douyin_users.txt         one "nickname|sec_uid" per line
  bilibili_users.txt       one "nickname|uid" per line
  x_cookies.txt            Netscape cookie file
  douyin_cookies.txt       Netscape cookie file
  bilibili_cookies.txt     Netscape cookie file
  x_downloaded.db          gallery-dl SQLite archive (X)
  douyin_users.db          f2 SQLite database (Douyin)
  douyin_last_run.json     last download date per Douyin user
  update_history.json      run history shown in the dashboard
```

---

## Building from Source

**1. Install dependencies**
```
pip install pyinstaller sv_ttk f2 aiohttp aiofiles
```

**2. Place tool binaries in `packaging/`**

| File | Download from |
|------|--------------|
| `gallery-dl.exe` | [github.com/mikf/gallery-dl/releases](https://github.com/mikf/gallery-dl/releases) |
| `yt-dlp.exe` | [github.com/yt-dlp/yt-dlp/releases](https://github.com/yt-dlp/yt-dlp/releases) |

**3. (Optional) Install Inno Setup** for a `Setup.exe` installer
Download from [jrsoftware.org/isinfo.php](https://jrsoftware.org/isinfo.php)

**4. Run the build**
```
build.bat
```

**Output**
```
dist\MediaDownloader\                    ← portable app
dist\MediaDownloader_Setup_x.x.x.exe    ← installer (if Inno Setup is present)
```
