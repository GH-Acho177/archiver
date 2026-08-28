"""
FastAPI backend for the React UI.
Run with:  python run_api.py
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import threading
import time
import urllib.request
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

import sys, os, shutil

# Frozen (PyInstaller): __file__ is _internal\src\api.pyc — go up to the EXE dir.
# Dev: __file__ is src/api.py — go up to the project root.
if getattr(sys, "frozen", False):
    _ROOT    = Path(sys.executable).resolve().parent
    _MEIPASS = Path(getattr(sys, "_MEIPASS", _ROOT))
    _HELPERS = _MEIPASS / "helpers"
    # Add _internal\ to PATH so subprocess can find bundled yt-dlp.exe / gallery-dl.exe
    os.environ["PATH"] = str(_MEIPASS) + os.pathsep + os.environ.get("PATH", "")
else:
    _ROOT    = Path(__file__).resolve().parent.parent
    _HELPERS = _ROOT / "helpers"
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HELPERS))
os.chdir(_ROOT)

from src.creator_store import CreatorStore, DuplicateEntryError
from src.config import (
    APP_VERSION, DOWNLOAD_PATH_FILE, PLATFORMS,
    SETTINGS_FILE, UPDATE_HISTORY_FILE, POST_INDEX_FILE, GDL, ARCHIVES_DIR,
    _MEDIA_EXTS,
)

def _resolve_downloader(command: str) -> str:
    """Resolve a bundled downloader in both frozen and source checkouts."""
    exe_name = command if command.lower().endswith(".exe") else f"{command}.exe"
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(_MEIPASS / exe_name)
    candidates.append(_ROOT / "packaging" / exe_name)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(command) or command


_GALLERY_DL = _resolve_downloader(GDL)
_YT_DLP = _resolve_downloader("yt-dlp")

def _find_ffmpeg_location() -> "str | None":
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(_MEIPASS)
    candidates.append(_ROOT / "packaging")
    for directory in candidates:
        if (directory / "ffmpeg.exe").is_file() and (directory / "ffprobe.exe").is_file():
            return str(directory)
    ffmpeg = shutil.which("ffmpeg")
    return str(Path(ffmpeg).parent) if ffmpeg else None


_FFMPEG_LOCATION = _find_ffmpeg_location()

def _yt_dlp_command(concurrent_fragments: int = 1) -> list[str]:
    cmd = [_YT_DLP, "--no-update"]
    if _FFMPEG_LOCATION:
        cmd += ["--ffmpeg-location", _FFMPEG_LOCATION]
    if concurrent_fragments > 1:
        cmd += ["--concurrent-fragments", str(concurrent_fragments)]
    return cmd


_NO_WINDOW = 0x08000000  # Windows CREATE_NO_WINDOW flag (ignored on non-Windows)


def _stop_process(proc) -> None:
    """Stop a downloader and its children, including any spawned FFmpeg."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
                timeout=5,
                check=False,
            )
        else:
            proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

# ── Persistent log file ───────────────────────────────────────────────────────

def _open_log_file():
    try:
        log_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Archiver"
        log_dir.mkdir(parents=True, exist_ok=True)
        return open(log_dir / "archiver.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        return None

_log_fh = _open_log_file()

def _file_log(text: str) -> None:
    if _log_fh:
        try:
            _log_fh.write(text)
        except Exception:
            pass

# Log startup diagnostics once
import datetime as _dt_startup
_file_log(f"\n{'='*60}\n[startup] {_dt_startup.datetime.now().isoformat()}\n")
_file_log(f"[startup] frozen={getattr(sys, 'frozen', False)}\n")
_file_log(f"[startup] ROOT={_ROOT}\n")
if getattr(sys, "frozen", False):
    _file_log(f"[startup] MEIPASS={_MEIPASS}\n")
    _file_log(f"[startup] HELPERS={_HELPERS}\n")
    _file_log(f"[startup] PATH prefix={str(_MEIPASS)}\n")
del _dt_startup

_ANSI_RE      = re.compile(r"\x1b\[[0-9;]*[mK]")
_TWEET_ID_RE  = re.compile(r"(?:^|_)(\d{15,20})(?:_\d+)*(?:_|$)")
_DOUYIN_ID_RE = re.compile(r"(\d{15,20})")
_BVID_RE      = re.compile(r"(BV[a-zA-Z0-9]{10})")
_XHS_NOTE_RE  = re.compile(r"(?<![0-9a-f])([0-9a-f]{24})(?![0-9a-f])", re.I)
_DOUYIN_NAMING = "{create:.10}_{aweme_id}_{desc:.60}"
_X_FILENAME = "{date:%Y-%m-%d}_{tweet_id}_{num}.{extension}"
_BILIBILI_FILENAME = "%(upload_date>%Y-%m-%d)s_%(id)s_%(title)s.%(ext)s"


def _ensure_entry_download_folder(entry, store, download_root: Path) -> Path:
    """Create and return the folder used by sync for a tracked account."""
    display = (
        entry.handle.split("|")[0]
        if "|" in entry.handle else entry.handle
    )
    creator = (
        store.get_creator(entry.creator_id)
        if entry.creator_id else None
    )
    parent_name = creator.name if creator else display
    safe_parent = re.sub(r'[\\/:*?"<>|]', "_", parent_name).strip() or "account"
    if creator:
        safe_account = re.sub(
            r'[\\/:*?"<>|]', "_", f"{display} [{entry.platform}]"
        ).strip() or entry.platform
        folder = download_root / safe_parent / safe_account
    else:
        folder = download_root / safe_parent
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _find_nonempty_duplicate_folder(download_root: Path) -> Path | None:
    """Return the first non-empty duplicate quarantine under downloads."""
    if not download_root.exists():
        return None
    for directory in download_root.rglob("*"):
        if (
            directory.is_dir()
            and directory.name.casefold().strip("_")
            in {"duplicate", "duplicates", "duplicated"}
        ):
            try:
                if any(path.is_file() for path in directory.rglob("*")):
                    return directory
            except OSError:
                return directory
    return None


# ── Corrupt-file detection ────────────────────────────────────────────────────

_MIN_VIDEO_BYTES = 50 * 1024  # 50 KB minimum — anything smaller is truncated
_MIN_IMAGE_BYTES = 100
_MP4_BOX_TYPES   = {b"ftyp", b"mdat", b"moov", b"free", b"wide", b"skip", b"pdin"}
_IMAGE_EXTS      = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _is_valid_video(path: Path) -> bool:
    try:
        if path.stat().st_size < _MIN_VIDEO_BYTES:
            return False
        with path.open("rb") as f:
            header = f.read(12)
        if len(header) < 8:
            return False
        box_type = header[4:8]
        if box_type in _MP4_BOX_TYPES:
            return True
        if header[:4] == b"\x1a\x45\xdf\xa3":  # WebM
            return True
        if header[:4] == b"RIFF":               # AVI
            return True
        if header[0] == 0x47:                   # MPEG-TS
            return True
        return False
    except OSError:
        return False


def _is_valid_image(path: Path) -> bool:
    """Validate common image signatures without requiring Pillow."""
    try:
        if path.stat().st_size < _MIN_IMAGE_BYTES:
            return False
        with path.open("rb") as file:
            header = file.read(16)
        return (
            header.startswith(b"\xff\xd8\xff")
            or header.startswith(b"\x89PNG\r\n\x1a\n")
            or header.startswith((b"GIF87a", b"GIF89a"))
            or (len(header) >= 12 and header[:4] == b"RIFF"
                and header[8:12] == b"WEBP")
            or header.startswith(b"BM")
        )
    except OSError:
        return False


def _is_valid_media(path: Path) -> bool:
    if path.suffix.lower() in _IMAGE_EXTS:
        return _is_valid_image(path)
    return _is_valid_video(path)


def _deduplicate_account_files(folder: Path, platform: str, log_fn) -> int:
    """Remove byte-identical copies of the same post/media role."""
    import hashlib
    groups: dict[tuple[str, str, str, int], list[Path]] = {}
    if not folder.exists():
        return 0
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _MEDIA_EXTS:
            continue
        if platform == "bilibili":
            id_match = _BVID_RE.search(path.name)
        elif platform == "xiaohongshu":
            id_match = _XHS_NOTE_RE.search(path.stem)
        else:
            id_match = _DOUYIN_ID_RE.search(path.stem)
        if not id_match:
            continue
        post_id = (id_match.group(1)
                   if platform in {"bilibili", "xiaohongshu"}
                   else id_match.group())
        if platform == "douyin":
            role_match = re.search(r"_(video|image_\d+|live_\d+)$", path.stem)
            role = (role_match.group(1) if role_match else
                    "video" if path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
                    else path.stem)
        elif platform == "x":
            role_match = re.search(r"_(\d+)$", path.stem)
            role = role_match.group(1) if role_match else path.stem
        else:
            role = "media"
        try:
            size = path.stat().st_size
        except OSError:
            continue
        groups.setdefault((post_id, role, path.suffix.lower(), size), []).append(path)

    removed = 0
    for candidates in groups.values():
        if len(candidates) < 2:
            continue
        by_digest: dict[bytes, list[Path]] = {}
        for path in candidates:
            try:
                digest = hashlib.sha256()
                with path.open("rb") as file:
                    for chunk in iter(lambda: file.read(1024 * 1024), b""):
                        digest.update(chunk)
                by_digest.setdefault(digest.digest(), []).append(path)
            except OSError:
                continue
        for duplicates in by_digest.values():
            if len(duplicates) < 2:
                continue
            # Prefer the newest naming policy, then the shortest stable path.
            def score(path: Path) -> tuple[int, int]:
                if platform == "douyin":
                    current = bool(re.match(
                        r"^\d{4}-\d{2}-\d{2}_\d{15,20}_.+_"
                        r"(?:video|image_\d+|live_\d+)$", path.stem
                    ))
                elif platform == "bilibili":
                    current = bool(re.match(
                        r"^\d{4}-\d{2}-\d{2}_BV[A-Za-z0-9]{10}_.+", path.stem
                    ))
                elif platform == "xiaohongshu":
                    current = bool(re.match(
                        r"^\d{4}-\d{2}-\d{2}_[0-9a-f]{24}_.+_"
                        r"(?:video|image_\d+)$", path.stem, re.I
                    ))
                else:
                    current = bool(re.match(
                        r"^\d{4}-\d{2}-\d{2}_\d{15,20}_\d+$", path.stem
                    ))
                return (int(current), -len(str(path)))
            keep = max(duplicates, key=score)
            for duplicate in duplicates:
                if duplicate == keep:
                    continue
                try:
                    duplicate.unlink()
                    removed += 1
                    log_fn(
                        f"  [dedupe] Removed {duplicate.name}; kept {keep.name}\n"
                    )
                except OSError as exc:
                    log_fn(f"  [dedupe] Could not remove {duplicate.name}: {exc}\n")
    return removed


def _extract_group_duplicates(
    group_folder: Path,
    account_folders: list[Path],
    log_fn,
    progress_fn=None,
) -> int:
    """Move byte-identical media copies into a group's _Duplicates folder."""
    import hashlib

    duplicate_root = group_folder / "_Duplicates"
    by_size: dict[int, list[Path]] = {}
    for account_folder in account_folders:
        if not account_folder.exists():
            continue
        for path in account_folder.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in _MEDIA_EXTS
                and duplicate_root not in path.parents
            ):
                try:
                    by_size.setdefault(path.stat().st_size, []).append(path)
                except OSError:
                    pass

    moved = 0
    total_files = sum(len(paths) for paths in by_size.values())
    scanned_files = sum(
        len(paths) for paths in by_size.values() if len(paths) < 2
    )
    if progress_fn:
        progress_fn(scanned_files, total_files)
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        by_digest: dict[bytes, list[Path]] = {}
        for path in same_size:
            try:
                digest = hashlib.sha256()
                with path.open("rb") as file:
                    for chunk in iter(lambda: file.read(1024 * 1024), b""):
                        digest.update(chunk)
                by_digest.setdefault(digest.digest(), []).append(path)
            except OSError:
                pass
            finally:
                scanned_files += 1
                if progress_fn:
                    progress_fn(scanned_files, total_files)
        for copies in by_digest.values():
            if len(copies) < 2:
                continue
            def title_quality(path: Path) -> tuple[int, int, int]:
                stem = re.sub(
                    r"^\d{4}-\d{2}-\d{2}_", "", path.stem
                )
                stem = _BVID_RE.sub("", stem)
                stem = _DOUYIN_ID_RE.sub("", stem)
                stem = re.sub(
                    r"_(?:video|image_\d+|live_\d+|\d+)$", "", stem
                )
                title = stem.strip(" _-")
                meaningful = bool(
                    title
                    and title.casefold() not in {
                        "untitled", "unknown", "video", "image"
                    }
                )
                return (
                    int(meaningful),
                    len(title) if meaningful else 0,
                    -len(str(path)),
                )

            # Same-folder duplicates retain their best-titled copy in place.
            # Cross-folder duplicate sets quarantine the best-titled survivor.
            keep = max(copies, key=title_quality)
            same_folder = len({copy.parent for copy in copies}) == 1
            for duplicate in copies:
                if duplicate == keep:
                    continue
                try:
                    duplicate.unlink()
                    moved += 1
                    log_fn(
                        f"  [dedupe] Removed duplicate "
                        f"{duplicate.relative_to(group_folder)}\n"
                    )
                except OSError as exc:
                    log_fn(
                        f"  [dedupe] Could not remove {duplicate.name}: {exc}\n"
                    )
            if same_folder:
                log_fn(
                    f"  [dedupe] Kept best-titled same-folder copy "
                    f"{keep.relative_to(group_folder)}\n"
                )
                continue
            if not keep.exists():
                continue
            try:
                relative = keep.relative_to(group_folder)
                target = duplicate_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    index = 2
                    while True:
                        candidate = target.with_name(
                            f"{target.stem}_duplicate_{index}{target.suffix}"
                        )
                        if not candidate.exists():
                            target = candidate
                            break
                        index += 1
                keep.replace(target)
                moved += 1
                log_fn(
                    f"  [dedupe] Quarantined surviving copy "
                    f"{relative} as "
                    f"_Duplicates/{target.relative_to(duplicate_root)}\n"
                )
            except OSError as exc:
                log_fn(
                    f"  [dedupe] Could not quarantine {keep.name}: {exc}\n"
                )
    return moved


def _post_ids_in_files(platform: str, files) -> set[str]:
    ids: set[str] = set()
    for file in files:
        if not file.is_file() or file.suffix.lower() not in _MEDIA_EXTS:
            continue
        result = _extract_post_id_and_date(platform, file)
        if result is not None:
            ids.add(result[0])
    return ids


def _normalize_local_names(folder: Path, platform: str, log_fn) -> int:
    """Normalize locally derivable Bilibili/X names without network requests."""
    import datetime as dt
    if platform not in {"bilibili", "douyin", "x"} or not folder.exists():
        return 0
    renamed = 0
    for source in list(folder.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in _MEDIA_EXTS:
            continue
        date_match = re.match(r"(\d{4}-\d{2}-\d{2})_", source.name)
        try:
            date = (date_match.group(1) if date_match else
                    dt.datetime.fromtimestamp(source.stat().st_mtime).strftime("%Y-%m-%d"))
        except OSError:
            continue
        if platform == "bilibili":
            id_match = _BVID_RE.search(source.stem)
            if (not id_match or re.search(r"\.f\d+\.", source.name, re.IGNORECASE)
                    or ".merging." in source.name.lower()):
                continue
            post_id = id_match.group(1)
            remainder = source.stem[id_match.end():].strip(" _-")
            if not remainder:
                continue
            title = remainder
            target_name = f"{date}_{post_id}_{title}{source.suffix.lower()}"
        elif platform == "douyin":
            id_match = _DOUYIN_ID_RE.search(source.stem)
            if not id_match:
                continue
            post_id = id_match.group()
            role_match = re.search(r"_(video|image_\d+|live_\d+)$", source.stem)
            if not role_match:
                continue
            # A dated Douyin name with a title and media role was produced
            # from remote metadata already. Do not rebuild it locally:
            # format_file_name may legitimately leave underscores at the end
            # of a sanitized title, while strip(" _-") below removes them.
            # Rebuilding here and reconciling from metadata afterwards would
            # therefore rename the same file back and forth on every run.
            if (
                date_match
                and id_match.start() == len(date_match.group(0))
                and source.stem[id_match.end():role_match.start()]
            ):
                continue
            role = role_match.group(1)
            remainder = source.stem[id_match.end():role_match.start()].strip(" _-")
            # A title cannot be reconstructed locally. Leave legacy names
            # unchanged until Douyin metadata supplies the real description.
            if not remainder:
                continue
            title = remainder
            target_name = (
                f"{date}_{post_id}_{title}_{role}{source.suffix.lower()}"
            )
        else:
            id_match = _DOUYIN_ID_RE.search(source.stem)
            if not id_match:
                continue
            post_id = id_match.group()
            num_match = re.search(r"_(\d+)$", source.stem)
            media_num = num_match.group(1) if num_match else "1"
            target_name = f"{date}_{post_id}_{media_num}{source.suffix.lower()}"
        target = source.with_name(target_name)
        if target == source:
            continue
        try:
            if target.exists():
                log_fn(f"  [naming] Collision kept unchanged: {source.name}\n")
                continue
            source.rename(target)
            renamed += 1
            log_fn(f"  [naming] {source.name} -> {target.name}\n")
        except OSError as exc:
            log_fn(f"  [naming] Could not rename {source.name}: {exc}\n")
    return renamed


def _repair_unmerged_bilibili(root: Path, log_fn) -> int:
    """Merge video/audio pairs left by yt-dlp runs that lacked FFmpeg."""
    if not _FFMPEG_LOCATION or not root.exists():
        return 0
    ffmpeg = Path(_FFMPEG_LOCATION) / "ffmpeg.exe"
    if not ffmpeg.is_file():
        return 0

    repaired = 0
    for audio in root.rglob("*.m4a"):
        match = re.match(r"^(.*)\.f\d+\.m4a$", audio.name, re.IGNORECASE)
        if not match:
            continue
        base_name = match.group(1)
        videos = sorted(audio.parent.glob(f"{base_name}.f*.mp4"))
        if not videos:
            continue
        video = videos[0]
        output = audio.parent / f"{base_name}.mp4"
        temporary = audio.parent / f"{base_name}.merging.mp4"
        try:
            if temporary.exists():
                temporary.unlink()
            result = subprocess.run(
                [
                    str(ffmpeg), "-y", "-loglevel", "error",
                    "-i", str(video), "-i", str(audio),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c", "copy", "-movflags", "+faststart", str(temporary),
                ],
                capture_output=True,
                text=True,
                creationflags=_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if result.returncode != 0 or not _is_valid_video(temporary):
                detail = (result.stderr or "invalid merged output").strip()
                log_fn(f"[error] Could not merge {video.name}: {detail}\n")
                if temporary.exists():
                    temporary.unlink()
                continue
            temporary.replace(output)
            video.unlink()
            audio.unlink()
            repaired += 1
            log_fn(f"[merge] Repaired {output.name}\n")
        except OSError as exc:
            log_fn(f"[error] Could not repair {video.name}: {exc}\n")
    return repaired


def _seed_bilibili_archive_from_disk(folder: Path, archive: Path) -> int:
    """Rebuild yt-dlp's archive from BVIDs in files that still exist on disk."""
    ids = {
        match.group(1)
        for file in folder.rglob("*")
        if (file.is_file()
            and file.suffix.lower() in {".mp4", ".webm", ".mkv"}
            and not re.search(r"\.f\d+\.", file.name, re.IGNORECASE)
            and ".merging." not in file.name.lower()
            and _is_valid_video(file))
        for match in [_BVID_RE.search(file.name)]
        if match
    }
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(
        "".join(f"bilibili {post_id}\n" for post_id in sorted(ids)),
        encoding="utf-8",
    )
    return len(ids)


def _seed_gallery_archive_from_disk(folder: Path, archive: Path) -> int:
    """Rebuild gallery-dl's Twitter archive from local tweet media filenames."""
    import sqlite3
    entries: set[str] = set()
    if folder.exists():
        for file in folder.rglob("*"):
            if (not file.is_file()
                    or file.suffix.lower() not in _MEDIA_EXTS
                    or not _is_valid_media(file)):
                continue
            match = re.search(r"(?:^|_)(\d{15,20})_(\d+)$", file.stem)
            if match:
                tweet_id, media_num = match.groups()
                entries.add(f"twitter{tweet_id}_0_{media_num}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(archive) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS archive "
            "(entry TEXT PRIMARY KEY) WITHOUT ROWID"
        )
        connection.execute("DELETE FROM archive")
        connection.executemany(
            "INSERT OR IGNORE INTO archive(entry) VALUES (?)",
            ((entry,) for entry in sorted(entries)),
        )
    return len(entries)


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _display_width(value) -> int:
    """Terminal column width, including CJK full-width characters."""
    import unicodedata
    text = _strip_ansi(str(value))
    return sum(
        2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        for char in text
    )


def _pad_display(value, width: int, right: bool = False) -> str:
    text = str(value)
    padding = " " * max(0, width - _display_width(text))
    return padding + text if right else text + padding


def _summary_table(results: list[dict], full: bool = False) -> str:
    """Format post-count results as an aligned, CJK-safe text table."""
    headers = [
        "Account", "Platform", "Local", "On remote",
        "Downloaded", "Remote", "IDs listed", "Verified",
    ]
    rows: list[list[str]] = []
    for result in results:
        overlap = result.get("local_remote_posts")
        if overlap is None:
            overlap_text = "?"
        elif result.get("remote_ids_complete"):
            overlap_text = str(overlap)
        else:
            overlap_text = f"{overlap}+"
        remote = result.get("remote_total")
        row = [
            str(result.get("display", "")),
            str(result.get("platform", "")),
            str(result.get("total_posts", 0)),
            overlap_text,
            str(result.get("downloaded_posts", 0)),
            str(remote if remote is not None else "?"),
            str(result.get("remote_ids_seen", 0)),
            "Verified" if result.get("verified") else "Incomplete",
        ]
        if full:
            if result.get("verified"):
                status = "VERIFIED"
            elif not result.get("remote_ids_complete"):
                status = "UNVERIFIED"
            elif remote is not None:
                missing = max(
                    0, int(remote) - (result.get("local_remote_posts") or 0)
                )
                status = f"INCOMPLETE ({missing} missing)"
            else:
                status = "UNVERIFIED"
            row.append(status)
        rows.append(row)
    if full:
        headers.append("Status")

    widths = [
        max(_display_width(header), *( _display_width(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]
    numeric = set(range(2, 7))

    def render(row: list[str]) -> str:
        cells = [
            _pad_display(value, widths[index], index in numeric)
            for index, value in enumerate(row)
        ]
        return "| " + " | ".join(cells) + " |"

    separator = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    return "\n".join([
        separator,
        render(headers),
        separator,
        *(render(row) for row in rows),
        separator,
    ])


def _maintenance_summary_table(results: list[dict]) -> str:
    """Maintenance is local-only; do not display unavailable remote fields."""
    headers = ["Account", "Platform", "Local posts"]
    rows = [
        [
            str(result.get("display", "")),
            str(result.get("platform", "")),
            str(result.get("total_posts", 0)),
        ]
        for result in results
    ]
    widths = [
        max(_display_width(header), *(
            _display_width(row[index]) for row in rows
        ))
        for index, header in enumerate(headers)
    ]

    def render(row: list[str]) -> str:
        return "| " + " | ".join(
            _pad_display(value, widths[index], index == 2)
            for index, value in enumerate(row)
        ) + " |"

    separator = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    return "\n".join([
        separator,
        render(headers),
        separator,
        *(render(row) for row in rows),
        separator,
    ])


def _duplicate_summary_table(results: list[dict]) -> str:
    """Format group-level duplicate extraction counts."""
    headers = ["Group", "Accounts", "Duplicates detected"]
    rows = [
        [
            str(result.get("group", "")),
            str(result.get("accounts", 0)),
            str(result.get("duplicates", 0)),
        ]
        for result in results
    ]
    widths = [
        max(_display_width(header), *(
            _display_width(row[index]) for row in rows
        ))
        for index, header in enumerate(headers)
    ]

    def render(row: list[str]) -> str:
        return "| " + " | ".join(
            _pad_display(value, widths[index], index > 0)
            for index, value in enumerate(row)
        ) + " |"

    separator = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    return "\n".join([
        separator,
        render(headers),
        separator,
        *(render(row) for row in rows),
        separator,
    ])


class _PrintCapture:
    """Routes concurrent print() calls through the logger for their worker."""
    def __init__(self, log_fn):
        self._log = log_fn
        self._thread_logs: dict[int, object] = {}
        self._lock = threading.Lock()
    def bind(self, log_fn) -> None:
        with self._lock:
            self._thread_logs[threading.get_ident()] = log_fn
    def unbind(self) -> None:
        with self._lock:
            self._thread_logs.pop(threading.get_ident(), None)
    def write(self, text: str) -> int:
        if text:
            with self._lock:
                log_fn = self._thread_logs.get(threading.get_ident(), self._log)
            log_fn(text)
        return len(text)
    def flush(self) -> None: pass
    def isatty(self) -> bool: return False


# ── Application state ─────────────────────────────────────────────────────────

class AppState:
    def __init__(self):
        for d in ("config", "config/avatars", "config/archives", "downloads", "logs"):
            Path(d).mkdir(parents=True, exist_ok=True)

        self._store = CreatorStore()
        self._store.migrate_from_legacy(PLATFORMS)

        self.running    = False
        self.status     = "Idle"
        self.stop_flag  = threading.Event()
        self._proc      = None
        self._procs:    list = []
        self._procs_lock = threading.Lock()
        self._mode        = "update"
        self._from_days   = 0
        self._creator_ids: list[str] | None = None
        self._entry_ids: list[str] | None = None
        self._maintenance_creator_ids: list[str] | None = None

        self._log_listeners: list[asyncio.Queue] = []
        self._log_backlog: deque[str] = deque(maxlen=5000)
        self._log_history: deque[tuple[int, str, str | None]] = deque(maxlen=5000)
        self._log_sequence = 0
        self._log_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._print_capture_lock = threading.Lock()
        self._print_capture_users = 0
        self._print_capture = _PrintCapture(self.log_write)
        self._original_stdout = None
        self._original_stderr = None
        self._progress: dict[str, dict] = {}
        self._progress_lock = threading.Lock()
        self._bilibili_cooldown_until = 0.0
        self._bilibili_cooldown_lock = threading.Lock()

        self._total_downloads: int = self._compute_total_downloads()
        self._sched_thread: threading.Thread | None = None
        self._sched_stop:   threading.Event = threading.Event()
        self._next_sync_at: float = 0.0

        self._tg_bot     = None
        self._tg_status  = "stopped"
        self._tg_pending: dict[int, dict] = {}  # chat_id → pending multi-step action

        # Auto-start scheduler and bot if previously enabled
        cfg = self._load_settings()
        if cfg.get("auto_update_enabled"):
            self.start_scheduler(int(cfg.get("auto_update_interval", 60)))
        tg_token = cfg.get("telegram_token", "")
        if tg_token:
            self.start_tg_bot(tg_token)

    # ── Log broadcast ──────────────────────────────────────────────────────────

    def log_write(self, text: str, account_key: str | None = None) -> None:
        text = _strip_ansi(text)
        # Simulate terminal carriage return: keep only the last \r-overwritten
        # state per line so progress bars appear as a single updated line.
        if "\r" in text:
            segments = text.split("\n")
            processed = []
            for seg in segments:
                if "\r" in seg:
                    parts = [p for p in seg.split("\r") if p]
                    processed.append(parts[-1] if parts else "")
                else:
                    processed.append(seg)
            text = "\n".join(processed)
        _file_log(text)
        if not isinstance(sys.stdout, _PrintCapture):
            try:
                print(text, end="", flush=True)
            except (UnicodeEncodeError, AttributeError, TypeError):
                pass
        with self._log_lock:
            self._log_sequence += 1
            self._log_history.append((self._log_sequence, text, account_key))
            listeners = list(self._log_listeners)
            if not listeners:
                self._log_backlog.append(text)
        if not self._loop or self._loop.is_closed():
            return
        for q in listeners:
            self._loop.call_soon_threadsafe(q.put_nowait, text)

    def begin_print_capture(self, log_fn=None) -> None:
        """Share one stdout capture safely across concurrent f2 accounts."""
        with self._print_capture_lock:
            if self._print_capture_users == 0:
                self._original_stdout, self._original_stderr = sys.stdout, sys.stderr
                sys.stdout = self._print_capture
                sys.stderr = self._print_capture
            self._print_capture_users += 1
            self._print_capture.bind(log_fn or self.log_write)

    def end_print_capture(self) -> None:
        with self._print_capture_lock:
            self._print_capture.unbind()
            self._print_capture_users = max(0, self._print_capture_users - 1)
            if self._print_capture_users == 0:
                if self._original_stdout is not None:
                    sys.stdout = self._original_stdout
                if self._original_stderr is not None:
                    sys.stderr = self._original_stderr
                self._original_stdout = self._original_stderr = None

    # ── Status ─────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        with self._progress_lock:
            progress = [dict(item) for item in self._progress.values()]
        return {
            "running":          self.running,
            "status":           self.status,
            "mode":             self._mode,
            "from_days":        self._from_days,
            "tracking":         len(self._store.all_entries()),
            "last_sync":        self._get_last_sync(),
            "version":          APP_VERSION,
            "total_downloads":  self._total_downloads,
            "scheduler_active": self._sched_thread is not None and self._sched_thread.is_alive(),
            "next_sync_at":     self._next_sync_at,
            "progress":         progress,
        }

    def progress_clear(self) -> None:
        with self._progress_lock:
            self._progress.clear()

    def progress_update(self, key: str, **values) -> None:
        with self._progress_lock:
            item = self._progress.setdefault(key, {"key": key})
            item.update(values)

    def _get_last_sync(self) -> str:
        try:
            hist = json.loads(Path(UPDATE_HISTORY_FILE).read_text("utf-8"))
            if isinstance(hist, list) and hist:
                last = hist[-1]
                return f"{last.get('date', '')} {last.get('time', '')}".strip()
        except Exception:
            pass
        return "—"

    def _compute_total_downloads(self) -> int:
        try:
            hist = json.loads(Path(UPDATE_HISTORY_FILE).read_text("utf-8"))
            if isinstance(hist, list):
                return sum(u.get("count", 0) for e in hist for u in e.get("users", []))
        except Exception:
            pass
        return 0

    # ── Scheduler ──────────────────────────────────────────────────────────────

    def start_scheduler(self, interval_minutes: int) -> None:
        self.stop_scheduler()
        self._sched_stop = threading.Event()
        self._sched_thread = threading.Thread(
            target=self._scheduler_worker,
            args=(interval_minutes, self._sched_stop), daemon=True)
        self._sched_thread.start()

    def stop_scheduler(self) -> None:
        self._sched_stop.set()
        self._next_sync_at = 0.0
        self._sched_thread = None

    def _scheduler_worker(self, interval_minutes: int,
                          stop_event: threading.Event) -> None:
        sleep_secs = interval_minutes * 60
        next_run = time.time() + sleep_secs
        self._next_sync_at = next_run
        self.log_write(f"[scheduler] Next sync in {interval_minutes}m\n")
        while not stop_event.is_set():
            remaining = next_run - time.time()
            if remaining <= 0:
                if not self.running:
                    self.log_write("[scheduler] Starting scheduled sync…\n")
                    self.start(None, None)
                next_run = time.time() + sleep_secs
                self._next_sync_at = next_run
            stop_event.wait(timeout=min(60.0, max(1.0, remaining)))

    # ── Telegram bot ──────────────────────────────────────────────────────────

    def start_tg_bot(self, token: str) -> None:
        self.stop_tg_bot()
        import sys as _sys
        _sys.path.insert(0, str(_HELPERS))
        try:
            import tg_bot as _tg
            self._tg_bot = _tg.TelegramBot(
                token,
                on_message=self._on_tg_message,
                on_error=lambda _: self._set_tg_status("error"),
                on_log=self.log_write,
            )
            self._tg_bot.start()
            self._set_tg_status("running")
        except Exception as exc:
            self.log_write(f"[Bot] Failed to start: {exc}\n")
            self._set_tg_status("error")

    def stop_tg_bot(self) -> None:
        if self._tg_bot is not None:
            self._tg_bot.stop()
            self._tg_bot = None
        self._set_tg_status("stopped")

    def _set_tg_status(self, s: str) -> None:
        self._tg_status = s

    def _handle_tg_pending(self, pending: dict, text: str, chat_id: int) -> None:
        if pending["action"] == "delete_account":
            if text.strip().casefold() != "delete":
                if self._tg_bot:
                    self._tg_bot.send_message(
                        chat_id, "Account deletion cancelled."
                    )
                return
            if self.running:
                if self._tg_bot:
                    self._tg_bot.send_message(
                        chat_id,
                        "Cannot delete an account while sync is running. "
                        "Stop the sync and try again.",
                    )
                return
            entry = self._store.get_entry(pending["entry_id"])
            if entry is None:
                if self._tg_bot:
                    self._tg_bot.send_message(
                        chat_id, "That account no longer exists."
                    )
                return
            display = entry.handle.split("|")[0]
            archive = _entry_archive_path(entry)
            try:
                archive_removed = archive.is_file()
                if archive_removed:
                    archive.unlink()
                self._store.remove_entry(entry.id)
            except OSError as exc:
                if self._tg_bot:
                    self._tg_bot.send_message(
                        chat_id, f"Could not delete account records: {exc}"
                    )
                return
            self.log_write(
                f"[account] Removed {entry.platform} account: {entry.handle}\n"
            )
            if self._tg_bot:
                record_note = (
                    " Archive record deleted." if archive_removed
                    else " No archive record existed."
                )
                self._tg_bot.send_message(
                    chat_id,
                    f"Deleted {display} ({entry.platform}).{record_note}",
                )
            return
        if pending["action"] != "assign_group":
            return
        entry_id = pending["entry_id"]
        creators = pending["creators"]
        display  = pending["display"]
        try:
            n = int(text)
        except ValueError:
            if self._tg_bot:
                self._tg_bot.send_message(chat_id, "⚠ Reply with a number.")
            self._tg_pending[chat_id] = pending  # restore so they can retry
            return
        if n == 0:
            creator = self._store.add_creator(display)
            self._store.assign_entry(entry_id, creator.id)
            entry = self._store.get_entry(entry_id)
            if entry is not None:
                _ensure_entry_download_folder(
                    entry, self._store, self._download_root()
                )
            if self._tg_bot:
                self._tg_bot.send_message(chat_id, f"✅ {display} added to new group \"{creator.name}\".")
            return
        if 1 <= n <= len(creators):
            c = creators[n - 1]
            self._store.assign_entry(entry_id, c.id)
            entry = self._store.get_entry(entry_id)
            if entry is not None:
                _ensure_entry_download_folder(
                    entry, self._store, self._download_root()
                )
            if self._tg_bot:
                self._tg_bot.send_message(chat_id, f"✅ {display} added to {c.name}.")
        else:
            if self._tg_bot:
                self._tg_bot.send_message(chat_id, f"⚠ Enter a number between 0 and {len(creators)}.")
            self._tg_pending[chat_id] = pending  # restore so they can retry

    def _tg_ordered_accounts(self) -> list:
        """Return accounts in the same stable group-wise order shown by bot."""
        ordered = []
        for creator in self._store.all_creators():
            ordered.extend(self._store.get_entries_for_creator(creator.id))
        ordered.extend(self._store.get_unassigned_entries())
        return ordered

    def _tg_send_accounts(
        self, chat_id: int, footer: "str | None" = None
    ) -> None:
        if not self._tg_bot:
            return
        entries = self._tg_ordered_accounts()
        if not entries:
            message = "No tracked accounts."
            if footer:
                message += "\n\n" + footer
            self._tg_bot.send_message(chat_id, message)
            return
        numbers = {entry.id: index for index, entry in enumerate(entries, 1)}
        lines = [f"Tracked accounts ({len(entries)})"]
        for creator in self._store.all_creators():
            group_entries = self._store.get_entries_for_creator(creator.id)
            if not group_entries:
                continue
            lines.append(f"\n{creator.name}")
            for entry in group_entries:
                display = entry.handle.split("|")[0]
                lines.append(
                    f"{numbers[entry.id]}. [{entry.platform}] {display} "
                    f"(id: {entry.id})"
                )
        unassigned = self._store.get_unassigned_entries()
        if unassigned:
            lines.append("\nUnassigned")
            for entry in unassigned:
                display = entry.handle.split("|")[0]
                lines.append(
                    f"{numbers[entry.id]}. [{entry.platform}] {display} "
                    f"(id: {entry.id})"
                )
        if footer:
            lines.extend(["", footer])

        # Telegram limits text messages to 4096 characters. Keep headings and
        # account lines intact while splitting large account collections.
        chunks: list[str] = []
        current = ""
        for line in lines:
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > 3800 and current:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)
        for chunk in chunks:
            self._tg_bot.send_message(chat_id, chunk)

    def _on_tg_message(self, text: str, chat_id: int, user_id: int) -> None:
        cfg     = self._load_settings()
        allowed = cfg.get("telegram_allowed_id")
        if allowed is None:
            # First message whitelists the sender
            import json as _j
            s = {}
            try:
                s = _j.loads(Path(SETTINGS_FILE).read_text("utf-8"))
            except Exception:
                pass
            s["telegram_allowed_id"] = user_id
            Path(SETTINGS_FILE).write_text(_j.dumps(s, indent=2, ensure_ascii=False), "utf-8")
            allowed = user_id
        if user_id != int(allowed):
            return
        command = text.strip().split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command in {"/status", "/syncstatus"}:
            if self._tg_bot:
                snapshot = self.get_status()
                running = bool(snapshot.get("running"))
                lines = [
                    "Archiver status",
                    f"Activity: {'Running' if running else 'Idle'}",
                    f"State: {snapshot.get('status', 'Idle')}",
                ]
                if running:
                    lines.append(
                        f"Mode: {str(snapshot.get('mode', 'update')).title()}"
                    )
                    active_items = [
                        item for item in snapshot.get("progress", [])
                        if item.get("state") not in {"finished", "stopped"}
                    ]
                    if active_items:
                        lines.append(f"Active accounts: {len(active_items)}")
                        for item in active_items[:10]:
                            state_name = str(
                                item.get("state", "running")
                            ).replace("_", " ").title()
                            done, total = item.get("done"), item.get("total")
                            if done is not None and total:
                                progress = f"{done}/{max(done, total)}"
                            elif item.get("percent") is not None:
                                progress = f"{item['percent']:.1f}%"
                            else:
                                progress = state_name
                            lines.append(
                                f"• {item.get('platform', '?')} · "
                                f"{item.get('account', '?')} — {progress}"
                            )
                        if len(active_items) > 10:
                            lines.append(
                                f"…and {len(active_items) - 10} more"
                            )
                self._tg_bot.send_message(chat_id, "\n".join(lines))
            return
        if command in {"/sync", "/startsync"}:
            parts = text.strip().split()
            mode = parts[1].casefold() if len(parts) > 1 else "update"
            if mode not in {"update", "full"} or len(parts) > 2:
                if self._tg_bot:
                    self._tg_bot.send_message(
                        chat_id,
                        "Usage: /sync [update|full]",
                    )
                return
            result = self.start(mode, None, None)
            if self._tg_bot:
                if "error" in result:
                    self._tg_bot.send_message(
                        chat_id, f"Could not start sync: {result['error']}"
                    )
                else:
                    label = "Full" if mode == "full" else "Update"
                    self._tg_bot.send_message(
                        chat_id,
                        f"Started {label} sync for all tracked accounts. "
                        "Use /status to check progress.",
                    )
            return
        if command in {"/stopsync", "/stop"}:
            result = self.stop()
            if self._tg_bot:
                if "error" in result:
                    self._tg_bot.send_message(
                        chat_id, f"Could not stop sync: {result['error']}"
                    )
                else:
                    self._tg_bot.send_message(
                        chat_id,
                        "Stop requested. Use /status to confirm when it finishes.",
                    )
            return
        if command == "/cancel":
            cancelled = self._tg_pending.pop(chat_id, None) is not None
            if self._tg_bot:
                self._tg_bot.send_message(
                    chat_id,
                    "Pending action cancelled." if cancelled
                    else "There is no pending action.",
                )
            return
        if command in {"/accounts", "/listaccounts"}:
            self._tg_send_accounts(chat_id)
            return
        if command in {"/deleteaccount", "/removeaccount"}:
            if self.running:
                if self._tg_bot:
                    self._tg_bot.send_message(
                        chat_id,
                        "Cannot delete an account while sync is running. "
                        "Stop the sync and try again.",
                    )
                return
            parts = text.strip().split(maxsplit=1)
            if len(parts) < 2:
                self._tg_send_accounts(
                    chat_id,
                    footer="Use /deleteaccount NUMBER to select an account.",
                )
                return
            entries = self._tg_ordered_accounts()
            selector = parts[1].strip()
            entry = next((item for item in entries if item.id == selector), None)
            if entry is None:
                try:
                    number = int(selector)
                    entry = entries[number - 1] if 1 <= number <= len(entries) else None
                except ValueError:
                    entry = None
            if entry is None:
                if self._tg_bot:
                    self._tg_bot.send_message(
                        chat_id,
                        "Account not found. Use /accounts to see current numbers.",
                    )
                return
            creator = (
                self._store.get_creator(entry.creator_id)
                if entry.creator_id else None
            )
            display = entry.handle.split("|")[0]
            location = creator.name if creator else "Unassigned"
            self._tg_pending[chat_id] = {
                "action": "delete_account",
                "entry_id": entry.id,
            }
            if self._tg_bot:
                self._tg_bot.send_message(
                    chat_id,
                    f"Delete {display} ({entry.platform}) from {location}?\n"
                    "Reply DELETE to confirm, or /cancel.",
                )
            return
        # Handle pending multi-step flows (e.g. group selection after adding account)
        pending = self._tg_pending.pop(chat_id, None)
        if pending:
            self._handle_tg_pending(pending, text.strip(), chat_id)
            return
        # Extract first URL from message — handles Douyin share text (Chinese + URL mixed)
        import re as _re
        url = _extract_shared_url(text)
        pid = _detect_platform(url)
        if pid is None:
            if self._tg_bot:
                self._tg_bot.send_message(chat_id, "⚠ Unrecognised URL. Send an X, Douyin, Bilibili, or Xiaohongshu link.")
            return
        if pid == "xiaohongshu":
            threading.Thread(
                target=_xhs_bot_worker,
                args=(url, self._tg_bot, chat_id, not self.running),
                daemon=True,
            ).start()
            return
        if self.running:
            # Account management remains available while a sync is active.
            if pid == "x" and "/status/" not in url:
                profile = _re.search(
                    r'(?:x|twitter)\.com/([A-Za-z0-9_]+)', url
                )
                if profile and profile.group(1) not in {
                    "i", "home", "search", "explore", "notifications",
                    "messages", "settings", "intent", "compose",
                }:
                    username = profile.group(1)
                    _tg_add_account(
                        "x", username, username, self._tg_bot, chat_id
                    )
                    return
            if pid == "bilibili":
                final_url = url
                if "b23.tv/" in url.lower():
                    import urllib.request as _busy_ulr
                    try:
                        request = _busy_ulr.Request(
                            url, headers={"User-Agent": "Mozilla/5.0"}
                        )
                        final_url = _busy_ulr.urlopen(
                            request, timeout=8
                        ).geturl()
                    except Exception:
                        pass
                profile = _re.search(
                    r'space\.bilibili\.com/(\d+)', final_url
                )
                if profile:
                    uid = profile.group(1)
                    name = _fetch_bilibili_name(uid)
                    _tg_add_account(
                        "bilibili", f"{name}|{uid}", name,
                        self._tg_bot, chat_id,
                    )
                    return
            if pid == "douyin":
                douyin_cfg = PLATFORMS["douyin"]
                cookie_file = douyin_cfg.get("cookies_file", "")
                cookie_str = (
                    _parse_cookies(cookie_file)
                    if cookie_file and Path(cookie_file).exists() else ""
                )
                threading.Thread(
                    target=_f2_bot_worker,
                    args=(
                        url, cookie_str, str(self._download_root() / "URL"),
                        self._tg_bot, chat_id, False, False,
                    ),
                    daemon=True,
                ).start()
                return
            if self._tg_bot:
                self._tg_bot.send_message(chat_id, "⏳ A sync is already running — try again when it finishes.")
            return
        dl_root = self._download_root()
        pcfg    = PLATFORMS[pid]
        dl      = pcfg["downloader"]
        cookies = pcfg.get("cookies_file", "")

        # X profile URL (no /status/) → add as tracked account
        if pid == "x" and "/status/" not in url:
            m = _re.search(r'(?:x|twitter)\.com/([A-Za-z0-9_]+)', url)
            if m and m.group(1) not in {
                "i", "home", "search", "explore", "notifications",
                "messages", "settings", "intent", "compose",
            }:
                _tg_add_account("x", m.group(1), m.group(1), self._tg_bot, chat_id)
                return

        # Bilibili space URL or b23.tv short link → add as tracked account
        if pid == "bilibili":
            _bfinal = url
            if "b23.tv/" in url.lower():
                import urllib.request as _ulr
                try:
                    _req = _ulr.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    _bfinal = _ulr.urlopen(_req, timeout=8).geturl()
                except Exception:
                    pass
            m = _re.search(r'space\.bilibili\.com/(\d+)', _bfinal)
            if m:
                uid  = m.group(1)
                name = _fetch_bilibili_name(uid)
                _tg_add_account("bilibili", f"{name}|{uid}", name, self._tg_bot, chat_id)
                return

        self.running = True
        self.stop_flag.clear()
        self.status = "Downloading…"
        if dl == "f2":
            # "Downloading…" message is sent inside the worker after URL type is known
            cookie_str = _parse_cookies(cookies) if cookies and Path(cookies).exists() else ""
            url_dir = str(dl_root / "URL")
            threading.Thread(target=_f2_bot_worker,
                args=(url, cookie_str, url_dir, self._tg_bot, chat_id),
                daemon=True).start()
            return
        if self._tg_bot:
            self._tg_bot.send_message(chat_id, f"⬇ Downloading from {PLATFORMS[pid]['label']}…")
        url_dir = str(dl_root / "URL")
        cmd: list[str] = []
        if dl == "gallery-dl":
            cmd = [_GALLERY_DL, "-D", url_dir]
            if cookies and Path(cookies).exists():
                cmd += ["--cookies", cookies]
            cmd.append(url)
        elif dl == "yt-dlp":
            account_workers = int(cfg.get("per_account_workers", 4))
            cmd = _yt_dlp_command(account_workers)
            if cookies and Path(cookies).exists():
                cmd += ["--cookies", cookies]
            cmd += ["-P", url_dir, "--windows-filenames",
                    "-o", "%(upload_date>%Y-%m-%d)s_%(id)s_%(title)s.%(ext)s", url]
        if not cmd:
            self.running = False
            self.status = "Idle"
            return
        threading.Thread(target=_url_worker, args=(cmd,), daemon=True).start()

    # ── Start / Stop ───────────────────────────────────────────────────────────

    def start(self, mode: str | None, from_days: int | None,
              creator_ids: list[str] | None = None,
              entry_ids: list[str] | None = None) -> dict:
        if self.running:
            return {"error": "Already running"}
        duplicate_folder = _find_nonempty_duplicate_folder(
            self._download_root()
        )
        if duplicate_folder is not None:
            return {
                "error": (
                    "Sync blocked: duplicate quarantine contains files. "
                    f"Review and clear this folder first: {duplicate_folder}"
                )
            }
        if not self._store.all_entries():
            return {"error": "No entries — add some in Accounts first."}
        if mode is not None and mode not in {"update", "full"}:
            return {"error": "Mode must be 'update' or 'full'."}
        if creator_ids == []:
            return {"error": "Select at least one creator or account group."}
        if entry_ids == []:
            return {"error": "Select at least one account."}
        if mode:
            self._mode = mode
        if from_days is not None:
            self._from_days = max(0, from_days)
        self._creator_ids = creator_ids
        self._entry_ids = entry_ids
        self.running = True
        self.stop_flag.clear()
        self.progress_clear()
        self.status = "Running…"
        threading.Thread(target=self._run_worker, daemon=True).start()
        return {"ok": True}

    def stop(self) -> dict:
        if not self.running:
            return {"error": "Not running"}
        self.stop_flag.set()
        self.status = "Stopping…"
        _stop_process(self._proc)
        with self._procs_lock:
            for p in self._procs:
                _stop_process(p)
        return {"ok": True}

    # ── Worker ─────────────────────────────────────────────────────────────────

    def start_maintenance(
        self,
        creator_ids: list[str] | None = None,
    ) -> dict:
        if self.running:
            return {"error": "Another operation is already running"}
        if creator_ids == []:
            return {"error": "Select at least one group."}
        self._maintenance_creator_ids = creator_ids
        self.running = True
        self.stop_flag.clear()
        self.progress_clear()
        self.status = "Maintenance…"
        threading.Thread(target=self._maintenance_worker, daemon=True).start()
        return {"ok": True}

    def _maintain_entry(
        self,
        entry,
        dl_root: Path,
        douyin_slots=None,
    ) -> dict:
        """Maintain one account folder; safe to run in an account worker."""
        import asyncio as _aio

        display = (entry.handle.split("|")[0]
                   if "|" in entry.handle else entry.handle)
        progress_key = f"maintenance:{entry.platform}:{entry.handle}"
        self.progress_update(
            progress_key,
            platform=entry.platform,
            account=display,
            account_id=entry.handle.split("|")[-1],
            operation="maintenance",
            state="running",
            percent=None,
        )
        creator = (self._store.get_creator(entry.creator_id)
                   if entry.creator_id else None)
        parent_name = creator.name if creator else display
        safe_parent = re.sub(r'[\\/:*?"<>|]', "_", parent_name).strip()
        safe_account = re.sub(
            r'[\\/:*?"<>|]', "_", f"{display} [{entry.platform}]"
        ).strip()
        folder = (dl_root / safe_parent / safe_account
                  if creator else dl_root / safe_parent)
        remote_total = None
        remote_ids: set[str] = set()
        local_count = 0
        maintenance_error = False
        # Maintenance is local-only; remote verification belongs to sync.
        local_files = [
            path for path in (folder.rglob("*") if folder.exists() else [])
            if path.is_file() and path.suffix.lower() in _MEDIA_EXTS
        ]
        total_files = len(local_files)
        local_ids: set[str] = set()
        self.progress_update(
            progress_key,
            state="scanning",
            percent=0.0 if total_files else None,
            done=0,
            total=total_files,
        )
        for scanned, path in enumerate(local_files, 1):
            result = _extract_post_id_and_date(entry.platform, path)
            if result is not None:
                local_ids.add(result[0])
            self.progress_update(
                progress_key,
                state="scanning",
                percent=(scanned / total_files * 100.0),
                done=scanned,
                total=total_files,
            )
        local_count = len(local_ids)
        self.progress_update(
            progress_key,
            state="finished",
            percent=100.0 if total_files else None,
            done=total_files,
            total=total_files,
            local=local_count,
            remote=None,
        )
        return {
            "platform": entry.platform,
            "display": display,
            "total_posts": local_count,
        }
        self.log_write(f"\n→ {entry.platform}: {display}\n")

        try:
            if entry.platform == "douyin":
                sys.path.insert(0, str(_HELPERS))
                import contextlib
                import f2_user as _f2_user
                cookies = PLATFORMS["douyin"].get("cookies_file", "")
                cookie_str = (_parse_cookies(cookies)
                              if cookies and Path(cookies).exists() else "")
                url = PLATFORMS["douyin"]["url_fn"](entry.handle)
                limiter = (
                    douyin_slots
                    if douyin_slots is not None
                    else contextlib.nullcontext()
                )
                last_error = None
                with limiter:
                    for attempt in range(2):
                        if self.stop_flag.is_set():
                            break
                        self.begin_print_capture()
                        try:
                            stats = _aio.run(_f2_user.maintain_user(
                                url, cookie_str, str(folder),
                                stop_check=self.stop_flag.is_set,
                                rename_files=False,
                            ))
                            if isinstance(stats, dict):
                                value = stats.get("remote_total")
                                remote_total = (
                                    int(value) if value is not None else None
                                )
                                remote_ids.update(
                                    str(post_id)
                                    for post_id in stats.get("remote_ids", [])
                                    if post_id
                                )
                            last_error = None
                            break
                        except Exception as exc:
                            last_error = exc
                        finally:
                            self.end_print_capture()
                        if attempt == 0:
                            self.log_write(
                                "  [maintenance] Douyin remote listing timed "
                                "out; retrying once\n"
                            )
                            if self.stop_flag.wait(3):
                                break
                if last_error is not None:
                    maintenance_error = True
                    self.log_write(
                        f"  [maintenance] Douyin metadata failed after retry: "
                        f"{last_error}\n"
                    )
            elif entry.platform == "bilibili":
                uid = entry.handle.split("|")[-1]
                cookies = PLATFORMS["bilibili"].get("cookies_file", "")
                remote_total = _fetch_bilibili_remote_total(uid, cookies)
                listed_ids, enumeration_ok = _fetch_bilibili_remote_ids(
                    PLATFORMS["bilibili"]["url_fn"](entry.handle),
                    cookies,
                    self.stop_flag.is_set,
                )
                remote_ids.update(listed_ids)
                if not enumeration_ok:
                    maintenance_error = True
                    self.log_write(
                        "  [maintenance] Bilibili remote ID enumeration "
                        "did not complete\n"
                    )

            local_ids = _post_ids_in_files(
                entry.platform,
                folder.rglob("*") if folder.exists() else [],
            )
            local_count = len(local_ids)
            remote_ids_complete = bool(
                remote_total is not None and len(remote_ids) >= remote_total
            )
            local_remote_posts = (
                len(local_ids & remote_ids)
                if remote_ids or remote_ids_complete else None
            )
            verified = bool(
                remote_ids_complete
                and remote_total is not None
                and local_remote_posts is not None
                and local_remote_posts >= remote_total
            )
            return {
                "platform": entry.platform,
                "display": display,
                "total_posts": len(local_ids),
                "downloaded_posts": 0,
                "remote_total": remote_total,
                "local_remote_posts": local_remote_posts,
                "remote_ids_seen": len(remote_ids),
                "remote_ids_complete": remote_ids_complete,
                "verified": verified,
            }
        except Exception as exc:
            maintenance_error = True
            self.log_write(
                f"  [maintenance] {entry.platform}/{display} failed: {exc}\n"
            )
            return {
                "platform": entry.platform,
                "display": display,
                "total_posts": 0,
                "downloaded_posts": 0,
                "remote_total": remote_total,
                "local_remote_posts": None,
                "remote_ids_seen": len(remote_ids),
                "remote_ids_complete": False,
                "verified": False,
            }
        finally:
            self.progress_update(
                progress_key,
                state=(
                    "stopped" if self.stop_flag.is_set()
                    else "error" if maintenance_error
                    else "finished"
                ),
                local=local_count,
                remote=remote_total,
            )

    def _maintenance_worker(self) -> None:
        results: list[dict] = []
        duplicate_results: list[dict] = []
        self.log_write("Maintenance: local group duplicate extraction\n")
        self.log_write("─" * 44 + "\n")
        try:
            dl_root = self._download_root()
            entries = self._store.all_entries()
            selected_ids = self._maintenance_creator_ids
            if selected_ids is None:
                # Maintenance is group-wise; unassigned accounts are never
                # compared against one another as an implicit group.
                entries = [
                    entry for entry in entries if entry.creator_id is not None
                ]
            else:
                selected = set(selected_ids)
                entries = [
                    entry for entry in entries
                    if entry.creator_id in selected
                ]
            workers = max(
                1, int(self._load_settings().get("parallel_workers", 1))
            )
            self.log_write(f"Workers  : {workers}\n")

            from concurrent.futures import ThreadPoolExecutor
            # Douyin rate-limits concurrent account-history requests much
            # more aggressively than Bilibili. Keep two Douyin crawls active
            # while allowing the configured worker pool to maintain other
            # platforms concurrently.
            douyin_slots = threading.Semaphore(min(2, workers))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(
                        self._maintain_entry, entry, dl_root, douyin_slots
                    )
                    for entry in entries
                ]
                for future in futures:
                    if self.stop_flag.is_set():
                        break
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        self.log_write(
                            f"[error] Maintenance account worker crashed: {exc}\n"
                        )

            selected_by_creator: dict[str, list] = {}
            for entry in entries:
                if entry.creator_id:
                    selected_by_creator.setdefault(
                        entry.creator_id, []
                    ).append(entry)
            for creator_id, group_entries in selected_by_creator.items():
                creator = self._store.get_creator(creator_id)
                if creator is None:
                    continue
                safe_group = re.sub(
                    r'[\\/:*?"<>|]', "_", creator.name
                ).strip()
                group_folder = dl_root / safe_group
                account_folders = []
                for entry in group_entries:
                    display = (
                        entry.handle.split("|")[0]
                        if "|" in entry.handle else entry.handle
                    )
                    safe_account = re.sub(
                        r'[\\/:*?"<>|]',
                        "_",
                        f"{display} [{entry.platform}]",
                    ).strip()
                    account_folders.append(group_folder / safe_account)
                group_progress_key = f"maintenance-group:{creator_id}"
                group_total = sum(
                    1
                    for account_folder in account_folders
                    if account_folder.exists()
                    for path in account_folder.rglob("*")
                    if path.is_file()
                    and path.suffix.lower() in _MEDIA_EXTS
                )
                self.progress_update(
                    group_progress_key,
                    platform=(
                        group_entries[0].platform
                        if group_entries else "maintenance"
                    ),
                    account=creator.name,
                    account_id="",
                    operation="maintenance",
                    state="scanning",
                    percent=0.0 if group_total else None,
                    done=0,
                    total=group_total,
                )

                def group_progress(done: int, total: int) -> None:
                    self.progress_update(
                        group_progress_key,
                        state="scanning",
                        percent=(done / total * 100.0) if total else None,
                        done=done,
                        total=total,
                    )

                moved = _extract_group_duplicates(
                    group_folder,
                    account_folders,
                    self.log_write,
                    group_progress,
                )
                self.progress_update(
                    group_progress_key,
                    state="finished",
                    percent=100.0 if group_total else None,
                    done=group_total,
                    total=group_total,
                )
                duplicate_results.append({
                    "group": creator.name,
                    "accounts": len(group_entries),
                    "duplicates": moved,
                })
                if moved:
                    self.log_write(
                        f"  [dedupe] {creator.name}: handled "
                        f"{moved} duplicate files\n"
                    )

            if results:
                self.log_write("\nMaintenance summary\n")
                self.log_write(_maintenance_summary_table(results) + "\n")
                if duplicate_results:
                    self.log_write("\nGroup duplicate summary\n")
                    self.log_write(
                        _duplicate_summary_table(duplicate_results) + "\n"
                    )
                    self.log_write(
                        "Total duplicates detected: "
                        f"{sum(item['duplicates'] for item in duplicate_results)}\n"
                    )
        except Exception as exc:
            self.log_write(f"[error] Maintenance failed: {exc}\n")
        finally:
            self.running = False
            self.status = "Stopped" if self.stop_flag.is_set() else "Idle"
            self.log_write("\nMaintenance finished\n" + "─" * 44 + "\n")
            threading.Thread(
                target=_retroactive_index_scan_bg, daemon=True
            ).start()

    def _maintenance_worker_sequential(self) -> None:
        import asyncio as _aio
        results: list[dict] = []
        self.log_write("Maintenance: filename and duplicate validation\n")
        self.log_write("─" * 44 + "\n")
        try:
            dl_root = self._download_root()
            for entry in self._store.all_entries():
                if self.stop_flag.is_set():
                    break
                display = (entry.handle.split("|")[0]
                           if "|" in entry.handle else entry.handle)
                progress_key = f"maintenance:{entry.platform}:{entry.handle}"
                self.progress_update(
                    progress_key,
                    platform=entry.platform,
                    account=display,
                    account_id=entry.handle.split("|")[-1],
                    operation="maintenance",
                    state="running",
                    percent=None,
                )
                creator = (self._store.get_creator(entry.creator_id)
                           if entry.creator_id else None)
                parent_name = creator.name if creator else display
                safe_parent = re.sub(r'[\\/:*?"<>|]', "_", parent_name).strip()
                safe_account = re.sub(
                    r'[\\/:*?"<>|]', "_", f"{display} [{entry.platform}]"
                ).strip()
                folder = (dl_root / safe_parent / safe_account
                          if creator else dl_root / safe_parent)
                remote_total = None
                remote_ids: set[str] = set()
                maintenance_error = False
                self.log_write(f"\n→ {entry.platform}: {display}\n")
                if entry.platform == "douyin":
                    sys.path.insert(0, str(_HELPERS))
                    try:
                        import f2_user as _f2_user
                        cookies = PLATFORMS["douyin"].get("cookies_file", "")
                        cookie_str = (_parse_cookies(cookies)
                                      if cookies and Path(cookies).exists() else "")
                        url = PLATFORMS["douyin"]["url_fn"](entry.handle)
                        stats = _aio.run(_f2_user.maintain_user(
                            url, cookie_str, str(folder),
                            stop_check=self.stop_flag.is_set,
                            rename_files=False,
                        ))
                        if isinstance(stats, dict):
                            value = stats.get("remote_total")
                            remote_total = int(value) if value is not None else None
                            remote_ids.update(
                                str(post_id)
                                for post_id in stats.get("remote_ids", [])
                                if post_id
                            )
                    except Exception as exc:
                        maintenance_error = True
                        self.log_write(
                            f"  [maintenance] Douyin metadata failed: {exc}\n"
                        )
                elif entry.platform == "bilibili":
                    uid = entry.handle.split("|")[-1]
                    cookies = PLATFORMS["bilibili"].get("cookies_file", "")
                    remote_total = _fetch_bilibili_remote_total(uid, cookies)
                local_ids = _post_ids_in_files(
                    entry.platform,
                    folder.rglob("*") if folder.exists() else [],
                )
                remote_ids_complete = bool(
                    remote_total is not None
                    and len(remote_ids) >= remote_total
                )
                results.append({
                    "platform": entry.platform,
                    "display": display,
                    "total_posts": len(local_ids),
                    "downloaded_posts": 0,
                    "remote_total": remote_total,
                    "local_remote_posts": (
                        len(local_ids & remote_ids)
                        if remote_ids or remote_ids_complete else None
                    ),
                    "remote_ids_seen": len(remote_ids),
                    "remote_ids_complete": remote_ids_complete,
                })
                self.progress_update(
                    progress_key,
                    state="error" if maintenance_error else "finished",
                    local=len(local_ids),
                    remote=remote_total,
                )
            if results:
                self.log_write("\nMaintenance summary\n")
                self.log_write(_summary_table(results) + "\n")
        except Exception as exc:
            self.log_write(f"[error] Maintenance failed: {exc}\n")
        finally:
            self.running = False
            self.status = "Stopped" if self.stop_flag.is_set() else "Idle"
            self.log_write("\nMaintenance finished\n" + "─" * 44 + "\n")
            threading.Thread(target=_retroactive_index_scan_bg, daemon=True).start()

    def _run_worker(self):
        try:
            self._do_sync()
        except Exception as exc:
            self.log_write(f"[error] Worker crashed: {exc}\n")
        finally:
            self.running = False
            self.status = "Stopped" if self.stop_flag.is_set() else "Idle"
            self.log_write("\n" + "─" * 44 + "\n")
            threading.Thread(target=_retroactive_index_scan_bg, daemon=True).start()

    def _do_sync(self):
        import datetime as dt
        run_start = dt.datetime.now()

        cfg        = self._load_settings()
        full       = self._mode == "full"
        from_date  = ""
        # Full mode always covers the entire remote account. A date filter
        # would make it impossible to verify that every remote post is local.
        if self._from_days > 0 and not full:
            from_date = (dt.date.today() - dt.timedelta(days=self._from_days)).isoformat()

        sleep_user = float(cfg.get("sleep_user", 2))
        workers    = int(cfg.get("parallel_workers", 1))
        account_workers = int(cfg.get("per_account_workers", 4))
        dl_root    = self._download_root()
        repaired   = _repair_unmerged_bilibili(dl_root, self.log_write)

        self.log_write(f"Mode     : {'Full archive' if full else 'Update'}\n")
        if repaired:
            self.log_write(f"Repaired : {repaired} unmerged Bilibili files\n")
        if from_date:
            self.log_write(f"From     : {from_date}\n")
        self.log_write(f"Workers  : {workers}\n")
        self.log_write(f"Per acct : {account_workers}\n")
        self.log_write("─" * 44 + "\n")

        all_results: list[dict] = []
        was_stopped = False
        results_lock = threading.Lock()
        # Bilibili applies anti-bot limits across the whole client/IP. Running
        # several space extractors together makes HTTP 412 substantially more
        # likely, so serialize account metadata extraction.
        bilibili_slot = threading.Semaphore(1)
        # Douyin's browser adapter serializes only Edge enumeration internally.
        # Account jobs remain concurrent so one account can download media
        # while Edge enumerates the next account.
        douyin_slots = None
        # Signed profile requests are sensitive to concurrent enumeration.
        # The adapter still downloads a note's media concurrently.
        xiaohongshu_slot = threading.Semaphore(1)

        def _sync_account(pid: str, pcfg: dict, entry) -> None:
            if self.stop_flag.is_set():
                return
            creator = self._store.get_creator(entry.creator_id) if entry.creator_id else None
            display = entry.handle.split("|")[0] if "|" in entry.handle else entry.handle
            creator_name = creator.name if creator else display
            slot_acquired = False
            platform_slot = (
                bilibili_slot if pid == "bilibili"
                else douyin_slots if pid == "douyin"
                else xiaohongshu_slot if pid == "xiaohongshu"
                else None
            )
            if platform_slot is not None:
                while not self.stop_flag.is_set():
                    if platform_slot.acquire(timeout=0.25):
                        slot_acquired = True
                        break
                if not slot_acquired:
                    return
            try:
                result = self._run_handle(
                    pid, pcfg, entry.handle, full, from_date,
                    dl_root, cfg, creator_name, creator is not None,
                )
            finally:
                if slot_acquired and platform_slot is not None:
                    platform_slot.release()
            if (full and result and not result.get("rate_limited")
                    and not self.stop_flag.is_set()):
                remote = result.get("remote_total")
                local = result.get("total_posts", 0)
                overlap = result.get("local_remote_posts") or 0
                if (remote is None or overlap < remote
                        or not result.get("remote_ids_complete", False)):
                    self.log_write(
                        f"  [verify] Coverage is not complete "
                        f"(Local {local}, Remote "
                        f"{remote if remote is not None else '?'}, "
                        f"IDs listed {result.get('remote_ids_seen', 0)}); "
                        "retrying once\n"
                    )
                    if not self.stop_flag.wait(max(1.0, sleep_user)):
                        retry_slot_acquired = False
                        if platform_slot is not None:
                            while not self.stop_flag.is_set():
                                if platform_slot.acquire(timeout=0.25):
                                    retry_slot_acquired = True
                                    break
                        try:
                            retry = (
                                self._run_handle(
                                    pid, pcfg, entry.handle, full, from_date,
                                    dl_root, cfg, creator_name,
                                    creator is not None,
                                )
                                if (
                                    platform_slot is None
                                    or retry_slot_acquired
                                )
                                and not self.stop_flag.is_set()
                                else None
                            )
                        finally:
                            if retry_slot_acquired and platform_slot is not None:
                                platform_slot.release()
                        if retry:
                            retry["downloaded_posts"] = (
                                result.get("downloaded_posts", 0)
                                + retry.get("downloaded_posts", 0)
                            )
                            retry["count"] = (
                                result.get("count", 0)
                                + retry.get("count", 0)
                            )
                            result = retry
            if result:
                remote = result.get("remote_total")
                result["verified"] = bool(
                    result.get("remote_ids_complete", False)
                    and remote is not None
                    and (result.get("local_remote_posts") or 0) >= remote
                )
            if result:
                with results_lock:
                    all_results.append(result)

        account_jobs: list[tuple[str, dict, object]] = []
        for pid, pcfg in PLATFORMS.items():
            entries = self._store.get_entries_for_download(
                pid, self._creator_ids, self._entry_ids
            )
            if entries:
                self.log_write(
                    f"\nPlatform : {pcfg['label']} ({len(entries)} accounts)\n"
                )
                account_jobs.extend((pid, pcfg, entry) for entry in entries)

        # `parallel_workers` is an account pool, not a platform pool. This
        # allows multiple accounts from the same platform to run together.
        from concurrent.futures import ThreadPoolExecutor
        has_douyin = any(pid == "douyin" for pid, _, _ in account_jobs)
        douyin_browser = None
        if has_douyin:
            from helpers import douyin_browser
            douyin_browser.begin_browser_session()
        try:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                futures = [
                    pool.submit(_sync_account, pid, pcfg, entry)
                    for pid, pcfg, entry in account_jobs
                ]
                for future in futures:
                    try:
                        future.result()
                    except Exception as exc:
                        self.log_write(f"[error] Account worker crashed: {exc}\n")
        finally:
            if douyin_browser is not None:
                douyin_browser.end_browser_session()

        if self.stop_flag.is_set():
            was_stopped = True

        if all_results:
            self.log_write("\nSync summary\n")
            self.log_write(_summary_table(all_results, full=full) + "\n")

        self._write_history(run_start, all_results, was_stopped)

    def _write_history(self, run_start, results: list, stopped: bool) -> None:
        import datetime as dt
        secs    = int((dt.datetime.now() - run_start).total_seconds())
        mins, s = divmod(secs, 60)
        entry = {
            "run_key":  run_start.strftime("%Y%m%d_%H%M%S"),
            "date":     run_start.strftime("%Y-%m-%d"),
            "time":     run_start.strftime("%H:%M:%S"),
            "duration": f"{mins}m {s}s" if mins else f"{s}s",
            "mode":     "Full" if self._mode == "full" else "Update",
            "stopped":  stopped,
            "users":    results,
        }
        hist: list = []
        try:
            hist = json.loads(Path(UPDATE_HISTORY_FILE).read_text("utf-8"))
            if not isinstance(hist, list):
                hist = []
        except Exception:
            pass
        hist.append(entry)
        try:
            Path(UPDATE_HISTORY_FILE).write_text(
                json.dumps(hist[-200:], indent=2, ensure_ascii=False), "utf-8")
        except Exception:
            pass
        self._total_downloads = self._compute_total_downloads()

    def _run_handle(self, pid, pcfg, handle, full, from_date, dl_root, cfg,
                    creator_name: str, grouped: bool = False) -> "dict | None":
        import subprocess
        import datetime as _dt

        url     = pcfg["url_fn"](handle)
        dl      = pcfg["downloader"]
        cookies = pcfg.get("cookies_file", "")
        display = handle.split("|")[0] if "|" in handle else handle
        progress_key = f"sync:{pid}:{handle}"
        self.progress_update(
            progress_key,
            platform=pid,
            account=display,
            account_id=handle.split("|")[-1],
            operation="sync",
            state="running",
            percent=None,
        )

        account_log = lambda text: self.log_write(text, account_key=progress_key)
        account_log(f"→ {handle}\n")

        safe_creator = re.sub(r'[\\/:*?"<>|]', "_", creator_name).strip()
        safe_account = re.sub(r'[\\/:*?"<>|]', "_", f"{display} [{pid}]").strip()
        watch_dir = (dl_root / safe_creator / safe_account
                     if grouped else dl_root / safe_creator)
        watch_dir.mkdir(parents=True, exist_ok=True)
        before = set(watch_dir.rglob("*")) if watch_dir.exists() else set()

        sleep_req = float(cfg.get("sleep_req", 1))
        account_workers = int(cfg.get("per_account_workers", 4))
        effective_account_workers = (
            min(3, max(1, account_workers))
            if pid == "douyin" else account_workers
        )
        if pid == "douyin" and effective_account_workers != account_workers:
            account_log(
                f"  [throttle] Douyin tasks limited to "
                f"{effective_account_workers} for request stability\n"
            )
        remote_total: int | None = None
        remote_seen_ids: set[str] = set()
        remote_names: dict[str, str] = {}
        enumeration_ok = False
        account_error: str | None = None
        rate_limited = False
        update_boundary_reached = False

        if pid == "bilibili":
            with self._bilibili_cooldown_lock:
                cooldown_left = max(
                    0, int(self._bilibili_cooldown_until - time.time())
                )
            if cooldown_left:
                minutes, seconds = divmod(cooldown_left, 60)
                account_error = (
                    "Bilibili is cooling down after HTTP 412 "
                    f"({minutes}m {seconds}s remaining)."
                )
                account_log(f"[warning] {account_error}\n")
                total_post_ids = _post_ids_in_files(
                    pid, watch_dir.rglob("*") if watch_dir.exists() else []
                )
                self.progress_update(
                    progress_key,
                    state="error",
                    percent=None,
                    local=len(total_post_ids),
                    remote=None,
                    downloaded=0,
                    error=account_error,
                )
                return {
                    "platform": pid,
                    "handle": handle,
                    "display": display,
                    "count": 0,
                    "downloaded_posts": 0,
                    "total_posts": len(total_post_ids),
                    "remote_total": None,
                    "local_remote_posts": None,
                    "remote_ids_seen": 0,
                    "remote_ids_complete": False,
                    "enumeration_ok": False,
                    "rate_limited": True,
                    "corrupt": 0,
                    "files": [],
                    "folder": str(watch_dir),
                }

        # ── f2 (Douyin) — Python library, not CLI ─────────────────────────────
        if dl == "f2":
            import asyncio as _aio
            sys.path.insert(0, str(_HELPERS))
            try:
                import f2_user as _f2_user
            except Exception as _ie:
                import traceback as _tb
                msg = f"[error] f2_user import failed: {_ie}\n{_tb.format_exc()}"
                account_log(msg)
                _file_log(msg)
                self.progress_update(
                    progress_key,
                    state="error",
                    error=f"F2 could not load: {_ie}",
                )
                return None

            today      = _dt.date.today().isoformat()
            interval   = f"{from_date}|{today}" if from_date else "all"
            cookie_str = _parse_cookies(cookies) if cookies and Path(cookies).exists() else ""
            safe_h     = re.sub(r'[\\/:*?"<>|]', "_", handle).strip()
            arc_f2     = str(Path(ARCHIVES_DIR) / f"douyin_{safe_h}.txt")

            self.begin_print_capture(account_log)
            def _f2_progress(done, total, phase):
                effective_total = (
                    max(int(total), int(done))
                    if total is not None else None
                )
                percent = (
                    min(
                        100.0,
                        (float(done) / float(effective_total)) * 100.0,
                    )
                    if effective_total else None
                )
                self.progress_update(
                    progress_key,
                    state=phase,
                    percent=percent,
                    done=int(done),
                    total=effective_total,
                )
            try:
                f2_stats = _aio.run(_f2_user.download_user(
                    url, cookie_str, str(watch_dir), interval,
                    naming=_DOUYIN_NAMING,
                    stop_check=self.stop_flag.is_set,
                    full=full,
                    archive_file=arc_f2,
                    sleep_req=sleep_req,
                    max_tasks=effective_account_workers,
                    progress_callback=_f2_progress,
                ))
                if isinstance(f2_stats, dict):
                    value = f2_stats.get("remote_total")
                    remote_total = int(value) if value is not None else None
                    remote_seen_ids.update(
                        str(post_id)
                        for post_id in f2_stats.get("remote_ids", [])
                        if post_id
                    )
                    remote_names.update(
                        (str(post_id), str(name))
                        for post_id, name in f2_stats.get("remote_names", {}).items()
                        if post_id and name
                    )
                    listing_complete = bool(
                        f2_stats.get("remote_ids_complete", False)
                    )
                    enumeration_ok = not full or listing_complete
                    if full and not listing_complete:
                        account_error = (
                            "Douyin Full sync received only a partial remote "
                            f"list ({len(remote_seen_ids)} post IDs). Partial "
                            "downloads were kept; retry this account to "
                            "continue, but archive completeness is not yet "
                            "verified."
                        )
                        account_log(f"[error] {account_error}\n")
            except Exception as exc:
                account_error = f"Douyin sync failed: {exc}"
                import traceback as _tb
                msg = f"[error] f2 download failed: {exc}\n{_tb.format_exc()}"
                account_log(msg)
                _file_log(msg)
            finally:
                self.end_print_capture()

        elif dl == "xiaohongshu":
            sys.path.insert(0, str(_HELPERS))
            import xiaohongshu_user as _xhs_user

            self.begin_print_capture(account_log)
            def _xhs_progress(done, total, phase):
                self.progress_update(
                    progress_key,
                    state=phase,
                    percent=(
                        min(100.0, float(done) / float(total) * 100.0)
                        if total else None
                    ),
                    done=int(done),
                    total=int(total) if total is not None else None,
                )
            try:
                xhs_stats = _xhs_user.download_user(
                    handle.split("|")[-1], cookies, str(watch_dir),
                    full=full,
                    stop_check=self.stop_flag.is_set,
                    max_tasks=effective_account_workers,
                    progress_callback=_xhs_progress,
                )
                remote_total = int(xhs_stats.get("remote_total", 0))
                remote_seen_ids.update(xhs_stats.get("remote_ids", []))
                remote_names.update(
                    (str(post_id), str(name))
                    for post_id, name in xhs_stats.get("remote_names", {}).items()
                    if post_id and name
                )
                enumeration_ok = True
            except Exception as exc:
                account_error = f"Xiaohongshu sync failed: {exc}"
                import traceback as _tb
                msg = f"[error] Xiaohongshu sync failed: {exc}\n{_tb.format_exc()}"
                account_log(msg)
                _file_log(msg)
            finally:
                self.end_print_capture()

        else:
            # ── Subprocess downloaders ─────────────────────────────────────────
            safe_handle_base = re.sub(r'[\\/:*?"<>|]', "_", handle).strip()
            arc_dir = Path(ARCHIVES_DIR)
            arc     = str(arc_dir / f"{pid}_{safe_handle_base}.db")
            cmd: list[str] = []

            if dl == "gallery-dl":
                seeded = _seed_gallery_archive_from_disk(watch_dir, Path(arc))
                account_log(
                    f"  [disk] Found {seeded} existing X media files locally\n"
                )
                cmd = [_GALLERY_DL, "-D", str(watch_dir)]
                cmd += ["--download-archive", arc]
                if from_date:
                    cmd += ["-o", f"extractor.date-min={from_date}T00:00:00"]
                if cookies and Path(cookies).exists():
                    cmd += ["--cookies", cookies]
                cmd += ["-o", f"filename={_X_FILENAME}"]
                cmd.append(url)

            elif dl == "yt-dlp":
                arc_path = arc_dir / f"{pid}_{safe_handle_base}.txt"
                arc_txt = str(arc_path)
                uid_match = re.search(r"(\d+)(?:/video)?$", url)
                if uid_match:
                    remote_total = _fetch_bilibili_remote_total(
                        uid_match.group(1), cookies
                    )
                # Metadata requests are serialized by bilibili_slot above.
                # Keep media concurrency modest and add jitter between videos.
                bilibili_fragments = min(2, max(1, account_workers))
                bilibili_sleep = max(2.0, sleep_req)
                cmd = _yt_dlp_command(bilibili_fragments) + [
                    "--sleep-requests", str(bilibili_sleep),
                    "--min-sleep-interval", "2",
                    "--max-sleep-interval", "5",
                    "--extractor-retries", "3",
                    "--retry-sleep", "extractor:10",
                ]
                seeded = _seed_bilibili_archive_from_disk(watch_dir, arc_path)
                account_log(
                    f"  [disk] Found {seeded} existing Bilibili posts locally\n"
                )
                if full:
                    # Keep scanning the complete remote playlist. The rebuilt
                    # archive skips existing IDs before per-video extraction,
                    # while missing IDs are downloaded and appended normally.
                    cmd += ["--download-archive", arc_txt]
                else:
                    # Update is a recent-post sync. The playlist is newest
                    # first, so download new IDs and stop at the first post
                    # already represented by a local file. Full mode is
                    # responsible for finding older gaps.
                    cmd += [
                        "--download-archive", arc_txt,
                        "--break-on-existing",
                    ]
                if from_date:
                    cmd += ["--dateafter", from_date.replace("-", "")]
                if cookies and Path(cookies).exists():
                    cmd += ["--cookies", cookies]
                cmd += ["-P", str(watch_dir), "--windows-filenames",
                        "-o", _BILIBILI_FILENAME,
                        url]

            if cmd:
                _file_log(f"[subprocess] cmd={cmd}\n")
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        creationflags=_NO_WINDOW if sys.platform == "win32" else 0,
                    )
                    with self._procs_lock:
                        self._procs.append(proc)
                    self._proc = proc
                    for raw in proc.stdout:
                        line = raw.decode("utf-8", errors="replace")
                        account_log(line)
                        if (
                            dl == "yt-dlp"
                            and not full
                            and (
                                "already in the archive, stopping due to "
                                "--break-on-existing"
                            ) in line
                        ):
                            update_boundary_reached = True
                        if dl == "yt-dlp" and re.search(
                            r"(?:HTTP Error|server\s*\()\s*412\b|"
                            r"blocked by server\s*\(412\)",
                            line,
                            re.IGNORECASE,
                        ):
                            rate_limited = True
                            cooldown_seconds = 15 * 60
                            with self._bilibili_cooldown_lock:
                                self._bilibili_cooldown_until = max(
                                    self._bilibili_cooldown_until,
                                    time.time() + cooldown_seconds,
                                )
                            account_error = (
                                "Bilibili rejected requests with HTTP 412. "
                                "Bilibili sync is paused for 15 minutes; "
                                "refresh cookies before retrying."
                            )
                            account_log(f"[warning] {account_error}\n")
                            _stop_process(proc)
                            break
                        percent_match = re.search(
                            r"\b(\d{1,3}(?:\.\d+)?)%", line
                        )
                        if percent_match:
                            self.progress_update(
                                progress_key,
                                state="downloading",
                                percent=min(100.0, float(percent_match.group(1))),
                            )
                        if dl == "gallery-dl":
                            previous_seen = len(remote_seen_ids)
                            remote_seen_ids.update(
                                match.group()
                                for match in re.finditer(r"\d{15,20}", line)
                            )
                            if len(remote_seen_ids) > previous_seen:
                                self.progress_update(
                                    progress_key,
                                    state="downloading",
                                    done=len(remote_seen_ids),
                                )
                        if dl == "yt-dlp":
                            previous_seen = len(remote_seen_ids)
                            remote_seen_ids.update(
                                match.group(1)
                                for match in _BVID_RE.finditer(line)
                            )
                            total_match = re.search(
                                r"Downloading\s+\d+\s+items?\s+of\s+(\d+)",
                                line,
                            )
                            if total_match:
                                remote_total = int(total_match.group(1))
                            if len(remote_seen_ids) > previous_seen:
                                percent = (
                                    min(
                                        100.0,
                                        len(remote_seen_ids) / remote_total * 100.0,
                                    )
                                    if remote_total else None
                                )
                                self.progress_update(
                                    progress_key,
                                    state=(
                                        "scanning"
                                        if "recorded in the archive" in line
                                        else "downloading"
                                    ),
                                    percent=percent,
                                    done=len(remote_seen_ids),
                                    total=remote_total,
                                )
                        if self.stop_flag.is_set():
                            _stop_process(proc)
                            break
                    return_code = proc.wait()
                    if update_boundary_reached:
                        account_log(
                            "→ Update complete: reached the first post "
                            "already stored locally\n"
                        )
                    enumeration_ok = (
                        (return_code == 0 or update_boundary_reached)
                        and not rate_limited
                        and not self.stop_flag.is_set()
                    )
                    if (not enumeration_ok and not rate_limited
                            and not self.stop_flag.is_set()):
                        account_error = (
                            f"{dl} exited with code {return_code}. "
                            "Open the full log for details."
                        )
                    with self._procs_lock:
                        if proc in self._procs:
                            self._procs.remove(proc)
                    if dl == "gallery-dl" and remote_seen_ids:
                        remote_total = len(remote_seen_ids)
                except FileNotFoundError as exc:
                    account_error = f"{dl} executable was not found: {exc}"
                    account_log(f"[error] {dl} not found — check PATH or bundled exe: {exc}\n")
                except Exception as exc:
                    account_error = f"{dl} failed: {exc}"
                    account_log(f"[error] {dl} subprocess failed: {exc}\n")

        after     = set(watch_dir.rglob("*")) if watch_dir.exists() else set()
        new_files = [f for f in (after - before)
                     if f.is_file() and f.suffix.lower() in _MEDIA_EXTS]

        # Check only newly downloaded files for corruption
        corrupt_files: list[Path] = [f for f in new_files if not _is_valid_media(f)]
        if corrupt_files:
            bad_ids: set[str] = set()
            for f in corrupt_files:
                account_log(f"  [corrupt] {f.name} — removed for re-download\n")
                mo = _DOUYIN_ID_RE.search(f.stem)
                if mo:
                    bad_ids.add(mo.group(1))
                try:
                    f.unlink()
                except OSError:
                    pass
            # Purge from Douyin archive so next Update sync picks them up
            if bad_ids and dl == "f2":
                safe_h = re.sub(r'[\\/:*?"<>|]', "_", handle).strip()
                arc_f2 = Path(ARCHIVES_DIR) / f"douyin_{safe_h}.txt"
                if arc_f2.exists():
                    try:
                        lines = arc_f2.read_text("utf-8").splitlines()
                        kept  = [l for l in lines if l.strip() not in bad_ids]
                        arc_f2.write_text("\n".join(kept) + ("\n" if kept else ""), "utf-8")
                    except Exception:
                        pass

            # Do not index or count files that were just deleted as corrupt.
            corrupt_set = set(corrupt_files)
            new_files = [
                f for f in new_files
                if f not in corrupt_set and f.exists()
            ]

        downloaded_post_ids = _post_ids_in_files(
            pid, [f for f in new_files if f.exists()]
        )
        total_post_ids = _post_ids_in_files(
            pid, watch_dir.rglob("*") if watch_dir.exists() else []
        )
        remote_ids_complete = bool(
            enumeration_ok
            and remote_total is not None
            and len(remote_seen_ids) >= remote_total
        )
        account_id = handle.split("|")[-1] if "|" in handle else handle
        # The account detail page is a local-disk view. Reconcile it after
        # every sync so removed files disappear and newly discovered files
        # begin as Unchecked. A complete Full enumeration can verify all local
        # post IDs without issuing another request per post.
        _reconcile_local_post_index(
            pid,
            account_id,
            watch_dir,
            remote_seen_ids if full and remote_ids_complete else None,
            reset_unverified=full,
            remote_names=remote_names,
        )
        if full:
            if remote_ids_complete:
                account_log(
                    f"  [verify] Compared {len(total_post_ids)} local posts "
                    f"with {len(remote_seen_ids)} currently available "
                    "remote posts\n"
                )
            else:
                account_log(
                    "  [verify] Remote post list was incomplete; local "
                    "files remain Unchecked\n"
                )
        local_remote_posts = (
            len(total_post_ids & remote_seen_ids)
            if remote_seen_ids or remote_ids_complete else None
        )
        self.progress_update(
            progress_key,
            state=(
                "stopped" if self.stop_flag.is_set()
                else "finished" if enumeration_ok
                else "error"
            ),
            percent=None,
            local=len(total_post_ids),
            remote=remote_total,
            downloaded=len(downloaded_post_ids),
            error=(
                account_error
                if not enumeration_ok and not self.stop_flag.is_set()
                else None
            ),
        )

        return {
            "platform": pid,
            "handle":   handle,
            "display":  display,
            "count":    len(new_files),
            "downloaded_posts": len(downloaded_post_ids),
            "total_posts": len(total_post_ids),
            "remote_total": remote_total,
            "local_remote_posts": local_remote_posts,
            "remote_ids_seen": len(remote_seen_ids),
            "remote_ids_complete": remote_ids_complete,
            "enumeration_ok": enumeration_ok,
            "rate_limited": rate_limited,
            "corrupt":  len(corrupt_files),
            "files":    [f.name for f in new_files],
            "folder":   str(watch_dir),
        }

    def _load_settings(self) -> dict:
        try:
            return json.loads(Path(SETTINGS_FILE).read_text("utf-8"))
        except Exception:
            return {}

    def _download_root(self) -> Path:
        try:
            p = Path(DOWNLOAD_PATH_FILE).read_text("utf-8").strip()
            if p:
                path = Path(p).expanduser()
                return path if path.is_absolute() else (_ROOT / path).resolve()
        except Exception:
            pass
        return _ROOT / "downloads"


state = AppState()


# ── FastAPI ───────────────────────────────────────────────────────────────────

def _asyncio_exception_handler(loop, context):
    # Suppress Windows pipe-close noise (ConnectionResetError from _ProactorBasePipeTransport)
    exc = context.get("exception")
    if isinstance(exc, ConnectionResetError):
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_asyncio_exception_handler)
    state._loop = loop
    # Mounted applications share Archiver's lifespan.
    from viewer.app import initialize as initialize_viewer
    initialize_viewer()
    yield


app = FastAPI(title="Archiver API", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DuplicateEntryError)
async def duplicate_entry_handler(_, exc: DuplicateEntryError):
    return PlainTextResponse(str(exc), status_code=409)


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/api/status")
def get_status():
    return state.get_status()


# ── Bot control ───────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    mode:        Optional[str]       = None
    from_days:   Optional[int]       = None
    creator_ids: Optional[list[str]] = None
    entry_ids:   Optional[list[str]] = None


@app.post("/api/start")
def start(req: StartRequest = StartRequest()):
    result = state.start(
        req.mode, req.from_days, req.creator_ids, req.entry_ids
    )
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/stop")
def stop():
    result = state.stop()
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


class MaintenanceRequest(BaseModel):
    creator_ids: Optional[list[str]] = None


@app.post("/api/maintenance")
def start_maintenance(req: MaintenanceRequest = MaintenanceRequest()):
    result = state.start_maintenance(req.creator_ids)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# ── Accounts ─────────────────────────────────────────────────────────────────

@app.get("/api/accounts")
def get_accounts():
    return {
        "creators": [{"id": c.id, "name": c.name}
                     for c in state._store.all_creators()],
        "entries":  [{"id": e.id, "platform": e.platform,
                      "handle": e.handle, "creator_id": e.creator_id}
                     for e in state._store.all_entries()],
    }


class AddEntryRequest(BaseModel):
    platform:   str
    handle:     str
    creator_id: Optional[str] = None


@app.post("/api/accounts/entries", status_code=201)
def add_entry(req: AddEntryRequest):
    e = state._store.add_entry(req.platform, req.handle, req.creator_id)
    _ensure_entry_download_folder(e, state._store, state._download_root())
    return {"id": e.id, "platform": e.platform,
            "handle": e.handle, "creator_id": e.creator_id}


class AddLinkRequest(BaseModel):
    url: str

@app.post("/api/accounts/add_link", status_code=201)
def add_link(req: AddLinkRequest):
    """Auto-detect platform from a profile URL and add as a tracked account."""
    url = req.url.strip()
    pid = _detect_platform(url)
    if pid is None:
        raise HTTPException(400, "Unrecognised link — paste a profile URL for X, Douyin, Bilibili, or Xiaohongshu.")

    import re as _re2

    if pid == "x":
        if "/status/" in url:
            raise HTTPException(400, "That looks like a post URL — paste the profile page instead.")
        m = _re2.search(r'(?:x|twitter)\.com/([A-Za-z0-9_]+)', url)
        if not m or m.group(1) in {
            "i", "home", "search", "explore", "notifications",
            "messages", "settings", "intent", "compose",
        }:
            raise HTTPException(400, "Could not parse an X profile URL.")
        username = m.group(1)
        e = state._store.add_entry("x", username, None)
        _ensure_entry_download_folder(e, state._store, state._download_root())
        return {"id": e.id, "platform": "x", "handle": username, "display": username}

    if pid == "bilibili":
        final_url = url
        if "b23.tv/" in url.lower():
            import urllib.request as _ulr
            try:
                _req2 = _ulr.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                final_url = _ulr.urlopen(_req2, timeout=8).geturl()
            except Exception:
                pass
        m = _re2.search(r'space\.bilibili\.com/(\d+)', final_url)
        if not m:
            raise HTTPException(400, "Paste a Bilibili user space URL (space.bilibili.com/UID or a b23.tv link that leads to one).")
        uid    = m.group(1)
        name   = _fetch_bilibili_name(uid)
        handle = f"{name}|{uid}"
        e = state._store.add_entry("bilibili", handle, None)
        _ensure_entry_download_folder(e, state._store, state._download_root())
        return {"id": e.id, "platform": "bilibili", "handle": handle, "display": name}

    if pid == "douyin":
        import asyncio as _aio
        sys.path.insert(0, str(_HELPERS))
        m = _re2.search(r'douyin\.com/user/([^/?&#\s]+)', url)
        if m:
            sec_uid = m.group(1)
        else:
            try:
                from f2.apps.douyin.utils import SecUserIdFetcher
                sec_uid = _aio.run(SecUserIdFetcher.get_sec_user_id(url))
            except Exception:
                raise HTTPException(400, "Could not resolve a Douyin profile URL.")
        nickname = sec_uid
        try:
            from f2.apps.douyin.handler import DouyinHandler
            from f2.apps.douyin.utils import ClientConfManager
            cookies   = PLATFORMS["douyin"].get("cookies_file", "")
            cookie_str = _parse_cookies(cookies) if cookies and Path(cookies).exists() else ""
            kw = {
                "cookie": cookie_str, "languages": "zh_CN",
                "timeout": 10, "max_retries": 1, "max_connections": 2, "max_tasks": 2,
                "page_counts": 20, "max_counts": None, "headers": ClientConfManager.headers(),
            }
            async def _profile():
                p = await DouyinHandler(kw).fetch_user_profile(sec_uid)
                return (
                    getattr(p, "nickname", None) or sec_uid,
                    getattr(p, "avatar_url", None),
                )
            nickname, avatar_url = _aio.run(_profile())
            if avatar_url:
                _cache_avatar_url("douyin", sec_uid, avatar_url)
        except Exception:
            pass
        handle = f"{nickname}|{sec_uid}"
        e = state._store.add_entry("douyin", handle, None)
        _ensure_entry_download_folder(e, state._store, state._download_root())
        return {"id": e.id, "platform": "douyin", "handle": handle, "display": nickname}

    if pid == "xiaohongshu":
        sys.path.insert(0, str(_HELPERS))
        import xiaohongshu_user as _xhs_user
        final_url = _resolve_xiaohongshu_url(url)
        user_id = _xhs_user.resolve_profile_id(final_url)
        if not user_id:
            raise HTTPException(
                400, "Paste a Xiaohongshu profile URL (/user/profile/...), not a note URL."
            )
        cookie_file = PLATFORMS["xiaohongshu"]["cookies_file"]
        try:
            nickname, avatar_url = _xhs_user.account_info(user_id, cookie_file)
        except Exception as exc:
            raise HTTPException(400, f"Could not read Xiaohongshu account: {exc}") from exc
        if avatar_url:
            _cache_avatar_url("xiaohongshu", user_id, avatar_url)
        handle = f"{nickname}|{user_id}"
        e = state._store.add_entry("xiaohongshu", handle, None)
        _ensure_entry_download_folder(e, state._store, state._download_root())
        return {
            "id": e.id, "platform": "xiaohongshu",
            "handle": handle, "display": nickname,
        }

    raise HTTPException(400, "Unsupported platform")


@app.delete("/api/accounts/entries/{entry_id}", status_code=204)
def remove_entry(entry_id: str):
    entry = state._store.get_entry(entry_id)
    if entry is None:
        raise HTTPException(404, "Account not found")
    archive = _entry_archive_path(entry)
    try:
        if archive.is_file():
            archive.unlink()
    except OSError as exc:
        raise HTTPException(500, f"Could not remove account archive: {exc}") from exc
    state._store.remove_entry(entry_id)


class AssignRequest(BaseModel):
    creator_id: Optional[str] = None


@app.patch("/api/accounts/entries/{entry_id}")
def assign_entry(entry_id: str, req: AssignRequest):
    state._store.assign_entry(entry_id, req.creator_id)
    entry = state._store.get_entry(entry_id)
    if entry is not None:
        _ensure_entry_download_folder(
            entry, state._store, state._download_root()
        )
    return {"ok": True}


@app.delete("/api/accounts/entries/{entry_id}/archive")
def clear_entry_archive(entry_id: str):
    if state.running:
        raise HTTPException(409, "Stop the current download before clearing archive records.")
    entry = state._store.get_entry(entry_id)
    if entry is None:
        raise HTTPException(404, "Account not found")
    archive = _entry_archive_path(entry)
    try:
        existed = archive.is_file()
        if existed:
            archive.unlink()
    except OSError as exc:
        raise HTTPException(500, f"Could not clear archive records: {exc}") from exc
    state.log_write(
        f"[archive] Cleared {entry.platform} records for {entry.handle}"
        + ("" if existed else " (no archive existed)") + "\n"
    )
    return {"ok": True, "cleared": existed}


def _entry_archive_path(entry) -> Path:
    safe_handle = re.sub(r'[\\/:*?"<>|]', "_", entry.handle).strip()
    suffix = ".txt" if entry.platform in {"douyin", "bilibili", "xiaohongshu"} else ".db"
    return Path(ARCHIVES_DIR) / f"{entry.platform}_{safe_handle}{suffix}"


class AddCreatorRequest(BaseModel):
    name: str


@app.post("/api/accounts/creators", status_code=201)
def add_creator(req: AddCreatorRequest):
    c = state._store.add_creator(req.name)
    return {"id": c.id, "name": c.name}


@app.delete("/api/accounts/creators/{creator_id}", status_code=204)
def remove_creator(creator_id: str):
    state._store.remove_creator(creator_id)


class RenameCreatorRequest(BaseModel):
    name: str


def _group_folder_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def _rewrite_group_path_references(old_dir: Path, new_dir: Path) -> None:
    """Update persisted file/folder paths after a group directory move."""
    old_text = str(old_dir.resolve())
    new_text = str(new_dir.resolve())

    def rewrite(value, key: str | None = None):
        if isinstance(value, dict):
            return {k: rewrite(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(item, key) for item in value]
        if isinstance(value, str) and key in {"file", "folder"}:
            old_fold = old_text.casefold()
            value_fold = value.casefold()
            if value_fold == old_fold or value_fold.startswith(old_fold + os.sep):
                return new_text + value[len(old_text):]
        return value

    for path in (Path(POST_INDEX_FILE), Path(UPDATE_HISTORY_FILE)):
        if not path.is_file():
            continue
        try:
            original = json.loads(path.read_text("utf-8"))
            updated = rewrite(original)
            if updated != original:
                path.write_text(
                    json.dumps(updated, indent=2, ensure_ascii=False), "utf-8"
                )
        except Exception as exc:
            state.log_write(
                f"[warning] Could not update paths in {path.name}: {exc}\n"
            )


@app.patch("/api/accounts/creators/{creator_id}")
def rename_creator(creator_id: str, req: RenameCreatorRequest):
    if state.running:
        raise HTTPException(
            409, "Stop the current sync or maintenance before renaming a group."
        )
    creator = state._store.get_creator(creator_id)
    if creator is None:
        raise HTTPException(404, "Group not found")
    new_name = req.name.strip()
    if not new_name:
        raise HTTPException(400, "Group name cannot be empty")
    old_name = creator.name
    if new_name == old_name:
        return {"ok": True}

    old_leaf = _group_folder_name(old_name)
    new_leaf = _group_folder_name(new_name)
    if not new_leaf:
        raise HTTPException(400, "Group name does not contain a valid folder name")
    root = state._download_root()
    old_dir = root / old_leaf
    new_dir = root / new_leaf
    moved = False
    if old_leaf != new_leaf and old_dir.exists():
        case_only = old_leaf.casefold() == new_leaf.casefold()
        if new_dir.exists() and not case_only:
            raise HTTPException(
                409,
                f'A download folder named "{new_leaf}" already exists. '
                "Choose another group name or move that folder first.",
            )
        try:
            if case_only:
                temporary = old_dir.with_name(
                    f".archiver-group-rename-{creator_id}"
                )
                if temporary.exists():
                    raise OSError(f"temporary path already exists: {temporary}")
                old_dir.rename(temporary)
                try:
                    temporary.rename(new_dir)
                except OSError:
                    temporary.rename(old_dir)
                    raise
            else:
                old_dir.rename(new_dir)
            moved = True
        except OSError as exc:
            raise HTTPException(
                500, f"Could not rename the group download folder: {exc}"
            ) from exc
    try:
        state._store.rename_creator(creator_id, new_name)
    except Exception as exc:
        if moved:
            try:
                new_dir.rename(old_dir)
            except OSError:
                pass
        raise HTTPException(500, f"Could not save the new group name: {exc}") from exc
    if moved:
        _rewrite_group_path_references(old_dir, new_dir)
        state.log_write(f'[account] Renamed group folder "{old_leaf}" -> "{new_leaf}"\n')
    return {"ok": True}


# ── Settings ─────────────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings():
    s: dict = {}
    try:
        s = json.loads(Path(SETTINGS_FILE).read_text("utf-8"))
    except Exception:
        pass
    try:
        s["download_path"] = Path(DOWNLOAD_PATH_FILE).read_text("utf-8").strip()
    except Exception:
        s["download_path"] = str(_ROOT / "downloads")
    return s


class SaveSettingsRequest(BaseModel):
    download_path:        Optional[str]   = None
    parallel_workers:     Optional[int]   = Field(None, ge=1, le=10)
    per_account_workers:  Optional[int]   = Field(None, ge=1, le=10)
    sleep_user:           Optional[float] = Field(None, ge=0)
    sleep_req:            Optional[float] = Field(None, ge=0)
    auto_update_enabled:  Optional[bool]  = None
    auto_update_interval: Optional[int]   = Field(None, ge=1)
    viewer_volume:        Optional[int]   = Field(None, ge=0, le=100)
    viewer_loop:          Optional[bool]  = None
    viewer_theme:         Optional[str]   = Field(None, pattern="^(dark|light)$")


@app.put("/api/settings")
def save_settings(req: SaveSettingsRequest):
    s: dict = {}
    try:
        s = json.loads(Path(SETTINGS_FILE).read_text("utf-8"))
    except Exception:
        pass
    data = req.model_dump(exclude_none=True)
    dp = data.pop("download_path", None)
    normalized_dp: str | None = None
    if dp is not None:
        raw_dp = dp.strip()
        candidate = Path(raw_dp).expanduser() if raw_dp else (_ROOT / "downloads")
        if not candidate.is_absolute():
            candidate = _ROOT / candidate
        try:
            candidate = candidate.resolve()
            candidate.mkdir(parents=True, exist_ok=True)
            if not candidate.is_dir():
                raise OSError("the destination is not a directory")
            # Verify that the app can actually create files there. This catches
            # unavailable mapped/network drives and read-only destinations now,
            # instead of failing later in a background download worker.
            import tempfile
            with tempfile.NamedTemporaryFile(
                prefix=".archiver-write-test-", dir=candidate, delete=True
            ):
                pass
        except OSError as exc:
            raise HTTPException(
                400, f"Download location is unavailable or not writable: {candidate} ({exc})"
            ) from exc
        normalized_dp = str(candidate)
    s.update(data)
    Path(SETTINGS_FILE).write_text(json.dumps(s, indent=2, ensure_ascii=False), "utf-8")
    if normalized_dp is not None:
        Path(DOWNLOAD_PATH_FILE).write_text(normalized_dp, "utf-8")
    # Start/stop scheduler based on saved settings
    if s.get("auto_update_enabled"):
        state.start_scheduler(int(s.get("auto_update_interval", 60)))
    else:
        state.stop_scheduler()
    return {"ok": True, "download_path": normalized_dp}


# ── Cookies ───────────────────────────────────────────────────────────────────

@app.get("/api/cookies/{platform}")
def get_cookies(platform: str):
    if platform not in PLATFORMS:
        raise HTTPException(404, "Unknown platform")
    p = Path(PLATFORMS[platform]["cookies_file"])
    return {"content": p.read_text("utf-8") if p.exists() else ""}


class SaveCookiesRequest(BaseModel):
    content: str


@app.put("/api/cookies/{platform}")
def save_cookies(platform: str, req: SaveCookiesRequest):
    if platform not in PLATFORMS:
        raise HTTPException(404, "Unknown platform")
    p = Path(PLATFORMS[platform]["cookies_file"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.content, "utf-8")
    return {"ok": True}


# ── Single URL download ───────────────────────────────────────────────────────

def _resolve_xiaohongshu_url(url: str) -> str:
    if not any(
        domain in url.lower()
        for domain in ("xhslink.com/", "xhslink.cn/")
    ):
        return url
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"}
        )
        return urllib.request.urlopen(request, timeout=10).geturl()
    except Exception as exc:
        _file_log(f"[Bot] Xiaohongshu short-link resolution failed: {exc}\n")
        return url


def _detect_platform(url: str) -> Optional[str]:
    u = url.lower()
    if any(x in u for x in ("x.com/", "twitter.com/")):
        return "x"
    if any(x in u for x in ("douyin.com/", "v.douyin.com/", "iesdouyin.com/")):
        return "douyin"
    if any(x in u for x in ("bilibili.com/", "b23.tv/")):
        return "bilibili"
    if any(
        x in u
        for x in ("xiaohongshu.com/", "xhslink.com/", "xhslink.cn/")
    ):
        return "xiaohongshu"
    return None


def _extract_shared_url(text: str) -> str:
    """Extract a URL from copied share text and trim trailing punctuation."""
    match = re.search(r"https?://[^\s<>\[\]()\"']+", text.strip())
    if not match:
        return text.strip()
    return match.group(0).rstrip(".,;:!?)'，。；：！？）】》")


class DownloadUrlRequest(BaseModel):
    url: str


@app.post("/api/download")
def download_url(req: DownloadUrlRequest):
    url = _extract_shared_url(req.url)
    pid = _detect_platform(url)
    if pid is None:
        raise HTTPException(400, "Unrecognised URL — supports X, Douyin, Bilibili, and Xiaohongshu")
    if state.running:
        raise HTTPException(400, "A sync is already running — stop it first")
    dl_root = state._download_root()
    pcfg    = PLATFORMS[pid]
    dl      = pcfg["downloader"]
    cookies = pcfg.get("cookies_file", "")
    url_path = dl_root / "URL"
    try:
        url_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(400, f"Cannot create URL download folder: {exc}") from exc
    url_dir = str(url_path)
    state.log_write(f"URL      : {url}\nPlatform : {pcfg['label']}\n")
    # f2 (Douyin single video) — library call
    if dl == "f2":
        cookie_str = _parse_cookies(cookies) if cookies and Path(cookies).exists() else ""
        state.running = True
        state.stop_flag.clear()
        state.status = "Downloading…"
        threading.Thread(
            target=_f2_one_worker,
            args=(url, cookie_str, url_dir),
            daemon=True,
        ).start()
        return {"ok": True, "platform": pid}

    if dl == "xiaohongshu":
        sys.path.insert(0, str(_HELPERS))
        import xiaohongshu_user as _xhs_user
        final_url = _resolve_xiaohongshu_url(url)
        note_id = _xhs_user.resolve_note_id(final_url)
        if not note_id:
            raise HTTPException(400, "Paste a Xiaohongshu note URL, not a profile URL")
        state.running = True
        state.stop_flag.clear()
        state.status = "Downloading…"
        def _xhs_one_worker():
            try:
                result = _xhs_user.download_note(
                    note_id, cookies, url_dir, state.stop_flag.is_set
                )
                state.log_write(
                    f"Downloaded Xiaohongshu note {note_id} "
                    f"({result['media']} media files)\n"
                )
            except Exception as exc:
                state.log_write(f"[error] Xiaohongshu download failed: {exc}\n")
            finally:
                state.running = False
                state.status = "Idle"
        threading.Thread(target=_xhs_one_worker, daemon=True).start()
        return {"ok": True, "platform": pid}

    cmd: list[str] = []
    if dl == "gallery-dl":
        cmd = [_GALLERY_DL, "-D", url_dir]
        if cookies and Path(cookies).exists():
            cmd += ["--cookies", cookies]
        cmd.append(url)
    elif dl == "yt-dlp":
        cfg = state._load_settings()
        sleep_req = float(cfg.get("sleep_req", 1))
        account_workers = int(cfg.get("per_account_workers", 4))
        cmd = _yt_dlp_command(account_workers) + ["--sleep-requests", str(sleep_req)]
        if cookies and Path(cookies).exists():
            cmd += ["--cookies", cookies]
        cmd += ["-P", url_dir, "--windows-filenames",
                "-o", "%(upload_date>%Y-%m-%d)s_%(id)s_%(title)s.%(ext)s", url]

    if not cmd:
        raise HTTPException(400, "No downloader configured for platform")

    state.running = True
    state.stop_flag.clear()
    state.status = "Downloading…"
    threading.Thread(target=_url_worker, args=(cmd,), daemon=True).start()
    return {"ok": True, "platform": pid}


@app.get("/api/history")
def get_history(limit: int = 100):
    try:
        hist = json.loads(Path(UPDATE_HISTORY_FILE).read_text("utf-8"))
        if isinstance(hist, list):
            runs = list(reversed(hist))[:limit]
            for run in runs:
                run["users"] = [u for u in run.get("users", []) if u.get("count", 0) > 0]
            return runs
    except Exception:
        pass
    return []


# ── Downloads file list ───────────────────────────────────────────────────────

_PLAT_DIRS = {"x", "bilibili", "douyin", "xiaohongshu"}

def _indexed_download_owner_by_file() -> dict[str, tuple[str, str]]:
    """Return the platform/account recorded for each downloaded file."""
    lookup: dict[str, tuple[str, str]] = {}
    for platform, accounts in _load_post_index().items():
        if platform not in PLATFORMS or not isinstance(accounts, dict):
            continue
        for account_id, posts in accounts.items():
            if not isinstance(posts, dict):
                continue
            for post_id, meta in posts.items():
                if str(post_id).startswith("_") or not isinstance(meta, dict):
                    continue
                raw = meta.get("file")
                if raw:
                    try:
                        lookup[os.path.normcase(str(Path(raw).resolve()))] = (
                            platform, str(account_id)
                        )
                    except OSError:
                        pass
    return lookup


@app.get("/api/downloads")
def list_downloads(limit: int = 500):
    import datetime as _dt
    base = state._download_root()
    indexed_owners = _indexed_download_owner_by_file()
    indexed_platforms = {
        path: owner[0] for path, owner in indexed_owners.items()
    }
    rows = []
    if base.exists():
        for f in base.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in _MEDIA_EXTS:
                continue
            try:
                parts = f.relative_to(base).parts
                plat = indexed_platforms.get(
                    os.path.normcase(str(f.resolve())),
                    parts[0] if parts and parts[0] in _PLAT_DIRS else "—",
                )
                st    = f.stat()
                rows.append({
                    "name":     f.name,
                    "path":     str(f),
                    "platform": plat,
                    "account_id": indexed_owners.get(
                        os.path.normcase(str(f.resolve())), ("", "")
                    )[1],
                    "size":     st.st_size,
                    "mtime":    st.st_mtime,
                    "date":     _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                })
            except Exception:
                continue
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    for r in rows:
        del r["mtime"]
    return rows[:limit]


@app.delete("/api/downloads/file", status_code=204)
def delete_download_file(path: str):
    p = Path(path)
    if p.exists() and p.is_file():
        p.unlink()


class RedownloadRequest(BaseModel):
    platform: str
    path:     Optional[str] = None
    post_id:  Optional[str] = None


@app.post("/api/files/redownload")
def redownload_file(req: RedownloadRequest):
    platform = req.platform
    if platform not in PLATFORMS:
        raise HTTPException(400, f"Unknown platform: {platform}")

    post_id = req.post_id

    if not post_id and req.path:
        f = Path(req.path)
        resolved = str(f.resolve())
        idx = _load_post_index()
        for acc_data in idx.get(platform, {}).values():
            for pid, meta in acc_data.items():
                if pid.startswith("_"):
                    continue
                if meta.get("file") == resolved:
                    post_id = pid
                    break
            if post_id:
                break
        if not post_id:
            result = _extract_post_id_and_date(platform, f)
            if result:
                post_id = result[0]

    if not post_id:
        raise HTTPException(400, "Could not determine post ID for this file")

    # Delete the existing file so the downloader doesn't skip it
    if req.path:
        try:
            p = Path(req.path)
            if p.exists() and p.is_file():
                p.unlink()
        except Exception:
            pass

    if platform == "x":
        url = f"https://x.com/i/status/{post_id}"
    elif platform == "bilibili":
        url = f"https://www.bilibili.com/video/{post_id}"
    elif platform == "douyin":
        url = f"https://www.douyin.com/video/{post_id}"
    elif platform == "xiaohongshu":
        url = f"https://www.xiaohongshu.com/explore/{post_id}"
    else:
        raise HTTPException(400, f"Redownload not supported for {platform}")

    return download_url(DownloadUrlRequest(url=url))


class MissingPostRequest(BaseModel):
    platform: str
    account_id: str
    post_ids: list[str]


@app.post("/api/posts/download-missing")
def download_missing_post(req: MissingPostRequest):
    """Download one remote-only post into its tracked account folder."""
    if state.running:
        raise HTTPException(400, "Another download or sync is already running")
    entry = next(
        (
            item for item in state._store.all_entries()
            if item.platform == req.platform
            and item.handle.split("|")[-1] == req.account_id
        ),
        None,
    )
    if entry is None:
        raise HTTPException(404, "Tracked account not found")
    if req.platform not in PLATFORMS:
        raise HTTPException(400, "Unsupported platform")
    target = _ensure_entry_download_folder(
        entry, state._store, state._download_root()
    )
    state.running = True
    state.stop_flag.clear()
    state.status = "Downloading…"
    post_ids = list(dict.fromkeys(post_id for post_id in req.post_ids if post_id))
    if not post_ids:
        state.running = False
        state.status = "Idle"
        raise HTTPException(400, "No missing posts selected")
    display = entry.handle.split("|")[0]
    threading.Thread(
        target=_missing_posts_worker,
        args=(req.platform, req.account_id, display, post_ids, target),
        daemon=True,
    ).start()
    return {"ok": True}


def _missing_posts_worker(
    platform: str,
    account_id: str,
    display: str,
    post_ids: list[str],
    target: Path,
) -> None:
    key = f"missing:{platform}:{account_id}"
    total = len(post_ids)
    state.progress_update(
        key,
        platform=platform,
        account=display,
        account_id=account_id,
        operation="missing",
        state="downloading",
        percent=0.0,
        done=0,
        total=total,
        downloaded=0,
    )
    succeeded = 0
    try:
        if platform == "douyin":
            import asyncio as _aio
            sys.path.insert(0, str(_HELPERS))
            import f2_user as _f2_user
            cookies = PLATFORMS["douyin"].get("cookies_file", "")
            cookie_string = (
                _parse_cookies(cookies)
                if cookies and Path(cookies).is_file() else ""
            )

            def _douyin_progress(done: int, expected: int, current: str) -> None:
                state.progress_update(
                    key,
                    state="downloading",
                    percent=done / max(1, expected) * 100.0,
                    done=done,
                    total=expected,
                    current_file=current,
                )

            _aio.run(_f2_user.download_selected_posts(
                account_id,
                cookie_string,
                str(target),
                set(post_ids),
                naming=_DOUYIN_NAMING,
                stop_check=state.stop_flag.is_set,
                progress_callback=_douyin_progress,
            ))
            for post_id in post_ids:
                landed = _valid_media_for_post("douyin", post_id, target)
                if landed:
                    succeeded += 1
                    state.log_write(
                        f"[missing] Download complete: {post_id} "
                        f"({len(landed)} media file"
                        f"{'s' if len(landed) != 1 else ''})\n"
                    )
                else:
                    state.log_write(
                        f"[error] Missing-post download failed: {post_id} "
                        "did not create valid media\n"
                    )
            _reconcile_local_post_index(platform, account_id, target)
        else:
            for index, post_id in enumerate(post_ids, 1):
                if state.stop_flag.is_set():
                    break
                state.progress_update(
                    key,
                    state="downloading",
                    percent=(index - 1) / total * 100.0,
                    done=index - 1,
                    total=total,
                    current_file=post_id,
                    downloaded=succeeded,
                )
                if _missing_post_worker(
                    platform, account_id, post_id, target, manage_state=False,
                ):
                    succeeded += 1
                state.progress_update(
                    key,
                    percent=index / total * 100.0,
                    done=index,
                    downloaded=succeeded,
                )
        stopped = state.stop_flag.is_set()
        failed = total - succeeded
        state.progress_update(
            key,
            state=(
                "stopped" if stopped
                else "error" if failed
                else "finished"
            ),
            percent=(None if stopped else 100.0),
            done=succeeded if stopped else total,
            total=total,
            downloaded=succeeded,
            error=(
                f"{failed} of {total} missing posts failed to download"
                if failed and not stopped else None
            ),
        )
        state.log_write(
            f"[missing] Batch complete: {succeeded}/{total} downloaded\n",
            account_key=key,
        )
    finally:
        state.running = False
        state.status = "Idle"


def _missing_post_worker(
    platform: str, account_id: str, post_id: str, target: Path,
    *, manage_state: bool = True,
) -> bool:
    """Platform-specific single-post download followed by local re-indexing."""
    import asyncio as _aio

    succeeded = False
    try:
        state.log_write(
            f"[missing] Downloading {platform}/{post_id} into {target}\n"
        )
        cookies = PLATFORMS[platform].get("cookies_file", "")
        if platform == "douyin":
            sys.path.insert(0, str(_HELPERS))
            import f2_one as _f2_one
            cookie_string = (
                _parse_cookies(cookies)
                if cookies and Path(cookies).is_file() else ""
            )
            _aio.run(_f2_one.download_one(
                post_id,
                cookie_string,
                str(target),
                _DOUYIN_NAMING,
            ))
        elif platform == "xiaohongshu":
            sys.path.insert(0, str(_HELPERS))
            import xiaohongshu_user as _xhs_user
            _xhs_user.download_note(
                post_id, cookies, str(target), state.stop_flag.is_set,
            )
        else:
            if platform == "bilibili":
                url = f"https://www.bilibili.com/video/{post_id}"
                cmd = _yt_dlp_command() + [
                    "-P", str(target), "--windows-filenames",
                    "-o", _BILIBILI_FILENAME,
                ]
                if cookies and Path(cookies).is_file():
                    cmd += ["--cookies", cookies]
                cmd.append(url)
            elif platform == "x":
                url = f"https://x.com/i/status/{post_id}"
                cmd = [_GALLERY_DL, "-D", str(target)]
                if cookies and Path(cookies).is_file():
                    cmd += ["--cookies", cookies]
                cmd += [
                    "-o",
                    f"filename={_X_FILENAME}",
                ]
                cmd.append(url)
            else:
                raise RuntimeError(f"Unsupported platform: {platform}")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_NO_WINDOW if sys.platform == "win32" else 0,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if output:
                state.log_write(output)
            if result.returncode != 0:
                raise RuntimeError(f"downloader exited with code {result.returncode}")
        landed = _valid_media_for_post(platform, post_id, target)
        if not landed:
            raise RuntimeError(
                "downloader returned without creating a valid matching media file"
            )
        _reconcile_local_post_index(platform, account_id, target)
        state.log_write(
            f"[missing] Download complete: {post_id} "
            f"({len(landed)} media file{'s' if len(landed) != 1 else ''})\n"
        )
        succeeded = True
    except Exception as exc:
        state.log_write(f"[error] Missing-post download failed: {exc}\n")
    finally:
        if manage_state:
            state.running = False
            state.status = "Idle"
    return succeeded


def _valid_media_for_post(
    platform: str, post_id: str, target: Path,
) -> list[Path]:
    landed: list[Path] = []
    for file in target.rglob("*") if target.exists() else []:
        if not file.is_file() or file.suffix.lower() not in _MEDIA_EXTS:
            continue
        parsed = _extract_post_id_and_date(platform, file)
        if parsed is not None and parsed[0] == post_id and _is_valid_media(file):
            landed.append(file)
    return landed


# ── Telegram bot ──────────────────────────────────────────────────────────────

@app.get("/api/telegram/status")
def tg_status():
    return {
        "status":    state._tg_status,
        "token_set": bool(state._load_settings().get("telegram_token", "")),
    }


class TgStartRequest(BaseModel):
    token: str


@app.post("/api/telegram/start")
def tg_start(req: TgStartRequest):
    token = req.token.strip()
    s = {}
    try:
        s = json.loads(Path(SETTINGS_FILE).read_text("utf-8"))
    except Exception:
        pass
    # "reuse" is a UI sentinel meaning "restart with the already-saved token"
    if not token or token == "reuse":
        token = s.get("telegram_token", "")
    if not token:
        raise HTTPException(400, "Token is required")
    s["telegram_token"] = token
    Path(SETTINGS_FILE).write_text(json.dumps(s, indent=2, ensure_ascii=False), "utf-8")
    state.start_tg_bot(token)
    return {"ok": True}


@app.post("/api/telegram/stop")
def tg_stop():
    state.stop_tg_bot()
    s = {}
    try:
        s = json.loads(Path(SETTINGS_FILE).read_text("utf-8"))
    except Exception:
        pass
    s["telegram_token"] = ""
    Path(SETTINGS_FILE).write_text(json.dumps(s, indent=2, ensure_ascii=False), "utf-8")
    return {"ok": True}


# ── Database reset ────────────────────────────────────────────────────────────

@app.post("/api/database/reset", status_code=204)
def reset_database():
    arc = Path(ARCHIVES_DIR)
    if arc.exists():
        for f in arc.iterdir():
            if f.is_file():
                f.unlink()


def _douyin_post_id_from_url(url_or_id: str) -> "str | None":
    """Resolve IDs in standard, note, and profile-modal Douyin URLs."""
    if url_or_id.isdigit():
        return url_or_id
    match = re.search(r"/(?:video|note)/(\d+)", url_or_id)
    if match:
        return match.group(1)
    try:
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(url_or_id).query)
        for key in ("modal_id", "vid"):
            value = query.get(key, [""])[0]
            if value.isdigit():
                return value
    except (TypeError, ValueError):
        pass
    return None


def _f2_one_worker(url_or_id: str, cookie_str: str, outdir: str) -> None:
    import asyncio as _aio
    sys.path.insert(0, str(_HELPERS))
    try:
        # Resolve aweme_id: if it's already a pure numeric ID use it directly,
        # otherwise let f2's AwemeIdFetcher resolve any URL (short or long).
        aweme_id = _douyin_post_id_from_url(url_or_id)
        if not aweme_id:
            from f2.apps.douyin.utils import AwemeIdFetcher
            aweme_id = _aio.run(AwemeIdFetcher.get_aweme_id(url_or_id))
        state.log_write(f"Resolved : {aweme_id}\n")
        import f2_one as _f2_one
        _aio.run(_f2_one.download_one(aweme_id, cookie_str, outdir))
    except Exception as exc:
        state.log_write(f"[error] f2 download failed: {exc}\n")
    finally:
        state.running = False
        state.status = "Idle"
        state.log_write("─" * 44 + "\n")


def _f2_bot_worker(
    url_or_id: str,
    cookie_str: str,
    outdir: str,
    bot,
    chat_id: int,
    download_allowed: bool = True,
    manage_activity: bool = True,
) -> None:
    """Bot-triggered f2 worker: handles video downloads and profile-URL account addition."""
    import asyncio as _aio
    import re as _re2
    sys.path.insert(0, str(_HELPERS))
    try:
        resolved_url = url_or_id
        if "v.douyin.com/" in url_or_id.lower():
            import urllib.request as _urlrequest
            try:
                request = _urlrequest.Request(
                    url_or_id,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                resolved_url = _urlrequest.urlopen(
                    request, timeout=10
                ).geturl()
            except Exception:
                # F2 remains the fallback if direct redirect expansion fails.
                pass
        # Direct profile URL — extract sec_uid without a network resolve call
        m = _re2.search(
            r'(?:douyin\.com|iesdouyin\.com)/(?:share/)?user/'
            r'([^/?&#\s]+)',
            resolved_url,
        )
        if m:
            _aio.run(_add_douyin_account_async(m.group(1), cookie_str, bot, chat_id))
            return

        if not download_allowed:
            if bot:
                bot.send_message(
                    chat_id,
                    "A sync is already running. Send a Douyin profile link "
                    "to add an account, or wait before downloading a post."
                )
            return

        embedded_id = _douyin_post_id_from_url(resolved_url)
        if embedded_id:
            aweme_id = embedded_id
            if bot:
                bot.send_message(chat_id, "⬇ Downloading from 抖音…")
        else:
            from f2.apps.douyin.utils import AwemeIdFetcher, SecUserIdFetcher
            try:
                aweme_id = _aio.run(AwemeIdFetcher.get_aweme_id(resolved_url))
                state.log_write(f"Resolved : {aweme_id}\n")
                if bot:
                    bot.send_message(chat_id, "⬇ Downloading from 抖音…")
            except Exception:
                # Short link resolved to a profile page — add as tracked account
                sec_uid = _aio.run(SecUserIdFetcher.get_sec_user_id(resolved_url))
                _aio.run(_add_douyin_account_async(sec_uid, cookie_str, bot, chat_id))
                return
        import f2_one as _f2_one
        _aio.run(_f2_one.download_one(aweme_id, cookie_str, outdir))
    except Exception as exc:
        state.log_write(f"[error] f2 download failed: {exc}\n")
    finally:
        if manage_activity:
            state.running = False
            state.status = "Idle"
        state.log_write("─" * 44 + "\n")


async def _add_douyin_account_async(sec_uid: str, cookie_str: str, bot, chat_id: int) -> None:
    """Fetch the Douyin display name then delegate to _tg_add_account."""
    sys.path.insert(0, str(_HELPERS))
    try:
        from f2.apps.douyin.handler import DouyinHandler
        from f2.apps.douyin.utils import ClientConfManager
        kw = {
            "cookie": cookie_str,
            "languages": "zh_CN",
            "timeout": 15, "max_retries": 2, "max_connections": 2, "max_tasks": 2,
            "page_counts": 20, "max_counts": None, "headers": ClientConfManager.headers(),
        }
        profile  = await DouyinHandler(kw).fetch_user_profile(sec_uid)
        nickname = getattr(profile, "nickname", None) or sec_uid
    except Exception:
        nickname = sec_uid
    _tg_add_account("douyin", f"{nickname}|{sec_uid}", nickname, bot, chat_id)


def _fetch_bilibili_name(uid: str) -> str:
    """Resolve a Bilibili display name with API retries and yt-dlp fallback."""
    import urllib.request as _ulr, json as _j
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://space.bilibili.com/{uid}/",
    }
    cookies_file = PLATFORMS["bilibili"].get("cookies_file", "")
    if cookies_file and Path(cookies_file).is_file():
        cookie = _parse_cookies(cookies_file)
        if cookie:
            headers["Cookie"] = cookie
    for attempt in range(3):
        try:
            req = _ulr.Request(
                f"https://api.bilibili.com/x/web-interface/card?mid={uid}",
                headers=headers,
            )
            data = _j.loads(
                _ulr.urlopen(req, timeout=10).read().decode("utf-8")
            )
            card = (data.get("data") or {}).get("card") or {}
            name = str(card.get("name") or "").strip()
            if data.get("code") == 0 and name and name != uid:
                return name
        except Exception:
            pass
        if attempt < 2:
            time.sleep(1 + attempt)

    # The public card endpoint is sometimes rate-limited while yt-dlp's
    # signed space extractor still succeeds.
    try:
        cmd = _yt_dlp_command() + [
            "--flat-playlist", "--playlist-items", "1",
            "--dump-single-json",
        ]
        if cookies_file and Path(cookies_file).is_file():
            cmd += ["--cookies", cookies_file]
        cmd.append(f"https://space.bilibili.com/{uid}/video")
        completed = subprocess.run(
            cmd,
            capture_output=True,
            timeout=45,
            creationflags=_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if completed.returncode == 0:
            payload = _j.loads(completed.stdout.decode("utf-8", errors="replace"))
            candidates = [
                payload.get("uploader"),
                payload.get("channel"),
                payload.get("playlist_uploader"),
            ]
            entries = payload.get("entries") or []
            if entries:
                candidates.extend([
                    entries[0].get("uploader"),
                    entries[0].get("channel"),
                ])
            for candidate in candidates:
                name = str(candidate or "").strip()
                if name and name != uid:
                    return name
    except Exception:
        pass
    return uid


def _fetch_bilibili_remote_total(uid: str, cookies_file: str = "") -> int | None:
    """Fetch the published-video count independently of yt-dlp's playlist."""
    import urllib.request as _ulr, json as _j
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://space.bilibili.com/{uid}/video",
        }
        if cookies_file and Path(cookies_file).is_file():
            cookie = _parse_cookies(cookies_file)
            if cookie:
                headers["Cookie"] = cookie
        req = _ulr.Request(
            f"https://api.bilibili.com/x/web-interface/card?mid={uid}",
            headers=headers,
        )
        data = _j.loads(_ulr.urlopen(req, timeout=8).read().decode("utf-8"))
        if data.get("code") != 0:
            return None
        value = (data.get("data") or {}).get("archive_count")
        return int(value) if value is not None else None
    except Exception:
        return None


def _fetch_bilibili_remote_ids(
    url: str,
    cookies_file: str = "",
    stop_check=None,
) -> tuple[set[str], bool]:
    """Enumerate a Bilibili account without downloading any media."""
    cmd = _yt_dlp_command() + [
        "--flat-playlist",
        "--print", "%(id)s",
        "--extractor-retries", "3",
        "--retry-sleep", "extractor:5",
    ]
    if cookies_file and Path(cookies_file).is_file():
        cmd += ["--cookies", cookies_file]
    cmd.append(url)
    ids: set[str] = set()
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW if sys.platform == "win32" else 0,
        )
        while proc.poll() is None:
            if stop_check and stop_check():
                _stop_process(proc)
                return ids, False
            line = proc.stdout.readline()
            if line:
                text = line.decode("utf-8", errors="replace").strip()
                if _BVID_RE.fullmatch(text):
                    ids.add(text)
            else:
                time.sleep(0.05)
        for raw in proc.stdout:
            text = raw.decode("utf-8", errors="replace").strip()
            if _BVID_RE.fullmatch(text):
                ids.add(text)
        return ids, proc.wait() == 0
    except OSError:
        if proc is not None:
            _stop_process(proc)
        return ids, False


def _xhs_bot_worker(url: str, bot, chat_id: int, download_allowed: bool) -> None:
    sys.path.insert(0, str(_HELPERS))
    import xiaohongshu_user as _xhs_user
    final_url = _resolve_xiaohongshu_url(url)
    state.log_write(f"[Bot] Xiaohongshu link: {url} -> {final_url}\n")
    user_id = _xhs_user.resolve_profile_id(final_url)
    if user_id:
        try:
            name, avatar = _xhs_user.account_info(
                user_id, PLATFORMS["xiaohongshu"]["cookies_file"]
            )
            if avatar:
                _cache_avatar_url("xiaohongshu", user_id, avatar)
            _tg_add_account(
                "xiaohongshu", f"{name}|{user_id}", name, bot, chat_id
            )
        except Exception as exc:
            if bot:
                bot.send_message(chat_id, f"Could not add Xiaohongshu account: {exc}")
        return
    note_id = _xhs_user.resolve_note_id(final_url)
    if not note_id:
        if bot:
            bot.send_message(chat_id, "Could not recognise this Xiaohongshu link.")
        return
    if not download_allowed:
        if bot:
            bot.send_message(
                chat_id,
                "A sync is running. Xiaohongshu profiles can still be added, "
                "but note downloads must wait.",
            )
        return
    try:
        download_url(DownloadUrlRequest(url=final_url))
        if bot:
            bot.send_message(chat_id, "Downloading from Xiaohongshu…")
    except Exception as exc:
        if bot:
            bot.send_message(chat_id, f"Xiaohongshu download failed: {exc}")


def _tg_add_account(pid: str, handle: str, display: str, bot, chat_id: int) -> None:
    """Add a tracked account and, if groups exist, prompt for group assignment via bot."""
    existing = state._store.find_entry(pid, handle)
    if existing is not None:
        creator = (
            state._store.get_creator(existing.creator_id)
            if existing.creator_id else None
        )
        location = (
            f'group "{creator.name}"' if creator else "the unassigned list"
        )
        message = (
            f"Already added: {existing.handle.split('|')[0]} "
            f"({pid}), in {location}."
        )
        state.log_write(f"[account] {message}\n")
        if bot:
            bot.send_message(chat_id, message)
        return
    try:
        entry = state._store.add_entry(pid, handle, None)
    except DuplicateEntryError as exc:
        # Covers two simultaneous bot/app requests passing the check above.
        if bot:
            bot.send_message(chat_id, str(exc))
        return
    _ensure_entry_download_folder(
        entry, state._store, state._download_root()
    )
    state.log_write(f"Added {pid} account: {display}\n")
    creators = state._store.all_creators()
    if creators and bot:
        lines = [f"✅ Added: {display}.",
                 "Which group? Reply with a number, or 0 to create a new group:"]
        for i, c in enumerate(creators, 1):
            lines.append(f"{i}. {c.name}")
        bot.send_message(chat_id, "\n".join(lines))
        state._tg_pending[chat_id] = {
            "action":   "assign_group",
            "entry_id": entry.id,
            "creators": creators,
            "display":  display,
        }
    elif bot:
        bot.send_message(chat_id, f"✅ Added: {display}")


def _url_worker(cmd: list[str]):
    import subprocess as _sp
    try:
        proc = _sp.Popen(
            cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT,
            stdin=_sp.DEVNULL,
            creationflags=_NO_WINDOW if sys.platform == "win32" else 0,
        )
        state._proc = proc
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace")
            state.log_write(line)
            if state.stop_flag.is_set():
                _stop_process(proc)
                break
        return_code = proc.wait()
        if return_code != 0 and not state.stop_flag.is_set():
            state.log_write(f"[error] Downloader exited with code {return_code}\n")
    except FileNotFoundError as exc:
        state.log_write(f"[error] Command not found: {exc}\n")
    except Exception as exc:
        state.log_write(f"[error] Download failed: {exc}\n")
    finally:
        state.running = False
        state.status = "Idle"
        state.log_write("─" * 44 + "\n")


# ── Avatars ───────────────────────────────────────────────────────────────────

@app.get("/api/avatars/{platform}/{account_id}")
def serve_avatar(platform: str, account_id: str):
    p = Path("config/avatars") / f"{platform}_{account_id}.png"
    if not p.exists():
        raise HTTPException(404, "No avatar cached")
    return FileResponse(str(p), media_type="image/png")


@app.post("/api/avatars/{platform}/{account_id}/fetch")
async def fetch_avatar_endpoint(platform: str, account_id: str):
    loop = asyncio.get_event_loop()
    available = await loop.run_in_executor(None, _fetch_avatar_bg, platform, account_id)
    if not available:
        raise HTTPException(502, f"Could not fetch {platform} avatar")
    return {"ok": True}


def _parse_cookies(path: str) -> str:
    ck: dict = {}
    try:
        for line in Path(path).read_text("utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) >= 7:
                ck[fields[5].strip()] = fields[6].strip()
    except Exception:
        pass
    return "; ".join(f"{k}={v}" for k, v in ck.items())


def _cache_avatar_url(platform: str, account_id: str, url: str) -> bool:
    import urllib.request
    cache = Path("config/avatars") / f"{platform}_{account_id}.png"
    try:
        request = urllib.request.Request(
            str(url).replace(r"\/", "/"),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 Chrome/126 Safari/537.36",
                "Referer": (
                    "https://www.douyin.com/" if platform == "douyin"
                    else "https://www.xiaohongshu.com/" if platform == "xiaohongshu"
                    else "https://www.bilibili.com/"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            content = response.read()
            content_type = response.headers.get("Content-Type", "")
        if len(content) < 100 or (content_type and not content_type.startswith("image/")):
            raise ValueError(
                f"unexpected avatar response ({content_type}, {len(content)} bytes)"
            )
        cache.write_bytes(content)
        return True
    except Exception as exc:
        state.log_write(f"[avatar] {platform} {account_id}: {exc}\n")
        return False


def _fetch_avatar_bg(platform: str, account_id: str) -> bool:
    import ssl, urllib.request, json as _j, http.client, subprocess as _sp, re as _re
    cache = Path("config/avatars") / f"{platform}_{account_id}.png"
    if cache.exists():
        return True
    try:
        ctx = ssl.create_default_context()
        url = None

        if platform == "bilibili":
            conn = http.client.HTTPSConnection("api.bilibili.com", timeout=8, context=ctx)
            conn.request("GET", f"/x/web-interface/card?mid={account_id}",
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://www.bilibili.com/"})
            data = _j.loads(conn.getresponse().read())
            conn.close()
            url = (data.get("data") or {}).get("card", {}).get("face")

        elif platform == "x":
            cf = Path(PLATFORMS["x"]["cookies_file"])
            if not cf.exists():
                return
            r = _sp.run(
                [_GALLERY_DL, "--cookies", str(cf), "--simulate", "--dump-json",
                 "--range", "1", f"https://x.com/{account_id}/media"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30, creationflags=_NO_WINDOW,
            )
            try:
                for entry in _j.loads(r.stdout):
                    if not isinstance(entry, list):
                        continue
                    meta = (entry[2] if len(entry) == 3 and isinstance(entry[2], dict) else
                            entry[1] if len(entry) == 2 and isinstance(entry[1], dict) else None)
                    if not meta:
                        continue
                    user = meta.get("user") or meta.get("author") or {}
                    raw  = user.get("profile_image") or user.get("profile_image_url_https") or ""
                    if raw:
                        url = _re.sub(r"_normal\.", "_400x400.", raw)
                        break
            except Exception:
                pass

        elif platform == "douyin":
            cf = Path(PLATFORMS["douyin"]["cookies_file"])
            if not cf.exists():
                return
            import asyncio as _aio
            sys.path.insert(0, str(_HELPERS))
            from f2.apps.douyin.handler import DouyinHandler
            from f2.apps.douyin.utils import ClientConfManager
            kw = {
                "cookie": _parse_cookies(str(cf)), "languages": "en_US",
                "timeout": 15, "max_retries": 2, "max_connections": 2, "max_tasks": 2,
                "page_counts": 1, "max_counts": None, "headers": ClientConfManager.headers(),
            }
            async def _get_dy_url():
                profile = await DouyinHandler(kw).fetch_user_profile(account_id)
                for attr in ("avatar_thumb", "avatar_medium", "avatar_larger", "avatar_url", "avatar"):
                    obj = getattr(profile, attr, None)
                    if obj is None:
                        continue
                    if isinstance(obj, str) and obj.startswith("http"):
                        return obj
                    ul = getattr(obj, "url_list", None)
                    if ul:
                        return ul[0]
                return None
            url = _aio.run(_get_dy_url())

        elif platform == "xiaohongshu":
            sys.path.insert(0, str(_HELPERS))
            import xiaohongshu_user as _xhs_user
            _, url = _xhs_user.account_info(
                account_id, PLATFORMS["xiaohongshu"]["cookies_file"]
            )

        if url:
            return _cache_avatar_url(platform, account_id, url)
    except Exception as exc:
        state.log_write(f"[avatar] {platform} {account_id}: {exc}\n")
    return cache.exists()


# ── Posts & ghost check ───────────────────────────────────────────────────────

_post_index_lock = threading.RLock()


def _load_post_index() -> dict:
    try:
        p = Path(POST_INDEX_FILE)
        if p.exists():
            return json.loads(p.read_text("utf-8"))
    except Exception:
        pass
    return {}


def _save_post_index(index: dict) -> None:
    try:
        Path(POST_INDEX_FILE).write_text(
            json.dumps(index, indent=2, ensure_ascii=False), "utf-8")
    except Exception:
        pass


def _missing_expected_name(platform: str, post_id: str) -> str:
    """Policy-shaped fallback when a remote list omits title/date metadata."""
    if platform == "douyin":
        return f"unknown-date_{post_id}_untitled"
    if platform == "bilibili":
        return f"unknown-date_{post_id}_untitled.mp4"
    if platform == "xiaohongshu":
        return f"unknown-date_{post_id}_untitled_media"
    return f"unknown-date_{post_id}_1.media"


def _reconcile_local_post_index(
    platform: str,
    account_id: str,
    folder: Path,
    verified_remote_ids: "set[str] | None" = None,
    *,
    reset_unverified: bool = False,
    remote_names: "dict[str, str] | None" = None,
) -> None:
    """Make one account's index exactly reflect parseable local media posts."""
    import datetime as _dt

    local: dict[str, list[tuple[Path, str]]] = {}
    if folder.is_dir():
        for file in folder.rglob("*"):
            if not file.is_file() or file.suffix.lower() not in _MEDIA_EXTS:
                continue
            parsed = _extract_post_id_and_date(platform, file)
            if parsed is not None:
                post_id, date_str = parsed
                local.setdefault(post_id, []).append((file, date_str))

    today = _dt.date.today().isoformat()
    with _post_index_lock:
        index = _load_post_index()
        platform_index = index.setdefault(platform, {})
        previous = platform_index.get(account_id, {})
        reconciled = {
            key: value for key, value in previous.items()
            if key.startswith("_")
            or (
                verified_remote_ids is None
                and not reset_unverified
                and value.get("status") == "missing"
            )
        }
        for post_id, candidates in local.items():
            old = previous.get(post_id, {})
            old_path = Path(old.get("file", "")) if old.get("file") else None
            selected = next(
                (item for item in candidates if old_path and item[0] == old_path),
                sorted(candidates, key=lambda item: str(item[0]).casefold())[0],
            )
            file, parsed_date = selected
            date_str = parsed_date or old.get("date", "")
            if not date_str:
                try:
                    date_str = _dt.datetime.fromtimestamp(
                        file.stat().st_mtime
                    ).strftime("%Y-%m-%d")
                except OSError:
                    date_str = ""
            status = old.get("status", "unchecked")
            checked = old.get("checked", "")
            if not checked or status not in {"ok", "gone"}:
                status = "unchecked"
                checked = ""
            if reset_unverified and verified_remote_ids is None:
                status = "unchecked"
                checked = ""
            if verified_remote_ids is not None:
                status = "ok" if post_id in verified_remote_ids else "gone"
                checked = today
            reconciled[post_id] = {
                "date": date_str,
                "file": str(file.resolve()),
                "files": [
                    str(candidate.resolve())
                    for candidate, _ in sorted(
                        candidates, key=lambda item: str(item[0]).casefold()
                    )
                ],
                "status": status,
                "checked": checked,
            }
        if verified_remote_ids is not None:
            for post_id in verified_remote_ids - set(local):
                reconciled[post_id] = {
                    "date": "",
                    "file": "",
                    "files": [],
                    "status": "missing",
                    "checked": today,
                    "expected_name": (
                        (remote_names or {}).get(post_id)
                        or _missing_expected_name(platform, post_id)
                    ),
                }
        platform_index[account_id] = reconciled
        _save_post_index(index)


@app.get("/api/posts/{platform}/{account_id}")
def get_posts(platform: str, account_id: str):
    index = _load_post_index()
    raw = {
        key: value
        for key, value in index.get(platform, {}).get(account_id, {}).items()
        if not key.startswith("_")
    }
    rows = []
    for post_id, meta in raw.items():
        files = meta.get("files") or [meta.get("file", "")]
        local_files = [
            file for file in files if _indexed_local_file_exists(file)
        ]
        if meta.get("status") == "missing" and not local_files:
            rows.append({
                "post_id": post_id,
                "date": meta.get("date", ""),
                "file": "",
                "files": [],
                "status": "missing",
                "checked": meta.get("checked", ""),
                "expected_name": meta.get("expected_name", ""),
            })
            continue
        if not local_files:
            continue
        primary = meta.get("file", "")
        if primary not in local_files:
            primary = local_files[0]
        rows.append({
            "post_id": post_id,
            "date": meta.get("date", ""),
            "file": primary,
            "files": local_files,
            "status": (
                meta.get("status", "unchecked")
                if meta.get("checked") else "unchecked"
            ),
            "checked": meta.get("checked", ""),
            "expected_name": meta.get("expected_name", ""),
        })
    posts = sorted(
        rows,
        key=lambda row: (row["date"], row["file"]),
        reverse=True,
    )
    return posts


def _indexed_local_file_exists(value: str) -> bool:
    if not value:
        return False
    path = Path(value)
    if path.is_file():
        return True
    if path.is_absolute():
        return False
    try:
        return any(state._download_root().rglob(path.name))
    except OSError:
        return False


def _apply_remote_list_status(
    platform: str,
    account_id: str,
    remote_ids: "set[str]",
    remote_names: "dict[str, str] | None" = None,
    *,
    complete: bool = True,
) -> int:
    """Apply positive matches, and absences only for a complete snapshot."""
    import datetime as _dt

    today = _dt.date.today().isoformat()
    with _post_index_lock:
        index = _load_post_index()
        account = index.setdefault(platform, {}).setdefault(account_id, {})
        rebuilt = {
            key: value for key, value in account.items() if key.startswith("_")
        }
        count = 0
        for post_id, meta in account.items():
            if post_id.startswith("_"):
                continue
            files = meta.get("files") or [meta.get("file", "")]
            local_files = [file for file in files if _indexed_local_file_exists(file)]
            if not local_files:
                continue
            count += 1
            meta["files"] = local_files
            meta["file"] = local_files[0]
            if post_id in remote_ids:
                meta["status"] = "ok"
                meta["checked"] = today
            elif complete:
                meta["status"] = "gone"
                meta["checked"] = today
            elif meta.get("status") not in {"ok", "gone"} or not meta.get("checked"):
                # Absence from a partial list proves nothing. Keep previously
                # verified results, otherwise leave the post pending.
                meta["status"] = "unchecked"
                meta["checked"] = ""
            rebuilt[post_id] = meta
        for post_id in remote_ids - set(rebuilt):
            rebuilt[post_id] = {
                "date": "", "file": "", "files": [],
                "status": "missing", "checked": today,
                "expected_name": (
                    (remote_names or {}).get(post_id)
                    or _missing_expected_name(platform, post_id)
                ),
            }
        index[platform][account_id] = rebuilt
        _save_post_index(index)
    return count


_check_jobs: dict[str, dict] = {}


@app.get("/api/posts/{platform}/{account_id}/check")
def get_check_status(platform: str, account_id: str):
    return _check_jobs.get(f"{platform}_{account_id}", {"running": False, "done": 0, "total": 0})


@app.post("/api/posts/{platform}/{account_id}/check")
def start_ghost_check(platform: str, account_id: str):
    key = f"{platform}_{account_id}"
    if _check_jobs.get(key, {}).get("running"):
        return {"ok": False, "error": "Already checking"}
    _check_jobs[key] = {"running": True, "done": 0, "total": 0}
    if platform == "bilibili":
        target, kwargs = _run_bilibili_remote_list_check_bg, {}
    elif platform == "douyin":
        target, kwargs = _run_douyin_remote_list_check_bg, {}
    else:
        target, kwargs = _run_ghost_check_bg, {"force": True}
    threading.Thread(
        target=target,
        args=(platform, account_id, key),
        kwargs=kwargs,
        daemon=True,
    ).start()
    return {"ok": True}


def _run_bilibili_remote_list_check_bg(
    platform: str, account_id: str, job_key: str,
) -> None:
    """Verify Bilibili local posts using one account-list enumeration."""
    cookies = PLATFORMS["bilibili"].get("cookies_file", "")
    url = f"https://space.bilibili.com/{account_id}/video"
    state.log_write(
        f"[remote-list] bilibili/{account_id}: fetching available posts\n"
    )
    ids, enumeration_ok = _fetch_bilibili_remote_ids(
        url, cookies,
    )
    # `archive_count` is not a valid denominator for an availability check:
    # it may include hidden/private/otherwise non-enumerable submissions.
    # A successful yt-dlp playlist traversal is the authoritative snapshot of
    # posts that are available remotely right now.
    complete = enumeration_ok
    local_total = _apply_remote_list_status(
        platform, account_id, ids, complete=complete,
    )
    _check_jobs[job_key]["total"] = local_total
    _check_jobs[job_key]["done"] = local_total if complete else 0
    if complete:
        state.log_write(
            f"[remote-list] Compared {local_total} local posts with "
            f"{len(ids)} currently available Bilibili posts\n"
        )
        _download_missing_after_check(platform, account_id)
    else:
        state.log_write(
            f"[remote-list] Bilibili enumeration failed after returning "
            f"{len(ids)} available posts; positive matches were verified "
            "and unknown posts remain pending\n"
        )
    _check_jobs[job_key]["running"] = False


def _run_douyin_remote_list_check_bg(
    platform: str, account_id: str, job_key: str,
) -> None:
    """Verify Douyin local posts from one Edge-captured profile listing."""
    import asyncio as _aio
    from helpers.douyin_browser import fetch_user_posts
    from f2.apps.douyin.filter import UserPostFilter
    from f2.apps.douyin.utils import format_file_name

    ids: set[str] = set()
    remote_names: dict[str, str] = {}
    completed = False
    state.log_write(
        f"[remote-list] douyin/{account_id}: fetching available posts "
        "through Edge\n"
    )

    def _completion(ok: bool, _seen: int) -> None:
        nonlocal completed
        completed = ok

    async def _collect() -> None:
        cookie_file = PLATFORMS["douyin"].get("cookies_file", "")
        cookie_header = (
            _parse_cookies(cookie_file)
            if cookie_file and Path(cookie_file).is_file() else ""
        )
        async for page in fetch_user_posts(
            account_id,
            completion_callback=_completion,
            cookie_header=cookie_header,
        ):
            normalized = UserPostFilter({"aweme_list": page})._to_list()
            for item in normalized:
                post_id = str(item.get("aweme_id") or "")
                if not post_id:
                    continue
                ids.add(post_id)
                try:
                    remote_names[post_id] = format_file_name(
                        _DOUYIN_NAMING, item,
                    )
                except Exception:
                    pass

    error = None
    try:
        _aio.run(_collect())
    except Exception as exc:
        error = exc
        completed = False

    local_total = _apply_remote_list_status(
        platform,
        account_id,
        ids,
        remote_names,
        complete=completed,
    )
    _check_jobs[job_key]["total"] = local_total
    _check_jobs[job_key]["done"] = local_total if completed else 0
    if completed:
        state.log_write(
            f"[remote-list] Compared {local_total} local posts with "
            f"{len(ids)} currently available Douyin posts\n"
        )
        _download_missing_after_check(platform, account_id)
    else:
        reason = f": {error}" if error else ""
        state.log_write(
            f"[remote-list] Douyin enumeration incomplete after returning "
            f"{len(ids)} posts{reason}; positive matches were verified "
            "and unknown posts remain pending\n"
        )
    _check_jobs[job_key]["running"] = False


def _run_ghost_check_bg(
    platform: str, account_id: str, job_key: str, *, force: bool = False,
) -> None:
    import datetime as _dt, subprocess as _sp
    STALE = 7
    today = _dt.date.today().isoformat()
    pcfg  = PLATFORMS.get(platform, {})
    cookies = pcfg.get("cookies_file", "")

    index     = _load_post_index()
    all_posts = {k: v for k, v in index.get(platform, {}).get(account_id, {}).items()
                 if not k.startswith("_")}

    def _needs_check(meta: dict) -> bool:
        if force:
            return True
        s = meta.get("status", "unchecked")
        if s == "gone":
            return False
        if s == "ok":
            try:
                return (_dt.date.today() - _dt.date.fromisoformat(meta["checked"])).days >= STALE
            except Exception:
                pass
        return True

    to_check = [(pid, m) for pid, m in all_posts.items() if _needs_check(m)]
    total = len(to_check)
    _check_jobs[job_key]["total"] = total

    if not to_check:
        _check_jobs[job_key]["running"] = False
        return

    state.log_write(f"[ghost-check] {platform}/{account_id}: {total} posts to check\n")

    def _save_result(post_id: str, status: str) -> None:
        with _post_index_lock:
            idx = _load_post_index()
            acc = idx.setdefault(platform, {}).setdefault(account_id, {})
            if post_id in acc:
                # Request failure is not proof of remote deletion.
                files = acc[post_id].get("files") or [
                    acc[post_id].get("file", "")
                ]
                has_local = any(
                    _indexed_local_file_exists(file) for file in files if file
                )
                if status == "gone" and not has_local:
                    del acc[post_id]
                else:
                    acc[post_id]["status"] = (
                        "unchecked" if status == "error" and has_local
                        else "missing" if not has_local
                        else status
                    )
                    acc[post_id]["checked"] = (
                        "" if status == "error" else today
                    )
            _save_post_index(idx)
        _check_jobs[job_key]["done"] = _check_jobs[job_key].get("done", 0) + 1
        state.log_write(f"[ghost-check] {post_id}: {status}\n")

    if platform == "xiaohongshu":
        sys.path.insert(0, str(_HELPERS))
        import xiaohongshu_user as _xhs_user
        try:
            from xhs_cli.client import XhsClient
            xhs_cookies = _xhs_user.load_netscape_cookies(cookies)
            with XhsClient(xhs_cookies, request_delay=1.0, max_retries=2) as client:
                for post_id, _ in to_check:
                    try:
                        detail = client.get_note_detail(post_id)
                        # Feed responses contain `items`; HTML fallback returns
                        # the note object directly. Either non-empty form means
                        # the remote post is available.
                        status = "ok" if detail else "gone"
                    except Exception:
                        status = "error"
                    _save_result(post_id, status)
        except Exception as exc:
            state.log_write(f"[ghost-check] Xiaohongshu error: {exc}\n")

    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _check_one(post_id: str) -> tuple[str, str]:
            try:
                if platform == "bilibili":
                    r = _sp.run(
                        _yt_dlp_command() + ["--simulate", "--no-warnings",
                         "--cookies", cookies, f"https://www.bilibili.com/video/{post_id}"],
                        capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
                    )
                    if r.returncode == 0:
                        return post_id, "ok"
                    out = r.stdout + r.stderr
                    if any(k in out.lower() for k in ("deleted", "unavailable", "private", "404", "not exist")):
                        return post_id, "gone"
                    return post_id, "error"

                elif platform == "x":
                    url = f"https://x.com/i/status/{post_id}"
                    r   = _sp.run(
                        [_GALLERY_DL, "--simulate", "--cookies", cookies, url],
                        capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
                    )
                    out = r.stdout + r.stderr
                    if "[error]" in out.lower() and any(
                            k in out for k in ("not found", "deleted", "suspended",
                                               "unavailable", "TweetUnavailable")):
                        return post_id, "gone"
                    return post_id, "ok" if r.returncode == 0 else "error"
            except Exception:
                pass
            return post_id, "error"

        with ThreadPoolExecutor(max_workers=5) as pool:
            futs = {pool.submit(_check_one, pid): pid for pid, _ in to_check}
            for fut in as_completed(futs):
                post_id, status = fut.result()
                _save_result(post_id, status)

    done = _check_jobs[job_key].get("done", 0)
    state.log_write(f"[ghost-check] Finished: {done}/{total} checked\n")
    _download_missing_after_check(platform, account_id)
    _check_jobs[job_key]["running"] = False


def _download_missing_after_check(platform: str, account_id: str) -> None:
    """Automatically download remote-only posts found by a successful check."""
    index = _load_post_index()
    account = index.get(platform, {}).get(account_id, {})
    missing_ids = [
        post_id for post_id, meta in account.items()
        if not post_id.startswith("_") and meta.get("status") == "missing"
    ]
    if not missing_ids:
        return
    entry = next(
        (
            item for item in state._store.all_entries()
            if item.platform == platform
            and item.handle.split("|")[-1] == account_id
        ),
        None,
    )
    if entry is None:
        state.log_write(
            f"[missing] Cannot auto-download {platform}/{account_id}: "
            "tracked account not found\n"
        )
        return
    if state.running:
        state.log_write(
            f"[missing] {len(missing_ids)} posts found, but another "
            "operation is running; leaving them marked Missing\n"
        )
        return
    target = _ensure_entry_download_folder(
        entry, state._store, state._download_root()
    )
    display = entry.handle.split("|")[0]
    state.log_write(
        f"[missing] Re-check found {len(missing_ids)} remote-only posts; "
        "downloading automatically\n"
    )
    state.running = True
    state.stop_flag.clear()
    state.status = "Downloading missing posts…"
    _missing_posts_worker(
        platform, account_id, display, missing_ids, target,
    )


# ── Retroactive index scan ────────────────────────────────────────────────────

def _extract_post_id_and_date(platform: str, f: Path) -> "tuple[str, str] | None":
    """Return (post_id, date_str) from a downloaded file, or None."""
    if platform == "x":
        meta_f = f.with_suffix(".json")
        if meta_f.exists():
            try:
                m   = json.loads(meta_f.read_text("utf-8", errors="replace"))
                tid = str(m.get("tweet_id") or m.get("id") or "")
                if tid.isdigit() and len(tid) >= 15:
                    d = str(m.get("date", ""))[:10]
                    return tid, d
            except Exception:
                pass
        mo = _TWEET_ID_RE.search(f.stem)
        if mo:
            return mo.group(1), ""
    elif platform == "douyin":
        mo = _DOUYIN_ID_RE.search(f.stem)
        if mo:
            return mo.group(1), ""
    elif platform == "bilibili":
        mo = _BVID_RE.search(f.name)
        if mo:
            return mo.group(1), ""
    elif platform == "xiaohongshu":
        mo = _XHS_NOTE_RE.search(f.stem)
        if mo:
            return mo.group(1).lower(), ""
    return None


_scan_jobs: dict = {}


def _retroactive_index_scan_bg() -> None:
    import datetime as _dt
    import re as _re
    dl_root = state._download_root()
    index   = _load_post_index()
    added   = 0

    for entry in state._store.all_entries():
        platform   = entry.platform
        handle     = entry.handle
        account_id = handle.split("|")[-1] if "|" in handle else handle
        display    = handle.split("|")[0]  if "|" in handle else handle

        # Mirror _run_handle, including per-account subfolders inside groups.
        creator = state._store.get_creator(entry.creator_id) if entry.creator_id else None
        creator_name = creator.name if creator else display
        safe_creator = _re.sub(r'[\\/:*?"<>|]', "_", creator_name).strip()
        safe_account = _re.sub(
            r'[\\/:*?"<>|]', "_", f"{display} [{platform}]"
        ).strip()
        watch_dir = (dl_root / safe_creator / safe_account
                     if creator else dl_root / safe_creator)
        candidate_folders: list[Path] = [watch_dir] if watch_dir.is_dir() else []

        acc_index = index.setdefault(platform, {}).setdefault(account_id, {})

        for folder in candidate_folders:
            for f in folder.rglob("*"):
                if not f.is_file() or f.suffix.lower() not in _MEDIA_EXTS:
                    continue
                result = _extract_post_id_and_date(platform, f)
                if result is None:
                    continue
                post_id, date_str = result
                existing = acc_index.get(post_id)
                if (existing and existing.get("file")
                        and Path(existing["file"]).is_file()):
                    continue
                if not date_str:
                    try:
                        date_str = _dt.datetime.fromtimestamp(
                            f.stat().st_mtime).strftime("%Y-%m-%d")
                    except Exception:
                        date_str = ""
                acc_index[post_id] = {
                    "date":    date_str,
                    "file":    str(f.resolve()),
                    "status":  "unchecked",
                    "checked": "",
                }
                added += 1

    _save_post_index(index)
    state.log_write(f"[index-scan] Done — {added} new posts indexed\n")
    _scan_jobs["latest"] = {"running": False, "added": added}


@app.post("/api/files/open")
def open_file(path: str):
    import subprocess as _sp, sys as _sys, os as _os
    p = Path(path)
    if not p.is_absolute():
        p = (state._download_root() / p).resolve()
    if not p.exists():
        # Legacy entries stored bare filenames — search by name in download root
        matches = list(state._download_root().rglob(p.name))
        if not matches:
            raise HTTPException(404, "File not found")
        p = matches[0]
    if _sys.platform == "win32":
        _os.startfile(str(p))
    elif _sys.platform == "darwin":
        _sp.Popen(["open", str(p)])
    else:
        _sp.Popen(["xdg-open", str(p)])
    return {"ok": True}


@app.post("/api/downloads/open")
def open_downloads_folder():
    import subprocess as _sp, sys as _sys
    folder = state._download_root()
    folder.mkdir(parents=True, exist_ok=True)
    if _sys.platform == "win32":
        _sp.Popen(["explorer", str(folder)], creationflags=_NO_WINDOW)
    elif _sys.platform == "darwin":
        _sp.Popen(["open", str(folder)])
    else:
        _sp.Popen(["xdg-open", str(folder)])
    return {"ok": True}


@app.post("/api/browse/folder")
def browse_folder():
    try:
        import webview as _wv
        result = _wv.windows[0].create_file_dialog(_wv.FOLDER_DIALOG)
        if result:
            return {"path": result[0]}
    except Exception:
        pass
    return {"path": None}


@app.get("/api/index/scan")
def get_scan_status_ep():
    return _scan_jobs.get("latest", {"running": False, "added": 0})


@app.post("/api/index/scan")
def trigger_scan():
    if _scan_jobs.get("latest", {}).get("running"):
        raise HTTPException(400, "Scan already running")
    _scan_jobs["latest"] = {"running": True, "added": 0}
    threading.Thread(target=_retroactive_index_scan_bg, daemon=True).start()
    return {"ok": True}


# ── Log WebSocket ─────────────────────────────────────────────────────────────

@app.get("/api/logs")
def get_logs(after: int = 0):
    """Reliable polling fallback for environments where WebSockets are blocked."""
    with state._log_lock:
        events = [{"seq": seq, "text": text, "account_key": account_key}
                  for seq, text, account_key in state._log_history if seq > after]
        last = state._log_sequence
    return {"last": last, "events": events}


@app.websocket("/ws/log")
async def log_ws(ws: WebSocket):
    await ws.accept()
    q: asyncio.Queue[str] = asyncio.Queue()
    with state._log_lock:
        backlog = list(state._log_backlog)
        state._log_backlog.clear()
        state._log_listeners.append(q)
    try:
        for text in backlog:
            await ws.send_text(text)
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=20.0)
                await ws.send_text(msg)
            except asyncio.TimeoutError:
                await ws.send_text("__ping__")
    except WebSocketDisconnect:
        pass
    finally:
        with state._log_lock:
            if q in state._log_listeners:
                state._log_listeners.remove(q)


# ── SPA catch-all (must be last) ──────────────────────────────────────────────

from viewer.app import app as viewer_app
app.mount("/viewer", viewer_app, name="viewer")


_UI_DIST = Path(__file__).resolve().parent.parent / "ui" / "dist"


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    if not _UI_DIST.exists():
        raise HTTPException(404, "UI not built — run: cd ui && npm run build")
    target = _UI_DIST / full_path
    path = str(target) if target.is_file() else str(_UI_DIST / "index.html")
    resp = FileResponse(path)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp
