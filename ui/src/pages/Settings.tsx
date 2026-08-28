import { useEffect, useState, useCallback, useRef } from "react";
import {
  getSettings, saveSettings, getCookies, saveCookies,
  getTgStatus, startTgBot, stopTgBot, resetDatabase,
  browseFolder,
  PLATFORM_META, type AppSettings, type TgStatus,
} from "../api";
import { PlatformChip } from "../components/PlatformChip";
import { useLang, type Lang, type Theme } from "../i18n";

const COOKIE_PLATFORMS = ["x", "douyin", "bilibili", "xiaohongshu"] as const;

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4">
      <label className="w-52 text-xs text-dim shrink-0">{label}</label>
      {children}
    </div>
  );
}

function NumInput({
  value, onChange, min, max, step,
}: { value: number; onChange: (n: number) => void; min?: number; max?: number; step?: number }) {
  return (
    <input
      type="number" min={min} max={max} step={step ?? 1}
      value={value}
      onChange={e => onChange(Number(e.target.value))}
      className="w-24 px-2 py-1 rounded bg-bg border border-border text-text text-xs
                 focus:outline-none focus:border-accent"
    />
  );
}


export default function Settings({ active }: { active: boolean }) {
  const { t, lang, setLang, theme, setTheme } = useLang();

  const [cfg, setCfg]         = useState<AppSettings>({});
  const [cookies, setCookies] = useState<Record<string, string>>({});
  const [saved, setSaved]     = useState(false);
  const [error, setError]     = useState("");

  const [tg, setTg]           = useState<TgStatus>({ status: "stopped", token_set: false });
  const [tgToken, setTgToken] = useState("");
  const [tgBusy, setTgBusy]   = useState(false);
  const [showToken, setShowToken] = useState(false);

  const [dbMsg, setDbMsg]     = useState("");
  const [dbBusy, setDbBusy]   = useState(false);
  const loaded = useRef(false);

  const refreshTg = useCallback(() => {
    getTgStatus().then(setTg).catch(() => {});
  }, []);

  useEffect(() => {
    if (!active || loaded.current) return;
    loaded.current = true;
    getSettings().then(setCfg).catch(() => {});
    Promise.all(
      COOKIE_PLATFORMS.map(p => getCookies(p).then(r => [p, r.content] as const))
    ).then(pairs => {
      setCookies(Object.fromEntries(pairs));
    }).catch(() => {});
    refreshTg();
  }, [active, refreshTg]);

  const set = <K extends keyof AppSettings>(k: K, v: AppSettings[K]) =>
    setCfg(s => ({ ...s, [k]: v }));

  const saveDownloadPath = async (path: string) => {
    setError("");
    try {
      await saveSettings({ download_path: path });
      const latest = await getSettings();
      setCfg(current => ({ ...current, download_path: latest.download_path }));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const handleSave = async () => {
    setError("");
    try {
      await saveSettings(cfg);
      await Promise.all(COOKIE_PLATFORMS.map(p => saveCookies(p, cookies[p] ?? "")));
      setCfg(await getSettings());
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    if (!active) return;
    const saveShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void handleSave();
      }
    };
    window.addEventListener("keydown", saveShortcut);
    return () => window.removeEventListener("keydown", saveShortcut);
  }, [active, cfg, cookies]);

  return (
    <div className="flex flex-col h-full bg-bg overflow-y-auto">
      <div className="max-w-2xl mx-auto w-full px-6 py-6 space-y-6">

        {/* Theme */}
        <section>
          <h2 className="text-sm font-semibold text-text mb-3">{t("set.theme")}</h2>
          <div className="bg-panel border border-border rounded-md p-4">
            <div className="flex gap-2">
              {(["dark", "light"] as Theme[]).map(th => (
                <button
                  key={th}
                  onClick={() => {
                    setTheme(th);
                    set("viewer_theme", th);
                    void saveSettings({ viewer_theme: th });
                  }}
                  className={`px-4 py-1.5 rounded text-sm transition-colors ${
                    theme === th
                      ? "bg-accent text-white"
                      : "border border-border text-dim hover:bg-hover hover:text-text"
                  }`}
                >
                  {th === "dark" ? t("set.dark") : t("set.light")}
                </button>
              ))}
            </div>
          </div>
        </section>

        {/* Language */}
        <section>
          <h2 className="text-sm font-semibold text-text mb-3">{t("set.language")}</h2>
          <div className="bg-panel border border-border rounded-md p-4">
            <div className="flex gap-2">
              {(["en", "zh"] as Lang[]).map(l => (
                <button
                  key={l}
                  onClick={() => setLang(l)}
                  className={`px-4 py-1.5 rounded text-sm transition-colors ${
                    lang === l
                      ? "bg-accent text-white"
                      : "border border-border text-dim hover:bg-hover hover:text-text"
                  }`}
                >
                  {l === "en" ? "English" : "中文"}
                </button>
              ))}
            </div>
          </div>
        </section>

        {/* Viewer playback */}
        <section>
          <h2 className="text-sm font-semibold text-text mb-1">Viewer</h2>
          <p className="mb-3 text-xs text-dim">Playback preferences used by Browse.</p>
          <div className="overflow-hidden rounded-md border border-border bg-panel">
            <div className="flex min-h-16 items-center gap-5 px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-text">Default volume</div>
                <div className="mt-0.5 text-xs text-dim">Applied whenever a video starts playing.</div>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                value={cfg.viewer_volume ?? 80}
                onChange={event => set("viewer_volume", Number(event.target.value))}
                className="w-36 accent-accent"
              />
              <span className="w-10 text-right font-mono text-xs text-dim">{cfg.viewer_volume ?? 80}%</span>
            </div>
            <label className="flex min-h-16 cursor-pointer items-center gap-5 border-t border-border px-4 py-3">
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-text">Loop videos</span>
                <span className="mt-0.5 block text-xs text-dim">Replay the current video automatically.</span>
              </span>
              <input
                type="checkbox"
                checked={cfg.viewer_loop ?? true}
                onChange={event => set("viewer_loop", event.target.checked)}
                className="h-4 w-4 accent-accent"
              />
            </label>
          </div>
        </section>

        {/* Download location */}
        <section>
          <h2 className="text-sm font-semibold text-text mb-3">{t("set.dl_location")}</h2>
          <div className="bg-panel border border-border rounded-md p-4">
            <div className="flex gap-2">
              <input
                type="text"
                value={cfg.download_path ?? ""}
                onChange={e => set("download_path", e.target.value)}
                onBlur={e => void saveDownloadPath(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    e.currentTarget.blur();
                  }
                }}
                placeholder="downloads"
                className="flex-1 px-3 py-1.5 rounded bg-bg border border-border text-text text-sm
                           placeholder:text-dim focus:outline-none focus:border-accent"
              />
              <button
                onClick={async () => {
                  const r = await browseFolder().catch(() => null);
                  if (r?.path) {
                    set("download_path", r.path);
                    await saveDownloadPath(r.path);
                  }
                }}
                className="px-3 py-1.5 rounded text-sm border border-border text-dim hover:text-text hover:border-accent transition-colors shrink-0"
              >
                {t("set.browse")}
              </button>
            </div>
          </div>
        </section>

        {/* Download options */}
        <section>
          <h2 className="text-sm font-semibold text-text mb-3">{t("set.dl_options")}</h2>
          <div className="bg-panel border border-border rounded-md p-4 space-y-4">
            <Field label={t("set.workers")}>
              <NumInput value={cfg.parallel_workers ?? 1} min={1} max={10}
                onChange={v => set("parallel_workers", v)} />
            </Field>
            <Field label={t("set.account_workers")}>
              <NumInput value={cfg.per_account_workers ?? 4} min={1} max={10}
                onChange={v => set("per_account_workers", v)} />
            </Field>
            <Field label={t("set.sleep_user")}>
              <NumInput value={cfg.sleep_user ?? 2} min={0} step={0.5}
                onChange={v => set("sleep_user", v)} />
            </Field>
            <Field label={t("set.sleep_req")}>
              <NumInput value={cfg.sleep_req ?? 1} min={0} step={0.5}
                onChange={v => set("sleep_req", v)} />
            </Field>
          </div>
        </section>

        {/* Auto-sync */}
        <section>
          <h2 className="text-sm font-semibold text-text mb-3">{t("set.auto_sync")}</h2>
          <div className="bg-panel border border-border rounded-md p-4 space-y-4">
            <Field label={t("set.enable_sync")}>
              <button
                onClick={() => set("auto_update_enabled", !cfg.auto_update_enabled)}
                className={`w-10 h-5 rounded-full transition-colors relative shrink-0 ${cfg.auto_update_enabled ? "bg-accent" : "bg-border"}`}
              >
                <span className={`absolute left-0 top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${cfg.auto_update_enabled ? "translate-x-[22px]" : "translate-x-0.5"}`} />
              </button>
            </Field>
            {cfg.auto_update_enabled && (
              <Field label={t("set.interval")}>
                <NumInput value={cfg.auto_update_interval ?? 60} min={1}
                  onChange={v => set("auto_update_interval", v)} />
              </Field>
            )}
          </div>
        </section>

        {/* Cookies */}
        <section>
          <h2 className="text-sm font-semibold text-text mb-1">{t("set.cookies")}</h2>
          <p className="text-xs text-dim mb-3">{t("set.cookies_hint")}</p>
          <div className="space-y-2">
            {COOKIE_PLATFORMS.map(p => {
              const meta = PLATFORM_META[p];
              const hasContent = !!(cookies[p]?.trim());
              const loadFile = (file: File) => {
                const reader = new FileReader();
                reader.onload = ev => setCookies(c => ({ ...c, [p]: (ev.target?.result as string) ?? "" }));
                reader.readAsText(file);
              };
              return (
                <div
                  key={p}
                  className="bg-panel border border-border rounded-md flex items-center gap-3 px-4 py-3"
                  onDragOver={e => e.preventDefault()}
                  onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) loadFile(f); }}
                >
                  <PlatformChip platform={p} />
                  <span className="text-xs font-semibold text-text">{meta.label}</span>
                  <span className={`text-xs ${hasContent ? "text-green-400" : "text-dim"}`}>
                    {hasContent ? t("set.loaded") : t("set.not_set")}
                  </span>
                  <div className="ml-auto flex items-center gap-2">
                    {hasContent && (
                      <button
                        onClick={() => setCookies(c => ({ ...c, [p]: "" }))}
                        className="text-xs text-dim hover:text-red-400 transition-colors"
                      >
                        {t("set.clear")}
                      </button>
                    )}
                    <label className="text-xs text-dim hover:text-text transition-colors cursor-pointer px-2 py-1 rounded border border-border hover:border-accent">
                      {t("set.browse")}
                      <input
                        type="file" accept=".txt" className="hidden"
                        onChange={e => { const f = e.target.files?.[0]; if (f) loadFile(f); e.target.value = ""; }}
                      />
                    </label>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* Save */}
        {error && (
          <div className="px-3 py-2 rounded bg-red-900/40 border border-red-700/50 text-red-300 text-xs">
            {error}
          </div>
        )}
        <div className="flex items-center gap-3">
          <button
            onClick={handleSave}
            title="Save settings (Ctrl+S)"
            className="px-5 py-2 rounded text-sm font-medium bg-accent text-white
                       hover:opacity-90 transition-opacity"
          >
            {t("set.save")}
          </button>
          {saved && <span className="text-xs text-green-400">{t("set.saved")}</span>}
        </div>

        {/* Telegram bot */}
        <section>
          <h2 className="text-sm font-semibold text-text mb-1">{t("set.tg_bot")}</h2>
          <p className="text-xs text-dim mb-3">{t("set.tg_hint")}</p>
          <div className="bg-panel border border-border rounded-md p-4 space-y-4">
            {/* Status */}
            <Field label={t("set.tg_status")}>
              <span className={`flex items-center gap-1.5 text-xs font-medium ${
                tg.status === "running" ? "text-green-400"
                  : tg.status === "error" ? "text-red-400"
                  : "text-dim"
              }`}>
                <span className={`w-2 h-2 rounded-full ${
                  tg.status === "running" ? "bg-green-400"
                    : tg.status === "error" ? "bg-red-400"
                    : "bg-dim"
                }`} />
                {tg.status === "running" ? t("set.tg_running")
                  : tg.status === "error" ? t("set.tg_error")
                  : t("set.tg_stopped")}
              </span>
            </Field>

            {/* Token input */}
            <Field label={t("set.tg_token")}>
              <div className="flex items-center gap-2 flex-1">
                <input
                  type={showToken ? "text" : "password"}
                  value={tgToken}
                  onChange={e => setTgToken(e.target.value)}
                  placeholder={tg.token_set ? t("set.tg_ph_saved") : t("set.tg_ph")}
                  className="flex-1 px-3 py-1 rounded bg-bg border border-border text-text text-xs
                             font-mono placeholder:text-dim focus:outline-none focus:border-accent"
                />
                <button
                  onClick={() => setShowToken(v => !v)}
                  className="text-xs text-dim hover:text-text transition-colors px-1"
                >
                  {showToken ? t("set.hide") : t("set.show")}
                </button>
              </div>
            </Field>

            {/* Actions */}
            <div className="flex gap-2">
              <button
                disabled={tgBusy || (!tgToken.trim() && !tg.token_set)}
                onClick={async () => {
                  setTgBusy(true);
                  try {
                    await startTgBot(tgToken.trim() || "reuse");
                    setTgToken("");
                    refreshTg();
                  } catch { /* ignore */ }
                  setTgBusy(false);
                }}
                className="px-4 py-1.5 rounded text-xs bg-accent text-white
                           disabled:opacity-40 hover:opacity-90 transition-opacity"
              >
                {tgBusy ? t("set.starting") : tgToken.trim() ? t("set.save_start") : t("set.restart")}
              </button>
              <button
                disabled={tgBusy || tg.status === "stopped"}
                onClick={async () => {
                  setTgBusy(true);
                  try { await stopTgBot(); refreshTg(); } catch { /* ignore */ }
                  setTgBusy(false);
                }}
                className="px-4 py-1.5 rounded text-xs bg-panel border border-border text-text
                           disabled:opacity-40 hover:bg-hover transition-colors"
              >
                {t("set.stop")}
              </button>
            </div>
          </div>
        </section>

        {/* Database reset */}
        <section className="pb-8">
          <h2 className="text-sm font-semibold text-text mb-3">{t("set.database")}</h2>
          <div className="bg-panel border border-border rounded-md p-4">
            <p className="text-xs text-dim mb-4">{t("set.db_hint")}</p>
            <div className="flex items-center gap-3">
              <button
                disabled={dbBusy}
                onClick={async () => {
                  setDbMsg("");
                  setDbBusy(true);
                  try {
                    await resetDatabase();
                    setDbMsg(t("set.db_cleared"));
                  } catch {
                    setDbMsg(t("set.db_failed"));
                  }
                  setDbBusy(false);
                  setTimeout(() => setDbMsg(""), 3000);
                }}
                className="px-4 py-1.5 rounded text-xs bg-red-700 text-white
                           disabled:opacity-40 hover:opacity-90 transition-opacity"
              >
                {dbBusy ? t("set.resetting") : t("set.reset_db")}
              </button>
              {dbMsg && (
                <span className={`text-xs ${dbMsg.startsWith("✓") ? "text-green-400" : "text-red-400"}`}>
                  {dbMsg}
                </span>
              )}
            </div>
          </div>
        </section>

      </div>
    </div>
  );
}
