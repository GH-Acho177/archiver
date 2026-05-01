declare global {
  interface Window {
    pywebview?: { api: {
      start_drag(): void;
      minimize_window(): void;
      toggle_maximize(): void;
      close_window(): void;
      is_maximized(): Promise<boolean>;
    }};
  }
}

const api = () => window.pywebview?.api;

export function TitleBar() {
  return (
    <div
      className="flex items-center h-8 shrink-0 bg-panel border-b border-border select-none"
      onMouseDown={() => api()?.start_drag()}
    >
      <span className="px-3 text-xs text-dim font-semibold tracking-wide">Archiver</span>
      <div className="ml-auto flex" onMouseDown={e => e.stopPropagation()}>
        <button
          className="w-10 h-8 flex items-center justify-center text-dim hover:bg-hover hover:text-text"
          onClick={() => api()?.minimize_window()}
          title="Minimize"
        >
          ─
        </button>
        <button
          className="w-10 h-8 flex items-center justify-center text-dim hover:bg-hover hover:text-text"
          onClick={() => api()?.toggle_maximize()}
          title="Maximize"
        >
          □
        </button>
        <button
          className="w-10 h-8 flex items-center justify-center text-dim hover:bg-[#c42b1c] hover:text-white"
          onClick={() => api()?.close_window()}
          title="Close"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
