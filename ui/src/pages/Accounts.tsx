import { useCallback, useEffect, useRef, useState } from "react";
import {
  addCreator, addEntry, assignEntry, getAccounts,
  removeCreator, removeEntry, renameCreator,
  avatarUrl, fetchAvatar,
  getPosts, startCheck, getCheckStatus, openFile,
  PLATFORM_META, type AccountsData, type Creator, type Entry,
  type PostEntry, type CheckStatus,
} from "../api";
import { PlatformChip } from "../components/PlatformChip";
import { useLang } from "../i18n";

// ── Helpers ───────────────────────────────────────────────────────────────────

function accountId(entry: Entry): string {
  return entry.handle.includes("|") ? entry.handle.split("|").pop()! : entry.handle;
}

function displayName(entry: Entry): string {
  return entry.handle.includes("|") ? entry.handle.split("|")[0] : entry.handle;
}

// ── Avatar image ──────────────────────────────────────────────────────────────

function AvatarImg({ platform, accId }: { platform: string; accId: string }) {
  const [ok, setOk]       = useState(true);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setOk(true);
    setReady(false);
    fetchAvatar(platform, accId)
      .catch(() => {})
      .finally(() => setReady(true));
  }, [platform, accId]);

  if (!ready || !ok) {
    return <PlatformChip platform={platform} className="w-20 h-20 p-4" />;
  }
  return (
    <img
      src={`${avatarUrl(platform, accId)}?t=${Date.now()}`}
      alt=""
      onError={() => setOk(false)}
      className="w-20 h-20 rounded-full shrink-0 object-cover bg-panel"
    />
  );
}

// ── Posts modal ───────────────────────────────────────────────────────────────

function PostsModal({ entry, onClose }: { entry: Entry; onClose: () => void }) {
  const { t } = useLang();
  const aid   = accountId(entry);
  const dname = displayName(entry);

  const [posts, setPosts]       = useState<PostEntry[]>([]);
  const [loading, setLoading]   = useState(true);
  const [job, setJob]           = useState<CheckStatus>({ running: false, done: 0, total: 0 });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadPosts = useCallback(() => {
    getPosts(entry.platform, aid).then(setPosts).catch(() => {}).finally(() => setLoading(false));
  }, [entry.platform, aid]);

  useEffect(() => {
    loadPosts();
    getCheckStatus(entry.platform, aid).then(setJob).catch(() => {});
  }, [loadPosts, entry.platform, aid]);

  const startPoll = () => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      const s = await getCheckStatus(entry.platform, aid).catch(() => null);
      if (!s) return;
      setJob(s);
      loadPosts();
      if (!s.running) {
        clearInterval(pollRef.current!);
        pollRef.current = null;
      }
    }, 1500);
  };

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const handleCheck = async () => {
    await startCheck(entry.platform, aid);
    setJob({ running: true, done: 0, total: 0 });
    startPoll();
  };

  const gone      = posts.filter(p => p.status === "gone").length;
  const ok        = posts.filter(p => p.status === "ok").length;
  const unchecked = posts.filter(p => p.status === "unchecked").length;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-panel border border-border rounded-lg w-[600px] max-h-[80vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-border shrink-0">
          <AvatarImg platform={entry.platform} accId={aid} />
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-text text-sm">{dname}</div>
            <div className="text-xs text-dim mt-0.5">
              {loading ? t("loading") : `${posts.length} ${t("posts.posts")}`}
              {gone > 0 && <span className="ml-2 text-red-400">⚠ {gone} {t("posts.gone_count")}</span>}
              {unchecked > 0 && <span className="ml-2 text-dim">{unchecked} {t("posts.unchecked")}</span>}
              {ok > 0 && !gone && !unchecked && <span className="ml-2 text-green-400">{t("posts.all_ok")}</span>}
            </div>
          </div>
          <button onClick={onClose} className="text-dim hover:text-text text-sm px-1">✕</button>
        </div>

        {/* Progress bar */}
        {job.running && (
          <div className="px-5 py-2 border-b border-border shrink-0">
            <div className="flex items-center gap-3 text-xs text-dim">
              <span>{t("posts.checking")} {job.done}/{job.total || "?"}</span>
              <div className="flex-1 h-1 bg-border rounded-full overflow-hidden">
                <div
                  className="h-full bg-accent transition-all"
                  style={{ width: job.total ? `${(job.done / job.total) * 100}%` : "0%" }}
                />
              </div>
            </div>
          </div>
        )}

        {/* Post list */}
        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <p className="text-dim text-xs text-center mt-8">{t("loading")}</p>
          ) : posts.length === 0 ? (
            <p className="text-dim text-xs text-center mt-8">{t("posts.empty")}</p>
          ) : (
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 bg-panel border-b border-border">
                <tr>
                  <th className="text-left px-4 py-2 font-semibold text-dim w-32">{t("posts.col_date")}</th>
                  <th className="text-left px-4 py-2 font-semibold text-dim w-16">{t("posts.col_status")}</th>
                  <th className="text-left px-4 py-2 font-semibold text-dim">{t("posts.col_file")}</th>
                </tr>
              </thead>
              <tbody>
                {posts.map(p => (
                  <tr key={p.post_id}
                    onDoubleClick={() => p.file && openFile(p.file)}
                    className={`border-b border-border/30 transition-colors ${p.status === "gone" ? "bg-red-900/10 hover:bg-red-900/20" : "hover:bg-hover"} ${p.file ? "cursor-pointer" : ""}`}
                  >
                    <td className="px-4 py-1.5 text-dim font-mono whitespace-nowrap">{p.date || "—"}</td>
                    <td className="px-4 py-1.5">
                      {p.status === "gone" ? (
                        <span className="px-1.5 py-0.5 rounded text-xs bg-red-700 text-white">{t("posts.gone_badge")}</span>
                      ) : p.status === "ok" ? (
                        <span className="text-green-400">✓</span>
                      ) : (
                        <span className="text-dim">—</span>
                      )}
                    </td>
                    <td className={`px-4 py-1.5 font-mono truncate max-w-xs ${
                      p.status === "gone" ? "text-red-400" : "text-text"
                    }`} title={p.file}>
                      {p.file || p.post_id}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-2 px-5 py-3 border-t border-border shrink-0">
          <button
            onClick={handleCheck}
            disabled={job.running}
            className="px-4 py-1.5 rounded text-xs bg-accent text-white
                       disabled:opacity-40 hover:opacity-90 transition-opacity"
          >
            {job.running
              ? t("posts.checking_btn")
              : unchecked > 0
                ? `▶ ${t("posts.check_btn")} (${unchecked} ${t("posts.pending")})`
                : t("posts.recheck")}
          </button>
          <button onClick={onClose}
            className="ml-auto px-4 py-1.5 rounded text-xs text-dim hover:text-text hover:bg-hover transition-colors">
            {t("close")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Context menu ──────────────────────────────────────────────────────────────
interface MenuItem {
  label: string;
  danger?: boolean;
  divider?: boolean;
  submenu?: MenuItem[];
  onClick: () => void;
}

function ContextMenu({
  items, onClose, x, y,
}: { items: MenuItem[]; onClose: () => void; x?: number; y?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [subIdx, setSubIdx] = useState<number | null>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const fixed = x !== undefined && y !== undefined;
  const cls = fixed
    ? "fixed z-50 w-52 rounded border border-border bg-panel shadow-xl py-1"
    : "absolute z-50 right-0 top-6 w-52 rounded border border-border bg-panel shadow-xl py-1";
  const style = fixed ? { left: x, top: y } : undefined;

  return (
    <div ref={ref} className={cls} style={style}>
      {items.map((item, i) =>
        item.divider ? (
          <div key={i} className="border-t border-border my-1" />
        ) : item.submenu ? (
          <div
            key={i}
            className="relative"
            onMouseEnter={() => setSubIdx(i)}
            onMouseLeave={() => setSubIdx(null)}
          >
            <button className={`w-full text-left px-3 py-1.5 text-xs hover:bg-hover transition-colors flex items-center justify-between ${item.danger ? "text-red-400" : "text-text"}`}>
              {item.label}
              <span className="text-dim text-[10px] ml-2">▶</span>
            </button>
            {subIdx === i && (
              <div className="absolute left-full top-0 w-52 rounded border border-border bg-panel shadow-xl py-1 z-50">
                {item.submenu.map((sub, j) =>
                  sub.divider ? (
                    <div key={j} className="border-t border-border my-1" />
                  ) : (
                    <button key={j}
                      onClick={() => { sub.onClick(); onClose(); }}
                      className={`w-full text-left px-3 py-1.5 text-xs hover:bg-hover transition-colors ${sub.danger ? "text-red-400" : "text-text"}`}>
                      {sub.label}
                    </button>
                  )
                )}
              </div>
            )}
          </div>
        ) : (
          <button key={i}
            onClick={() => { item.onClick(); onClose(); }}
            onMouseEnter={() => setSubIdx(null)}
            className={`w-full text-left px-3 py-1.5 text-xs hover:bg-hover transition-colors ${
              item.danger ? "text-red-400" : "text-text"
            }`}>
            {item.label}
          </button>
        )
      )}
    </div>
  );
}

// ── Entry row ─────────────────────────────────────────────────────────────────
function EntryRow({
  entry, creators, onRefresh,
}: { entry: Entry; creators: Creator[]; onRefresh: () => void }) {
  const { t } = useLang();
  const [menu, setMenu]           = useState(false);
  const [menuPos, setMenuPos]     = useState({ x: 0, y: 0 });
  const [showPosts, setShowPosts] = useState(false);
  const aid   = accountId(entry);
  const dname = displayName(entry);

  const moveTargets = creators.filter(c => c.id !== entry.creator_id);
  const moveSubmenu: MenuItem[] = [
    ...moveTargets.map(c => ({
      label: c.name,
      onClick: () => assignEntry(entry.id, c.id).then(onRefresh),
    })),
    ...(entry.creator_id ? [
      ...(moveTargets.length > 0 ? [{ label: "", divider: true, onClick: () => {} }] : []),
      { label: t("ctx.unassign"), onClick: () => assignEntry(entry.id, null).then(onRefresh) },
    ] : []),
    ...(moveTargets.length > 0 || entry.creator_id ? [{ label: "", divider: true, onClick: () => {} }] : []),
    { label: `${t("acc.create")} "${dname}"`, onClick: () => addCreator(dname).then(c => assignEntry(entry.id, c.id)).then(onRefresh) },
  ];

  const menuItems: MenuItem[] = [
    { label: t("ctx.posts"),       onClick: () => setShowPosts(true) },
    { label: "", divider: true,    onClick: () => {} },
    { label: t("ctx.move_to"),     submenu: moveSubmenu, onClick: () => {} },
    { label: "", divider: true,    onClick: () => {} },
    { label: t("ctx.remove_acc"),  danger: true, onClick: () => removeEntry(entry.id).then(onRefresh) },
  ];

  const openAt = (x: number, y: number) => {
    setMenuPos({ x, y });
    setMenu(true);
  };

  return (
    <>
      <div
        className="flex items-center gap-3 px-3 py-3 hover:bg-hover group transition-colors relative"
        onContextMenu={e => { e.preventDefault(); openAt(e.clientX, e.clientY); }}
        onDoubleClick={() => setShowPosts(true)}
      >
        <AvatarImg platform={entry.platform} accId={aid} />
        <div className="flex-1 min-w-0">
          <div className="text-sm text-text truncate font-medium">{dname}</div>
          <div className="text-xs text-dim mt-0.5">{entry.platform}</div>
        </div>
        <button
          onClick={e => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); openAt(r.left, r.bottom + 4); }}
          className="opacity-0 group-hover:opacity-100 text-dim hover:text-text transition-opacity text-sm px-1"
        >
          ⋯
        </button>
        {menu && <ContextMenu items={menuItems} onClose={() => setMenu(false)} x={menuPos.x} y={menuPos.y} />}
      </div>
      {showPosts && <PostsModal entry={entry} onClose={() => setShowPosts(false)} />}
    </>
  );
}

// ── Creator group ─────────────────────────────────────────────────────────────
function CreatorGroup({
  creator, entries, allCreators, onRefresh,
}: { creator: Creator; entries: Entry[]; allCreators: Creator[]; onRefresh: () => void }) {
  const { t } = useLang();
  const [menu, setMenu]         = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [nameVal, setNameVal]   = useState(creator.name);

  const commitRename = async () => {
    setRenaming(false);
    if (nameVal.trim() && nameVal.trim() !== creator.name) {
      await renameCreator(creator.id, nameVal.trim());
      onRefresh();
    } else {
      setNameVal(creator.name);
    }
  };

  const menuItems: MenuItem[] = [
    { label: t("ctx.rename"),       onClick: () => setRenaming(true) },
    { label: t("ctx.delete_group"), danger: true, onClick: () => removeCreator(creator.id).then(onRefresh) },
  ];

  return (
    <div className="border border-border rounded-md overflow-hidden mb-3">
      <div className="flex items-center gap-2 px-3 py-2 bg-panel group relative">
        <div className="w-1 h-4 rounded-full bg-accent shrink-0" />
        {renaming ? (
          <input
            autoFocus
            value={nameVal}
            onChange={e => setNameVal(e.target.value)}
            onBlur={commitRename}
            onKeyDown={e => {
              if (e.key === "Enter") commitRename();
              if (e.key === "Escape") { setRenaming(false); setNameVal(creator.name); }
            }}
            className="flex-1 bg-bg border border-accent rounded px-2 py-0.5 text-sm text-text focus:outline-none"
          />
        ) : (
          <span className="flex-1 font-semibold text-sm text-text">{creator.name}</span>
        )}
        <span className="text-xs text-dim">{entries.length}</span>
        <button
          onClick={() => setMenu(v => !v)}
          className="opacity-0 group-hover:opacity-100 text-dim hover:text-text transition-opacity px-1"
        >
          ⋯
        </button>
        {menu && <ContextMenu items={menuItems} onClose={() => setMenu(false)} />}
      </div>

      <div className="bg-bg divide-y divide-border/50">
        {entries.length === 0 ? (
          <p className="px-3 py-3 text-xs text-dim italic">{t("acc.no_entries")}</p>
        ) : (
          entries.map(e => (
            <EntryRow key={e.id} entry={e} creators={allCreators} onRefresh={onRefresh} />
          ))
        )}
      </div>
    </div>
  );
}

// ── Add Entry modal ───────────────────────────────────────────────────────────
const PLATFORMS = ["x", "douyin", "bilibili"] as const;

function AddEntryModal({
  creators, onAdd, onClose,
}: { creators: Creator[]; onAdd: () => void; onClose: () => void }) {
  const { t } = useLang();
  const [platform, setPlatform]   = useState<string>("x");
  const [handle, setHandle]       = useState("");
  const [creatorId, setCreatorId] = useState<string>("");
  const [error, setError]         = useState("");

  const hints: Record<string, string> = {
    x:        t("entry.hint.x"),
    douyin:   t("entry.hint.douyin"),
    bilibili: t("entry.hint.bilibili"),
  };

  const submit = async () => {
    const h = handle.trim();
    if (!h) { setError(t("entry.required")); return; }
    try {
      await addEntry(platform, h, creatorId || null);
      onAdd();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-panel border border-border rounded-lg w-96 p-5 shadow-xl">
        <h2 className="font-semibold text-text mb-4">{t("entry.title")}</h2>

        <label className="block text-xs text-dim mb-1">{t("entry.platform")}</label>
        <div className="flex gap-1 mb-4">
          {PLATFORMS.map(p => (
            <button key={p} onClick={() => setPlatform(p)}
              className={`flex-1 py-1.5 rounded text-xs transition-colors ${
                platform === p ? "bg-accent text-white" : "bg-bg border border-border text-dim hover:bg-hover"
              }`}>
              {PLATFORM_META[p]?.label ?? p}
            </button>
          ))}
        </div>

        <label className="block text-xs text-dim mb-1">{t("entry.handle")}</label>
        <input
          autoFocus
          value={handle}
          onChange={e => setHandle(e.target.value)}
          onKeyDown={e => e.key === "Enter" && submit()}
          placeholder={hints[platform]}
          className="w-full px-3 py-1.5 rounded bg-bg border border-border text-text text-sm
                     placeholder:text-dim focus:outline-none focus:border-accent mb-4"
        />

        <label className="block text-xs text-dim mb-1">{t("entry.group")}</label>
        <select value={creatorId} onChange={e => setCreatorId(e.target.value)}
          className="w-full px-3 py-1.5 rounded bg-bg border border-border text-text text-sm
                     focus:outline-none focus:border-accent mb-4">
          <option value="">{t("entry.unassigned")}</option>
          {creators.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>

        {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

        <div className="flex justify-end gap-2">
          <button onClick={onClose}
            className="px-4 py-1.5 rounded text-sm text-dim hover:text-text hover:bg-hover transition-colors">
            {t("cancel")}
          </button>
          <button onClick={submit}
            className="px-4 py-1.5 rounded text-sm bg-accent text-white hover:opacity-90 transition-opacity">
            {t("entry.add")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Accounts page ─────────────────────────────────────────────────────────────
export default function Accounts({ active }: { active: boolean }) {
  const { t } = useLang();
  const [data, setData]               = useState<AccountsData>({ creators: [], entries: [] });
  const [showAdd, setShowAdd]         = useState(false);
  const [newGroup, setNewGroup]       = useState("");
  const [addingGroup, setAddingGroup] = useState(false);

  const refresh = useCallback(async () => {
    try { setData(await getAccounts()); } catch { /* ignore */ }
  }, []);

  useEffect(() => { if (active) refresh(); }, [active, refresh]);

  const grouped = new Map<string | null, Entry[]>();
  grouped.set(null, []);
  data.creators.forEach(c => grouped.set(c.id, []));
  data.entries.forEach(e => {
    const key = e.creator_id ?? null;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key)!.push(e);
  });

  const unassigned = grouped.get(null) ?? [];

  const createGroup = async () => {
    const n = newGroup.trim();
    if (!n) return;
    await addCreator(n);
    setNewGroup("");
    setAddingGroup(false);
    refresh();
  };

  return (
    <div className="flex flex-col h-full bg-bg">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          {addingGroup ? (
            <>
              <input autoFocus value={newGroup} onChange={e => setNewGroup(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") createGroup(); if (e.key === "Escape") { setAddingGroup(false); setNewGroup(""); } }}
                placeholder={t("acc.group_ph")}
                className="px-2 py-1 rounded bg-panel border border-accent text-text text-xs focus:outline-none w-36"
              />
              <button onClick={createGroup}
                className="px-2 py-1 rounded text-xs bg-accent text-white hover:opacity-90">{t("acc.create")}</button>
              <button onClick={() => { setAddingGroup(false); setNewGroup(""); }}
                className="text-xs text-dim hover:text-text">{t("cancel")}</button>
            </>
          ) : (
            <button onClick={() => setAddingGroup(true)}
              className="text-xs text-dim hover:text-text transition-colors">{t("acc.add_group")}</button>
          )}
        </div>
        <button onClick={() => setShowAdd(true)}
          className="px-3 py-1.5 rounded text-xs bg-accent text-white hover:opacity-90 transition-opacity">
          {t("acc.add_account")}
        </button>
      </div>

      {/* Scrollable list */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {data.entries.length === 0 ? (
          <p className="text-dim text-sm text-center mt-12">{t("acc.empty")}</p>
        ) : (
          <>
            {unassigned.length > 0 && (
              <div className="border border-border rounded-md overflow-hidden mb-3">
                <div className="px-3 py-2 bg-panel text-xs text-dim font-semibold">{t("acc.unassigned")}</div>
                <div className="bg-bg divide-y divide-border/50">
                  {unassigned.map(e => (
                    <EntryRow key={e.id} entry={e} creators={data.creators} onRefresh={refresh} />
                  ))}
                </div>
              </div>
            )}
            {data.creators.map(c => (
              <CreatorGroup key={c.id} creator={c}
                entries={grouped.get(c.id) ?? []} allCreators={data.creators} onRefresh={refresh} />
            ))}
          </>
        )}
      </div>

      {showAdd && (
        <AddEntryModal creators={data.creators} onAdd={refresh} onClose={() => setShowAdd(false)} />
      )}
    </div>
  );
}
