"""Xiaohongshu account/note adapter for Archiver."""

from __future__ import annotations

import concurrent.futures
import http.cookiejar
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

_NOTE_RE = re.compile(r"[0-9a-f]{24}", re.I)
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def load_netscape_cookies(path: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    cookie_path = Path(path)
    if not cookie_path.is_file():
        return cookies
    jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
        return {cookie.name: cookie.value for cookie in jar}
    except Exception:
        pass
    # Also accept a plain `name=value; ...` export.
    text = cookie_path.read_text("utf-8", errors="ignore")
    for part in text.replace("\n", ";").split(";"):
        if "=" in part:
            name, value = part.strip().split("=", 1)
            if name and not name.startswith("#"):
                cookies[name] = value
    return cookies


def resolve_profile_id(url: str) -> str | None:
    match = re.search(r"xiaohongshu\.com/user/profile/([^/?&#\s]+)", url, re.I)
    return match.group(1) if match else None


def resolve_note_id(url: str) -> str | None:
    match = re.search(r"xiaohongshu\.com/(?:explore|discovery/item)/([0-9a-f]{24})", url, re.I)
    return match.group(1) if match else None


def _safe(value: str, limit: int = 80) -> str:
    value = _BAD_NAME.sub("_", value).strip(" ._")
    return (value or "untitled")[:limit].rstrip(" .")


def _note_card(detail: dict) -> dict:
    items = detail.get("items", []) if isinstance(detail, dict) else []
    if items:
        return items[0].get("note_card", items[0].get("noteCard", items[0])) or {}
    # get_note_detail() falls back to the profile HTML when no xsec token is
    # available. That parser returns the note object directly instead of the
    # feed API's {items: [{note_card: ...}]} wrapper.
    return detail if isinstance(detail, dict) else {}


def _media(card: dict) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if card.get("type") == "video":
        stream = (((card.get("video") or {}).get("media") or {}).get("stream") or {})
        variants = stream.get("h264") or stream.get("h265") or stream.get("av1") or []
        for variant in variants:
            url = variant.get("master_url") or variant.get("masterUrl") or variant.get("url")
            if url:
                result.append((str(url), "video.mp4"))
                break
    for index, image in enumerate(card.get("image_list") or card.get("imageList") or [], 1):
        url = (image.get("url_default") or image.get("urlDefault")
               or image.get("url_pre") or image.get("urlPre"))
        if not url:
            for info in image.get("info_list") or image.get("infoList") or []:
                url = info.get("url")
                if url:
                    break
        if url:
            suffix = Path(urlparse(str(url)).path).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                suffix = ".webp"
            result.append((str(url), f"image_{index:02d}{suffix}"))
    return result


def _local_files(folder: Path) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    if folder.exists():
        for path in folder.iterdir():
            if not path.is_file():
                continue
            match = _NOTE_RE.search(path.stem)
            if match:
                found.setdefault(match.group().lower(), []).append(path)
    return found


def _complete(note_id: str, folder: Path, expected: int | None = None) -> bool:
    files = _local_files(folder).get(note_id.lower(), [])
    if expected is None:
        return bool(files)
    return len(files) >= expected > 0


def _filename_base(note_id: str, card: dict) -> str:
    timestamp = (card.get("time") or card.get("last_update_time")
                 or card.get("lastUpdateTime") or 0)
    try:
        numeric = int(timestamp)
        if numeric > 10_000_000_000:
            numeric //= 1000
        date = datetime.fromtimestamp(numeric).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        date = datetime.now().strftime("%Y-%m-%d")
    title = (card.get("title") or card.get("display_title")
             or card.get("displayTitle") or card.get("desc") or "untitled")
    return f"{date}_{note_id}_{_safe(str(title))}"


def _download(url: str, target: Path, cookies: dict[str, str], stop_check=None) -> bool:
    if stop_check and stop_check():
        return False
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/145 Safari/537.36",
        "Referer": "https://www.xiaohongshu.com/",
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
    }
    request = urllib.request.Request(url, headers=headers)
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=45) as response, temporary.open("wb") as output:
            while True:
                if stop_check and stop_check():
                    return False
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        temporary.replace(target)
        return target.stat().st_size > 0
    except Exception as exc:
        print(f"[error] Xiaohongshu media failed: {exc}")
        return False
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def account_info(user_id: str, cookie_file: str) -> tuple[str, str | None]:
    from xhs_cli.client import XhsClient
    cookies = load_netscape_cookies(cookie_file)
    if not cookies.get("a1"):
        raise RuntimeError("Xiaohongshu cookies are missing the required a1 cookie")
    with XhsClient(cookies, request_delay=1.0, max_retries=3) as client:
        info = client.get_user_info(user_id) or {}
    basic = info.get("basic_info", info)
    name = basic.get("nickname") or basic.get("nick_name") or user_id
    avatar = basic.get("imageb") or basic.get("images") or basic.get("avatar")
    return str(name), str(avatar) if avatar else None


def download_user(
    user_id: str,
    cookie_file: str,
    outdir: str,
    *,
    full: bool = False,
    stop_check=None,
    max_tasks: int = 3,
    progress_callback=None,
) -> dict:
    from xhs_cli.client import XhsClient
    from xhs_cli.cookies import cache_note_context

    cookies = load_netscape_cookies(cookie_file)
    if not cookies.get("a1"):
        raise RuntimeError(
            "Xiaohongshu login required: import cookies containing the a1 cookie"
        )
    folder = Path(outdir)
    folder.mkdir(parents=True, exist_ok=True)
    remote_ids: set[str] = set()
    remote_names: dict[str, str] = {}
    cursor = ""
    with XhsClient(cookies, request_delay=1.0, max_retries=3) as client:
        while True:
            if stop_check and stop_check():
                break
            data = client.get_user_notes(user_id, cursor=cursor) or {}
            notes = data.get("notes") or []
            note_pairs = [
                (note, str(note.get("note_id") or note.get("id") or ""))
                for note in notes
            ]
            note_pairs = [(note, note_id) for note, note_id in note_pairs if note_id]
            page_ids = [note_id for _, note_id in note_pairs]
            remote_ids.update(page_ids)
            if progress_callback:
                progress_callback(len(remote_ids), None, "scanning")
            local = _local_files(folder)
            missing = [note for note, note_id in note_pairs
                       if note_id.lower() not in local]
            if not full and notes and not missing:
                print("[update] Reached a fully local Xiaohongshu page; stopping recent-post scan")
                break

            for note in notes if full else missing:
                if stop_check and stop_check():
                    break
                note_id = str(note.get("note_id") or note.get("id") or "")
                if not note_id:
                    continue
                token = str(note.get("xsec_token") or "")
                if token:
                    cache_note_context(note_id, token, "pc_user")
                try:
                    detail = client.get_note_detail(
                        note_id, xsec_token=token, xsec_source="pc_user"
                    )
                    card = _note_card(detail)
                    media = _media(card)
                    if media:
                        remote_names[note_id] = (
                            f"{_filename_base(note_id, card)}_{media[0][1]}"
                        )
                    if _complete(note_id, folder, len(media)):
                        continue
                    base = _filename_base(note_id, card)
                    jobs = [
                        (url, folder / f"{base}_{role}")
                        for url, role in media
                        if not (folder / f"{base}_{role}").exists()
                    ]
                    if progress_callback:
                        progress_callback(len(remote_ids), None, "downloading")
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=min(3, max(1, max_tasks))
                    ) as pool:
                        futures = [
                            pool.submit(_download, url, target, cookies, stop_check)
                            for url, target in jobs
                        ]
                        for future in futures:
                            future.result()
                except Exception as exc:
                    print(f"[error] Xiaohongshu note {note_id} failed: {exc}")
            if not data.get("has_more") or not notes:
                break
            next_cursor = str(data.get("cursor") or "")
            if not next_cursor or next_cursor == cursor:
                print("[warning] Xiaohongshu pagination cursor did not advance")
                break
            cursor = next_cursor
            time.sleep(0.5)
    return {
        "remote_total": len(remote_ids),
        "remote_ids": sorted(remote_ids),
        "remote_names": remote_names,
    }


def download_note(note_id: str, cookie_file: str, outdir: str, stop_check=None) -> dict:
    from xhs_cli.client import XhsClient
    cookies = load_netscape_cookies(cookie_file)
    if not cookies.get("a1"):
        raise RuntimeError("Xiaohongshu login required: import cookies containing a1")
    folder = Path(outdir)
    folder.mkdir(parents=True, exist_ok=True)
    with XhsClient(cookies, request_delay=1.0, max_retries=3) as client:
        card = _note_card(client.get_note_detail(note_id))
    media = _media(card)
    base = _filename_base(note_id, card)
    downloaded = 0
    for url, role in media:
        target = folder / f"{base}_{role}"
        if target.exists() or _download(url, target, cookies, stop_check):
            downloaded += 1
    if not media:
        raise RuntimeError("Xiaohongshu returned no downloadable media for this note")
    return {"note_id": note_id, "media": downloaded}
