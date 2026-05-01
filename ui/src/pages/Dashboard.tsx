import { useEffect, useRef, useState, useCallback } from "react";
import { getStatus, startSync, stopSync, downloadUrl, getSettings, saveSettings, getHistory, getAccounts, openDownloadsFolder, openFile, avatarUrl, fetchAvatar, type Status, type HistoryEntry, type HistoryUser, type AccountsData } from "../api";
import { PlatformChip } from "../components/PlatformChip";
import { useLang } from "../i18n";

function DownloadModal({ onClose }: { onClose: () => void }) {
  const { t } = useLang();
  const [url, setUrl]     = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy]   = useState(false);

  const submit = async () => {
    const u = url.trim();
    if (!u) return;
    setBusy(true);
    setError("");
    try {
      await downloadUrl(u);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-panel border border-border rounded-lg w-96 p-5 shadow-xl">
        <h2 className="font-semibold text-text mb-1">{t("dl.title")}</h2>
        <p className="text-xs text-dim mb-4">{t("dl.hint")}</p>
        <input
          autoFocus
          value={url}
          onChange={e => setUrl(e.target.value)}
          onKeyDown={e => e.key === "Enter" && submit()}
          placeholder={t("dl.placeholder")}
          className="w-full px-3 py-1.5 rounded bg-bg border border-border text-text text-sm
                     placeholder:text-dim focus:outline-none focus:border-accent mb-4"
        />
        {error && <p className="text-red-400 text-xs mb-3">{error}</p>}
        <div className="flex justify-end gap-2">
          <button onClick={onClose}
            className="px-4 py-1.5 rounded text-sm text-dim hover:text-text hover:bg-hover transition-colors">
            {t("cancel")}
          </button>
          <button onClick={submit} disabled={busy || !url.trim()}
            className="px-4 py-1.5 rounded text-sm bg-accent text-white
                       disabled:opacity-40 hover:opacity-90 transition-opacity">
            {busy ? t("dl.starting") : t("dl.download")}
          </button>
        </div>
      </div>
    </div>
  );
}

function classifyLine(line: string): string {
  if (/\[error\]|ERROR/i.test(line))        return "log-line-error";
  if (/\[warning\]|WARNING|⚠/i.test(line))  return "log-line-warning";
  if (/✓|→ Up to date/.test(line))          return "log-line-success";
  if (/^─+$/.test(line.trim()))              return "log-line-dim";
  if (/^(Mode|From|Workers|Platform|Users|Interval)\s*:/.test(line)) return "log-line-info";
  return "";
}

const DEFAULT_STATUS: Status = {
  running: false, status: "Idle", mode: "update",
  from_days: 0, tracking: 0, last_sync: "—", version: "",
  total_downloads: 0, scheduler_active: false, next_sync_at: 0,
};

// ── Start sync modal ──────────────────────────────────────────────────────────

function StartSyncModal({ mode, fromDays, onClose, onStart }: {
  mode: "update" | "full";
  fromDays: number;
  onClose: () => void;
  onStart: (targets: string[] | null) => void;
}) {
  const { t } = useLang();
  const [data, setData]         = useState<AccountsData | null>(null);
  const [selected, setSelected] = useState<string[] | null>(null);

  useEffect(() => {
    getAccounts().then(setData).catch(() => {});
  }, []);

  const hasUnassigned = data?.entries.some(e => !e.creator_id) ?? false;
  const options: { id: string; name: string }[] = data ? [
    ...(hasUnassigned ? [{ id: "__unassigned__", name: t("acc.unassigned") }] : []),
    ...data.creators,
  ] : [];

  const toggle = (id: string) => {
    const cur = selected ?? options.map(o => o.id);
    const next = cur.includes(id) ? cur.filter(x => x !== id) : [...cur, id];
    setSelected(next.length === 0 ? [] : next.length === options.length ? null : next);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-panel border border-border rounded-lg w-80 p-5 shadow-xl">
        <h2 className="font-semibold text-text mb-1">{t("sync.title")}</h2>
        <p className="text-xs text-dim mb-4">
          {t("sync.mode")} <span className="text-text capitalize">{mode === "update" ? t("dash.update") : t("dash.full")}</span>
          {mode === "full" && fromDays > 0 && (
            <> · {t("sync.last")} <span className="text-text">{fromDays}</span> {t("sync.days")}</>
          )}
        </p>
        <p className="text-xs text-dim mb-2 font-medium uppercase tracking-wide">{t("sync.accs_label")}</p>
        <div className="border border-border rounded-md overflow-hidden mb-4">
          <button
            onClick={() => setSelected(selected === null ? [] : null)}
            className={`w-full text-left px-3 py-2 text-sm transition-colors flex items-center gap-2 ${
              selected === null ? "bg-accent/10 text-accent" : "text-text hover:bg-hover"
            }`}
          >
            <span className={`w-3.5 h-3.5 rounded border shrink-0 flex items-center justify-center text-xs ${
              selected === null ? "bg-accent border-accent text-white" : "border-border"
            }`}>{selected === null && "✓"}</span>
            {t("sync.all_accs")}
          </button>
          {options.length > 0 && <div className="border-t border-border" />}
          <div className="max-h-52 overflow-y-auto">
            {data === null ? (
              <p className="text-xs text-dim px-3 py-2">{t("loading")}</p>
            ) : options.length === 0 ? (
              <p className="text-xs text-dim px-3 py-2">{t("sync.no_creators")}</p>
            ) : options.map(opt => {
              const checked = selected === null || selected.includes(opt.id);
              return (
                <button key={opt.id} onClick={() => toggle(opt.id)}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-hover transition-colors flex items-center gap-2 border-t border-border/40">
                  <span className={`w-3.5 h-3.5 rounded border shrink-0 flex items-center justify-center text-xs ${
                    checked ? "bg-accent border-accent text-white" : "border-border"
                  }`}>{checked && "✓"}</span>
                  <span className="truncate">{opt.name}</span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <button onClick={onClose}
            className="px-4 py-1.5 rounded text-sm text-dim hover:text-text hover:bg-hover transition-colors">
            {t("cancel")}
          </button>
          <button onClick={() => { onClose(); onStart(selected); }}
            className="px-4 py-1.5 rounded text-sm bg-accent text-white hover:opacity-90 transition-opacity">
            {t("sync.start")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── History sub-components ────────────────────────────────────────────────────

function HistoryAvatar({ platform, handle }: { platform: string; handle?: string }) {
  const [ok, setOk]         = useState(true);
  const [ready, setReady]   = useState(false);
  const accId = handle ? (handle.includes("|") ? handle.split("|").pop()! : handle) : null;

  useEffect(() => {
    if (!accId) return;
    fetchAvatar(platform, accId)
      .catch(() => {})
      .finally(() => setReady(true));
  }, [platform, accId]);

  if (!accId || !ok) return <PlatformChip platform={platform} />;
  if (!ready) return <PlatformChip platform={platform} />;
  return (
    <img
      src={avatarUrl(platform, accId)}
      alt=""
      onError={() => setOk(false)}
      className="w-5 h-5 rounded-full shrink-0 object-cover bg-panel"
    />
  );
}

function UserRow({ user }: { user: HistoryUser }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-t border-border/50 first:border-t-0">
      <button
        onClick={() => user.files.length > 0 && setOpen(v => !v)}
        className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
          user.files.length > 0 ? "hover:bg-hover cursor-pointer" : "cursor-default"
        }`}
      >
        <HistoryAvatar platform={user.platform} handle={user.handle} />
        <span className="flex-1 text-sm text-text truncate">{user.display || user.handle}</span>
        <span className={`text-sm font-mono ${user.count > 0 ? "text-green-400" : "text-dim"}`}>
          +{user.count}
        </span>
        {user.corrupt > 0 && (
          <span className="text-sm text-red-400 font-mono">{user.corrupt} corrupt</span>
        )}
        {user.files.length > 0 && (
          <span className="text-dim text-sm">{open ? "▴" : "▾"}</span>
        )}
      </button>
      {open && (
        <div className="px-4 pb-2 space-y-0.5">
          {user.files.map((f, i) => (
            <div key={i}
              onDoubleClick={() => openFile(user.folder + "\\" + f)}
              className="font-mono text-xs text-dim truncate pl-8 rounded px-1 cursor-pointer hover:text-text hover:bg-hover transition-colors"
            >{f}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function RunCard({ entry }: { entry: HistoryEntry }) {
  const { t } = useLang();
  const [open, setOpen] = useState(true);
  const total = entry.users.reduce((s, u) => s + u.count, 0);
  return (
    <div className="border border-border rounded-md overflow-hidden mb-3">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 bg-panel hover:bg-hover transition-colors text-left"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-text">{entry.date}</span>
            <span className="text-sm text-dim">{entry.time}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded ${
              entry.mode === "Full" ? "bg-accent/20 text-accent" : "bg-border text-dim"
            }`}>{entry.mode === "Full" ? t("dash.full") : t("dash.update")}</span>
            {entry.stopped && <span className="text-xs text-yellow-500">{t("dash.stopped")}</span>}
          </div>
          <div className="text-xs text-dim mt-0.5">
            {entry.users.length} {entry.users.length !== 1 ? t("dash.accounts") : t("dash.account")} · {entry.duration}
          </div>
        </div>
        <div className="text-right shrink-0">
          <span className={`text-sm font-semibold ${total > 0 ? "text-green-400" : "text-dim"}`}>
            +{total}
          </span>
          <div className="text-xs text-dim">{t("dash.files")}</div>
        </div>
        <span className="text-dim text-sm ml-2">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="bg-bg divide-y divide-border/30">
          {entry.users.map((u, i) => <UserRow key={i} user={u} />)}
        </div>
      )}
    </div>
  );
}

export default function Dashboard({ active }: { active: boolean }) {
  const { t } = useLang();
  const [status, setStatus]     = useState<Status>(DEFAULT_STATUS);
  const [mode, setMode]         = useState<"update" | "full">("update");
  const [fromDays, setFromDays] = useState(0);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [error, setError]       = useState("");
  const [showDl, setShowDl]     = useState(false);
  const [autoSync, setAutoSync] = useState(false);
  const [showSyncModal, setShowSyncModal]   = useState(false);
  const [activeTab, setActiveTab]           = useState<"log" | "history">("log");
  const [historyEntries, setHistoryEntries] = useState<HistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [countdown, setCountdown]           = useState("");
  const logRef  = useRef<HTMLDivElement>(null);
  const wsRef   = useRef<WebSocket | null>(null);
  const bufRef  = useRef("");

  useEffect(() => {
    getSettings().then(s => {
      setAutoSync(!!s.auto_update_enabled);
    }).catch(() => {});
  }, []);

  const initializedRef = useRef(false);
  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      try {
        const s = await getStatus();
        if (mounted) {
          setStatus(s);
          if (!initializedRef.current) {
            setMode(s.mode);
            setFromDays(s.from_days);
            initializedRef.current = true;
          }
        }
      } catch { /* server not ready yet */ }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    const connect = () => {
      const ws = new WebSocket(`ws://${window.location.host}/ws/log`);
      wsRef.current = ws;

      ws.onmessage = (e) => {
        if (e.data === "__ping__") return;
        bufRef.current += e.data;
        const parts = bufRef.current.split("\n");
        bufRef.current = parts.pop() ?? "";
        const newLines = parts.filter(l => l.length > 0);
        if (newLines.length > 0) {
          setLogLines(prev => [...prev.slice(-2000), ...newLines]);
        }
      };

      ws.onclose = () => {
        setTimeout(() => { if (wsRef.current === ws) connect(); }, 3000);
      };
    };
    connect();
    return () => {
      const ws = wsRef.current;
      if (ws) { wsRef.current = null; ws.close(); }
    };
  }, []);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }, [logLines]);

  useEffect(() => {
    if (!status.scheduler_active || !status.next_sync_at) {
      setCountdown("");
      return;
    }
    const tick = () => {
      const remaining = Math.max(0, Math.round(status.next_sync_at - Date.now() / 1000));
      const m = Math.floor(remaining / 60);
      const s = remaining % 60;
      setCountdown(m > 0 ? `${m}m ${s.toString().padStart(2, "0")}s` : `${s}s`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [status.scheduler_active, status.next_sync_at]);

  const handleStart = useCallback(async (targets: string[] | null) => {
    setError("");
    try {
      await startSync(mode, fromDays, targets);
      setStatus(s => ({ ...s, running: true, status: "Running…" }));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [mode, fromDays]);

  const loadHistory = useCallback(() => {
    setHistoryLoading(true);
    getHistory(200).then(setHistoryEntries).catch(() => {}).finally(() => setHistoryLoading(false));
  }, []);

  useEffect(() => {
    if (activeTab === "history") loadHistory();
  }, [activeTab, loadHistory]);

  const handleAutoToggle = useCallback(async () => {
    const next = !autoSync;
    setAutoSync(next);
    try {
      await saveSettings({ auto_update_enabled: next });
    } catch {
      setAutoSync(!next);
    }
  }, [autoSync]);

  const handleStop = useCallback(async () => {
    setError("");
    try {
      await stopSync();
      setStatus(s => ({ ...s, status: "Stopping…" }));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  return (
    <div className="flex flex-col h-full bg-bg overflow-hidden">
      {/* ── Status strip ─────────────────────────────────── */}
      <div className="flex items-center gap-6 px-5 py-3 border-b border-border text-sm text-dim shrink-0">
        <span className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${status.running ? "bg-green-400" : "bg-dim"}`} />
          <span className={status.running ? "text-text font-medium" : "text-dim"}>{status.status}</span>
        </span>
        <span>{t("dash.last_sync")} <span className="text-text">{status.last_sync}</span></span>
        <span>{t("dash.tracking")} <span className="text-text">{status.tracking}</span></span>
        <span>{t("dash.total")} <span className="text-text">{status.total_downloads.toLocaleString()}</span></span>
        <span className="ml-auto text-dim text-xs">v{status.version}</span>
      </div>

      {/* ── Controls row 1: actions ──────────────────────── */}
      <div className="flex items-center gap-3 px-5 py-2.5 border-b border-border/50 shrink-0">
        <button
          onClick={() => setShowSyncModal(true)}
          disabled={status.running}
          className="px-4 py-2 rounded text-sm font-medium bg-accent text-white
                     disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90 transition-opacity"
        >
          {t("dash.start_sync")}
        </button>
        <button
          onClick={handleStop}
          disabled={!status.running}
          className="px-4 py-2 rounded text-sm font-medium bg-panel border border-border text-text
                     disabled:opacity-40 disabled:cursor-not-allowed hover:bg-hover transition-colors"
        >
          {t("dash.stop")}
        </button>

        <div className="w-px h-5 bg-border mx-1" />

        <button
          onClick={handleAutoToggle}
          className={`flex items-center gap-2 text-sm px-3 py-1.5 rounded border transition-colors ${
            autoSync
              ? "border-accent text-accent bg-accent/10"
              : "border-border text-dim hover:bg-hover hover:text-text"
          }`}
        >
          <span className={`w-2 h-2 rounded-full shrink-0 ${autoSync ? "bg-accent animate-pulse" : "bg-border"}`} />
          {t("dash.auto_sync")}
        </button>
        {autoSync && countdown && (
          <span className="text-xs text-dim">· {t("dash.next_in")} <span className="text-text font-mono">{countdown}</span></span>
        )}

        <div className="w-px h-5 bg-border mx-1" />

        <button
          onClick={() => openDownloadsFolder().catch(() => {})}
          className="text-xs text-dim hover:text-text transition-colors"
        >
          {t("dash.open_folder")}
        </button>
      </div>

      {/* ── Controls row 2: mode + download ──────────────── */}
      <div className="flex items-center gap-3 px-5 py-2 border-b border-border shrink-0 bg-panel/40">
        <div className="flex rounded overflow-hidden border border-border text-xs">
          {(["update", "full"] as const).map(m => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`px-3 py-1.5 transition-colors ${
                mode === m ? "bg-accent text-white" : "bg-panel text-dim hover:bg-hover"
              }`}
            >
              {m === "update" ? t("dash.update") : t("dash.full")}
            </button>
          ))}
        </div>

        {mode === "full" && (
          <label className="flex items-center gap-2 text-xs text-dim">
            {t("dash.last")}
            <input
              type="number"
              min={0}
              value={fromDays}
              onChange={e => setFromDays(Math.max(0, Number(e.target.value)))}
              className="w-14 px-2 py-1 rounded bg-bg border border-border text-text text-xs
                         focus:outline-none focus:border-accent"
            />
            {t("dash.days")} <span className="text-dim">{t("dash.days_hint")}</span>
          </label>
        )}

        <div className="w-px h-4 bg-border mx-1" />

        <button
          onClick={() => setShowDl(true)}
          disabled={status.running}
          className="text-xs text-dim hover:text-text disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {t("dash.dl_url")}
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mx-5 mt-2 px-3 py-2 rounded bg-red-900/40 border border-red-700/50 text-red-300 text-xs shrink-0">
          {error}
          <button onClick={() => setError("")} className="ml-2 text-red-400 hover:text-red-200">✕</button>
        </div>
      )}

      {/* ── Tab bar ──────────────────────────────────────── */}
      <div className="flex items-center border-b border-border shrink-0">
        {(["log", "history"] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-5 py-2 text-sm transition-colors border-b-2 -mb-px ${
              activeTab === tab
                ? "border-accent text-text font-medium"
                : "border-transparent text-dim hover:text-text"
            }`}
          >
            {tab === "log" ? t("dash.tab_log") : t("dash.tab_history")}
          </button>
        ))}
        {activeTab === "log" && (
          <button
            onClick={() => setLogLines([])}
            className="ml-auto px-4 py-2 text-sm text-dim hover:text-text transition-colors"
          >
            {t("dash.clear")}
          </button>
        )}
        {activeTab === "history" && (
          <button
            onClick={loadHistory}
            className="ml-auto px-4 py-2 text-sm text-dim hover:text-text transition-colors"
          >
            {t("refresh")}
          </button>
        )}
      </div>

      {/* ── Log panel ────────────────────────────────────── */}
      {activeTab === "log" && (
        <div
          ref={logRef}
          className="flex-1 overflow-y-auto font-mono text-xs leading-5 p-4 bg-log-bg"
          style={{ wordBreak: "break-all" }}
        >
          {logLines.length === 0 ? (
            <span className="text-dim">{t("dash.log_empty")}</span>
          ) : (
            logLines.map((line, i) => (
              <div key={i} className={classifyLine(line)}>{line}</div>
            ))
          )}
        </div>
      )}

      {/* ── History panel ────────────────────────────────── */}
      {activeTab === "history" && (
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {historyLoading ? (
            <p className="text-dim text-sm text-center mt-12">{t("loading")}</p>
          ) : historyEntries.length === 0 ? (
            <p className="text-dim text-sm text-center mt-12">{t("dash.hist_empty")}</p>
          ) : (
            historyEntries
              .filter(e => e.users.reduce((s, u) => s + u.count, 0) > 0)
              .map(e => <RunCard key={e.run_key} entry={e} />)
          )}
        </div>
      )}

      {showDl && <DownloadModal onClose={() => setShowDl(false)} />}
      {showSyncModal && (
        <StartSyncModal
          mode={mode}
          fromDays={fromDays}
          onClose={() => setShowSyncModal(false)}
          onStart={handleStart}
        />
      )}
    </div>
  );
}
