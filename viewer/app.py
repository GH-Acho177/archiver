from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import random
import re
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager, closing
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config_root import CONFIG_DIR


ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[1]
)
STATIC = Path(__file__).resolve().parent / "static"
INDEX_FILE = CONFIG_DIR / "post_index.json"
CREATORS_FILE = CONFIG_DIR / "creators.json"
DB_FILE = CONFIG_DIR / "viewer.db"
DOWNLOAD_PATH_FILE = CONFIG_DIR / "download_path.txt"
MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv", ".m4v",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv", ".m4v"}
ROLE_SUFFIX = re.compile(r"_(?:video|image_\d+|live_\d+)$", re.I)
LEADING_META = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}_)?(?:BV[0-9A-Za-z]+|\d{10,20}|[0-9a-f]{24})_?",
    re.I,
)


class ViewerIndex:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.posts: list[dict] = []
        self.media: dict[str, Path] = {}
        self.sequences: dict[tuple[str, str, str, str], list[str]] = {}

    def rebuild(self) -> int:
        creators, accounts = _account_metadata()
        raw = _read_json(INDEX_FILE)
        posts: list[dict] = []
        post_by_key: dict[tuple[str, str, str], dict] = {}
        media_map: dict[str, Path] = {}
        represented: set[Path] = set()
        folder_accounts = {
            (platform, _safe_folder(str(meta.get("name") or ""))): account_id
            for (platform, account_id), meta in accounts.items()
        }

        for platform, account_map in raw.items() if isinstance(raw, dict) else []:
            if not isinstance(account_map, dict):
                continue
            for account_id, post_map in account_map.items():
                if not isinstance(post_map, dict):
                    continue
                account = accounts.get((platform, account_id), {})
                for post_id, meta in post_map.items():
                    if str(post_id).startswith("_") or not isinstance(meta, dict):
                        continue
                    paths = meta.get("files") or [meta.get("file", "")]
                    valid = _valid_media_paths(paths)
                    if not valid:
                        continue
                    represented.update(path.resolve() for path in valid)
                    post = _make_post(
                        platform, str(account_id), str(post_id), valid,
                        date=str(meta.get("date") or ""), account=account,
                        creators=creators,
                    )
                    posts.append(post)
                    post_by_key[(platform, str(account_id), str(post_id))] = post

        # Include valid archive files that have not yet reached post_index.json.
        download_root = _download_root()
        if download_root.is_dir():
            loose: dict[tuple[str, str, str], list[Path]] = {}
            for path in download_root.rglob("*"):
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if (not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS
                        or resolved in represented
                        or "duplicated" in {part.casefold() for part in path.parts}):
                    continue
                platform = _platform_from_path(path)
                post_id = _post_id_from_name(path.stem) or _token(str(resolved))[:16]
                account_name = path.parent.name.removesuffix(f" [{platform}]")
                account_id = folder_accounts.get(
                    (platform, _safe_folder(account_name)), account_name,
                )
                existing = post_by_key.get((platform, account_id, post_id))
                if existing is not None:
                    _append_media(existing, path)
                    represented.add(resolved)
                else:
                    loose.setdefault((platform, account_id, post_id), []).append(path)
            for (platform, account_name, post_id), paths in loose.items():
                post = _make_post(
                    platform, account_name, post_id, paths,
                    date="", account={"name": account_name}, creators=creators,
                )
                posts.append(post)
                post_by_key[(platform, account_name, post_id)] = post

        posts.sort(key=lambda item: (item["date"], item["modified"]), reverse=True)
        for post in posts:
            for item in post["media"]:
                media_map[item["token"]] = Path(item.pop("path"))
        with self.lock:
            self.posts = posts
            self.media = media_map
            self.sequences.clear()
        return len(posts)


index = ViewerIndex()
db_lock = threading.RLock()
_deletion_worker_started = False
_deletion_process_lock = threading.Lock()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _download_root() -> Path:
    try:
        configured = DOWNLOAD_PATH_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        configured = ""
    return Path(configured) if configured else ROOT / "downloads"


def _account_metadata() -> tuple[dict[str, str], dict[tuple[str, str], dict]]:
    data = _read_json(CREATORS_FILE)
    creators = {
        str(item.get("id")): str(item.get("name") or "")
        for item in data.get("creators", []) if isinstance(item, dict)
    }
    accounts: dict[tuple[str, str], dict] = {}
    for item in data.get("entries", []):
        if not isinstance(item, dict):
            continue
        platform = str(item.get("platform") or "unknown")
        handle = str(item.get("handle") or "")
        account_id = handle.rsplit("|", 1)[-1]
        accounts[(platform, account_id)] = {
            "name": handle.split("|", 1)[0] or account_id,
            "creator_id": item.get("creator_id"),
        }
    return creators, accounts


def _valid_media_paths(values) -> list[Path]:
    result = []
    for value in values if isinstance(values, list) else []:
        try:
            path = Path(str(value))
            if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
                result.append(path)
        except OSError:
            pass
    return sorted(set(result), key=lambda path: path.name.casefold())


def _token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _safe_folder(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", value).strip().casefold()


def _append_media(post: dict, path: Path) -> None:
    token = _token(str(path.resolve()))
    if any(item["token"] == token for item in post["media"]):
        return
    post["media"].append({
        "token": token, "path": str(path.resolve()), "name": path.name,
        "kind": "video" if path.suffix.lower() in VIDEO_EXTENSIONS else "image",
        "url": f"api/media/{token}",
    })
    post["media"].sort(key=lambda item: item["name"].casefold())
    try:
        post["modified"] = max(post["modified"], path.stat().st_mtime)
    except OSError:
        pass


def _title(paths: list[Path], post_id: str) -> str:
    stem = ROLE_SUFFIX.sub("", paths[0].stem)
    stem = LEADING_META.sub("", stem).strip(" _-")
    return stem.replace("_#", "  #").replace("_", " ").strip() or f"Post {post_id}"


def _release_date(paths: list[Path], fallback: str) -> str:
    """Prefer the naming-policy release date over index/download timestamps."""
    for path in paths:
        match = re.match(r"^(20\d{2})[-_.](\d{2})[-_.](\d{2})(?:_|\b)", path.name)
        if match:
            return "-".join(match.groups())
    return fallback


def _make_post(platform: str, account_id: str, post_id: str, paths: list[Path],
               *, date: str, account: dict, creators: dict[str, str]) -> dict:
    key = f"{platform}:{account_id}:{post_id}"
    modified = max((path.stat().st_mtime for path in paths), default=0)
    media = []
    for path in paths:
        token = _token(str(path.resolve()))
        media.append({
            "token": token, "path": str(path.resolve()),
            "name": path.name,
            "kind": "video" if path.suffix.lower() in VIDEO_EXTENSIONS else "image",
            "url": f"api/media/{token}",
        })
    creator_id = account.get("creator_id")
    return {
        "key": key, "platform": platform, "account_id": account_id,
        "account": account.get("name") or account_id,
        "avatar": f"api/avatar/{platform}/{account_id}",
        "group": creators.get(str(creator_id), "") if creator_id else "",
        "post_id": post_id, "date": _release_date(paths, date),
        "modified": modified,
        "title": _title(paths, post_id), "media": media,
    }


def _platform_from_path(path: Path) -> str:
    match = re.search(r"\[(douyin|bilibili|xiaohongshu|x)\]", str(path.parent), re.I)
    return match.group(1).lower() if match else "unknown"


def _post_id_from_name(stem: str) -> str:
    match = re.search(r"(BV[0-9A-Za-z]{10,}|\d{15,20}|[0-9a-f]{24})", stem, re.I)
    return match.group(1) if match else ""


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_FILE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS post_state (
            post_key TEXT PRIMARY KEY,
            reaction TEXT CHECK(reaction IN ('like', 'dislike') OR reaction IS NULL),
            saved INTEGER NOT NULL DEFAULT 0,
            viewed INTEGER NOT NULL DEFAULT 0,
            last_seen TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS deletion_queue (
            post_key TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL,
            paths_json TEXT NOT NULL,
            delete_after TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        )
    """)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(post_state)")
    }
    if "last_seen" not in columns:
        connection.execute("ALTER TABLE post_state ADD COLUMN last_seen TEXT")
        connection.commit()
    return connection


def _states() -> dict[str, dict]:
    # Load the compact state table rather than passing every archive key into
    # an IN clause; large libraries easily exceed SQLite's parameter limit.
    with db_lock, closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT post_key, reaction, saved, viewed, last_seen FROM post_state",
        ).fetchall()
    return {row["post_key"]: dict(row) for row in rows}


class StateUpdate(BaseModel):
    key: str
    reaction: str | None = None
    saved: bool | None = None
    viewed: bool | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize()
    yield


def initialize() -> int:
    """Initialise durable Viewer state in standalone or mounted mode."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()):
        pass
    total = index.rebuild()
    _start_deletion_worker()
    return total


def _start_deletion_worker() -> None:
    global _deletion_worker_started
    if _deletion_worker_started:
        return
    _deletion_worker_started = True
    threading.Thread(target=_deletion_worker, name="viewer-delete-queue", daemon=True).start()


def _deletion_worker() -> None:
    while True:
        try:
            _process_due_deletions()
        except Exception:
            pass
        threading.Event().wait(2.0)


def _process_due_deletions(force: bool = False) -> None:
    with _deletion_process_lock:
        now = datetime.now(timezone.utc).isoformat()
        with db_lock, closing(_connect()) as connection:
            if force:
                due = connection.execute(
                    "SELECT post_key, paths_json FROM deletion_queue WHERE status='pending'"
                ).fetchall()
            else:
                due = connection.execute(
                    "SELECT post_key, paths_json FROM deletion_queue "
                    "WHERE status='pending' AND delete_after<=?", (now,),
                ).fetchall()
            for row in due:
                connection.execute(
                    "UPDATE deletion_queue SET status='deleting' "
                    "WHERE post_key=? AND status='pending'", (row["post_key"],),
                )
            connection.commit()
        changed = False
        root = _download_root().resolve()
        for row in due:
            errors: list[str] = []
            for value in json.loads(row["paths_json"]):
                try:
                    path = Path(value).resolve()
                    if not path.is_relative_to(root):
                        raise OSError("path is outside the archive")
                    if path.is_file():
                        path.unlink()
                        changed = True
                except (OSError, ValueError) as exc:
                    errors.append(f"{value}: {exc}")
            with db_lock, closing(_connect()) as connection:
                connection.execute(
                    "UPDATE deletion_queue SET status=?, error=?, completed_at=CURRENT_TIMESTAMP "
                    "WHERE post_key=? AND status='deleting'",
                    ("error" if errors else "deleted", "\n".join(errors) or None, row["post_key"]),
                )
                connection.commit()
        if changed:
            index.rebuild()


def flush_pending_deletions() -> None:
    """Finalize every pending disliked-post deletion during a real app quit."""
    _process_due_deletions(force=True)


app = FastAPI(title="Archiver Viewer", version="0.1.0", lifespan=lifespan)


@app.get("/api/feed")
def feed(filter: str = "all", platform: str = "all", account_id: str = "",
         seed: str = "viewer", order: str = "random",
         offset: int = 0, limit: int = 16):
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    with index.lock:
        posts = list(index.posts)
    states = _states()
    result = []
    for post in posts:
        state = states.get(post["key"], {})
        if platform != "all" and post["platform"] != platform:
            continue
        if account_id and post["account_id"] != account_id:
            continue
        if filter == "unseen" and state.get("viewed"):
            continue
        if filter == "liked" and state.get("reaction") != "like":
            continue
        if filter == "saved" and not state.get("saved"):
            continue
        if filter == "disliked" and state.get("reaction") != "dislike":
            continue
        result.append({
            **post,
            "state": {
                "reaction": state.get("reaction"),
                "saved": bool(state.get("saved")),
                "viewed": bool(state.get("viewed")),
                "last_seen": state.get("last_seen"),
            },
        })
    if filter == "disliked":
        present = {post["key"] for post in result}
        with db_lock, closing(_connect()) as connection:
            deleted = connection.execute(
                "SELECT snapshot_json, status, completed_at FROM deletion_queue "
                "WHERE status IN ('deleted', 'error') ORDER BY created_at DESC"
            ).fetchall()
        for row in deleted:
            snapshot = json.loads(row["snapshot_json"])
            if snapshot.get("key") in present:
                continue
            snapshot["media"] = []
            snapshot["deletion"] = {
                "status": row["status"], "completed_at": row["completed_at"],
            }
            snapshot["state"] = {
                "reaction": "dislike", "saved": False,
                "viewed": True, "last_seen": None,
            }
            result.append(snapshot)
    if order != "newest":
        cache_key = (filter, platform, account_id, seed)
        with index.lock:
            sequence = index.sequences.get(cache_key)
        by_key = {post["key"]: post for post in result}
        if sequence is None:
            result = _balanced_random(result, seed)
            sequence = [post["key"] for post in result]
            with index.lock:
                index.sequences[cache_key] = sequence
        else:
            result = [by_key[key] for key in sequence if key in by_key]
            sequenced = set(sequence)
            result.extend(post for post in by_key.values() if post["key"] not in sequenced)
    return {"items": result[offset:offset + limit], "total": len(result)}


@app.get("/api/locate")
def locate_post(path: str):
    """Resolve a local media path to its containing viewer post."""
    try:
        token = _token(str(Path(path).resolve()))
    except (OSError, ValueError):
        raise HTTPException(400, "Invalid media path")
    with index.lock:
        posts = list(index.posts)
    match = next((post for post in posts
                  if any(media["token"] == token for media in post["media"])), None)
    if not match:
        # A recent download may not have reached the in-memory index yet.
        index.rebuild()
        with index.lock:
            posts = list(index.posts)
        match = next((post for post in posts
                      if any(media["token"] == token for media in post["media"])), None)
    if not match:
        raise HTTPException(404, "This media file is not in the Viewer index")
    account_posts = [post for post in posts
                     if post["platform"] == match["platform"]
                     and post["account_id"] == match["account_id"]]
    position = next((i for i, post in enumerate(account_posts) if post["key"] == match["key"]), 0)
    return {
        "key": match["key"], "position": position,
        "platform": match["platform"], "account_id": match["account_id"],
        "account": match["account"], "avatar": match["avatar"],
    }


def _balanced_random(posts: list[dict], seed: str) -> list[dict]:
    """Randomize without allowing accounts with more posts to dominate.

    Every active account contributes one random post per shuffled round. Thus
    the next account is selected uniformly, independent of archive size.
    """
    rng = random.Random(seed)
    buckets: dict[tuple[str, str], list[dict]] = {}
    for post in posts:
        buckets.setdefault((post["platform"], post["account_id"]), []).append(post)
    for bucket in buckets.values():
        # Weighted sampling without replacement. Unseen posts lead; recently
        # seen posts sink; older posts gradually regain probability.
        bucket.sort(
            key=lambda post: _weighted_random_key(
                rng, post["state"].get("last_seen"), post.get("date"),
            ),
            reverse=True,
        )
    ordered: list[dict] = []
    previous = None
    while buckets:
        accounts = list(buckets)
        rng.shuffle(accounts)
        if previous is not None and len(accounts) > 1 and accounts[0] == previous:
            accounts[0], accounts[1] = accounts[1], accounts[0]
        for account in accounts:
            ordered.append(buckets[account].pop())
            previous = account
            if not buckets[account]:
                del buckets[account]
    return ordered


def _weighted_random_key(rng: random.Random, last_seen: str | None,
                         release_date: str | None = None) -> float:
    if not last_seen:
        weight = 20.0
    else:
        try:
            seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (datetime.now(timezone.utc) - seen).total_seconds() / 86400)
            weight = 1.0 + min(12.0, age_days / 14.0)
        except ValueError:
            weight = 2.0
    # Prefer newer releases within each account without allowing accounts with
    # larger archives to dominate the feed. The boost is 4x for a post released
    # today, about 2x after three months, and fades close to neutral after a year.
    if release_date:
        try:
            released = datetime.fromisoformat(str(release_date).replace("Z", "+00:00"))
            if released.tzinfo is None:
                released = released.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (datetime.now(timezone.utc) - released).total_seconds() / 86400)
            weight *= 1.0 + 3.0 * math.exp(-age_days / 90.0)
        except ValueError:
            pass
    return -math.log(max(rng.random(), 1e-12)) / weight


def _queue_post_deletion(post: dict) -> str:
    paths = []
    with index.lock:
        for media in post.get("media", []):
            path = index.media.get(str(media.get("token") or ""))
            if path:
                paths.append(str(path.resolve()))
    delete_after = datetime.now(timezone.utc) + timedelta(minutes=5)
    snapshot = {key: value for key, value in post.items() if key != "state"}
    with db_lock, closing(_connect()) as connection:
        connection.execute("""
            INSERT INTO deletion_queue
              (post_key, snapshot_json, paths_json, delete_after, status, error, completed_at)
            VALUES (?, ?, ?, ?, 'pending', NULL, NULL)
            ON CONFLICT(post_key) DO UPDATE SET
              snapshot_json=excluded.snapshot_json, paths_json=excluded.paths_json,
              delete_after=excluded.delete_after, status='pending',
              error=NULL, completed_at=NULL
        """, (
            post["key"], json.dumps(snapshot, ensure_ascii=False),
            json.dumps(paths, ensure_ascii=False), delete_after.isoformat(),
        ))
        connection.commit()
    return delete_after.isoformat()


def _cancel_post_deletion(post_key: str) -> bool:
    with db_lock, closing(_connect()) as connection:
        cursor = connection.execute(
            "UPDATE deletion_queue SET status='cancelled', completed_at=CURRENT_TIMESTAMP "
            "WHERE post_key=? AND status='pending'", (post_key,),
        )
        connection.commit()
        return cursor.rowcount > 0


@app.post("/api/state")
def update_state(update: StateUpdate):
    with index.lock:
        post = next((item for item in index.posts if item["key"] == update.key), None)
        if post is None:
            raise HTTPException(404, "Post not found")
    if update.reaction not in {None, "like", "dislike"}:
        raise HTTPException(400, "Invalid reaction")
    with db_lock, closing(_connect()) as connection:
        current = connection.execute(
            "SELECT reaction, saved, viewed, last_seen FROM post_state WHERE post_key=?",
            (update.key,),
        ).fetchone()
        reaction = update.reaction if "reaction" in update.model_fields_set else (
            current["reaction"] if current else None
        )
        saved = int(update.saved) if update.saved is not None else int(current["saved"] if current else 0)
        viewed = int(update.viewed) if update.viewed is not None else int(current["viewed"] if current else 0)
        last_seen = (
            datetime.now(timezone.utc).isoformat()
            if update.viewed is True else current["last_seen"] if current else None
        )
        connection.execute("""
            INSERT INTO post_state(post_key, reaction, saved, viewed, last_seen, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(post_key) DO UPDATE SET
              reaction=excluded.reaction, saved=excluded.saved,
              viewed=excluded.viewed, last_seen=excluded.last_seen,
              updated_at=CURRENT_TIMESTAMP
        """, (update.key, reaction, saved, viewed, last_seen))
        connection.commit()
    deletion = None
    if "reaction" in update.model_fields_set:
        if reaction == "dislike":
            deletion = {"status": "pending", "delete_after": _queue_post_deletion(post)}
        elif _cancel_post_deletion(update.key):
            deletion = {"status": "cancelled"}
    return {"ok": True, "state": {
        "reaction": reaction, "saved": bool(saved),
        "viewed": bool(viewed), "last_seen": last_seen,
    }, "deletion": deletion}


@app.get("/api/deletion-queue")
def deletion_queue():
    with db_lock, closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT post_key, snapshot_json, delete_after, status, error, created_at, completed_at "
            "FROM deletion_queue ORDER BY created_at DESC"
        ).fetchall()
    return {"items": [{
        **dict(row), "post": json.loads(row["snapshot_json"]),
    } for row in rows]}


@app.get("/api/stats")
def stats():
    with index.lock:
        total = len(index.posts)
    with db_lock, closing(_connect()) as connection:
        row = connection.execute("""
            SELECT COUNT(*) AS touched,
                   SUM(reaction='like') AS liked,
                   SUM(reaction='dislike') AS disliked,
                   SUM(saved) AS saved, SUM(viewed) AS viewed
            FROM post_state
        """).fetchone()
    return {"total": total, **{key: int(row[key] or 0) for key in ("liked", "disliked", "saved", "viewed")}}


@app.get("/api/accounts/{platform}/{account_id}/related")
def related_accounts(platform: str, account_id: str):
    creators, accounts = _account_metadata()
    current = accounts.get((platform, account_id))
    if not current or not current.get("creator_id"):
        return {"group": "", "accounts": []}
    creator_id = str(current["creator_id"])
    counts: dict[tuple[str, str], int] = {}
    with index.lock:
        for post in index.posts:
            key = (post["platform"], post["account_id"])
            counts[key] = counts.get(key, 0) + 1
    related = []
    for (other_platform, other_id), meta in accounts.items():
        if (other_platform, other_id) == (platform, account_id):
            continue
        if str(meta.get("creator_id") or "") != creator_id:
            continue
        related.append({
            "platform": other_platform,
            "account_id": other_id,
            "account": meta.get("name") or other_id,
            "avatar": f"api/avatar/{other_platform}/{other_id}",
            "posts": counts.get((other_platform, other_id), 0),
        })
    related.sort(key=lambda item: (-item["posts"], item["account"].casefold()))
    return {"group": creators.get(creator_id, ""), "accounts": related}


@app.get("/api/accounts")
def accounts_list():
    creators, accounts = _account_metadata()
    counts: dict[tuple[str, str], int] = {}
    names: dict[tuple[str, str], str] = {}
    with index.lock:
        for post in index.posts:
            key = (post["platform"], post["account_id"])
            counts[key] = counts.get(key, 0) + 1
            names[key] = post["account"]
    result = []
    for platform, account_id in set(accounts) | set(counts):
        meta = accounts.get((platform, account_id), {})
        creator_id = meta.get("creator_id")
        result.append({
            "platform": platform,
            "account_id": account_id,
            "account": meta.get("name") or names.get((platform, account_id)) or account_id,
            "avatar": f"api/avatar/{platform}/{account_id}",
            "group": creators.get(str(creator_id), "") if creator_id else "",
            "posts": counts.get((platform, account_id), 0),
        })
    result.sort(key=lambda item: (item["account"].casefold(), item["platform"]))
    return {"accounts": result}


@app.post("/api/rescan")
def rescan():
    return {"ok": True, "total": index.rebuild()}


@app.get("/api/database")
def database_info():
    with db_lock, closing(_connect()) as connection:
        records = int(connection.execute(
            "SELECT COUNT(*) FROM post_state"
        ).fetchone()[0])
        sqlite_version = str(connection.execute(
            "SELECT sqlite_version()"
        ).fetchone()[0])
    try:
        size = DB_FILE.stat().st_size
    except OSError:
        size = 0
    return {
        "engine": f"SQLite {sqlite_version}",
        "path": str(DB_FILE.resolve()),
        "size": size,
        "records": records,
        "stores": "Likes, dislikes, saves, viewed state, and last-seen time",
    }


@app.get("/api/media/{token}")
def media(token: str):
    with index.lock:
        path = index.media.get(token)
    if not path or not path.is_file():
        raise HTTPException(404, "Media file not found")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0])


@app.get("/api/avatar/{platform}/{account_id}")
def avatar(platform: str, account_id: str):
    safe_platform = re.sub(r"[^a-z0-9_-]", "", platform.casefold())
    safe_account = re.sub(r"[^A-Za-z0-9_-]", "", account_id)
    path = CONFIG_DIR / "avatars" / f"{safe_platform}_{safe_account}.png"
    if path.is_file():
        return FileResponse(path, media_type="image/png")
    # A neutral silhouette is preferable to showing group initials as though
    # they were the account identity.
    svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>
    <rect width='64' height='64' rx='32' fill='#4b4b4b'/>
    <circle cx='32' cy='23' r='12' fill='#bbbbbb'/>
    <path d='M12 57c2-14 10-21 20-21s18 7 20 21' fill='#bbbbbb'/>
    </svg>"""
    return Response(svg, media_type="image/svg+xml")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="viewer")
