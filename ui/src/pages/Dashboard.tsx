import { useEffect, useRef, useState, useCallback } from "react";
import { getStatus, getLogs, startSync, stopSync, startMaintenance, downloadUrl, getSettings, saveSettings, getHistory, getAccounts, openDownloadsFolder, openFile, openPostInViewer, redownloadFile, avatarUrl, fetchAvatar, type Status, type HistoryEntry, type HistoryUser, type AccountsData } from "../api";
import { PlatformChip } from "../components/PlatformChip";
import { ContextMenu } from "../components/ContextMenu";
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
  if (/✓|VERIFIED|finished|Finished|→ Up to date/.test(line)) return "log-line-success";
  if (/UNVERIFIED|INCOMPLETE|\[verify\]/i.test(line)) return "log-line-warning";
  if (/^(?:─+|\+-[-+]+\+)$/.test(line.trim())) return "log-line-dim";
  if (/^(Mode|From|Workers|Per acct|Platform|Users|Interval)\s*:/.test(line)
      || /^(Sync|Maintenance) summary$/.test(line)
      || /^\|\s*Account\s*\|/.test(line)) return "log-line-info";
  return "";
}

function simplifyLines(lines: string[]): string[] {
  const out: string[] = [];
  for (const line of lines) {
    const important =
      /\[error\]|ERROR|\[warning\]|WARNING|UNVERIFIED|INCOMPLETE|\[verify\]/i.test(line)
      || /^\|/.test(line)
      || /^\+-[-+]+\+$/.test(line)
      || /^(Mode|From|Workers|Per acct|Platform|URL|Resolved)\s*:/.test(line)
      || /^(Sync|Maintenance) summary$/.test(line)
      || /^Maintenance(:| finished)/.test(line)
      || /^→\s/.test(line)
      || /^\s*\[(disk|corrupt|dedupe|naming|merge|archive)\]/i.test(line)
      || /^(Repaired|Added|Removed|Downloaded)\s*:/.test(line)
      || /^\[index-scan\].*Done/.test(line);
    if (important) {
      if (out[out.length - 1] !== line) out.push(line);
      continue;
    }
    if (/^\[download\]/.test(line)) continue;
    if (/^\[Merger\]|^\[Fixup|^Deleting original file/.test(line)) continue;
    if (/^\[(BiliBili|BilibiliSpaceVideo|youtube|Twitter|twitch|TikTok)\]/i.test(line)) continue;
    if (/has already been (downloaded|recorded)/.test(line)) continue;
    if (/^\[info\]/.test(line)) continue;
    if (/Format\(s\).+missing/.test(line)) continue;
    if (/[█░▓]{2,}/.test(line)) continue;
    if (/\d+%\|/.test(line)) continue;
    if (/^\s*INFO\s+/.test(line)) continue;
    if (/^\s*(处理第|等待 \d+ 秒|第.+页没有找到作品)/.test(line)) continue;
    // Keep ordinary useful messages. The rules above remove known verbose
    // downloader noise; dropping the remaining lines made Simple mode blank
    // whenever an account had no warning or summary line yet.
    if (out[out.length - 1] !== line) out.push(line);
  }
  return out;
}

type AccountLogLine = {
  text: string;
  accountKey: string | null;
};

const DEFAULT_STATUS: Status = {
  running: false, status: "Idle", mode: "update",
  from_days: 0, tracking: 0, last_sync: "—", version: "",
  total_downloads: 0, scheduler_active: false, next_sync_at: 0, progress: [],
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
  const [selected, setSelected] = useState<string[] | null>(() => {
    try {
      const saved = localStorage.getItem("archiver:last-sync-targets");
      return saved ? JSON.parse(saved) : null;
    } catch { return null; }
  });

  useEffect(() => {
    getAccounts().then(accounts => {
      setData(accounts);
      setSelected(current => {
        if (current === null) return null;
        const valid = new Set([
          ...accounts.creators.map(creator => creator.id),
          ...(accounts.entries.some(entry => !entry.creator_id) ? ["__unassigned__"] : []),
        ]);
        const available = current.filter(id => valid.has(id));
        return available.length === valid.size ? null : available;
      });
    }).catch(() => {});
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
          <button
            disabled={selected?.length === 0}
            onClick={() => {
              localStorage.setItem("archiver:last-sync-targets", JSON.stringify(selected));
              onClose();
              onStart(selected);
            }}
            className="px-4 py-1.5 rounded text-sm bg-accent text-white hover:opacity-90 transition-opacity disabled:opacity-40">
            {t("sync.start")}
          </button>
        </div>
      </div>
    </div>
  );
}

function StartMaintenanceModal({ onClose, onStart }: {
  onClose: () => void;
  onStart: (creatorIds: string[] | null) => void;
}) {
  const { t } = useLang();
  const [data, setData] = useState<AccountsData | null>(null);
  const [selected, setSelected] = useState<string[] | null>(null);

  useEffect(() => {
    getAccounts().then(setData).catch(() => {});
  }, []);

  const groups = data?.creators ?? [];
  const toggle = (id: string) => {
    const allIds = groups.map(group => group.id);
    const current = selected ?? allIds;
    const next = current.includes(id)
      ? current.filter(value => value !== id)
      : [...current, id];
    setSelected(
      next.length === 0 ? []
        : next.length === allIds.length ? null
        : next
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-panel border border-border rounded-lg w-96 p-5 shadow-xl">
        <h2 className="font-semibold text-text mb-4">
          {t("maintenance.title")}
        </h2>
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
            {t("maintenance.all_groups")}
          </button>
          <div className="max-h-72 overflow-y-auto border-t border-border">
            {data === null ? (
              <p className="text-xs text-dim px-3 py-2">{t("loading")}</p>
            ) : groups.length === 0 ? (
              <p className="text-xs text-dim px-3 py-2">
                {t("maintenance.no_groups")}
              </p>
            ) : groups.map(group => {
              const checked = selected === null || selected.includes(group.id);
              const accountCount = data!.entries.filter(
                entry => entry.creator_id === group.id
              ).length;
              return (
                <button
                  key={group.id}
                  onClick={() => toggle(group.id)}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-hover transition-colors flex items-center gap-2 border-b border-border/40 last:border-b-0"
                >
                  <span className={`w-3.5 h-3.5 rounded border shrink-0 flex items-center justify-center text-xs ${
                    checked ? "bg-accent border-accent text-white" : "border-border"
                  }`}>{checked && "✓"}</span>
                  <span className="truncate">{group.name}</span>
                  <span className="ml-auto text-[10px] text-dim">
                    {accountCount} {t("dash.accounts")}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded text-sm text-dim hover:text-text hover:bg-hover transition-colors"
          >
            {t("cancel")}
          </button>
          <button
            disabled={groups.length === 0 || selected?.length === 0}
            onClick={() => { onClose(); onStart(selected); }}
            className="px-4 py-1.5 rounded text-sm bg-accent text-white hover:opacity-90 transition-opacity disabled:opacity-40"
          >
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
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState<{x:number; y:number; path:string} | null>(null);
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
          {user.files.map((f, i) => {
            const path = user.folder + "\\" + f;
            return (
              <div key={i}
                onDoubleClick={() => openPostInViewer(path)}
                onContextMenu={e => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY, path }); }}
                className="font-mono text-xs text-dim truncate pl-8 rounded px-1 cursor-pointer hover:text-text hover:bg-hover transition-colors"
              >{f}</div>
            );
          })}
        </div>
      )}
      {menu && (
        <ContextMenu
          x={menu.x} y={menu.y}
          onClose={() => setMenu(null)}
          items={[
            { label: t("ctx.open"),       onClick: () => openFile(menu.path).catch(() => {}) },
            { label: t("ctx.redownload"), onClick: () => redownloadFile(user.platform, menu.path).catch(() => {}) },
          ]}
        />
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
  const [logLines, setLogLines] = useState<AccountLogLine[]>([]);
  const [selectedLogKey, setSelectedLogKey] = useState<string | null>(null);
  const [error, setError]       = useState("");
  const [showDl, setShowDl]     = useState(false);
  const [simpleLog, setSimpleLog] = useState(true);
  const [logQuery, setLogQuery] = useState("");
  const [wrapLog, setWrapLog] = useState(false);
  const [followLog, setFollowLog] = useState(true);
  const [copiedLog, setCopiedLog] = useState(false);
  const [autoSync, setAutoSync] = useState(false);
  const [showSyncModal, setShowSyncModal]   = useState(false);
  const [showMaintenanceModal, setShowMaintenanceModal] = useState(false);
  const [activeTab, setActiveTab]           = useState<"log" | "history">("log");
  const [historyEntries, setHistoryEntries] = useState<HistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [countdown, setCountdown]           = useState("");
  const [showCompletedProgress, setShowCompletedProgress] = useState(false);
  const [errorTooltip, setErrorTooltip] = useState<{
    text: string; x: number; y: number;
  } | null>(null);
  const logRef  = useRef<HTMLDivElement>(null);
  const logSearchRef = useRef<HTMLInputElement>(null);
  const logBuffersRef = useRef<Map<string, string>>(new Map());

  useEffect(() => {
    if (!active) return;
    const focusSearch = () => {
      setActiveTab("log");
      requestAnimationFrame(() => logSearchRef.current?.focus());
    };
    window.addEventListener("archiver:focus-search", focusSearch);
    return () => window.removeEventListener("archiver:focus-search", focusSearch);
  }, [active]);

  useEffect(() => {
    if (!active) return;
    const openSync = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "s" && !status.running) {
        event.preventDefault();
        setShowSyncModal(true);
      }
    };
    window.addEventListener("keydown", openSync);
    return () => window.removeEventListener("keydown", openSync);
  }, [active, status.running]);

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
            const savedMode = localStorage.getItem("archiver:last-sync-mode");
            const savedDaysRaw = localStorage.getItem("archiver:last-sync-days");
            const savedDays = savedDaysRaw === null ? Number.NaN : Number(savedDaysRaw);
            setMode(savedMode === "full" || savedMode === "update" ? savedMode : s.mode);
            setFromDays(Number.isFinite(savedDays) && savedDays >= 0 ? savedDays : s.from_days);
            initializedRef.current = true;
          }
        }
      } catch { /* server not ready yet */ }
    };
    tick();
    const id = setInterval(tick, 750);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    let mounted = true;
    let last = 0;
    const consume = (text: string, accountKey: string | null) => {
        const bufferKey = accountKey ?? "";
        const buffered = (logBuffersRef.current.get(bufferKey) ?? "") + text;
        const parts = buffered.split("\n");
        logBuffersRef.current.set(bufferKey, parts.pop() ?? "");
        const newLines = parts.filter(l => l.length > 0);
        if (newLines.length > 0) {
          setLogLines(prev => [
            ...prev,
            ...newLines.map(line => ({ text: line, accountKey })),
          ].slice(-5000));
        }
    };
    const poll = async () => {
      try {
        const result = await getLogs(last);
        if (!mounted) return;
        for (const event of result.events) consume(event.text, event.account_key);
        last = result.last;
      } catch { /* retry on the next interval */ }
    };
    void poll();
    const id = setInterval(poll, 500);
    return () => { mounted = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    const el = logRef.current;
    if (el && followLog) el.scrollTop = el.scrollHeight;
  }, [logLines, followLog]);

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

  const handleRetryAccounts = useCallback(async (items: Status["progress"]) => {
    if (status.running) return;
    setError("");
    try {
      const accounts = await getAccounts();
      const entryIds = items.flatMap(item => {
        const entry = accounts.entries.find(candidate => {
          const candidateId = candidate.handle.includes("|")
            ? candidate.handle.split("|").pop()!
            : candidate.handle;
          return candidate.platform === item.platform && candidateId === item.account_id;
        });
        return entry ? [entry.id] : [];
      });
      const uniqueEntryIds = [...new Set(entryIds)];
      if (uniqueEntryIds.length === 0) throw new Error(t("dash.retry_accounts_missing"));
      await startSync(status.mode, status.from_days, null, uniqueEntryIds);
      setSelectedLogKey(null);
      setStatus(current => ({ ...current, running: true, status: "Running…" }));
    } catch (retryError: unknown) {
      setError(retryError instanceof Error ? retryError.message : String(retryError));
    }
  }, [status.running, status.mode, status.from_days, t]);

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

  const handleMaintenance = useCallback(async (creatorIds: string[] | null) => {
    setError("");
    try {
      await startMaintenance(creatorIds);
      setStatus(s => ({ ...s, running: true, status: "Maintenance…" }));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const scopedLogLines = selectedLogKey
    ? logLines.filter(line => line.accountKey === selectedLogKey)
    : logLines;
  const scopedLogText = scopedLogLines.map(line => line.text);
  const modeLogLines = simpleLog ? simplifyLines(scopedLogText) : scopedLogText;
  const query = logQuery.trim().toLocaleLowerCase();
  const visibleLogLines = query
    ? modeLogLines.filter(line => line.toLocaleLowerCase().includes(query))
    : modeLogLines;
  const activeProgress = [...(status.progress ?? [])]
    .reverse()
    .sort((first, second) =>
      Number(first.state === "finished") - Number(second.state === "finished")
    );
  const incompleteProgress = activeProgress.filter(
    item => item.state !== "finished"
  );
  const completedProgress = activeProgress.filter(
    item => item.state === "finished"
  );
  const visibleProgress = showCompletedProgress
    ? [...incompleteProgress, ...completedProgress]
    : incompleteProgress;
  const selectedProgress = activeProgress.find(item => item.key === selectedLogKey);
  const failedSyncProgress = activeProgress.filter(
    item => item.operation === "sync" && item.state === "error"
  );

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
          title="Start sync (Ctrl+Shift+S)"
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
        <button
          onClick={() => setShowMaintenanceModal(true)}
          disabled={status.running}
          className="px-4 py-2 rounded text-sm font-medium bg-panel border border-border text-text
                     disabled:opacity-40 disabled:cursor-not-allowed hover:bg-hover transition-colors"
        >
          {t("dash.maintenance")}
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
              onClick={() => { setMode(m); localStorage.setItem("archiver:last-sync-mode", m); }}
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
              onChange={e => {
                const value = Math.max(0, Number(e.target.value));
                setFromDays(value);
                localStorage.setItem("archiver:last-sync-days", String(value));
              }}
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
          <div className="ml-auto flex items-center gap-1 min-w-0">
            <div className="flex rounded overflow-hidden border border-border text-xs mr-2">
              {(["simple", "full"] as const).map(v => (
                <button
                  key={v}
                  onClick={() => setSimpleLog(v === "simple")}
                  className={`px-3 py-1.5 transition-colors ${
                    (v === "simple") === simpleLog
                      ? "bg-accent text-white"
                      : "bg-panel text-dim hover:bg-hover"
                  }`}
                >
                  {v === "simple" ? t("dash.log_simple") : t("dash.log_full")}
                </button>
              ))}
            </div>
            <input
              ref={logSearchRef}
              value={logQuery}
              onChange={e => setLogQuery(e.target.value)}
              placeholder={t("dash.log_search")}
              className="w-40 px-2.5 py-1.5 rounded border border-border bg-bg text-xs text-text
                         placeholder:text-dim focus:outline-none focus:border-accent"
            />
            <button
              onClick={() => setWrapLog(v => !v)}
              className={`px-2.5 py-1.5 text-xs rounded border transition-colors ${
                wrapLog ? "border-accent text-accent bg-accent/10" : "border-border text-dim hover:text-text"
              }`}
            >
              {t("dash.log_wrap")}
            </button>
            <button
              onClick={() => setFollowLog(v => !v)}
              className={`px-2.5 py-1.5 text-xs rounded border transition-colors ${
                followLog ? "border-accent text-accent bg-accent/10" : "border-border text-dim hover:text-text"
              }`}
            >
              {t("dash.log_follow")}
            </button>
            <button
              onClick={() => {
                navigator.clipboard.writeText(visibleLogLines.join("\n"))
                  .then(() => {
                    setCopiedLog(true);
                    setTimeout(() => setCopiedLog(false), 1500);
                  })
                  .catch(() => {});
              }}
              disabled={visibleLogLines.length === 0}
              className="px-3 py-2 text-xs text-dim hover:text-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              {copiedLog ? t("dash.log_copied") : t("dash.copy")}
            </button>
            <button
              onClick={() => {
                setLogLines([]);
                setLogQuery("");
                logBuffersRef.current.clear();
              }}
              className="px-3 py-2 text-xs text-dim hover:text-text transition-colors"
            >
              {t("dash.clear")}
            </button>
          </div>
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
        <div className="flex-1 min-h-0 flex flex-col bg-log-bg">
          {activeProgress.length > 0 && (
            <div className="shrink-0 border-b border-border/60 px-4 py-2.5">
              <div className="mb-2 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setSelectedLogKey(null)}
                  className={`rounded border px-2.5 py-1 text-[10px] transition-colors ${
                    selectedLogKey === null
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border/70 bg-panel/50 text-dim hover:text-text"
                  }`}
                >
                  {t("dash.log_all_accounts")}
                </button>
                {selectedProgress && (
                  <span className="truncate text-[10px] text-dim">
                    {t("dash.log_account")}: {selectedProgress.account}
                  </span>
                )}
                {failedSyncProgress.length > 0 && (
                  <button
                    type="button"
                    disabled={status.running}
                    onClick={() => void handleRetryAccounts(failedSyncProgress)}
                    className="ml-auto rounded border border-red-500/40 bg-red-500/10 px-2.5 py-1 text-[10px] font-medium text-red-300 transition-colors hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {t("dash.retry_all")} ({failedSyncProgress.length})
                  </button>
                )}
              </div>
              {completedProgress.length > 0 && (
                <div className="mb-2 flex justify-end">
                  <button
                    type="button"
                    onClick={() => setShowCompletedProgress(value => !value)}
                    className="rounded border border-border/70 bg-panel/50 px-2.5 py-1 text-[10px] text-dim transition-colors hover:border-accent/50 hover:text-text"
                  >
                    {showCompletedProgress
                      ? t("dash.progress_hide_completed")
                      : t("dash.progress_show_completed").replace(
                          "{count}", String(completedProgress.length)
                        )}
                  </button>
                </div>
              )}
              {visibleProgress.length > 0 && (
                <div
                  className="grid max-h-[38vh] gap-2.5 overflow-y-auto overscroll-contain pr-1"
                  style={{ gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}
                >
                  {visibleProgress.map(item => {
                  const active = item.state === "running"
                    || item.state === "scanning"
                    || item.state === "downloading";
                  const finished = item.state === "finished";
                  return (
                    <div
                      key={item.key}
                      role="button"
                      tabIndex={0}
                      onClick={() => setSelectedLogKey(
                        current => current === item.key ? null : item.key
                      )}
                      onKeyDown={event => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setSelectedLogKey(
                            current => current === item.key ? null : item.key
                          );
                        }
                      }}
                      onMouseMove={event => {
                        if (item.state !== "error") return;
                        setErrorTooltip({
                          text: item.error || t("dash.progress_error_hint"),
                          x: Math.min(event.clientX + 14, window.innerWidth - 334),
                          y: Math.min(event.clientY + 14, window.innerHeight - 90),
                        });
                      }}
                      onMouseLeave={() => setErrorTooltip(null)}
                      className={`progress-card min-w-0 cursor-pointer rounded border bg-panel/50 px-3 py-2
                        ${selectedLogKey === item.key ? "ring-1 ring-accent border-accent" :
                          active ? "progress-card-active border-accent/50"
                          : finished ? "progress-card-finished border-green-500/30"
                          : item.state === "error" ? "border-red-500/40"
                          : "border-border/70"}`}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <HistoryAvatar platform={item.platform} handle={item.account_id} />
                        <span className="text-xs font-medium text-text truncate" title={item.account}>
                          {item.account}
                        </span>
                        <span className={`ml-auto w-1.5 h-1.5 rounded-full shrink-0 ${
                          active ? "bg-accent animate-pulse"
                            : finished ? "bg-green-400"
                            : item.state === "error" ? "bg-red-400" : "bg-dim"
                        }`} />
                      </div>
                      <div className="flex items-center justify-between mt-2 text-[10px] text-dim">
                        <span>{t(`dash.progress_state_${item.state}`)}</span>
                        <span className="font-mono">
                          {active && item.done != null && item.total
                            ? `${item.done} / ${Math.max(item.done, item.total)} · ${item.percent?.toFixed(1) ?? "?"}%`
                            : item.percent != null
                              ? `${item.percent.toFixed(1)}%`
                            : item.local != null
                              ? `${item.local} / ${item.remote ?? "?"}`
                              : "—"}
                        </span>
                      </div>
                      <div className="h-1.5 rounded-full bg-border mt-1.5 overflow-hidden">
                        {item.percent != null ? (
                          <div
                            className="h-full bg-accent transition-[width] duration-300"
                            style={{ width: `${item.percent}%` }}
                          />
                        ) : active ? (
                          <div className="progress-indeterminate h-full w-1/3 bg-accent rounded-full" />
                        ) : finished ? (
                          <div className="progress-complete h-full w-full bg-green-400/70" />
                        ) : null}
                      </div>
                    </div>
                  );
                })}
                </div>
              )}
            </div>
          )}
          <div
            ref={logRef}
            onScroll={e => {
              const el = e.currentTarget;
              setFollowLog(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
            }}
            className="flex-1 overflow-auto font-mono text-xs leading-5 px-4 py-3"
          >
            {visibleLogLines.length === 0 ? (
              <span className="text-dim">
                {scopedLogLines.length === 0 ? t("dash.log_empty") : t("dash.log_no_match")}
              </span>
            ) : (
              <div className={wrapLog ? "min-w-0" : "min-w-max"}>
                {visibleLogLines.map((line, i) => (
                  <div
                    key={`${i}-${line}`}
                    className={`${classifyLine(line)} ${
                      wrapLog ? "whitespace-pre-wrap break-words" : "whitespace-pre"
                    } hover:bg-white/[0.025]`}
                  >
                    {line}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="h-7 shrink-0 border-t border-border/60 px-4 flex items-center gap-3 text-[11px] text-dim">
            <span>{visibleLogLines.length} / {scopedLogLines.length} {t("dash.log_lines")}</span>
            <span>·</span>
            <span>{simpleLog ? t("dash.log_simple") : t("dash.log_full")}</span>
            <span className="ml-auto flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${followLog ? "bg-green-400" : "bg-dim"}`} />
              {followLog ? t("dash.log_following") : t("dash.log_paused")}
            </span>
          </div>
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
      {showMaintenanceModal && (
        <StartMaintenanceModal
          onClose={() => setShowMaintenanceModal(false)}
          onStart={handleMaintenance}
        />
      )}
      {errorTooltip && (
        <div
          className="pointer-events-none fixed z-[300] max-w-xs rounded border border-red-500/40 bg-panel px-3 py-2 text-xs leading-5 text-text shadow-xl"
          style={{ left: errorTooltip.x, top: errorTooltip.y }}
        >
          {errorTooltip.text}
        </div>
      )}
    </div>
  );
}
