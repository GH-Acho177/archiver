import { createContext, useContext, useState, useEffect, type ReactNode } from "react";

const STRINGS = {
  en: {
    // Nav
    "nav.dashboard": "Dashboard",
    "nav.browse":    "Browse",
    "nav.sync":      "Sync",
    "nav.library":   "Library",
    "nav.operations":"Operations",
    "nav.accounts":  "Accounts",
    "nav.account":   "Account",
    "nav.downloads": "Downloads",
    "nav.settings":  "Settings",
    "nav.setting":   "Setting",

    // Common
    "cancel":  "Cancel",
    "close":   "Close",
    "loading": "Loading…",
    "refresh": "↻ Refresh",

    // Dashboard — toolbar
    "dash.start_sync":  "▶  Start Sync",
    "dash.stop":        "■  Stop",
    "dash.maintenance": "Maintenance",
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
    "dash.tab_progress":"Progress",
    "dash.tab_history": "History",
    "dash.log_simple":  "Simple",
    "dash.log_full":    "Full",
    "dash.copy":        "Copy",
    "dash.clear":       "Clear",
    "dash.retry_all":   "Retry all",
    "dash.retry_accounts_missing": "The failed accounts are no longer available.",
    "dash.log_empty":   "Log output will appear here when a sync is running…",
    "dash.log_all_accounts": "All logs",
    "dash.log_account": "Account log",
    "dash.log_search":  "Search logs…",
    "dash.log_wrap":    "Wrap",
    "dash.log_follow":  "Follow",
    "dash.log_copied":  "Copied",
    "dash.log_no_match":"No matching log lines.",
    "dash.log_lines":   "lines",
    "dash.log_following":"Following output",
    "dash.log_paused":  "Follow paused",
    "dash.progress_platform":"Platform",
    "dash.progress_account":"Current account",
    "dash.progress_download":"Current file",
    "dash.progress_empty":"Progress events will appear here while an operation is running…",
    "dash.progress_events":"progress events",
    "dash.progress_state_running":"Preparing",
    "dash.progress_state_scanning":"Scanning posts",
    "dash.progress_state_downloading":"Downloading",
    "dash.progress_state_finished":"Finished",
    "dash.progress_state_stopped":"Stopped",
    "dash.progress_state_error":"Error",
    "dash.progress_error_hint":"The account operation did not complete. Open the full log for details.",
    "dash.progress_show_completed":"Show {count} completed",
    "dash.progress_hide_completed":"Hide completed",
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
    "dl.hint":        "Paste a post or profile URL from X, Douyin, Bilibili, or Xiaohongshu.",
    "dl.placeholder": "https://x.com/…  or  https://www.douyin.com/…",
    "dl.download":    "Download",
    "dl.starting":    "Starting…",

    // Start sync modal
    "sync.title":       "Start Sync",
    "maintenance.title":"Select groups for maintenance",
    "maintenance.all_groups":"All groups",
    "maintenance.no_groups":"No account groups are available.",
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
    "acc.search":       "Search accounts…",
    "acc.no_match":     "No accounts match your search.",
    "acc.empty":        "No accounts yet — click \"+ Add Account\" to get started.",
    "acc.unassigned":   "Unassigned",
    "acc.no_entries":   "No entries — assign one here",

    // Context menu
    "ctx.open":         "Open",
    "ctx.redownload":   "Redownload",
    "ctx.posts":        "Posts / Ghost check",
    "ctx.clear_archive": "Clear archive records",
    "ctx.clear_archive_title": "Clear archive records?",
    "ctx.clear_archive_confirm": "Clear this account's archive records? Its posts will be eligible for downloading again in Update mode.",
    "ctx.clear_archive_action": "Clear records",
    "ctx.clear_archive_done": "Archive records cleared.",
    "ctx.clear_archive_empty": "This account had no archive records.",
    "ctx.move_to":      "Move to…",
    "ctx.remove_acc":   "Remove account",
    "ctx.rename":       "Rename",
    "ctx.delete_group": "Delete group",
    "ctx.unassign":     "Unassign",

    // Add account modal
    "entry.title":         "Add Account",
    "entry.platform":      "Platform",
    "entry.handle":        "Profile link",
    "entry.link_hint":     "Paste any X, Douyin, Bilibili, or Xiaohongshu profile URL",
    "entry.group":         "Group (optional)",
    "entry.unassigned":    "— Unassigned —",
    "entry.add":           "Add",
    "entry.required":      "URL is required",
    "entry.group_prompt":  "Create a group for “{name}”?",
    "entry.group_hint":    "The group will use the account's display name.",
    "entry.create_group":  "Create group",
    "entry.leave_unassigned": "Leave unassigned",
    "back": "Back",
    "entry.choose_existing":"Choose existing group",
    "entry.choose_group":"Which group should this account join?",
    "entry.no_groups":"No existing groups. Go back and create a new group.",
    "entry.hint.x":        "Username  (e.g. elonmusk)",
    "entry.hint.douyin":   "Profile URL or sec_uid",
    "entry.hint.bilibili": "UID or space.bilibili.com/… URL",

    // Posts / ghost-check modal
    "posts.posts":        "posts",
    "posts.local_files":  "local files",
    "posts.gone_count":   "remote deleted",
    "posts.unchecked":    "unchecked",
    "posts.missing":      "missing locally",
    "posts.all_ok":       "✓ all found remotely",
    "posts.checking":     "Checking…",
    "posts.empty":        "No posts indexed yet — run a sync first.",
    "posts.col_date":     "Date",
    "posts.col_status":   "Remote",
    "posts.col_file":     "Local file",
    "posts.gone_badge":   "✕",
    "posts.missing_badge": "!",
    "posts.download_missing": "Missing posts",
    "posts.downloading_missing": "Downloading",
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
    "set.account_workers": "Per-account workers",
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
    "set.tg_hint":      "Create a bot via @BotFather, paste the token, and click Start. The first message you send from Telegram whitelists your account. Send any X, Douyin, Bilibili, or Xiaohongshu URL to queue a download.",
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
    "nav.browse":    "浏览",
    "nav.sync":      "同步",
    "nav.library":   "媒体库",
    "nav.operations":"操作",
    "nav.accounts":  "账号",
    "nav.account":   "账号",
    "nav.downloads": "下载",
    "nav.settings":  "设置",
    "nav.setting":   "设置",

    // Common
    "cancel":  "取消",
    "close":   "关闭",
    "loading": "加载中…",
    "refresh": "↻ 刷新",

    // Dashboard — toolbar
    "dash.start_sync":  "▶  开始同步",
    "dash.stop":        "■  停止",
    "dash.maintenance": "维护",
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
    "dash.tab_progress":"进度",
    "dash.tab_history": "历史",
    "dash.log_simple":  "简洁",
    "dash.log_full":    "完整",
    "dash.copy":        "复制",
    "dash.clear":       "清空",
    "dash.retry_all":   "全部重试",
    "dash.retry_accounts_missing": "失败的账号已不存在。",
    "dash.log_empty":   "同步运行时日志将显示在这里…",
    "dash.log_all_accounts": "全部日志",
    "dash.log_account": "账号日志",
    "dash.log_search":  "搜索日志…",
    "dash.log_wrap":    "换行",
    "dash.log_follow":  "跟随",
    "dash.log_copied":  "已复制",
    "dash.log_no_match":"没有匹配的日志。",
    "dash.log_lines":   "行",
    "dash.log_following":"正在跟随输出",
    "dash.log_paused":  "已暂停跟随",
    "dash.progress_platform":"平台",
    "dash.progress_account":"当前账号",
    "dash.progress_download":"当前文件",
    "dash.progress_empty":"操作运行时，进度事件将显示在这里…",
    "dash.progress_events":"条进度事件",
    "dash.progress_state_running":"准备中",
    "dash.progress_state_scanning":"正在扫描作品",
    "dash.progress_state_downloading":"下载中",
    "dash.progress_state_finished":"已完成",
    "dash.progress_state_stopped":"已停止",
    "dash.progress_state_error":"错误",
    "dash.progress_error_hint":"账号操作未能完成，请打开完整日志查看详情。",
    "dash.progress_show_completed":"显示 {count} 个已完成",
    "dash.progress_hide_completed":"收起已完成",
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
    "dl.hint":        "粘贴 X、抖音、B站或小红书的帖子或主页链接。",
    "dl.placeholder": "https://x.com/…  或  https://www.douyin.com/…",
    "dl.download":    "下载",
    "dl.starting":    "启动中…",

    // Start sync modal
    "sync.title":       "开始同步",
    "maintenance.title":"选择要维护的分组",
    "maintenance.all_groups":"所有分组",
    "maintenance.no_groups":"暂无可维护的账号分组。",
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
    "acc.search":      "搜索账号…",
    "acc.no_match":    "没有匹配的账号。",
    "acc.empty":       "暂无账号 — 点击「+ 添加账号」开始。",
    "acc.unassigned":  "未归类",
    "acc.no_entries":  "暂无账号 — 在此分配",

    // Context menu
    "ctx.open":         "打开",
    "ctx.redownload":   "重新下载",
    "ctx.posts":        "帖子 / 幽灵检测",
    "ctx.clear_archive": "清除下载记录",
    "ctx.clear_archive_title": "清除下载记录？",
    "ctx.clear_archive_confirm": "清除此账号的下载记录？下次更新模式会重新处理其帖子。",
    "ctx.clear_archive_action": "清除记录",
    "ctx.clear_archive_done": "下载记录已清除。",
    "ctx.clear_archive_empty": "此账号没有下载记录。",
    "ctx.move_to":      "移动到…",
    "ctx.remove_acc":   "删除账号",
    "ctx.rename":       "重命名",
    "ctx.delete_group": "删除分组",
    "ctx.unassign":     "取消分配",

    // Add account modal
    "entry.title":         "添加账号",
    "entry.platform":      "平台",
    "entry.handle":        "主页链接",
    "entry.link_hint":     "粘贴 X、抖音、Bilibili 或小红书的主页链接",
    "entry.group":         "分组（可选）",
    "entry.unassigned":    "— 未分配 —",
    "entry.add":           "添加",
    "entry.required":      "链接不能为空",
    "entry.group_prompt":  "要为“{name}”创建分组吗？",
    "entry.group_hint":    "分组将使用该账号的显示名称。",
    "entry.create_group":  "创建分组",
    "entry.leave_unassigned": "保持未分配",
    "back": "返回",
    "entry.choose_existing":"选择现有分组",
    "entry.choose_group":"要将此账号加入哪个分组？",
    "entry.no_groups":"暂无现有分组，请返回并创建新分组。",
    "entry.hint.x":        "用户名（如 elonmusk）",
    "entry.hint.douyin":   "主页链接或 sec_uid",
    "entry.hint.bilibili": "UID 或 space.bilibili.com/… 链接",

    // Posts / ghost-check modal
    "posts.posts":        "个帖子",
    "posts.local_files":  "个本地文件",
    "posts.gone_count":   "远程已删除",
    "posts.unchecked":    "未检测",
    "posts.missing":      "本地缺失",
    "posts.all_ok":       "✓ 远程全部存在",
    "posts.checking":     "检测中…",
    "posts.empty":        "暂无索引帖子 — 请先运行同步。",
    "posts.col_date":     "日期",
    "posts.col_status":   "远程",
    "posts.col_file":     "本地文件",
    "posts.gone_badge":   "✕",
    "posts.missing_badge": "!",
    "posts.download_missing": "缺失作品",
    "posts.downloading_missing": "正在下载",
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
    "set.account_workers": "单账号并发数",
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
    "set.tg_hint":      "通过 @BotFather 创建机器人，粘贴令牌并点击启动。您发送的第一条消息将自动加入白名单。发送任意 X、抖音、B站或小红书链接即可排队下载。",
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
