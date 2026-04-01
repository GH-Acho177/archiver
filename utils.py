import tkinter as tk
from pathlib import Path

from config import _LOG_TAGS


# ── Thread-safe stdout → Text widget ──────────────────────────────────────────
class TextRedirector:
    def __init__(self, widget, app):
        self.widget = widget
        self.app    = app

    def write(self, text):
        self.app.after(0, self._append, text)

    def _append(self, text):
        widget = self.widget
        widget.configure(state="normal")

        tag = ""
        for t, keywords in _LOG_TAGS.items():
            if any(k in text for k in keywords):
                tag = t
                break

        widget.insert(tk.END, text, tag if tag else ())
        widget.see(tk.END)
        widget.configure(state="disabled")

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


# ── Safe file deletion ─────────────────────────────────────────────────────────
def _del(p: Path):
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
