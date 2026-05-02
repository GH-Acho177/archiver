"""
FastAPI backend for the React UI.
Run with:  python run_api.py
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import sys, os

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

from src.creator_store import CreatorStore
from src.config import (
    APP_VERSION, DOWNLOAD_PATH_FILE, PLATFORMS,
    SETTINGS_FILE, UPDATE_HISTORY_FILE, POST_INDEX_FILE, GDL, ARCHIVES_DIR,
    _MEDIA_EXTS,
)

_NO_WINDOW = 0x08000000  # Windows CREATE_NO_WINDOW flag (ignored on non-Windows)

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

# ── Corrupt-file detection ────────────────────────────────────────────────────

_MIN_VIDEO_BYTES = 50 * 1024  # 50 KB minimum — anything smaller is truncated
_MP4_BOX_TYPES   = {b"ftyp", b"mdat", b"moov", b"free", b"wide", b"skip", b"pdin"}


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


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


class _PrintCapture:
    """Routes print() calls from f2 library code through log_write."""
    def __init__(self, log_fn):
        self._log = log_fn
    def write(self, text: str) -> int:
        if text:
            self._log(text)
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

        self._log_listeners: list[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None

        self._total_downloads: int = self._compute_total_downloads()
        self._sched_thread: threading.Thread | None = None
        self._sched_stop:   threading.Event = threading.Event()
        self._next_sync_at: float = 0.0

        self._tg_bot   = None
        self._tg_status = "stopped"

        # Auto-start scheduler and bot if previously enabled
        cfg = self._load_settings()
        if cfg.get("auto_update_enabled"):
            self.start_scheduler(int(cfg.get("auto_update_interval", 60)))
        tg_token = cfg.get("telegram_token", "")
        if tg_token:
            self.start_tg_bot(tg_token)

    # ── Log broadcast ──────────────────────────────────────────────────────────

    def log_write(self, text: str) -> None:
        text = _strip_ansi(text)
        _file_log(text)
        if not isinstance(sys.stdout, _PrintCapture):
            try:
                print(text, end="", flush=True)
            except (UnicodeEncodeError, AttributeError, TypeError):
                pass
        if not self._loop or self._loop.is_closed():
            return
        for q in list(self._log_listeners):
            self._loop.call_soon_threadsafe(q.put_nowait, text)

    # ── Status ─────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
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
        }

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
        self._sched_stop.clear()
        self._sched_thread = threading.Thread(
            target=self._scheduler_worker, args=(interval_minutes,), daemon=True)
        self._sched_thread.start()

    def stop_scheduler(self) -> None:
        self._sched_stop.set()
        self._sched_thread = None

    def _scheduler_worker(self, interval_minutes: int) -> None:
        sleep_secs = interval_minutes * 60
        next_run = time.time() + sleep_secs
        self._next_sync_at = next_run
        self.log_write(f"[scheduler] Next sync in {interval_minutes}m\n")
        while not self._sched_stop.is_set():
            remaining = next_run - time.time()
            if remaining <= 0:
                if not self.running:
                    self.log_write("[scheduler] Starting scheduled sync…\n")
                    self.start(None, None)
                next_run = time.time() + sleep_secs
                self._next_sync_at = next_run
            self._sched_stop.wait(timeout=min(60.0, max(1.0, remaining)))

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
        # Extract first URL from message — handles Douyin share text (Chinese + URL mixed)
        import re as _re
        _url_m = _re.search(r'https?://\S+', text)
        url = _url_m.group(0).rstrip('.,;:)\'"') if _url_m else text.strip()
        pid = _detect_platform(url)
        if pid is None:
            if self._tg_bot:
                self._tg_bot.send_message(chat_id, "⚠ Unrecognised URL. Send an X, Douyin, or Bilibili link.")
            return
        if self.running:
            if self._tg_bot:
                self._tg_bot.send_message(chat_id, "⏳ A sync is already running — try again when it finishes.")
            return
        dl_root = self._download_root()
        pcfg    = PLATFORMS[pid]
        dl      = pcfg["downloader"]
        cookies = pcfg.get("cookies_file", "")
        self.running = True
        self.stop_flag.clear()
        self.status = "Downloading…"
        if self._tg_bot:
            self._tg_bot.send_message(chat_id, f"⬇ Downloading from {PLATFORMS[pid]['label']}…")
        if dl == "f2":
            cookie_str = _parse_cookies(cookies) if cookies and Path(cookies).exists() else ""
            url_dir = str(dl_root / "URL")
            threading.Thread(target=_f2_one_worker,
                args=(url, cookie_str, url_dir),
                daemon=True).start()
            return
        url_dir = str(dl_root / "URL")
        cmd: list[str] = []
        if dl == "gallery-dl":
            cmd = [GDL, "-D", url_dir]
            if cookies and Path(cookies).exists():
                cmd += ["--cookies", cookies]
            cmd.append(url)
        elif dl == "yt-dlp":
            cmd = ["yt-dlp"]
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
              creator_ids: list[str] | None = None) -> dict:
        if self.running:
            return {"error": "Already running"}
        if not self._store.all_entries():
            return {"error": "No entries — add some in Accounts first."}
        if mode:
            self._mode = mode
        if from_days is not None:
            self._from_days = max(0, from_days)
        self._creator_ids = creator_ids or None
        self.running = True
        self.stop_flag.clear()
        self.status = "Running…"
        threading.Thread(target=self._run_worker, daemon=True).start()
        return {"ok": True}

    def stop(self) -> dict:
        if not self.running:
            return {"error": "Not running"}
        self.stop_flag.set()
        self.status = "Stopping…"
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        with self._procs_lock:
            for p in self._procs:
                if p.poll() is None:
                    p.terminate()
        return {"ok": True}

    # ── Worker ─────────────────────────────────────────────────────────────────

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
        if self._from_days > 0:
            from_date = (dt.date.today() - dt.timedelta(days=self._from_days)).isoformat()

        sleep_user = float(cfg.get("sleep_user", 2))
        sleep_req  = float(cfg.get("sleep_req",  1))
        workers    = int(cfg.get("parallel_workers", 1))
        dl_root    = self._download_root()

        self.log_write(f"Mode     : {'Full archive' if full else 'Update'}\n")
        if from_date:
            self.log_write(f"From     : {from_date}\n")
        self.log_write(f"Workers  : {workers}\n")
        self.log_write("─" * 44 + "\n")

        all_results: list[dict] = []
        was_stopped = False
        results_lock = threading.Lock()

        def _sync_platform(pid: str, pcfg: dict) -> None:
            entries = self._store.get_entries_for_download(pid, self._creator_ids)
            if not entries:
                return
            self.log_write(f"\nPlatform : {pcfg['label']} ({len(entries)} accounts)\n")
            for entry in entries:
                if self.stop_flag.is_set():
                    return
                creator = self._store.get_creator(entry.creator_id) if entry.creator_id else None
                display = entry.handle.split("|")[0] if "|" in entry.handle else entry.handle
                creator_name = creator.name if creator else display
                result = self._run_handle(pid, pcfg, entry.handle, full, from_date,
                                          dl_root, cfg, creator_name)
                if result and result.get("count", 0) > 0:
                    with results_lock:
                        all_results.append(result)
                time.sleep(sleep_user)

        platform_items = [(pid, pcfg) for pid, pcfg in PLATFORMS.items()
                          if self._store.get_entries_for_download(pid, self._creator_ids)]

        threads = [threading.Thread(target=_sync_platform, args=(pid, pcfg), daemon=True)
                   for pid, pcfg in platform_items]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if self.stop_flag.is_set():
            was_stopped = True

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
                    creator_name: str) -> "dict | None":
        import subprocess
        import datetime as _dt

        url     = pcfg["url_fn"](handle)
        dl      = pcfg["downloader"]
        cookies = pcfg.get("cookies_file", "")
        display = handle.split("|")[0] if "|" in handle else handle

        self.log_write(f"→ {handle}\n")

        safe_creator = re.sub(r'[\\/:*?"<>|]', "_", creator_name).strip()
        watch_dir    = dl_root / safe_creator
        watch_dir.mkdir(parents=True, exist_ok=True)

        before = set(watch_dir.rglob("*")) if watch_dir.exists() else set()

        # ── f2 (Douyin) — Python library, not CLI ─────────────────────────────
        if dl == "f2":
            import asyncio as _aio
            sys.path.insert(0, str(_HELPERS))
            try:
                import f2_user as _f2_user
            except Exception as _ie:
                import traceback as _tb
                msg = f"[error] f2_user import failed: {_ie}\n{_tb.format_exc()}"
                self.log_write(msg)
                _file_log(msg)
                return None

            today      = _dt.date.today().isoformat()
            interval   = f"{from_date}|{today}" if from_date else "all"
            cookie_str = _parse_cookies(cookies) if cookies and Path(cookies).exists() else ""
            safe_h     = re.sub(r'[\\/:*?"<>|]', "_", handle).strip()
            arc_f2     = str(Path(ARCHIVES_DIR) / f"douyin_{safe_h}.txt")

            old_stdout = sys.stdout
            sys.stdout = _PrintCapture(self.log_write)
            try:
                _aio.run(_f2_user.download_user(
                    url, cookie_str, str(watch_dir), interval,
                    stop_check=self.stop_flag.is_set,
                    full=full,
                    archive_file=arc_f2,
                    sleep_req=sleep_req,
                ))
            except Exception as exc:
                import traceback as _tb
                msg = f"[error] f2 download failed: {exc}\n{_tb.format_exc()}"
                self.log_write(msg)
                _file_log(msg)
            finally:
                sys.stdout = old_stdout

        else:
            # ── Subprocess downloaders ─────────────────────────────────────────
            safe_handle_base = re.sub(r'[\\/:*?"<>|]', "_", handle).strip()
            arc_dir = Path(ARCHIVES_DIR)
            arc     = str(arc_dir / f"{pid}_{safe_handle_base}.db")
            cmd: list[str] = []

            if dl == "gallery-dl":
                cmd = [GDL, "-D", str(watch_dir)]
                if not full:
                    cmd += ["--download-archive", arc]
                if from_date:
                    cmd += ["-o", f"extractor.date-min={from_date}T00:00:00"]
                if cookies and Path(cookies).exists():
                    cmd += ["--cookies", cookies]
                cmd += ["-o", "filename={date:%Y-%m-%d}_{tweet_id}_{num}.{extension}"]
                cmd.append(url)

            elif dl == "yt-dlp":
                arc_txt = str(arc_dir / f"{pid}_{safe_handle_base}.txt")
                cmd = ["yt-dlp"]
                if not full:
                    cmd += ["--download-archive", arc_txt, "--break-on-existing"]
                if from_date:
                    cmd += ["--dateafter", from_date.replace("-", "")]
                if cookies and Path(cookies).exists():
                    cmd += ["--cookies", cookies]
                cmd += ["-P", str(watch_dir), "--windows-filenames",
                        "-o", "%(upload_date>%Y-%m-%d)s_%(id)s_%(title)s.%(ext)s",
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
                        self.log_write(line)
                        if dl == "gallery-dl" and not full and line.startswith("# "):
                            proc.terminate()
                            self.log_write("  → Up to date.\n")
                            break
                        if self.stop_flag.is_set():
                            proc.terminate()
                            break
                    proc.wait()
                    with self._procs_lock:
                        if proc in self._procs:
                            self._procs.remove(proc)
                except FileNotFoundError as exc:
                    self.log_write(f"[error] {dl} not found — check PATH or bundled exe: {exc}\n")
                except Exception as exc:
                    self.log_write(f"[error] {dl} subprocess failed: {exc}\n")

        after     = set(watch_dir.rglob("*")) if watch_dir.exists() else set()
        new_files = [f for f in (after - before)
                     if f.is_file() and f.suffix.lower() in _MEDIA_EXTS]

        # Check only newly downloaded files for corruption
        corrupt_files: list[Path] = [f for f in new_files if not _is_valid_video(f)]
        if corrupt_files:
            bad_ids: set[str] = set()
            for f in corrupt_files:
                self.log_write(f"  [corrupt] {f.name} — removed for re-download\n")
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

        # Index newly downloaded files immediately
        if new_files:
            import datetime as _dt
            account_id = handle.split("|")[-1] if "|" in handle else handle
            idx = _load_post_index()
            acc = idx.setdefault(pid, {}).setdefault(account_id, {})
            changed = False
            for f in new_files:
                result = _extract_post_id_and_date(pid, f)
                if result is None:
                    continue
                post_id, date_str = result
                if post_id not in acc:
                    if not date_str:
                        try:
                            date_str = _dt.datetime.fromtimestamp(
                                f.stat().st_mtime).strftime("%Y-%m-%d")
                        except Exception:
                            date_str = ""
                    acc[post_id] = {
                        "date": date_str, "file": str(f.resolve()),
                        "status": "ok", "checked": "",
                    }
                    changed = True
            if changed:
                _save_post_index(idx)

        return {
            "platform": pid,
            "handle":   handle,
            "display":  display,
            "count":    len(new_files),
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
                return Path(p)
        except Exception:
            pass
        return Path("downloads")


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
    yield


app = FastAPI(title="Archiver API", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Status ────────────────────────────────────────────────────────────────────

@app.get("/api/status")
def get_status():
    return state.get_status()


# ── Bot control ───────────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    mode:        Optional[str]       = None
    from_days:   Optional[int]       = None
    creator_ids: Optional[list[str]] = None


@app.post("/api/start")
def start(req: StartRequest = StartRequest()):
    result = state.start(req.mode, req.from_days, req.creator_ids)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/stop")
def stop():
    result = state.stop()
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
    return {"id": e.id, "platform": e.platform,
            "handle": e.handle, "creator_id": e.creator_id}


@app.delete("/api/accounts/entries/{entry_id}", status_code=204)
def remove_entry(entry_id: str):
    state._store.remove_entry(entry_id)


class AssignRequest(BaseModel):
    creator_id: Optional[str] = None


@app.patch("/api/accounts/entries/{entry_id}")
def assign_entry(entry_id: str, req: AssignRequest):
    state._store.assign_entry(entry_id, req.creator_id)
    return {"ok": True}


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


@app.patch("/api/accounts/creators/{creator_id}")
def rename_creator(creator_id: str, req: RenameCreatorRequest):
    state._store.rename_creator(creator_id, req.name)
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
        s["download_path"] = ""
    return s


class SaveSettingsRequest(BaseModel):
    download_path:        Optional[str]   = None
    parallel_workers:     Optional[int]   = None
    sleep_user:           Optional[float] = None
    sleep_req:            Optional[float] = None
    auto_update_enabled:  Optional[bool]  = None
    auto_update_interval: Optional[int]   = None


@app.put("/api/settings")
def save_settings(req: SaveSettingsRequest):
    s: dict = {}
    try:
        s = json.loads(Path(SETTINGS_FILE).read_text("utf-8"))
    except Exception:
        pass
    data = req.model_dump(exclude_none=True)
    dp = data.pop("download_path", None)
    s.update(data)
    Path(SETTINGS_FILE).write_text(json.dumps(s, indent=2, ensure_ascii=False), "utf-8")
    if dp is not None:
        Path(DOWNLOAD_PATH_FILE).write_text(dp, "utf-8")
    # Start/stop scheduler based on saved settings
    if s.get("auto_update_enabled"):
        state.start_scheduler(int(s.get("auto_update_interval", 60)))
    else:
        state.stop_scheduler()
    return {"ok": True}


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

def _detect_platform(url: str) -> Optional[str]:
    u = url.lower()
    if any(x in u for x in ("x.com/", "twitter.com/")):
        return "x"
    if any(x in u for x in ("douyin.com/", "v.douyin.com/", "iesdouyin.com/")):
        return "douyin"
    if any(x in u for x in ("bilibili.com/", "b23.tv/")):
        return "bilibili"
    return None


class DownloadUrlRequest(BaseModel):
    url: str


@app.post("/api/download")
def download_url(req: DownloadUrlRequest):
    pid = _detect_platform(req.url)
    if pid is None:
        raise HTTPException(400, "Unrecognised URL — supports X, Douyin, Bilibili")
    if state.running:
        raise HTTPException(400, "A sync is already running — stop it first")
    dl_root = state._download_root()
    pcfg    = PLATFORMS[pid]
    dl      = pcfg["downloader"]
    cookies = pcfg.get("cookies_file", "")
    url_dir = str(dl_root / "URL")
    # f2 (Douyin single video) — library call
    if dl == "f2":
        cookie_str = _parse_cookies(cookies) if cookies and Path(cookies).exists() else ""
        state.running = True
        state.stop_flag.clear()
        state.status = "Downloading…"
        threading.Thread(target=_f2_one_worker, args=(req.url, cookie_str, url_dir), daemon=True).start()
        return {"ok": True, "platform": pid}

    cmd: list[str] = []
    if dl == "gallery-dl":
        cmd = [GDL, "-D", url_dir]
        if cookies and Path(cookies).exists():
            cmd += ["--cookies", cookies]
        cmd.append(req.url)
    elif dl == "yt-dlp":
        cmd = ["yt-dlp"]
        if cookies and Path(cookies).exists():
            cmd += ["--cookies", cookies]
        cmd += ["-P", url_dir, "--windows-filenames",
                "-o", "%(upload_date>%Y-%m-%d)s_%(id)s_%(title)s.%(ext)s", req.url]

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

_PLAT_DIRS = {"x", "bilibili", "douyin"}


@app.get("/api/downloads")
def list_downloads(limit: int = 500):
    import datetime as _dt
    base = state._download_root()
    rows = []
    if base.exists():
        for f in base.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in _MEDIA_EXTS:
                continue
            try:
                parts = f.relative_to(base).parts
                plat  = parts[0] if parts and parts[0] in _PLAT_DIRS else "—"
                st    = f.stat()
                rows.append({
                    "name":     f.name,
                    "path":     str(f),
                    "platform": plat,
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


def _f2_one_worker(url_or_id: str, cookie_str: str, outdir: str) -> None:
    import asyncio as _aio
    sys.path.insert(0, str(_HELPERS))
    try:
        # Resolve aweme_id: if it's already a pure numeric ID use it directly,
        # otherwise let f2's AwemeIdFetcher resolve any URL (short or long).
        if url_or_id.isdigit():
            aweme_id = url_or_id
        else:
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
                proc.terminate()
                break
        proc.wait()
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
    await loop.run_in_executor(None, _fetch_avatar_bg, platform, account_id)
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


def _fetch_avatar_bg(platform: str, account_id: str) -> None:
    import ssl, urllib.request, json as _j, http.client, subprocess as _sp, re as _re
    cache = Path("config/avatars") / f"{platform}_{account_id}.png"
    if cache.exists():
        return
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
                [GDL, "--cookies", str(cf), "--simulate", "--dump-json",
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

        if url:
            urllib.request.urlretrieve(url, str(cache))
    except Exception:
        pass


# ── Posts & ghost check ───────────────────────────────────────────────────────

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


@app.get("/api/posts/{platform}/{account_id}")
def get_posts(platform: str, account_id: str):
    index = _load_post_index()
    raw   = {k: v for k, v in index.get(platform, {}).get(account_id, {}).items()
             if not k.startswith("_")}
    posts = sorted(
        [{"post_id": pid, "date": m.get("date", ""), "file": m.get("file", ""),
          "status": m.get("status", "unchecked"), "checked": m.get("checked", "")}
         for pid, m in raw.items()],
        key=lambda x: x["date"], reverse=True,
    )
    return posts


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
    threading.Thread(target=_run_ghost_check_bg, args=(platform, account_id, key), daemon=True).start()
    return {"ok": True}


def _run_ghost_check_bg(platform: str, account_id: str, job_key: str) -> None:
    import datetime as _dt, subprocess as _sp
    STALE = 7
    today = _dt.date.today().isoformat()
    pcfg  = PLATFORMS.get(platform, {})
    cookies = pcfg.get("cookies_file", "")

    index     = _load_post_index()
    all_posts = {k: v for k, v in index.get(platform, {}).get(account_id, {}).items()
                 if not k.startswith("_")}

    def _needs_check(meta: dict) -> bool:
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
        if status == "error":
            return
        idx = _load_post_index()
        acc = idx.setdefault(platform, {}).setdefault(account_id, {})
        if post_id in acc:
            acc[post_id]["status"]  = status
            acc[post_id]["checked"] = today
        _save_post_index(idx)
        _check_jobs[job_key]["done"] = _check_jobs[job_key].get("done", 0) + 1
        state.log_write(f"[ghost-check] {post_id}: {status}\n")

    if platform == "douyin":
        import asyncio as _aio
        sys.path.insert(0, str(_HELPERS))
        try:
            from f2.apps.douyin.handler import DouyinHandler
            from f2.apps.douyin.utils import ClientConfManager
            kw = {
                "cookie": _parse_cookies(cookies), "languages": "en_US",
                "timeout": 30, "max_retries": 2, "max_connections": 5, "max_tasks": 5,
                "page_counts": 20, "max_counts": None, "headers": ClientConfManager.headers(),
            }
            handler = DouyinHandler(kw)

            async def _check_all():
                sem = _aio.Semaphore(5)
                async def _one(post_id: str, meta: dict):
                    async with sem:
                        try:
                            data   = (await handler.fetch_one_video(post_id))._to_dict()
                            status = ("ok" if isinstance(data, dict) and
                                      str(data.get("aweme_id", "")) == str(post_id) else "gone")
                        except Exception:
                            status = "error"
                    _save_result(post_id, status)
                await _aio.gather(*[_one(pid, m) for pid, m in to_check])

            _aio.run(_check_all())
        except Exception as exc:
            state.log_write(f"[ghost-check] Error: {exc}\n")

    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _check_one(post_id: str) -> tuple[str, str]:
            try:
                if platform == "bilibili":
                    r = _sp.run(
                        ["yt-dlp", "--simulate", "--no-warnings",
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
                        [GDL, "--simulate", "--cookies", cookies, url],
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
    _check_jobs[job_key]["running"] = False


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

        # Mirror _run_handle: files live in downloads/<safe_creator>/
        creator = state._store.get_creator(entry.creator_id) if entry.creator_id else None
        creator_name = creator.name if creator else display
        safe_creator = _re.sub(r'[\\/:*?"<>|]', "_", creator_name).strip()
        watch_dir = dl_root / safe_creator
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
                if post_id in acc_index:
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
                    "status":  "ok",
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

@app.websocket("/ws/log")
async def log_ws(ws: WebSocket):
    await ws.accept()
    q: asyncio.Queue[str] = asyncio.Queue()
    state._log_listeners.append(q)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=20.0)
                await ws.send_text(msg)
            except asyncio.TimeoutError:
                await ws.send_text("__ping__")
    except WebSocketDisconnect:
        pass
    finally:
        if q in state._log_listeners:
            state._log_listeners.remove(q)


# ── SPA catch-all (must be last) ──────────────────────────────────────────────

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
