declare global {
  interface Window {
    pywebview?: { api: {
      minimize_window(): void;
      start_drag(): void;
      toggle_maximize(): void;
      close_window(): void;
      is_maximized(): Promise<boolean>;
    }};
  }
}

const api = () => window.pywebview?.api;

export function TitleBar({ hideControls = false }: { hideControls?: boolean }) {
  const [maximized, setMaximized] = useState(false);

  const refreshWindowState = useCallback(() => {
    api()?.is_maximized?.().then(setMaximized).catch(() => {});
  }, []);

  const toggleMaximize = useCallback(() => {
    api()?.toggle_maximize();
    window.setTimeout(refreshWindowState, 80);
  }, [refreshWindowState]);

  useEffect(() => {
    refreshWindowState();
    window.addEventListener("pywebviewready", refreshWindowState);
    window.addEventListener("resize", refreshWindowState);
    return () => {
      window.removeEventListener("pywebviewready", refreshWindowState);
      window.removeEventListener("resize", refreshWindowState);
    };
  }, [refreshWindowState]);

  return (
    <div
      className="pywebview-drag-region flex h-9 shrink-0 items-center border-b border-border bg-panel select-none"
      onMouseDown={event => {
        if (event.button === 0) api()?.start_drag();
      }}
      onDoubleClick={toggleMaximize}
    >
      <span className="mx-3 h-2 w-2 rounded-sm bg-accent" />
      <span className="text-xs font-semibold tracking-wide text-text">Archiver</span>
      <div
        className={`${hideControls ? "invisible pointer-events-none" : "visible"} ml-auto flex`}
        onMouseDown={e => e.stopPropagation()}
        onDoubleClick={e => e.stopPropagation()}
      >
        <button
          className="flex h-9 w-11 items-center justify-center text-dim transition-colors hover:bg-hover hover:text-text"
          onClick={() => api()?.minimize_window()}
          title="Minimize"
        >
          ─
        </button>
        <button
          className="flex h-9 w-11 items-center justify-center text-dim transition-colors hover:bg-hover hover:text-text"
          onClick={toggleMaximize}
          title={maximized ? "Restore" : "Maximize"}
          aria-label={maximized ? "Restore window" : "Maximize window"}
        >
          {maximized ? (
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
              <path d="M3.5 1.5h7v7h-2v-5h-5v-2Z" stroke="currentColor" />
              <rect x="1.5" y="3.5" width="7" height="7" stroke="currentColor" />
            </svg>
          ) : (
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none" aria-hidden="true">
              <rect x="1.5" y="1.5" width="8" height="8" stroke="currentColor" />
            </svg>
          )}
        </button>
        <button
          className="flex h-9 w-11 items-center justify-center text-dim transition-colors hover:bg-[#c42b1c] hover:text-white"
          onClick={() => api()?.close_window()}
          title="Close"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
import { useCallback, useEffect, useState } from "react";
