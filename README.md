# Media Downloader GUI

A Windows desktop app for batch-downloading media from **X (Twitter)** and **Douyin**, built with Tkinter + Sun Valley theme.

---

## Requirements

| Tool | Purpose |
|------|---------|
| Python 3.10+ | Runtime |
| `sv_ttk` | Windows 11-style UI theme |
| `gallery-dl` | X (Twitter) downloads |
| `f2` | Douyin downloads |
| `yt-dlp` | Bilibili downloads |

```
pip install sv_ttk f2 gallery-dl yt-dlp
```

---

## Quick Start

Double-click **`x_download.bat`**, or:

```
python x_gui.py
```

---

## Features

### Platform Support
- **X (Twitter)** — downloads media from user `/media` pages via `gallery-dl`
- **Douyin** — downloads posts via `f2`, with smart date-interval update mode

### Tabs

**Dashboard**
- Platform selector (collapsible card list)
- Full / Update mode toggle
- Start / Stop controls
- Live log output
- Sleep tuning between requests and users

**Users**
- Checklist of accounts per platform — only checked users are downloaded
- Add / remove users; changes auto-saved
- X: plain username · Douyin: `nickname|sec_uid` (displays nickname only)
- **Import Following** (X): fetches your follow list and lets you pick accounts
- **Download Post URL** (Douyin): paste any post/modal URL to download a single video

**Settings**
- Cookie import — browse to a Netscape-format `cookies.txt` file per platform
- Cookie status indicator per platform

### Download Organisation

```
downloads/
  x/
    username/
      2026-03-23/        ← date downloaded
        post_id_1.jpg
        post_id_2.mp4
  douyin/
    nickname/
      2026-03-23/
        create_awemeid.mp4
    total/               ← single-URL downloads
      2026-03-23/
        nickname_create_awemeid.mp4
```

### Update Mode (Douyin)
Stores the last-run date per user in `config/douyin_last_run.json`. On next run, f2 is given a `start|end` date interval so only new posts are fetched — no full re-scan needed.

### Archive (X)
Downloaded post IDs are recorded in `config/x_downloaded.db` (SQLite). Re-running in Full mode skips already-downloaded posts automatically.

---

## Config Files

```
config/
  x_users.txt            one username per line
  douyin_users.txt       one "nickname|sec_uid" per line
  x_cookies.txt          Netscape cookie file (see Getting Cookies below)
  douyin_cookies.txt     Netscape cookie file (see Getting Cookies below)
  x_downloaded.db        gallery-dl SQLite archive
  douyin_last_run.json   last download date per Douyin sec_uid
```

---

## Getting Cookies

Cookies must be exported from your browser as a **Netscape-format `.txt` file**.

1. Install the **[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)** extension in Chrome/Edge (or an equivalent for Firefox).
2. Log in to the platform in your browser.
3. Click the extension icon while on that site → **Export** → save the file.
4. In the app go to **Settings → Authentication** → click **Import cookies.txt** and select the file.

| Platform | Log in at |
|----------|-----------|
| X (Twitter) | `x.com` |
| Douyin | `douyin.com` |
| Bilibili | `bilibili.com` |

Cookies expire when you log out or after a period of inactivity. Re-export and re-import if downloads start failing with auth errors.

---

## Adding Users

**X:** enter the plain username (e.g. `elonmusk`)

**Douyin:** use the `nickname|sec_uid` format. The sec_uid is the long string in the profile URL:
```
https://www.douyin.com/user/MS4wLjABAAAA...
                              ↑ this part
```
Display name can be anything recognisable to you.

---

## Download Post URL (Douyin)

Paste any of these URL formats into the **Post URL** dialog:

- `https://www.douyin.com/video/7619894879615526884`
- `https://www.douyin.com/user/self?modal_id=7619894879615526884&...`
- `https://v.douyin.com/xxxxxxx/` (short share link)
