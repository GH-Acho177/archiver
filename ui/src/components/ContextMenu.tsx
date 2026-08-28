import { useEffect, useLayoutEffect, useRef, useState } from "react";

export interface MenuItem {
  label:    string;
  danger?:  boolean;
  divider?: boolean;
  submenu?: MenuItem[];
  onClick:  () => void;
}

export function ContextMenu({
  items, onClose, x, y,
}: { items: MenuItem[]; onClose: () => void; x?: number; y?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [subIdx, setSubIdx] = useState<number | null>(null);
  const [position, setPosition] = useState({ left: x ?? 0, top: y ?? 0 });
  const [subPosition, setSubPosition] = useState({ left: 0, top: 0 });

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const keyboardHandler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", handler);
    document.addEventListener("keydown", keyboardHandler);
    return () => {
      document.removeEventListener("mousedown", handler);
      document.removeEventListener("keydown", keyboardHandler);
    };
  }, [onClose]);

  const fixed = x !== undefined && y !== undefined;
  useLayoutEffect(() => {
    if (!fixed || !ref.current) return;
    const margin = 8;
    const rect = ref.current.getBoundingClientRect();
    setPosition({
      left: Math.max(margin, Math.min(x!, window.innerWidth - rect.width - margin)),
      top: Math.max(margin, Math.min(y!, window.innerHeight - rect.height - margin)),
    });
  }, [fixed, x, y, items.length]);

  const openSubmenu = (
    index: number,
    target: HTMLButtonElement,
    count: number,
  ) => {
    const margin = 8;
    const width = 224;
    const height = Math.min(count * 32 + 12, window.innerHeight - margin * 2);
    const rect = target.getBoundingClientRect();
    const openLeft = rect.right + width + margin > window.innerWidth;
    setSubPosition({
      left: openLeft
        ? Math.max(margin, rect.left - width)
        : Math.min(window.innerWidth - width - margin, rect.right),
      top: Math.max(margin, Math.min(rect.top, window.innerHeight - height - margin)),
    });
    setSubIdx(index);
  };

  const cls = fixed
    ? "fixed z-[200] w-56 max-h-[calc(100vh-1rem)] overflow-y-auto rounded-lg border border-border bg-panel p-1.5 shadow-[0_12px_32px_rgb(0_0_0/.28)]"
    : "absolute z-50 right-0 top-7 w-56 max-h-[calc(100vh-1rem)] overflow-y-auto rounded-lg border border-border bg-panel p-1.5 shadow-[0_12px_32px_rgb(0_0_0/.28)]";
  const style = fixed ? position : undefined;

  return (
    <div ref={ref} className={cls} style={style} role="menu" aria-label="Actions">
      {items.map((item, i) =>
        item.divider ? (
          <div key={i} className="border-t border-border my-1" />
        ) : item.submenu ? (
          <div
            key={i}
            className="relative"
            onMouseLeave={() => setSubIdx(null)}
          >
            <button
              role="menuitem"
              onMouseEnter={event => openSubmenu(
                i, event.currentTarget, item.submenu!.length
              )}
              className={`flex min-h-8 w-full items-center justify-between rounded-md px-3 py-1.5 text-left text-xs transition-colors hover:bg-hover focus-visible:outline-offset-0 ${item.danger ? "text-red-400" : "text-text"}`}
            >
              {item.label}
              <span className="text-dim text-[10px] ml-2">▶</span>
            </button>
            {subIdx === i && (
              <div
                className="fixed z-[210] w-56 max-h-[calc(100vh-1rem)] overflow-y-auto rounded-lg border border-border bg-panel p-1.5 shadow-[0_12px_32px_rgb(0_0_0/.28)]"
                style={subPosition}
              >
                {item.submenu.map((sub, j) =>
                  sub.divider ? (
                    <div key={j} className="border-t border-border my-1" />
                  ) : (
                    <button key={j} role="menuitem"
                      onClick={() => { sub.onClick(); onClose(); }}
                      className={`min-h-8 w-full rounded-md px-3 py-1.5 text-left text-xs transition-colors hover:bg-hover focus-visible:outline-offset-0 ${sub.danger ? "text-red-400" : "text-text"}`}>
                      {sub.label}
                    </button>
                  )
                )}
              </div>
            )}
          </div>
        ) : (
          <button key={i} role="menuitem"
            onClick={() => { item.onClick(); onClose(); }}
            onMouseEnter={() => setSubIdx(null)}
            className={`min-h-8 w-full rounded-md px-3 py-1.5 text-left text-xs transition-colors hover:bg-hover focus-visible:outline-offset-0 ${
              item.danger ? "text-red-400" : "text-text"
            }`}>
            {item.label}
          </button>
        )
      )}
    </div>
  );
}
