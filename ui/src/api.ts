export interface Status {
  running:          boolean;
  status:           string;
  mode:             "update" | "full";
  from_days:        number;
  tracking:         number;
  last_sync:        string;
  version:          string;
  total_downloads:  number;
  scheduler_active: boolean;
  next_sync_at:     number;
}

export interface HistoryUser {
  platform: string;
  handle?:  string;
  display:  string;
  count:    number;
  corrupt:  number;
  files:    string[];
  folder:   string;
}

export interface HistoryEntry {
  run_key:  string;
  date:     string;
  time:     string;
  duration: string;
  mode:     string;
  stopped:  boolean;
  users:    HistoryUser[];
}

export interface DownloadFile {
  name:     string;
  path:     string;
  platform: string;
  size:     number;
  date:     string;
}

export interface TgStatus {
  status:    "running" | "stopped" | "error";
  token_set: boolean;
}

export interface Creator { id: string; name: string; }
export interface Entry   { id: string; platform: string; handle: string; creator_id: string | null; }
export interface AccountsData { creators: Creator[]; entries: Entry[]; }

export interface AppSettings {
  download_path?:        string;
  parallel_workers?:     number;
  sleep_user?:           number;
  sleep_req?:            number;
  auto_update_enabled?:  boolean;
  auto_update_interval?: number;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(body || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

const json = (body: unknown) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const put = (body: unknown) => ({
  method: "PUT",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// Status & sync
export const getStatus    = () => request<Status>("/api/status");
export const startSync    = (mode?: string, from_days?: number, creator_ids?: string[] | null) =>
  request("/api/start", json({ mode, from_days, creator_ids }));
export const stopSync     = () => request("/api/stop", { method: "POST" });

// Accounts
export const getAccounts  = () => request<AccountsData>("/api/accounts");
export const addEntry     = (platform: string, handle: string, creator_id?: string | null) =>
  request<Entry>("/api/accounts/entries", json({ platform, handle, creator_id }));
export const addEntryFromLink = (url: string) =>
  request<{ id: string; platform: string; handle: string; display: string }>(
    "/api/accounts/add_link", json({ url }));
export const removeEntry  = (id: string) =>
  request<void>(`/api/accounts/entries/${id}`, { method: "DELETE" });
export const assignEntry  = (id: string, creator_id: string | null) =>
  request(`/api/accounts/entries/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ creator_id }),
  });
export const addCreator   = (name: string) =>
  request<Creator>("/api/accounts/creators", json({ name }));
export const removeCreator = (id: string) =>
  request<void>(`/api/accounts/creators/${id}`, { method: "DELETE" });
export const renameCreator = (id: string, name: string) =>
  request(`/api/accounts/creators/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });

// Settings & cookies
export const getSettings  = () => request<AppSettings>("/api/settings");
export const saveSettings = (s: AppSettings) => request("/api/settings", put(s));
export const getCookies   = (platform: string) =>
  request<{ content: string }>(`/api/cookies/${platform}`);
export const saveCookies  = (platform: string, content: string) =>
  request(`/api/cookies/${platform}`, put({ content }));

// URL download
export const downloadUrl  = (url: string) =>
  request<{ ok: boolean; platform: string }>("/api/download", json({ url }));

// Downloads file list
export const listDownloads  = (limit = 500) =>
  request<DownloadFile[]>(`/api/downloads?limit=${limit}`);
export const deleteDownload = (path: string) =>
  request<void>(`/api/downloads/file?path=${encodeURIComponent(path)}`, { method: "DELETE" });
export const openFile = (path: string) =>
  request<{ ok: boolean }>(`/api/files/open?path=${encodeURIComponent(path)}`, { method: "POST" });
export const openDownloadsFolder = () =>
  request<{ ok: boolean }>("/api/downloads/open", { method: "POST" });
export const browseFolder = () =>
  request<{ path: string | null }>("/api/browse/folder", { method: "POST" });

// History
export const getHistory = (limit = 100) =>
  request<HistoryEntry[]>(`/api/history?limit=${limit}`);

// Telegram bot
export const getTgStatus = () => request<TgStatus>("/api/telegram/status");
export const startTgBot  = (token: string) => request("/api/telegram/start", json({ token }));
export const stopTgBot   = () => request("/api/telegram/stop", { method: "POST" });

// Database reset
export const resetDatabase = () =>
  request<void>("/api/database/reset", { method: "POST" });

// Avatars
// In pywebview, <img src> bypasses the fetch interceptor and hits pywebview's
// static-file server instead of FastAPI — use the full API origin when available.
const _apiBase = (): string => (window as unknown as Record<string, unknown>).__apiBase as string || "";
export const avatarUrl    = (platform: string, accountId: string) =>
  `${_apiBase()}/api/avatars/${platform}/${encodeURIComponent(accountId)}`;
export const fetchAvatar  = (platform: string, accountId: string) =>
  request(`/api/avatars/${platform}/${encodeURIComponent(accountId)}/fetch`, { method: "POST" });

// Posts & ghost check
export interface PostEntry {
  post_id:  string;
  date:     string;
  file:     string;
  status:   "ok" | "gone" | "unchecked" | "error";
  checked:  string;
}

export interface CheckStatus {
  running: boolean;
  done:    number;
  total:   number;
}

// Post index scan
export const triggerIndexScan = () =>
  request<{ ok: boolean; error?: string }>("/api/index/scan", { method: "POST" });
export const getScanStatus = () =>
  request<{ running: boolean; added: number }>("/api/index/scan");

export const getPosts       = (platform: string, accountId: string) =>
  request<PostEntry[]>(`/api/posts/${platform}/${encodeURIComponent(accountId)}`);
export const startCheck     = (platform: string, accountId: string) =>
  request<{ ok: boolean; error?: string }>(
    `/api/posts/${platform}/${encodeURIComponent(accountId)}/check`, { method: "POST" });
export const getCheckStatus = (platform: string, accountId: string) =>
  request<CheckStatus>(`/api/posts/${platform}/${encodeURIComponent(accountId)}/check`);

export const PLATFORM_META: Record<string, { label: string; icon: string; iconUrl: string; color: string; bg: string }> = {
  x:        { label: "X",        icon: "𝕏", iconUrl: "/icons/x.svg",        color: "#ffffff", bg: "#1d9bf0" },
  douyin:   { label: "Douyin",   icon: "抖", iconUrl: "/icons/douyin.svg",   color: "#fe2c55", bg: "#000000" },
  bilibili: { label: "Bilibili", icon: "哔", iconUrl: "/icons/bilibili.svg", color: "#fb7299", bg: "#ffffff" },
};
