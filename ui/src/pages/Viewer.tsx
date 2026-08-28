import { useEffect, useMemo, useRef, useState } from "react";

export default function Viewer({
  active, target,
}: {
  active: boolean;
  target: string;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const wasActive = useRef(active);
  const [feedSession, setFeedSession] = useState(() => crypto.randomUUID());

  useEffect(() => {
    if (active && !wasActive.current) setFeedSession(crypto.randomUUID());
    wasActive.current = active;
  }, [active]);

  const src = useMemo(() => {
    const base = ((window as unknown as Record<string, unknown>).__apiBase as string) || window.location.origin;
    const params = new URLSearchParams({ embedded: "1", ui: "11", seed: feedSession });
    if (target.startsWith("file:")) params.set("file", target.slice(5));
    else if (target) params.set("account", target);
    return `${base}/viewer/?${params}`;
  }, [target, feedSession]);

  useEffect(() => {
    const handleMessage = async (event: MessageEvent) => {
      if (event.data?.type !== "archiver:toggle-viewer-fullscreen") return;
      const bridge = (window as unknown as {
        pywebview?: { api?: { toggle_fullscreen?: () => Promise<boolean> } };
      }).pywebview;
      try {
        const fullscreen = await bridge?.api?.toggle_fullscreen?.();
        window.dispatchEvent(new CustomEvent("archiver:viewer-fullscreen-layout", {
          detail: Boolean(fullscreen),
        }));
        frame.current?.contentWindow?.postMessage({
          type: "archiver:viewer-fullscreen-changed", fullscreen: Boolean(fullscreen),
        }, "*");
      } catch {
        window.dispatchEvent(new CustomEvent("archiver:viewer-fullscreen-layout", {
          detail: false,
        }));
        frame.current?.contentWindow?.postMessage({
          type: "archiver:viewer-fullscreen-changed", fullscreen: false,
        }, "*");
      }
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  // Do not create the embedded document while another Archiver page is
  // active. This prevents its intersection observer from autoplaying hidden
  // videos at application startup; unmounting also guarantees playback stops.
  if (!active) return null;

  return <iframe ref={frame} src={src} title="Archive Viewer"
    className="h-full w-full border-0 bg-black" allow="autoplay; fullscreen" />;
}
