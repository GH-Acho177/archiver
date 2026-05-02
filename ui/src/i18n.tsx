import { createContext, useContext, useState, useEffect, type ReactNode } from "react";

const STRINGS = {
  en: {
    // Nav
    "nav.dashboard": "Dashboard",
    "nav.accounts":  "Accounts",
    "nav.downloads": "Downloads",
    "nav.settings":  "Settings",

    // Common
    "cancel":  "Cancel",
    "close":   "Close",
    "loading": "Loading…",
    "refresh": "↻ Refresh",

    // Dashboard — toolbar
    "dash.start_sync":  "▶  Start Sync",
    "dash.stop":        "■  Stop",
    "dash.auto_sync":   "Auto-sync",
    "dash.next_in":     "next in",
    "dash.open_folder": "📁 Open Folder",
    "dash.update":      "Update",
    "dash.full":        "Full",
    "dash.last":        "Last",
    "dash.days":        "days",
    "dash.days_hint":   "(0 = all)",
    "dash.dl_url":      "⬇ Download URL",

    // Dashboard — tabs
    "dash.tab_log":     "Log",
    "dash.tab_history": "History",
    "dash.log_simple":  "Simple",
    "dash.log_full":    "Full",
    "dash.copy":        "Copy",
    "dash.clear":       "Clear",
    "dash.log_empty":   "Log output will appear here when a sync is running…",
    "dash.hist_empty":  "No download history yet.",

    // Dashboard — status strip
    "dash.last_sync":    "Last sync:",
    "dash.tracking":     "Tracking:",
    "dash.total":        "Total:",
    "dash.stopped":      "stopped early",
    "dash.files":        "files",
    "dash.account":      "account",
    "dash.accounts":     "accounts",

    // Download URL modal
    "dl.title":       "Download URL",
    "dl.hint":        "Paste a post or profile URL from X, Douyin, or Bilibili.",
    "dl.placeholder": "https://x.com/…  or  https://www.douyin.com/…",
    "dl.download":    "Download",
    "dl.starting":    "Starting…",

    // Start sync modal
    "sync.title":       "Start Sync",
    "sync.mode":        "Mode:",
    "sync.last":        "Last",
    "sync.days":        "days",
    "sync.accs_label":  "Accounts",
    "sync.all_accs":    "All accounts",
    "sync.no_creators": "No creators defined.",
    "sync.start":       "Start",

    // Accounts page
    "acc.add_group":    "+ Add Group",
    "acc.add_account":  "+ Add Account",
    "acc.create":       "Create",
    "acc.group_ph":     "Group name…",
    "acc.empty":        "No accounts yet — click \"+ Add Account\" to get started.",
    "acc.unassigned":   "Unassigned",
    "acc.no_entries":   "No entries — assign one here",

    // Context menu
    "ctx.posts":        "Posts / Ghost check",
    "ctx.move_to":      "Move to…",
    "ctx.remove_acc":   "Remove account",
    "ctx.rename":       "Rename",
    "ctx.delete_group": "Delete group",
    "ctx.unassign":     "Unassign",

    // Add account modal
    "entry.title":         "Add Account",
    "entry.platform":      "Platform",
    "entry.handle":        "Handle / URL",
    "entry.group":         "Group (optional)",
    "entry.unassigned":    "— Unassigned —",
    "entry.add":           "Add",
    "entry.required":      "Handle is required",
    "entry.hint.x":        "Username  (e.g. elonmusk)",
    "entry.hint.douyin":   "Profile URL or sec_uid",
    "entry.hint.bilibili": "UID or space.bilibili.com/… URL",

    // Posts / ghost-check modal
    "posts.posts":        "posts",
    "posts.gone_count":   "gone",
    "posts.unchecked":    "unchecked",
    "posts.all_ok":       "✓ all ok",
    "posts.checking":     "Checking…",
    "posts.empty":        "No posts indexed yet — run a sync first.",
    "posts.col_date":     "Date",
    "posts.col_status":   "Status",
    "posts.col_file":     "File",
    "posts.gone_badge":   "Gone",
    "posts.check_btn":    "Check",
    "posts.pending":      "pending",
    "posts.recheck":      "▶ Re-check All",
    "posts.checking_btn": "Checking…",

    // Downloads page
    "dl_list.all":         "All",
    "dl_list.search":      "Search filename…",
    "dl_list.files":       "files",
    "dl_list.open_folder": "↗ Open folder",
    "dl_list.no_files":    "No downloaded files found.",
    "dl_list.no_match":    "No files match the current filter.",
    "dl_list.col_plat":    "Plat",
    "dl_list.col_name":    "Filename",
    "dl_list.col_size":    "Size",
    "dl_list.col_date":    "Date",
    "dl_list.del_title":   "Delete File?",
    "dl_list.del_desc":    "This permanently deletes the file from disk.",
    "dl_list.delete":      "Delete",

    // Settings page
    "set.theme":        "Theme",
    "set.dark":         "Dark",
    "set.light":        "Light",
    "set.language":     "Language",
    "set.dl_location":  "Download Location",
    "set.browse":       "Browse…",
    "set.dl_options":   "Download Options",
    "set.workers":      "Parallel workers",
    "set.sleep_user":   "Sleep between users (s)",
    "set.sleep_req":    "Sleep between requests (s)",
    "set.auto_sync":    "Auto-sync",
    "set.enable_sync":  "Enable scheduled sync",
    "set.interval":     "Interval (min)",
    "set.cookies":      "Cookies",
    "set.cookies_hint": "Export using the \"Get cookies.txt LOCALLY\" extension. Browse or drag-drop the .txt file.",
    "set.loaded":       "✓ Loaded",
    "set.not_set":      "Not set",
    "set.clear":        "Clear",
    "set.save":         "Save Settings",
    "set.saved":        "✓ Saved",
    "set.tg_bot":       "Telegram Bot",
    "set.tg_hint":      "Create a bot via @BotFather, paste the token, and click Start. The first message you send from Telegram whitelists your account. Send any X, Douyin, or Bilibili URL to queue a download.",
    "set.tg_status":    "Status",
    "set.tg_running":   "Running",
    "set.tg_error":     "Invalid token",
    "set.tg_stopped":   "Stopped",
    "set.tg_token":     "Bot token",
    "set.tg_ph_saved":  "••••••••  (token saved)",
    "set.tg_ph":        "Paste token from @BotFather",
    "set.hide":         "Hide",
    "set.show":         "Show",
    "set.save_start":   "Save & Start",
    "set.restart":      "Restart",
    "set.starting":     "Starting…",
    "set.stop":         "Stop",
    "set.database":     "Database",
    "set.db_hint":      "Delete all download archive records. Forces every file to be re-downloaded on the next sync.",
    "set.reset_db":     "Reset Database",
    "set.resetting":    "Resetting…",
    "set.db_cleared":   "✓ Archive records cleared",
    "set.db_failed":    "✗ Failed",
  },
  zh: {
    // Nav
    "nav.dashboard": "仪表盘",
    "nav.accounts":  "账号",
    "nav.downloads": "下载",
    "nav.settings":  "设置",

    // Common
    "cancel":  "取消",
    "close":   "关闭",
    "loading": "加载中…",
    "refresh": "↻ 刷新",

    // Dashboard — toolbar
    "dash.start_sync":  "▶  开始同步",
    "dash.stop":        "■  停止",
    "dash.auto_sync":   "自动同步",
    "dash.next_in":     "下次同步",
    "dash.open_folder": "📁 打开目录",
    "dash.update":      "更新",
    "dash.full":        "全部",
    "dash.last":        "最近",
    "dash.days":        "天",
    "dash.days_hint":   "（0=全部）",
    "dash.dl_url":      "⬇ 链接下载",

    // Dashboard — tabs
    "dash.tab_log":     "日志",
    "dash.tab_history": "历史",
    "dash.log_simple":  "简洁",
    "dash.log_full":    "完整",
    "dash.copy":        "复制",
    "dash.clear":       "清空",
    "dash.log_empty":   "同步运行时日志将显示在这里…",
    "dash.hist_empty":  "暂无下载历史。",

    // Dashboard — status strip
    "dash.last_sync":    "上次同步：",
    "dash.tracking":     "追踪：",
    "dash.total":        "总计：",
    "dash.stopped":      "提前停止",
    "dash.files":        "个文件",
    "dash.account":      "个账号",
    "dash.accounts":     "个账号",

    // Download URL modal
    "dl.title":       "链接下载",
    "dl.hint":        "粘贴 X、抖音或B站的帖子或主页链接。",
    "dl.placeholder": "https://x.com/…  或  https://www.douyin.com/…",
    "dl.download":    "下载",
    "dl.starting":    "启动中…",

    // Start sync modal
    "sync.title":       "开始同步",
    "sync.mode":        "模式：",
    "sync.last":        "最近",
    "sync.days":        "天",
    "sync.accs_label":  "账号",
    "sync.all_accs":    "所有账号",
    "sync.no_creators": "暂无创作者。",
    "sync.start":       "开始",

    // Accounts page
    "acc.add_group":   "+ 添加分组",
    "acc.add_account": "+ 添加账号",
    "acc.create":      "创建",
    "acc.group_ph":    "分组名称…",
    "acc.empty":       "暂无账号 — 点击「+ 添加账号」开始。",
    "acc.unassigned":  "未归类",
    "acc.no_entries":  "暂无账号 — 在此分配",

    // Context menu
    "ctx.posts":        "帖子 / 幽灵检测",
    "ctx.move_to":      "移动到…",
    "ctx.remove_acc":   "删除账号",
    "ctx.rename":       "重命名",
    "ctx.delete_group": "删除分组",
    "ctx.unassign":     "取消分配",

    // Add account modal
    "entry.title":         "添加账号",
    "entry.platform":      "平台",
    "entry.handle":        "账号 / 链接",
    "entry.group":         "分组（可选）",
    "entry.unassigned":    "— 未分配 —",
    "entry.add":           "添加",
    "entry.required":      "账号不能为空",
    "entry.hint.x":        "用户名（如 elonmusk）",
    "entry.hint.douyin":   "主页链接或 sec_uid",
    "entry.hint.bilibili": "UID 或 space.bilibili.com/… 链接",

    // Posts / ghost-check modal
    "posts.posts":        "个帖子",
    "posts.gone_count":   "已删除",
    "posts.unchecked":    "未检测",
    "posts.all_ok":       "✓ 全部正常",
    "posts.checking":     "检测中…",
    "posts.empty":        "暂无索引帖子 — 请先运行同步。",
    "posts.col_date":     "日期",
    "posts.col_status":   "状态",
    "posts.col_file":     "文件",
    "posts.gone_badge":   "已删除",
    "posts.check_btn":    "检测",
    "posts.pending":      "待检",
    "posts.recheck":      "▶ 重新检测全部",
    "posts.checking_btn": "检测中…",

    // Downloads page
    "dl_list.all":         "全部",
    "dl_list.search":      "搜索文件名…",
    "dl_list.files":       "个文件",
    "dl_list.open_folder": "↗ 打开目录",
    "dl_list.no_files":    "暂无下载文件。",
    "dl_list.no_match":    "没有匹配当前筛选的文件。",
    "dl_list.col_plat":    "平台",
    "dl_list.col_name":    "文件名",
    "dl_list.col_size":    "大小",
    "dl_list.col_date":    "日期",
    "dl_list.del_title":   "删除文件？",
    "dl_list.del_desc":    "这将从磁盘永久删除该文件。",
    "dl_list.delete":      "删除",

    // Settings page
    "set.theme":        "主题",
    "set.dark":         "深色",
    "set.light":        "浅色",
    "set.language":     "语言",
    "set.dl_location":  "下载位置",
    "set.browse":       "浏览…",
    "set.dl_options":   "下载选项",
    "set.workers":      "并发数",
    "set.sleep_user":   "用户间隔（秒）",
    "set.sleep_req":    "请求间隔（秒）",
    "set.auto_sync":    "自动同步",
    "set.enable_sync":  "启用定时同步",
    "set.interval":     "间隔（分钟）",
    "set.cookies":      "Cookies",
    "set.cookies_hint": "使用「Get cookies.txt LOCALLY」插件导出。浏览或拖放 .txt 文件。",
    "set.loaded":       "✓ 已加载",
    "set.not_set":      "未设置",
    "set.clear":        "清除",
    "set.save":         "保存设置",
    "set.saved":        "✓ 已保存",
    "set.tg_bot":       "Telegram 机器人",
    "set.tg_hint":      "通过 @BotFather 创建机器人，粘贴令牌并点击启动。您发送的第一条消息将自动加入白名单。发送任意 X、抖音或B站链接即可排队下载。",
    "set.tg_status":    "状态",
    "set.tg_running":   "运行中",
    "set.tg_error":     "令牌无效",
    "set.tg_stopped":   "已停止",
    "set.tg_token":     "机器人令牌",
    "set.tg_ph_saved":  "••••••••（令牌已保存）",
    "set.tg_ph":        "粘贴 @BotFather 的令牌",
    "set.hide":         "隐藏",
    "set.show":         "显示",
    "set.save_start":   "保存并启动",
    "set.restart":      "重启",
    "set.starting":     "启动中…",
    "set.stop":         "停止",
    "set.database":     "数据库",
    "set.db_hint":      "删除所有下载记录。下次同步时将强制重新下载所有文件。",
    "set.reset_db":     "重置数据库",
    "set.resetting":    "重置中…",
    "set.db_cleared":   "✓ 记录已清除",
    "set.db_failed":    "✗ 操作失败",
  },
} as const;

export type Lang  = "en" | "zh";
export type Theme = "dark" | "light";
type Keys = keyof typeof STRINGS["en"];


interface AppCtx {
  lang:     Lang;
  setLang:  (l: Lang) => void;
  t:        (k: Keys) => string;
  theme:    Theme;
  setTheme: (th: Theme) => void;
}

const AppContext = createContext<AppCtx>({
  lang: "en", setLang: () => {},
  t: k => STRINGS.en[k],
  theme: "dark", setTheme: () => {},
});

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const s = localStorage.getItem("lang");
    return s === "zh" ? "zh" : "en";
  });

  const [theme, setThemeState] = useState<Theme>(() => {
    const s = localStorage.getItem("theme");
    return s === "light" ? "light" : "dark";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const setLang = (l: Lang) => {
    localStorage.setItem("lang", l);
    setLangState(l);
  };

  const setTheme = (th: Theme) => {
    localStorage.setItem("theme", th);
    setThemeState(th);
  };

  const t = (k: Keys): string => STRINGS[lang][k];

  return (
    <AppContext.Provider value={{ lang, setLang, t, theme, setTheme }}>
      {children}
    </AppContext.Provider>
  );
}

export const useLang  = () => useContext(AppContext);
export const useTheme = () => useContext(AppContext);
