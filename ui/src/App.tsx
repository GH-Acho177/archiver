import { useEffect, useState } from "react";
import Dashboard from "./pages/Dashboard";
import Accounts  from "./pages/Accounts";
import Settings  from "./pages/Settings";
import Viewer    from "./pages/Viewer";
import { LangProvider, useLang } from "./i18n";
import { TitleBar } from "./components/TitleBar";

type Page = "dashboard" | "accounts" | "viewer" | "settings";

function AppShell() {
  const [page, setPage] = useState<Page>("dashboard");
  const [viewerTarget, setViewerTarget] = useState("");
  const [viewerReturnPage, setViewerReturnPage] = useState<Page | null>(null);
  const [viewerFullscreen, setViewerFullscreen] = useState(false);
  const { t } = useLang();

  useEffect(() => {
    const openViewer = (event: Event) => {
      setViewerReturnPage(current => current ?? (page === "viewer" ? "dashboard" : page));
      setViewerTarget(String((event as CustomEvent<string>).detail || ""));
      setPage("viewer");
    };
    window.addEventListener("archiver:open-viewer", openViewer);
    const updateViewerFullscreen = (event: Event) =>
      setViewerFullscreen(Boolean((event as CustomEvent<boolean>).detail));
    window.addEventListener("archiver:viewer-fullscreen-layout", updateViewerFullscreen);
    return () => {
      window.removeEventListener("archiver:open-viewer", openViewer);
      window.removeEventListener("archiver:viewer-fullscreen-layout", updateViewerFullscreen);
    };
  }, [page]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const editing = target?.matches("input, textarea, select, [contenteditable='true']");
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        window.dispatchEvent(new CustomEvent("archiver:focus-search"));
        return;
      }
      if (event.altKey && event.key === "ArrowLeft" && page === "viewer" && viewerReturnPage) {
        event.preventDefault();
        setPage(viewerReturnPage);
        setViewerReturnPage(null);
        return;
      }
      if (editing || !event.ctrlKey || event.altKey || event.metaKey) return;
      const shortcuts: Record<string, Page> = {
        "1": "dashboard", "2": "viewer", "3": "accounts", "4": "settings",
      };
      const destination = shortcuts[event.key];
      if (!destination) return;
      event.preventDefault();
      if (destination === "viewer") setViewerTarget("");
      setViewerReturnPage(null);
      setPage(destination);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [page, viewerReturnPage]);

  const NAV = [
    { id: "dashboard" as Page, icon: "◈", label: t("nav.sync"), shortcut: 1 },
    { id: "viewer" as Page, icon: "▶", label: t("nav.browse"), shortcut: 2 },
    { id: "accounts" as Page, icon: "☰", label: t("nav.account"), shortcut: 3 },
    { id: "settings" as Page, icon: "⚙", label: t("nav.setting"), shortcut: 4 },
  ];

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-bg text-text">
      <TitleBar hideControls={viewerFullscreen} />

      <div className="flex flex-1 min-h-0">
        {/* Sidebar */}
        <aside className={`${viewerFullscreen ? "hidden" : "flex"} flex-col w-44 shrink-0 bg-panel border-r border-border px-2 py-3`}>
          <nav className="flex flex-col gap-1">
            {NAV.map(({ id, icon, label, shortcut }) => (
              <button
                key={id}
                onClick={() => { if (id === "viewer") setViewerTarget(""); setViewerReturnPage(null); setPage(id); }}
                title={`${label} (Ctrl+${shortcut})`}
                className={[
                  "group flex min-h-10 items-center gap-3 rounded-md px-3 py-2 text-left transition-colors focus-visible:outline-offset-0",
                  page === id
                    ? "bg-accent/15 text-text"
                    : "text-dim hover:bg-hover hover:text-text",
                ].join(" ")}
              >
                <span className={`grid h-5 w-5 place-items-center text-sm ${page === id ? "text-accent" : "text-dim group-hover:text-text"}`}>{icon}</span>
                <span className={`text-sm ${page === id ? "font-semibold text-text" : ""}`}>
                  {label}
                </span>
              </button>
            ))}
          </nav>
          <div className="mt-auto px-2 pt-3 text-[10px] leading-4 text-dim/70">
            Ctrl+1–4 navigate<br />Ctrl+F search
          </div>
        </aside>

        {/* Main content — all pages mounted, only active one visible */}
        <main className="flex-1 min-w-0 overflow-hidden relative">
          <div className={`absolute inset-0 ${page === "dashboard" ? "z-10" : "z-0 pointer-events-none"}`}>
            <Dashboard active={page === "dashboard"} />
          </div>
          <div className={`absolute inset-0 ${page === "accounts" ? "z-10" : "z-0 pointer-events-none"}`}>
            <Accounts active={page === "accounts"} />
          </div>
          <div className={`absolute inset-0 ${page === "viewer" ? "z-10" : "z-0 pointer-events-none"}`}>
            <Viewer
              active={page === "viewer"}
              target={viewerTarget}
            />
          </div>
          <div className={`absolute inset-0 ${page === "settings" ? "z-10" : "z-0 pointer-events-none"}`}>
            <Settings active={page === "settings"} />
          </div>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <LangProvider>
      <AppShell />
    </LangProvider>
  );
}
