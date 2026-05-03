import { useEffect, useRef, useState } from "react";

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

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const fixed = x !== undefined && y !== undefined;
  const cls = fixed
    ? "fixed z-[200] w-52 rounded border border-border bg-panel shadow-xl py-1"
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
