import { PLATFORM_META } from "../api";

interface Props {
  platform: string;
  className?: string; // size + padding, e.g. "w-5 h-5 p-0.5" or "w-9 h-9 p-1.5"
}

export function PlatformChip({ platform, className = "w-5 h-5 p-0.5" }: Props) {
  if (platform === "—") return <span className="text-xs text-dim">—</span>;
  const meta = PLATFORM_META[platform];
  if (!meta) return <span className="text-xs text-dim">{platform}</span>;
  return (
    <span
      className={`inline-flex items-center justify-center rounded-full shrink-0 ${className}`}
      style={{ background: meta.bg }}
      title={meta.label}
    >
      <img src={meta.iconUrl} alt={meta.label} className="w-full h-full object-contain" />
    </span>
  );
}
