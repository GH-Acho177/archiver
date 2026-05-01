import { useEffect, useState } from "react";
import { getHistory, type HistoryEntry, type HistoryUser } from "../api";
import { PlatformChip } from "../components/PlatformChip";

function UserRow({ user }: { user: HistoryUser }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-t border-border/50 first:border-t-0">
      <button
        onClick={() => user.files.length > 0 && setOpen(v => !v)}
        className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors
          ${user.files.length > 0 ? "hover:bg-hover cursor-pointer" : "cursor-default"}`}
      >
        <PlatformChip platform={user.platform} />
        <span className="flex-1 text-xs text-text truncate">{user.display || user.handle}</span>
        <span className={`text-xs font-mono ${user.count > 0 ? "text-green-400" : "text-dim"}`}>
          +{user.count}
        </span>
        {user.corrupt > 0 && (
          <span className="text-xs text-red-400 font-mono">{user.corrupt} corrupt</span>
        )}
        {user.files.length > 0 && (
          <span className="text-dim text-xs">{open ? "▴" : "▾"}</span>
        )}
      </button>
      {open && (
        <div className="px-4 pb-2 space-y-0.5">
          {user.files.map((f, i) => (
            <div key={i} className="font-mono text-xs text-dim truncate pl-8">{f}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function RunCard({ entry }: { entry: HistoryEntry }) {
  const [open, setOpen] = useState(false);
  const active = entry.users.filter(u => u.count > 0);
  const total  = active.reduce((s, u) => s + u.count, 0);

  return (
    <div className="border border-border rounded-md overflow-hidden mb-3">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 bg-panel hover:bg-hover transition-colors text-left"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-text">{entry.date}</span>
            <span className="text-xs text-dim">{entry.time}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded ${
              entry.mode === "Full"
                ? "bg-accent/20 text-accent"
                : "bg-border text-dim"
            }`}>
              {entry.mode}
            </span>
            {entry.stopped && (
              <span className="text-xs text-yellow-500">stopped early</span>
            )}
          </div>
          <div className="text-xs text-dim mt-0.5">
            {active.length} account{active.length !== 1 ? "s" : ""} · {entry.duration}
          </div>
        </div>
        <div className="text-right shrink-0">
          <span className={`text-sm font-semibold ${total > 0 ? "text-green-400" : "text-dim"}`}>
            +{total}
          </span>
          <div className="text-xs text-dim">files</div>
        </div>
        <span className="text-dim text-xs ml-2">{open ? "▴" : "▾"}</span>
      </button>

      {open && (
        <div className="bg-bg divide-y divide-border/30">
          {active.map((u, i) => (
            <UserRow key={i} user={u} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function History({ active }: { active: boolean }) {
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!active) return;
    setLoading(true);
    getHistory(200)
      .then(setEntries)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [active]);

  const totalFiles = entries.reduce(
    (s, e) => s + e.users.reduce((ss, u) => ss + u.count, 0), 0
  );

  return (
    <div className="flex flex-col h-full bg-bg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-border shrink-0">
        <span className="text-xs text-dim">
          {loading ? "Loading…" : `${entries.length} runs · ${totalFiles.toLocaleString()} files`}
        </span>
        <button
          onClick={() => {
            setLoading(true);
            getHistory(200).then(setEntries).catch(() => {}).finally(() => setLoading(false));
          }}
          className="text-xs text-dim hover:text-text transition-colors"
        >
          ↻ Refresh
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {!loading && entries.length === 0 ? (
          <p className="text-dim text-sm text-center mt-12">No download history yet.</p>
        ) : (
          entries.map(e => <RunCard key={e.run_key} entry={e} />)
        )}
      </div>
    </div>
  );
}
