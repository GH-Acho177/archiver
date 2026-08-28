"""Resolve the runtime profile directory for source and packaged launches."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _selection_file() -> Path:
    # Keep the pointer outside both the program folder and the selected profile.
    # The hash makes the source checkout and each installed copy independently
    # selectable, which is useful when running a stable and development build.
    identity = str(application_root()).casefold().encode("utf-8")
    key = hashlib.sha256(identity).hexdigest()[:12]
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".local" / "share"
    return base / "Archiver" / "profiles" / f"{key}.txt"


def selected_config_dir() -> Path:
    override = os.environ.get("ARCHIVER_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    try:
        selected = _selection_file().read_text("utf-8").strip()
        if selected:
            return Path(selected).expanduser().resolve()
    except OSError:
        pass
    return (application_root() / "config").resolve()


def save_selected_config_dir(directory: str | Path) -> Path:
    target = Path(directory).expanduser()
    if not target.is_absolute():
        target = application_root() / target
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise OSError("the selected path is not a directory")

    # Check now rather than discovering an unavailable/read-only profile after
    # restart. Avoid tempfile.NamedTemporaryFile because some Windows security
    # products transiently lock deleted temp files.
    probe = target / f".archiver-write-test-{os.getpid()}"
    try:
        probe.write_text("ok", "utf-8")
    finally:
        try:
            probe.unlink()
        except OSError:
            pass

    pointer = _selection_file()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = pointer.with_suffix(".tmp")
    temporary.write_text(str(target), "utf-8")
    temporary.replace(pointer)
    return target


CONFIG_DIR = selected_config_dir()
