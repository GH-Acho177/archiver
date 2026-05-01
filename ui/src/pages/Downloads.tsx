import { useEffect, useState, useCallback } from "react";
import { listDownloads, deleteDownload, openDownloadsFolder, PLATFORM_META, type DownloadFile } from "../api";
import { PlatformChip } from "../components/PlatformChip";
import { useLang } from "../i18n";

const PLAT_FILTER = ["all", "x", "douyin", "bilibili", "—"] as const;
type PlatFilter = typeof PLAT_FILTER[number];

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

export default function Downloads({ active }: { active: boolean }) {
  const { t } = useLang();
  const [files, setFiles]         = useState<DownloadFile[]>([]);
  const [filter, setFilter]       = useState<PlatFilter>("all");
  const [loading, setLoading]     = useState(false);
  const [deleting, setDeleting]   = useState<string | null>(null);
  const [confirm, setConfirm]     = useState<DownloadFile | null>(null);
  const [search, setSearch]       = useState("");

  const load = useCallback(() => {
    setLoading(true);
    listDownloads(500)
      .then(setFiles)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (active) load();
  }, [active, load]);

  const handleDelete = async (file: DownloadFile) => {
    setDeleting(file.path);
    setConfirm(null);
    try {
      await deleteDownload(file.path);
      setFiles(prev => prev.filter(f => f.path !== file.path));
    } catch { /* ignore */ }
    setDeleting(null);
  };

  const visible = files.filter(f => {
    if (filter !== "all" && f.platform !== filter) return false;
    if (search && !f.name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const totalSize = visible.reduce((s, f) => s + f.size, 0);

  return (
    <div className="flex flex-col h-full bg-bg overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-border shrink-0 flex-wrap">
        {/* Platform filter */}
        <div className="flex rounded overflow-hidden border border-border text-xs">
          {PLAT_FILTER.map(p => (
            <button
              key={p}
              onClick={() => setFilter(p)}
              className={`px-3 py-1.5 capitalize transition-colors ${
                filter === p ? "bg-accent text-white" : "bg-panel text-dim hover:bg-hover"
              }`}
            >
              {p === "all" ? t("dl_list.all") : (PLATFORM_META[p]?.label ?? p)}
            </button>
          ))}
        </div>

        {/* Search */}
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder={t("dl_list.search")}
          className="px-2 py-1 rounded bg-panel border border-border text-text text-xs
                     placeholder:text-dim focus:outline-none focus:border-accent w-44"
        />

        {/* Stats */}
        <span className="text-xs text-dim">
          {loading ? t("loading") : `${visible.length} ${t("dl_list.files")} · ${formatSize(totalSize)}`}
        </span>

        {/* Open folder */}
        <button
          onClick={() => openDownloadsFolder().catch(() => {})}
          className="ml-auto text-xs text-dim hover:text-text transition-colors"
        >
          {t("dl_list.open_folder")}
        </button>

        {/* Refresh */}
        <button
          onClick={load}
          className="text-xs text-dim hover:text-text transition-colors"
        >
          {t("refresh")}
        </button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {!loading && visible.length === 0 ? (
          <p className="text-dim text-sm text-center mt-12">
            {files.length === 0 ? t("dl_list.no_files") : t("dl_list.no_match")}
          </p>
        ) : (
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-panel border-b border-border z-10">
              <tr>
                <th className="text-left px-4 py-2 font-semibold text-dim w-8">{t("dl_list.col_plat")}</th>
                <th className="text-left px-4 py-2 font-semibold text-dim">{t("dl_list.col_name")}</th>
                <th className="text-right px-4 py-2 font-semibold text-dim w-20">{t("dl_list.col_size")}</th>
                <th className="text-center px-4 py-2 font-semibold text-dim w-32">{t("dl_list.col_date")}</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {visible.map(f => (
                <tr
                  key={f.path}
                  className="border-b border-border/40 hover:bg-hover transition-colors group"
                >
                  <td className="px-4 py-2">
                    <PlatformChip platform={f.platform} />
                  </td>
                  <td className="px-4 py-2 text-text font-mono truncate max-w-xs" title={f.name}>
                    {f.name}
                  </td>
                  <td className="px-4 py-2 text-right text-dim font-mono">
                    {formatSize(f.size)}
                  </td>
                  <td className="px-4 py-2 text-center text-dim">{f.date}</td>
                  <td className="px-2 py-2">
                    <button
                      onClick={() => setConfirm(f)}
                      disabled={deleting === f.path}
                      className="opacity-0 group-hover:opacity-100 text-dim hover:text-red-400
                                 disabled:opacity-40 transition-opacity text-xs px-1"
                      title={t("dl_list.delete")}
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Delete confirmation */}
      {confirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-panel border border-border rounded-lg w-96 p-5 shadow-xl">
            <h2 className="font-semibold text-text mb-2">{t("dl_list.del_title")}</h2>
            <p className="text-xs text-dim mb-1 font-mono break-all">{confirm.name}</p>
            <p className="text-xs text-dim mb-4">{t("dl_list.del_desc")}</p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirm(null)}
                className="px-4 py-1.5 rounded text-sm text-dim hover:text-text hover:bg-hover transition-colors"
              >
                {t("cancel")}
              </button>
              <button
                onClick={() => handleDelete(confirm)}
                className="px-4 py-1.5 rounded text-sm bg-red-600 text-white hover:opacity-90 transition-opacity"
              >
                {t("dl_list.delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
