import io
import threading
import time
import tkinter as tk
from pathlib import Path

from src.config import _LOG_TAGS


# ── Thread-safe stdout → Text widget ──────────────────────────────────────────
class TextRedirector:
    """
    Collects writes from background threads into a list, then flushes them to
    the Text widget in a single batched operation every _FLUSH_MS milliseconds.
    Replacing the old per-write after(0) approach, which flooded the tkinter
    event queue and made the UI unresponsive during heavy download output.
    """
    _FLUSH_MS = 50

    def __init__(self, widget, app):
        self.widget   = widget
        self.app      = app
        self._pending: list[str] = []  # list.append is GIL-atomic; no lock needed
        self._schedule_flush()

    def write(self, text):
        self._pending.append(text)

    def _schedule_flush(self):
        try:
            self.app.after(self._FLUSH_MS, self._flush)
        except Exception:
            pass

    def _flush(self):
        if self._pending:
            pending, self._pending = self._pending, []
            text = "".join(pending)
            widget = self.widget
            widget.configure(state="normal")
            untagged: list[str] = []
            for line in text.splitlines(keepends=True):
                tag = ""
                for t, keywords in _LOG_TAGS.items():
                    if any(k in line for k in keywords):
                        tag = t
                        break
                if tag:
                    if untagged:
                        widget.insert(tk.END, "".join(untagged))
                        untagged.clear()
                    widget.insert(tk.END, line, tag)
                else:
                    untagged.append(line)
            if untagged:
                widget.insert(tk.END, "".join(untagged))
            widget.see(tk.END)
            widget.configure(state="disabled")
        self._schedule_flush()

    def flush(self):
        pass


# ── Per-line stdout wrapper (prefix + filter + callback) ──────────────────────
class _LineWriter:
    """Wraps a writer; buffers until newline, then applies prefix/filter/callback."""
    def __init__(self, inner, prefix="", skip_fn=None, line_cb=None):
        self._inner  = inner
        self._prefix = prefix
        self._skip   = skip_fn   # (line: str) -> bool  — True = drop
        self._cb     = line_cb   # (line: str) -> None
        self._buf    = ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line += "\n"
            if self._skip and self._skip(line):
                continue
            self._inner.write(self._prefix + line)
            if self._cb:
                self._cb(line)

    def flush(self):
        if self._buf:
            self._inner.write(self._prefix + self._buf)
            self._buf = ""
        self._inner.flush()


# ── Per-task buffering for parallel downloads ─────────────────────────────────
class _TaskBuffer:
    """Collects a task's output into a StringIO; flush_to() dumps atomically."""

    _FLUSH_INTERVAL = 3.0  # seconds between timed flushes

    def __init__(self):
        self._sio          = io.StringIO()
        self._buf          = ""          # partial line accumulator for prefixed writes
        self._flushed_pos  = 0
        self._last_flush_t = time.monotonic()

    # Raw write — no prefix; use for header lines already formatted
    def write_raw(self, text: str):
        self._sio.write(text)

    # Prefixed write — wraps a writer interface so it can be used with _LineWriter
    def make_prefixed_writer(self, prefix="", skip_fn=None, line_cb=None):
        return _LineWriter(self, prefix=prefix, skip_fn=skip_fn, line_cb=line_cb)

    # Writer interface (used by _LineWriter as its inner)
    def write(self, text: str):
        self._sio.write(text)

    def flush(self):
        pass

    def _push_new(self, target, lock: threading.Lock):
        all_content = self._sio.getvalue()
        new_content = all_content[self._flushed_pos:]
        if new_content:
            with lock:
                target.write(new_content)
                target.flush()
            self._flushed_pos  = len(all_content)
            self._last_flush_t = time.monotonic()

    def start_periodic_flush(self, target, lock: threading.Lock):
        """Start a daemon thread that flushes new content every _FLUSH_INTERVAL seconds."""
        self._stop_flush = threading.Event()
        def _loop():
            while not self._stop_flush.wait(timeout=self._FLUSH_INTERVAL):
                self._push_new(target, lock)
        threading.Thread(target=_loop, daemon=True).start()

    def timed_flush_to(self, target, lock: threading.Lock):
        """Push new content to target if the flush interval has elapsed."""
        if time.monotonic() - self._last_flush_t >= self._FLUSH_INTERVAL:
            self._push_new(target, lock)

    def flush_to(self, target, lock: threading.Lock):
        """Stop the periodic flush timer and push all remaining content."""
        if hasattr(self, "_stop_flush"):
            self._stop_flush.set()
        self._push_new(target, lock)


# ── Thread-local stdout router for parallel tasks ─────────────────────────────
class _ThreadRouter:
    """
    sys.stdout drop-in that routes writes per-thread via threading.local.
    Each parallel task calls set_target(writer) so its prints go to its
    own buffer rather than the shared default.
    """
    def __init__(self, default):
        self._default = default
        self._local   = threading.local()

    def write(self, text):
        getattr(self._local, "target", self._default).write(text)

    def flush(self):
        getattr(self._local, "target", self._default).flush()

    def set_target(self, writer):
        self._local.target = writer

    def clear_target(self):
        try:
            del self._local.target
        except AttributeError:
            pass

    @property
    def default(self):
        return self._default


# ── Safe file deletion ─────────────────────────────────────────────────────────
def _del(p: Path):
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
