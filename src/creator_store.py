import json
import threading
import uuid
from pathlib import Path

CREATORS_FILE = "config/creators.json"
UNASSIGNED_ID = "__unassigned__"  # sentinel used in creator_ids lists


def _entry_identity(platform: str, handle: str) -> tuple[str, str]:
    """Return the stable identity used to reject duplicate accounts."""
    pid = platform.strip().casefold()
    value = handle.strip()
    if pid in {"bilibili", "douyin", "xiaohongshu"} and "|" in value:
        value = value.rsplit("|", 1)[-1].strip()
    if pid == "x":
        value = value.lstrip("@").casefold()
    return pid, value


class DuplicateEntryError(ValueError):
    def __init__(self, entry: "Entry"):
        self.entry = entry
        super().__init__(
            f"This {entry.platform} account has already been added: "
            f"{entry.handle.split('|')[0]}"
        )


class Entry:
    __slots__ = ("id", "platform", "handle", "creator_id")

    def __init__(self, id: str, platform: str, handle: str,
                 creator_id: "str | None"):
        self.id         = id
        self.platform   = platform
        self.handle     = handle
        self.creator_id = creator_id


class Creator:
    __slots__ = ("id", "name")

    def __init__(self, id: str, name: str):
        self.id   = id
        self.name = name


class CreatorStore:
    def __init__(self, path: str = CREATORS_FILE):
        self._path     = Path(path)
        self._creators: list[Creator] = []
        self._entries:  list[Entry]   = []
        self._entry_lock = threading.RLock()
        self.load()

    # ── Persistence ────────────────────────────────────────────────────────────

    def load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._creators = [
                Creator(d["id"], d["name"])
                for d in data.get("creators", [])
            ]
            self._entries = [
                Entry(d["id"], d["platform"], d["handle"], d.get("creator_id"))
                for d in data.get("entries", [])
            ]
            before = len(self._creators)
            self._prune_empty_creators()
            if len(self._creators) != before:
                self.save()
        except Exception:
            pass

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "creators": [{"id": c.id, "name": c.name} for c in self._creators],
            "entries": [
                {"id": e.id, "platform": e.platform,
                 "handle": e.handle, "creator_id": e.creator_id}
                for e in self._entries
            ],
        }
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ── Creator queries ────────────────────────────────────────────────────────

    def all_creators(self) -> list[Creator]:
        return list(self._creators)

    def get_creator(self, creator_id: str) -> "Creator | None":
        for c in self._creators:
            if c.id == creator_id:
                return c
        return None

    # ── Entry queries ──────────────────────────────────────────────────────────

    def all_entries(self) -> list[Entry]:
        return list(self._entries)

    def get_entry(self, entry_id: str) -> "Entry | None":
        for e in self._entries:
            if e.id == entry_id:
                return e
        return None

    def find_entry(self, platform: str, handle: str) -> "Entry | None":
        identity = _entry_identity(platform, handle)
        with self._entry_lock:
            for entry in self._entries:
                if _entry_identity(entry.platform, entry.handle) == identity:
                    return entry
        return None

    def get_entries_for_creator(self, creator_id: str) -> list[Entry]:
        return [e for e in self._entries if e.creator_id == creator_id]

    def get_unassigned_entries(self) -> list[Entry]:
        return [e for e in self._entries if e.creator_id is None]

    def get_entries_for_platform(self, platform: str) -> list[Entry]:
        return [e for e in self._entries if e.platform == platform]

    def get_entries_for_download(self, platform: str,
                                 creator_ids: "list[str] | None" = None,
                                 entry_ids: "list[str] | None" = None) -> "list[Entry]":
        """Entries for the download worker.

        creator_ids=None → all entries for the platform.
        Otherwise only entries whose creator_id is in the list (UNASSIGNED_ID
        matches entries with creator_id=None).
        """
        if entry_ids is not None:
            wanted = set(entry_ids)
            return [
                e for e in self._entries
                if e.platform == platform and e.id in wanted
            ]
        if creator_ids is None:
            return [e for e in self._entries if e.platform == platform]
        selected: list[Entry] = []
        for e in self._entries:
            if e.platform != platform:
                continue
            match = (e.creator_id in creator_ids or
                     (e.creator_id is None and UNASSIGNED_ID in creator_ids))
            if match:
                selected.append(e)
        return selected

    # ── Creator mutations ──────────────────────────────────────────────────────

    def add_creator(self, name: str) -> Creator:
        c = Creator(_short_id(), name)
        self._creators.append(c)
        self.save()
        return c

    def rename_creator(self, creator_id: str, name: str) -> None:
        c = self.get_creator(creator_id)
        if c:
            c.name = name
            self.save()

    def remove_creator(self, creator_id: str) -> None:
        """Delete creator; its entries become unassigned."""
        for e in self._entries:
            if e.creator_id == creator_id:
                e.creator_id = None
        self._creators = [c for c in self._creators if c.id != creator_id]
        self.save()

    # ── Entry mutations ────────────────────────────────────────────────────────

    def add_entry(self, platform: str, handle: str,
                  creator_id: "str | None" = None) -> Entry:
        with self._entry_lock:
            existing = self.find_entry(platform, handle)
            if existing is not None:
                raise DuplicateEntryError(existing)
            e = Entry(_short_id(), platform, handle, creator_id)
            self._entries.append(e)
            self.save()
            return e

    def remove_entry(self, entry_id: str) -> None:
        self._entries = [e for e in self._entries if e.id != entry_id]
        self._prune_empty_creators()
        self.save()

    def assign_entry(self, entry_id: str, creator_id: "str | None") -> None:
        e = self.get_entry(entry_id)
        if e:
            e.creator_id = creator_id
            self._prune_empty_creators()
            self.save()

    def _prune_empty_creators(self) -> None:
        occupied = {e.creator_id for e in self._entries if e.creator_id}
        self._creators = [c for c in self._creators if c.id in occupied]

    def remove_entry_by_handle(self, platform: str, handle: str) -> None:
        """Used by suspended-account cleanup."""
        self._entries = [
            e for e in self._entries
            if not (e.platform == platform and e.handle == handle)
        ]
        self.save()

    # ── Migration ──────────────────────────────────────────────────────────────

    def migrate_from_legacy(self, platforms: dict) -> None:
        """Import existing *_users.txt files as unassigned entries (runs once)."""
        if self._path.exists():
            return
        for pid, cfg in platforms.items():
            p = Path(cfg["users_file"])
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                self._entries.append(Entry(_short_id(), pid, line, None))
        if self._entries:
            self.save()


def _short_id() -> str:
    return uuid.uuid4().hex[:8]
