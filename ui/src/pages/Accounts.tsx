import { useCallback, useEffect, useRef, useState } from "react";
import {
  addCreator, addEntryFromLink, assignEntry, getAccounts,
  getViewerAccounts,
  removeCreator, removeEntry, renameCreator, clearEntryArchive,
  avatarUrl, fetchAvatar,
  getPosts, startCheck, getCheckStatus, openFile, openPostInViewer, redownloadFile,
  PLATFORM_META, type AccountsData, type Creator, type Entry,
  type PostEntry, type CheckStatus, type ViewerAccount,
} from "../api";
import { PlatformChip } from "../components/PlatformChip";
import { ContextMenu, type MenuItem } from "../components/ContextMenu";
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
  const [ok, setOk] = useState(true);
  const [revision, setRevision] = useState("");
  const refreshAttempted = useRef(false);

  useEffect(() => {
    setOk(true);
    setRevision("");
    refreshAttempted.current = false;
  }, [platform, accId]);

  if (!ok) {
    return <PlatformChip platform={platform} className="w-20 h-20 p-4" />;
  }
  return (
    <img
      src={`${avatarUrl(platform, accId)}${revision ? `?t=${revision}` : ""}`}
      alt=""
      onError={() => {
        if (refreshAttempted.current) {
          setOk(false);
          return;
        }
        refreshAttempted.current = true;
        fetchAvatar(platform, accId)
          .then(() => setRevision(String(Date.now())))
          .catch(() => setOk(false));
      }}
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
  const [rowMenu, setRowMenu]   = useState<{x:number; y:number; p:PostEntry} | null>(null);

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
  const missing   = posts.filter(p => p.status === "missing").length;
  const localFiles = posts.reduce(
    (total, post) => total + (post.files?.length ?? (post.file ? 1 : 0)), 0,
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-panel border border-border rounded-lg w-[600px] max-h-[80vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-border shrink-0">
          <AvatarImg platform={entry.platform} accId={aid} />
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-text text-sm">{dname}</div>
            <div className="text-xs text-dim mt-0.5">
              {loading ? t("loading") : `${localFiles} ${t("posts.local_files")}`}
              {gone > 0 && <span className="ml-2 text-red-400">⚠ {gone} {t("posts.gone_count")}</span>}
              {missing > 0 && (
                <span className="ml-2 text-amber-400">! {missing} {t("posts.download_missing")}</span>
              )}
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
                    onDoubleClick={() => p.file && openPostInViewer(p.file)}
                    onContextMenu={e => {
                      if (p.status === "missing") return;
                      e.preventDefault(); setRowMenu({ x: e.clientX, y: e.clientY, p });
                    }}
                    className={`border-b border-border/30 transition-colors cursor-pointer ${p.status === "gone" ? "bg-red-900/10 hover:bg-red-900/20" : "hover:bg-hover"}`}
                  >
                    <td className="px-4 py-1.5 text-dim font-mono whitespace-nowrap">{p.date || "—"}</td>
                    <td className="px-4 py-1.5">
                      {p.status === "missing" ? (
                        <span
                          className="text-amber-400 font-semibold"
                          title="Post is available remotely but missing locally"
                        >{t("posts.missing_badge")}</span>
                      ) : p.status === "gone" ? (
                        <span
                          className="text-red-400 font-semibold"
                          title="Remote post was deleted; the local file still exists"
                        >{t("posts.gone_badge")}</span>
                      ) : p.status === "ok" ? (
                        <span
                          className="text-green-400 font-semibold"
                          title="Local file was found on the remote platform"
                        >✓</span>
                      ) : (
                        <span className="text-dim" title="Local file has not been verified remotely">—</span>
                      )}
                    </td>
                    <td className={`px-4 py-1.5 font-mono max-w-xs ${
                      p.status === "gone" ? "text-red-400" : "text-text"
                    }`} title={(p.files?.length ? p.files : [p.file]).filter(Boolean).join("\n")}>
                      {p.files?.length ? (
                        <div className="flex flex-col gap-1 py-0.5">
                          {p.files.map(file => (
                            <button
                              key={file}
                              type="button"
                              onDoubleClick={event => {
                                event.stopPropagation();
                                openPostInViewer(file);
                              }}
                              className="block max-w-full truncate text-left hover:underline"
                              title={file}
                            >
                              {file}
                            </button>
                          ))}
                        </div>
                      ) : (p.expected_name || p.post_id)}
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
      {rowMenu && (
        <ContextMenu
          x={rowMenu.x} y={rowMenu.y}
          onClose={() => setRowMenu(null)}
          items={[
            ...(rowMenu.p.file ? [{ label: t("ctx.open"), onClick: () => openFile(rowMenu.p.file).catch(() => {}) }] : []),
            { label: t("ctx.redownload"), onClick: () => redownloadFile(entry.platform, rowMenu.p.file || undefined, rowMenu.p.post_id).catch(() => {}) },
          ]}
        />
      )}
    </div>
  );
}

// ── Context menu ──────────────────────────────────────────────────────────────

// ── Entry row ─────────────────────────────────────────────────────────────────
function EntryRow({
  entry, creators, archive, onRefresh,
}: { entry: Entry; creators: Creator[]; archive?: ViewerAccount; onRefresh: () => void }) {
  const { t } = useLang();
  const [menu, setMenu]           = useState(false);
  const [menuPos, setMenuPos]     = useState({ x: 0, y: 0 });
  const [showPosts, setShowPosts] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveNotice, setArchiveNotice] = useState("");
  const [postAlerts, setPostAlerts] = useState({ missing: 0, gone: 0 });
  const aid   = accountId(entry);
  const dname = displayName(entry);

  const loadPostAlerts = useCallback(() => {
    getPosts(entry.platform, aid)
      .then(posts => setPostAlerts({
        missing: posts.filter(post => post.status === "missing").length,
        gone: posts.filter(post => post.status === "gone").length,
      }))
      .catch(() => {});
  }, [entry.platform, aid]);

  useEffect(() => { loadPostAlerts(); }, [loadPostAlerts]);

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
    { label: "Open in Viewer", onClick: () => window.dispatchEvent(
      new CustomEvent("archiver:open-viewer", { detail: `${entry.platform}:${aid}` })
    ) },
    {
      label: t("ctx.clear_archive"),
      onClick: () => setConfirmArchive(true),
    },
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
          <div className="flex flex-wrap items-center gap-x-2 text-xs text-dim mt-0.5">
            <span>{entry.platform}</span>
            <span className="text-emerald-400">Tracked</span>
            {archive ? (
              <span>{archive.posts.toLocaleString()} archived posts</span>
            ) : (
              <span className="text-dim/70">No local archive</span>
            )}
            {postAlerts.gone > 0 && (
              <span className="text-red-400">⚠ {postAlerts.gone} {t("posts.gone_count")}</span>
            )}
            {postAlerts.missing > 0 && (
              <span className="text-amber-400">! {postAlerts.missing} {t("posts.download_missing")}</span>
            )}
          </div>
        </div>
        <button
          onClick={event => { event.stopPropagation(); setShowPosts(true); }}
          className="rounded border border-border px-2.5 py-1 text-xs text-dim hover:bg-hover hover:text-text"
        >
          {t("ctx.posts")}
        </button>
        {archive && (
          <button
            onClick={event => {
              event.stopPropagation();
              window.dispatchEvent(new CustomEvent(
                "archiver:open-viewer", { detail: `${entry.platform}:${aid}` }
              ));
            }}
            className="rounded border border-border px-2.5 py-1 text-xs text-accent opacity-70 hover:bg-hover hover:opacity-100"
          >
            Viewer
          </button>
        )}
        <button
          onClick={e => { e.stopPropagation(); const r = e.currentTarget.getBoundingClientRect(); openAt(r.left, r.bottom + 4); }}
          className="opacity-0 group-hover:opacity-100 text-dim hover:text-text transition-opacity text-sm px-1"
        >
          ⋯
        </button>
        {menu && <ContextMenu items={menuItems} onClose={() => setMenu(false)} x={menuPos.x} y={menuPos.y} />}
      </div>
      {showPosts && <PostsModal entry={entry} onClose={() => { setShowPosts(false); loadPostAlerts(); }} />}
      {archiveNotice && (
        <div className="fixed right-5 bottom-5 z-[250] max-w-sm rounded-md border border-border bg-panel px-4 py-3 text-xs text-text shadow-xl">
          {archiveNotice}
        </div>
      )}
      {confirmArchive && (
        <div className="fixed inset-0 z-[240] flex items-center justify-center bg-black/50">
          <div className="w-96 rounded-lg border border-border bg-panel p-5 shadow-xl">
            <h2 className="mb-2 font-semibold text-text">{t("ctx.clear_archive_title")}</h2>
            <p className="mb-1 text-sm text-text">{dname}</p>
            <p className="mb-5 text-xs text-dim">{t("ctx.clear_archive_confirm")}</p>
            <div className="flex justify-end gap-2">
              <button
                disabled={archiveBusy}
                onClick={() => setConfirmArchive(false)}
                className="rounded px-4 py-1.5 text-sm text-dim hover:bg-hover hover:text-text disabled:opacity-50">
                {t("cancel")}
              </button>
              <button
                disabled={archiveBusy}
                onClick={async () => {
                  setArchiveBusy(true);
                  try {
                    const result = await clearEntryArchive(entry.id);
                    setConfirmArchive(false);
                    setArchiveNotice(t(
                      result.cleared ? "ctx.clear_archive_done" : "ctx.clear_archive_empty"
                    ));
                    setTimeout(() => setArchiveNotice(""), 3000);
                  } catch (error: unknown) {
                    setArchiveNotice(error instanceof Error ? error.message : String(error));
                    setTimeout(() => setArchiveNotice(""), 4000);
                  } finally {
                    setArchiveBusy(false);
                  }
                }}
                className="rounded bg-red-600 px-4 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50">
                {archiveBusy ? "…" : t("ctx.clear_archive_action")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ── Creator group ─────────────────────────────────────────────────────────────
function CreatorGroup({
  creator, entries, allCreators, archiveByKey, onRefresh,
}: { creator: Creator; entries: Entry[]; allCreators: Creator[]; archiveByKey: Map<string, ViewerAccount>; onRefresh: () => void }) {
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
            <EntryRow key={e.id} entry={e} creators={allCreators}
              archive={archiveByKey.get(`${e.platform}:${accountId(e)}`)} onRefresh={onRefresh} />
          ))
        )}
      </div>
    </div>
  );
}

// ── Add Entry modal ───────────────────────────────────────────────────────────

function AddEntryModal({
  onAdd, onClose,
}: { onAdd: () => void; onClose: () => void }) {
  const { t } = useLang();
  const [url, setUrl]     = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy]   = useState(false);
  const [groups, setGroups] = useState<Creator[]>([]);
  const [choosingGroup, setChoosingGroup] = useState(false);
  const [added, setAdded] = useState<{
    id: string; platform: string; handle: string; display: string;
  } | null>(null);

  const finish = () => {
    onAdd();
    onClose();
  };

  const submit = async () => {
    const u = url.trim();
    if (!u) { setError(t("entry.required")); return; }
    setBusy(true);
    setError("");
    try {
      const entry = await addEntryFromLink(u);
      setAdded(entry);
      const accounts = await getAccounts();
      setGroups(accounts.creators);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-panel border border-border rounded-lg w-96 p-5 shadow-xl">
        <h2 className="font-semibold text-text mb-4">{t("entry.title")}</h2>

        {added && choosingGroup ? (
          <div className="mb-4">
            <p className="text-sm text-text mb-2">
              {t("entry.choose_group")}
            </p>
            <div className="max-h-60 overflow-y-auto border border-border rounded-md">
              {groups.length === 0 ? (
                <p className="px-3 py-2 text-xs text-dim">
                  {t("entry.no_groups")}
                </p>
              ) : groups.map(group => (
                <button
                  key={group.id}
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    setError("");
                    try {
                      await assignEntry(added.id, group.id);
                      finish();
                    } catch (e: unknown) {
                      setError(e instanceof Error ? e.message : String(e));
                      setBusy(false);
                    }
                  }}
                  className="w-full px-3 py-2 text-left text-sm text-text hover:bg-hover border-b border-border/40 last:border-b-0 disabled:opacity-50"
                >
                  {group.name}
                </button>
              ))}
            </div>
          </div>
        ) : added ? (
          <div className="mb-4">
            <p className="text-sm text-text mb-2">
              {t("entry.group_prompt").replace("{name}", added.display)}
            </p>
            <p className="text-xs text-dim">{t("entry.group_hint")}</p>
          </div>
        ) : (
          <>
            <label className="block text-xs text-dim mb-1">{t("entry.handle")}</label>
            <input
              autoFocus
              value={url}
              onChange={e => setUrl(e.target.value)}
              onKeyDown={e => e.key === "Enter" && !busy && submit()}
              placeholder={t("entry.link_hint")}
              className="w-full px-3 py-1.5 rounded bg-bg border border-border text-text text-sm
                         placeholder:text-dim focus:outline-none focus:border-accent mb-4"
            />
          </>
        )}

        {error && <p className="text-red-400 text-xs mb-3">{error}</p>}

        <div className="flex justify-end gap-2">
          {added && choosingGroup ? (
            <button
              onClick={() => setChoosingGroup(false)}
              disabled={busy}
              className="px-4 py-1.5 rounded text-sm text-dim hover:text-text hover:bg-hover transition-colors disabled:opacity-50"
            >
              {t("back")}
            </button>
          ) : added ? (
            <>
              <button onClick={() => setChoosingGroup(true)} disabled={busy}
                className="px-4 py-1.5 rounded text-sm text-dim hover:text-text hover:bg-hover transition-colors disabled:opacity-50">
                {t("entry.choose_existing")}
              </button>
              <button
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    const creator = await addCreator(added.display);
                    await assignEntry(added.id, creator.id);
                    finish();
                  } catch (e: unknown) {
                    setError(e instanceof Error ? e.message : String(e));
                    setBusy(false);
                  }
                }}
                className="px-4 py-1.5 rounded text-sm bg-accent text-white hover:opacity-90 transition-opacity disabled:opacity-50">
                {busy ? "…" : t("entry.create_group")}
              </button>
            </>
          ) : (
            <>
              <button onClick={onClose}
                className="px-4 py-1.5 rounded text-sm text-dim hover:text-text hover:bg-hover transition-colors">
                {t("cancel")}
              </button>
              <button onClick={submit} disabled={busy}
                className="px-4 py-1.5 rounded text-sm bg-accent text-white hover:opacity-90 transition-opacity disabled:opacity-50">
                {busy ? "…" : t("entry.add")}
              </button>
            </>
          )}
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
  const [search, setSearch]           = useState("");
  const [archiveAccounts, setArchiveAccounts] = useState<ViewerAccount[]>([]);
  const searchRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await getAccounts());
    } catch { /* ignore */ }
    try { setArchiveAccounts((await getViewerAccounts()).accounts); } catch { /* ignore */ }
  }, []);

  useEffect(() => { if (active) refresh(); }, [active, refresh]);

  useEffect(() => {
    if (!active) return;
    const focusSearch = () => searchRef.current?.focus();
    window.addEventListener("archiver:focus-search", focusSearch);
    return () => window.removeEventListener("archiver:focus-search", focusSearch);
  }, [active]);

  const grouped = new Map<string | null, Entry[]>();
  grouped.set(null, []);
  data.creators.forEach(c => grouped.set(c.id, []));
  data.entries.forEach(e => {
    const key = e.creator_id ?? null;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key)!.push(e);
  });

  const unassigned = grouped.get(null) ?? [];
  const query = search.trim().toLocaleLowerCase();
  const entryMatches = (entry: Entry) => !query || [
    displayName(entry), accountId(entry), entry.handle, entry.platform,
  ].some(value => value.toLocaleLowerCase().includes(query));
  const visibleUnassigned = unassigned.filter(entryMatches);
  const visibleGroups = data.creators.map(creator => {
    const entries = grouped.get(creator.id) ?? [];
    const groupMatches = query && creator.name.toLocaleLowerCase().includes(query);
    return {
      creator,
      entries: groupMatches ? entries : entries.filter(entryMatches),
    };
  }).filter(group => !query || group.entries.length > 0);
  const visibleAccountCount = visibleUnassigned.length
    + visibleGroups.reduce((total, group) => total + group.entries.length, 0);
  const trackedKeys = new Set(data.entries.map(entry => `${entry.platform}:${accountId(entry)}`));
  const archiveByKey = new Map(
    archiveAccounts.map(account => [`${account.platform}:${account.account_id}`, account] as const)
  );
  const archivedOnly = archiveAccounts.filter(account => {
    if (trackedKeys.has(`${account.platform}:${account.account_id}`)) return false;
    if (!query) return true;
    return [account.account, account.account_id, account.platform, account.group]
      .some(value => value.toLocaleLowerCase().includes(query));
  });

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
        <div className="flex items-center gap-2">
          <input
            ref={searchRef}
            type="search"
            value={search}
            onChange={event => setSearch(event.target.value)}
            placeholder={t("acc.search")}
            className="w-48 rounded border border-border bg-panel px-2.5 py-1.5 text-xs text-text placeholder:text-dim focus:border-accent focus:outline-none"
          />
          <button onClick={() => setShowAdd(true)}
            className="px-3 py-1.5 rounded text-xs bg-accent text-white hover:opacity-90 transition-opacity">
            {t("acc.add_account")}
          </button>
        </div>
      </div>

      {/* Scrollable list */}
      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-4 py-4">
        {data.entries.length === 0 && archiveAccounts.length === 0 ? (
          <p className="text-dim text-sm text-center mt-12">{t("acc.empty")}</p>
        ) : query && visibleAccountCount === 0 && archivedOnly.length === 0 ? (
          <p className="text-dim text-sm text-center mt-12">{t("acc.no_match")}</p>
        ) : (
          <>
            {visibleUnassigned.length > 0 && (
              <div className="border border-border rounded-md overflow-hidden mb-3">
                <div className="px-3 py-2 bg-panel text-xs text-dim font-semibold">{t("acc.unassigned")}</div>
                <div className="bg-bg divide-y divide-border/50">
                  {visibleUnassigned.map(e => (
                    <EntryRow key={e.id} entry={e} creators={data.creators}
                      archive={archiveByKey.get(`${e.platform}:${accountId(e)}`)} onRefresh={refresh} />
                  ))}
                </div>
              </div>
            )}
            {visibleGroups.map(({ creator, entries }) => (
              <CreatorGroup key={creator.id} creator={creator}
                entries={entries} allCreators={data.creators} archiveByKey={archiveByKey} onRefresh={refresh} />
            ))}
            {archivedOnly.length > 0 && (
              <div className="border border-border rounded-md overflow-hidden mb-3">
                <div className="px-3 py-2 bg-panel text-xs text-dim font-semibold">
                  Archived only · {archivedOnly.length}
                </div>
                <div className="bg-bg divide-y divide-border/50">
                  {archivedOnly.map(account => {
                    const base = ((window as unknown as Record<string, unknown>).__apiBase as string) || "";
                    return (
                      <button key={`${account.platform}:${account.account_id}`}
                        onClick={() => window.dispatchEvent(new CustomEvent(
                          "archiver:open-viewer", { detail: `${account.platform}:${account.account_id}` }
                        ))}
                        className="flex w-full items-center gap-3 px-3 py-3 text-left hover:bg-hover transition-colors">
                        <img src={`${base}/viewer/${account.avatar}`} alt="" className="h-9 w-9 rounded-full object-cover" />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium text-text">{account.account}</span>
                          <span className="block text-xs text-dim">
                            {account.platform} · {account.posts} posts{account.group ? ` · ${account.group}` : ""}
                          </span>
                        </span>
                        <span className="text-xs text-accent">Open in Viewer</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {showAdd && (
        <AddEntryModal onAdd={refresh} onClose={() => setShowAdd(false)} />
      )}
    </div>
  );
}
