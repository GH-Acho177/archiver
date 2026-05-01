import { useState } from "react";
import Dashboard from "./pages/Dashboard";
import Accounts  from "./pages/Accounts";
import Downloads from "./pages/Downloads";
import Settings  from "./pages/Settings";
import { LangProvider, useLang } from "./i18n";
import { TitleBar } from "./components/TitleBar";

type Page = "dashboard" | "accounts" | "downloads" | "settings";

function AppShell() {
  const [page, setPage] = useState<Page>("dashboard");
  const { t } = useLang();

  const NAV = [
    { id: "dashboard" as Page, icon: "◈", label: t("nav.dashboard") },
    { id: "accounts"  as Page, icon: "☰", label: t("nav.accounts")  },
    { id: "downloads" as Page, icon: "⬇", label: t("nav.downloads") },
    { id: "settings"  as Page, icon: "⚙", label: t("nav.settings")  },
  ];

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-bg text-text">
      <TitleBar />

      <div className="flex flex-1 min-h-0">
        {/* Sidebar */}
        <aside className="flex flex-col w-48 shrink-0 bg-panel border-r border-border">
          <nav className="flex flex-col mt-2">
            {NAV.map(({ id, icon, label }) => (
              <button
                key={id}
                onClick={() => setPage(id)}
                className={[
                  "flex items-center gap-3 px-4 py-3 text-left transition-colors",
                  page === id
                    ? "bg-hover text-text border-l-2 border-accent"
                    : "text-dim hover:bg-hover hover:text-text border-l-2 border-transparent",
                ].join(" ")}
              >
                <span className={page === id ? "text-accent" : ""}>{icon}</span>
                <span className={`text-sm ${page === id ? "font-semibold text-text" : ""}`}>
                  {label}
                </span>
              </button>
            ))}
          </nav>
        </aside>

        {/* Main content — all pages mounted, only active one visible */}
        <main className="flex-1 min-w-0 overflow-hidden relative">
          <div className={`absolute inset-0 ${page === "dashboard" ? "z-10" : "z-0 pointer-events-none"}`}>
            <Dashboard active={page === "dashboard"} />
          </div>
          <div className={`absolute inset-0 ${page === "accounts" ? "z-10" : "z-0 pointer-events-none"}`}>
            <Accounts active={page === "accounts"} />
          </div>
          <div className={`absolute inset-0 ${page === "downloads" ? "z-10" : "z-0 pointer-events-none"}`}>
            <Downloads active={page === "downloads"} />
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
