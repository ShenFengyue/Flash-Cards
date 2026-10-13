"""Tkinter 桌面界面：双击 .pyw 打开，不需要浏览器，也不需要命令行参数。"""

import os
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from app import APP_NAME, APP_VERSION, ai, paths
from app.backup import apply_backup, build_backup, summarize_backup, validate_backup
from app.formatting import describe_due, format_datetime
from app.free_session import FreePracticeSession
from app.grading import describe_bands, grade_label
from app.review_session import ReviewSession
from app.scheduling import STATE_LABELS
from app.seeds import SEED_CARDS
from app.storage import Database, now_ms, query_cards, stats_of

# 配色：深色侧边栏 + 浅色内容区（参考 Linear / Notion 这类现代效率工具）
BG = "#f7f7f9"
CARD_BG = "#ffffff"
BORDER = "#e7e8ec"
BORDER_STRONG = "#d5d7de"
TEXT = "#15171c"
MUTED = "#71747d"
ACCENT = "#5b5bd6"
ACCENT_DARK = "#4a4ac0"
ACCENT_SOFT = "#eeeefc"
OK = "#0f9d63"
OK_SOFT = "#e9f7f1"
WARN = "#b0761a"
WARN_SOFT = "#fdf3e4"
DANGER = "#d33a2c"
DANGER_SOFT = "#fdecea"
SIDEBAR_BG = "#1b1c21"
SIDEBAR_HOVER = "#26272e"
SIDEBAR_ACTIVE = "#2e3038"
SIDEBAR_TEXT = "#e8e9ef"
SIDEBAR_MUTED = "#8b8d99"
FONT = "Microsoft YaHei UI"
MONO = "Consolas"


def truncate(text: str, length: int = 60) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= length else text[: length - 1] + "…"


def compact_datetime(timestamp_ms: int) -> str:
    """把时间戳压短一点，表格里看起来更清爽。"""
    moment = datetime.fromtimestamp(int(timestamp_ms) / 1000)
    if moment.year == datetime.now().year:
        return moment.strftime("%m-%d %H:%M")
    return moment.strftime("%Y-%m-%d")


class ScoreBar(tk.Canvas):
    """圆角的匹配度进度条：比系统自带的进度条更干净，颜色跟着评分区间走。"""

    def __init__(self, parent, height: int = 9, **kwargs):
        super().__init__(parent, height=height, bg=CARD_BG, highlightthickness=0, bd=0, **kwargs)
        self._value = 0.0
        self.bind("<Configure>", lambda _event: self._draw())

    def set(self, value: float) -> None:
        self._value = max(0.0, min(100.0, float(value)))
        self._draw()

    def _color(self) -> str:
        if self._value < 40:
            return DANGER
        if self._value < 65:
            return WARN
        if self._value < 85:
            return ACCENT
        return OK

    def _rounded(self, x1, y1, x2, y2, radius, **kwargs):
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _draw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = int(self["height"])
        if width <= 4:
            return
        radius = height / 2
        self._rounded(1, 1, width - 1, height - 1, radius, fill="#ecedf1", outline="")
        filled = (width - 2) * self._value / 100
        if filled >= 1:
            self._rounded(1, 1, max(1 + height, filled + 1), height - 1, radius, fill=self._color(), outline="")


class NavItem(tk.Frame):
    """侧边栏导航项：深色底、悬停高亮，选中时左侧有一条强调色指示条。"""

    def __init__(self, parent, text: str, command, badge_var=None):
        super().__init__(parent, bg=SIDEBAR_BG, cursor="hand2")
        self.indicator = tk.Frame(self, bg=SIDEBAR_BG, width=3)
        self.indicator.pack(side="left", fill="y")
        self.inner = tk.Frame(self, bg=SIDEBAR_BG)
        self.inner.pack(side="left", fill="both", expand=True, padx=(11, 12), pady=9)
        self.label = tk.Label(self.inner, text=text, bg=SIDEBAR_BG, fg=SIDEBAR_MUTED, font=(FONT, 11), anchor="w")
        self.label.pack(side="left")
        self.badge = None
        if badge_var is not None:
            self.badge = tk.Label(
                self.inner, textvariable=badge_var, bg=ACCENT, fg="#ffffff", font=(FONT, 8, "bold"), padx=6
            )
            self.badge.pack(side="right")
        self._active = False

        targets = [self, self.inner, self.label] + ([self.badge] if self.badge is not None else [])
        for widget in targets:
            widget.bind("<Button-1>", lambda _event: command())
            widget.bind("<Enter>", lambda _event: self._hover(True))
            widget.bind("<Leave>", lambda _event: self._hover(False))

    def _paint(self, background: str, foreground: str) -> None:
        self.configure(bg=background)
        self.inner.configure(bg=background)
        self.label.configure(bg=background, fg=foreground)
        self.indicator.configure(bg=ACCENT if self._active else background)

    def _hover(self, hovering: bool) -> None:
        if self._active:
            return
        if hovering:
            self._paint(SIDEBAR_HOVER, SIDEBAR_TEXT)
        else:
            self._paint(SIDEBAR_BG, SIDEBAR_MUTED)

    def set_active(self, active: bool) -> None:
        self._active = active
        self._paint(SIDEBAR_ACTIVE if active else SIDEBAR_BG, "#ffffff" if active else SIDEBAR_MUTED)

    def set_badge_visible(self, visible: bool) -> None:
        """数字为 0 时把徽标收起来，避免留一个空色块。"""
        if self.badge is None:
            return
        showing = bool(self.badge.winfo_manager())
        if visible and not showing:
            self.badge.pack(side="right")
        elif not visible and showing:
            self.badge.pack_forget()


class FlowBar(tk.Frame):
    """会折行的横向容器：窗口变窄时把内容自动排到下一行，不会把按钮挤掉。"""

    GAP = 8

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("bg", BG)
        super().__init__(parent, **kwargs)
        self._visible: list[tk.Widget] = []
        self._last_width = None
        self.bind("<Configure>", self._on_configure, add="+")

    def show(self, widgets) -> None:
        """只显示传入的控件（按传入顺序排列），其余的隐藏。"""
        self._visible = list(widgets)
        self._layout(self.winfo_width())

    def hide_all(self) -> None:
        self.show([])

    def _on_configure(self, event) -> None:
        if self._last_width != event.width:
            self._layout(event.width)

    def _layout(self, width: int) -> None:
        for child in self.winfo_children():
            child.grid_forget()
        if not self._visible:
            return

        available = width if width > 60 else 640
        row = 0
        column = 0
        used = 0
        for widget in self._visible:
            need = widget.winfo_reqwidth() + self.GAP
            if used and used + need > available:
                row += 1
                column = 0
                used = 0
            widget.grid(row=row, column=column, sticky="w", padx=(0, self.GAP), pady=(0, 6))
            used += need
            column += 1


def adaptive_wrap(label: tk.Label, container: tk.Misc, padding: int = 40) -> None:
    """让标签的换行宽度跟着容器宽度走，窗口缩放时文字不会被截断。"""

    def on_configure(event):
        label.configure(wraplength=max(160, event.width - padding))

    container.bind("<Configure>", on_configure, add="+")


class FlashcardApp(tk.Tk):
    """主窗口：顶部状态栏 + 三个页签（今日复习 / 自由练习 / 全部闪卡）。"""

    def __init__(self, db, settings):
        super().__init__()
        self.db = db
        self.settings = settings
        self.drafts = paths.load_drafts()
        self.cards: list[dict] = []
        self.stats: dict = {}

        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1120x760")
        # 允许窗口缩得比较小，布局会自动折行、内部区域自带滚动条
        self.minsize(720, 520)
        self.configure(bg=BG)

        self._setup_style()
        self._build_shell()

        # tkinter 默认会把界面回调里的异常悄悄吞掉（.pyw 没有控制台，看不到任何提示），
        # 这里改成写日志 + 弹窗，避免出现「点了没反应」却查不到原因的情况。
        self.report_callback_exception = self._report_callback_exception
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_all()
        self.review_tab.ensure_queue()
        self.show_page(0)

    # ---------- 样式 ----------
    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=(FONT, 10), background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD_BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
        style.configure("Title.TLabel", font=(FONT, 17, "bold"))

        # 扁平按钮：主按钮用强调色，普通按钮白底细边，危险操作用红色文字
        style.configure("TButton", padding=(12, 7), borderwidth=1, relief="flat", background=CARD_BG,
                        bordercolor=BORDER_STRONG, lightcolor=CARD_BG, darkcolor=CARD_BG)
        style.map(
            "TButton",
            background=[("active", "#f2f3f6"), ("pressed", "#e9eaee"), ("disabled", BG)],
            bordercolor=[("disabled", BORDER)],
            foreground=[("disabled", "#b3b5bd")],
        )
        style.configure("Primary.TButton", background=ACCENT, foreground="#ffffff", padding=(14, 8),
                        bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
        style.map(
            "Primary.TButton",
            background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK), ("disabled", "#c5c5e8")],
            foreground=[("disabled", "#f4f4fd")],
            bordercolor=[("disabled", "#c5c5e8")],
        )
        style.configure("Danger.TButton", foreground=DANGER, bordercolor="#eccdc9",
                        lightcolor=CARD_BG, darkcolor=CARD_BG)
        style.map("Danger.TButton", background=[("active", DANGER_SOFT)], foreground=[("disabled", "#c9a9a5")])
        style.configure("Sidebar.TButton", background=ACCENT, foreground="#ffffff", padding=(10, 7),
                        borderwidth=0, font=(FONT, 10), lightcolor=ACCENT, darkcolor=ACCENT)
        style.map("Sidebar.TButton", background=[("active", ACCENT_DARK)])
        style.configure("SidebarGhost.TButton", background=SIDEBAR_BG, foreground=SIDEBAR_TEXT, padding=(10, 7),
                        borderwidth=0, font=(FONT, 10), lightcolor=SIDEBAR_BG, darkcolor=SIDEBAR_BG)
        style.map("SidebarGhost.TButton", background=[("active", SIDEBAR_ACTIVE)])

        style.configure("Treeview", background=CARD_BG, fieldbackground=CARD_BG, rowheight=30,
                        borderwidth=0, font=(FONT, 10))
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"), background=BG, foreground=MUTED,
                        relief="flat", borderwidth=0, padding=(6, 8))
        style.map("Treeview.Heading", background=[("active", BG)])
        style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", TEXT)])

        style.configure("TEntry", padding=6, fieldbackground=CARD_BG, bordercolor=BORDER_STRONG,
                        lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG)
        style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)], darkcolor=[("focus", ACCENT)])
        style.configure("TCombobox", padding=5, fieldbackground=CARD_BG, bordercolor=BORDER_STRONG,
                        arrowcolor=MUTED, lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG)
        style.map("TCombobox", bordercolor=[("focus", ACCENT)], fieldbackground=[("readonly", CARD_BG)])
        style.configure("TScrollbar", background="#d9dae0", troughcolor=BG, borderwidth=0, arrowsize=12,
                        relief="flat")
        style.map("TScrollbar", background=[("active", "#c3c5cd")])
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TRadiobutton", background=BG, foreground=TEXT)
        style.map("TRadiobutton", background=[("active", BG)])

    def _build_shell(self):
        """整体布局：左侧深色导航栏 + 右侧内容区 + 底部状态栏。"""
        self.columnconfigure(0, weight=0, minsize=210)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # ---------- 左侧导航 ----------
        sidebar = tk.Frame(self, bg=SIDEBAR_BG, width=210)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)

        brand = tk.Frame(sidebar, bg=SIDEBAR_BG)
        brand.pack(fill="x", padx=18, pady=(20, 12))
        tk.Label(brand, text=APP_NAME, bg=SIDEBAR_BG, fg="#ffffff", font=(FONT, 13, "bold")).pack(anchor="w")
        tk.Label(brand, text=f"v{APP_VERSION}", bg=SIDEBAR_BG, fg=SIDEBAR_MUTED, font=(FONT, 8)).pack(anchor="w", pady=(2, 0))

        self.due_badge_var = tk.StringVar(value="")
        self.nav_items = [
            NavItem(sidebar, "今日复习", lambda: self.show_page(0), self.due_badge_var),
            NavItem(sidebar, "自由练习", lambda: self.show_page(1)),
            NavItem(sidebar, "全部闪卡", lambda: self.show_page(2)),
        ]
        for item in self.nav_items:
            item.pack(fill="x", pady=1)

        bottom = tk.Frame(sidebar, bg=SIDEBAR_BG)
        bottom.pack(side="bottom", fill="x", padx=16, pady=16)
        self.ai_status_var = tk.StringVar(value="AI 状态未知")
        self.ai_status_label = tk.Label(
            bottom, textvariable=self.ai_status_var, bg=SIDEBAR_BG, fg=SIDEBAR_MUTED,
            font=(FONT, 9), justify="left", anchor="w", wraplength=178,
        )
        self.ai_status_label.pack(anchor="w", pady=(0, 10))
        ttk.Button(bottom, text="API 设置", style="Sidebar.TButton", command=self.open_settings).pack(fill="x")
        ttk.Button(bottom, text="使用说明", style="SidebarGhost.TButton", command=self.open_manual).pack(fill="x", pady=(6, 0))

        # ---------- 右侧内容区 ----------
        content = tk.Frame(self, bg=BG)
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        top = tk.Frame(content, bg=BG)
        top.grid(row=0, column=0, sticky="ew", padx=22, pady=(20, 10))
        self.page_title_var = tk.StringVar(value="")
        tk.Label(top, textvariable=self.page_title_var, bg=BG, fg=TEXT, font=(FONT, 17, "bold")).pack(anchor="w")
        self.stats_var = tk.StringVar(value="")
        self.stats_label = tk.Label(top, textvariable=self.stats_var, bg=BG, fg=MUTED, font=(FONT, 9))
        self.stats_label.pack(anchor="w", pady=(3, 0))

        self.pages_frame = tk.Frame(content, bg=BG)
        self.pages_frame.grid(row=1, column=0, sticky="nsew", padx=22, pady=(0, 6))
        self.pages_frame.columnconfigure(0, weight=1)
        self.pages_frame.rowconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="准备就绪。数据保存在程序文件夹的 data 目录里。")
        tk.Label(content, textvariable=self.status_var, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w").grid(
            row=2, column=0, sticky="ew", padx=22, pady=(0, 10)
        )

        self.review_tab = ReviewTab(self.pages_frame, self)
        self.free_tab = FreeTab(self.pages_frame, self)
        self.cards_tab = CardsTab(self.pages_frame, self)
        self.pages = [self.review_tab, self.free_tab, self.cards_tab]
        self.page_titles = ["今日复习", "自由练习", "全部闪卡"]
        for page in self.pages:
            page.grid(row=0, column=0, sticky="nsew")
        self.current_page = 0

    def show_page(self, index: int) -> None:
        self.current_page = index
        self.pages[index].tkraise()
        for position, item in enumerate(self.nav_items):
            item.set_active(position == index)
        self.page_title_var.set(self.page_titles[index])
        if index == 0:
            self.review_tab.ensure_queue()
        elif index == 1:
            self.free_tab.reload()

    # ---------- 基础能力 ----------
    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def run_async(self, work, on_success, on_error) -> None:
        """在后台线程执行网络请求，结果回到主线程处理（数据库只在主线程读写）。"""

        def runner():
            try:
                result = work()
            except Exception as error:  # noqa: BLE001 - 交给界面统一提示
                self.after(0, lambda: on_error(error))
                return
            self.after(0, lambda: on_success(result))

        threading.Thread(target=runner, daemon=True).start()

    def reload_cards(self) -> list[dict]:
        self.cards = self.db.list_cards_with_schedule()
        self.stats = stats_of(self.cards)
        return self.cards

    def card_by_id(self, card_id: str) -> dict | None:
        for card in self.cards:
            if card["id"] == card_id:
                return card
        return None

    def refresh_all(self) -> None:
        self.reload_cards()
        self.stats_var.set(f"今日到期 {self.stats['due']} 张（新卡 {self.stats['new']}）· 共 {self.stats['total']} 张")
        self.due_badge_var.set(str(self.stats["due"]) if self.stats["due"] else "")
        self.nav_items[0].set_badge_visible(self.stats["due"] > 0)
        self.refresh_ai_status()
        self.cards_tab.reload()
        self.free_tab.reload()
        self.review_tab.on_data_changed()

    def refresh_ai_status(self) -> None:
        key = paths.effective_api_key(self.settings)
        source = paths.api_key_source(self.settings)
        if key:
            origin = "设置" if source == "settings" else "环境变量"
            self.ai_status_var.set(f"● DeepSeek 已连接\n{self.settings['model']} · 密钥来自{origin}")
            self.ai_status_label.configure(fg="#5fd39a")
        else:
            self.ai_status_var.set("● 未配置 API 密钥\n点下面的按钮填写")
            self.ai_status_label.configure(fg="#ff8a7a")

    def open_settings(self) -> None:
        SettingsDialog(self, self)

    def open_manual(self) -> None:
        manual = paths.BASE_DIR / "使用说明.txt"
        if not manual.exists():
            messagebox.showinfo(APP_NAME, "没有找到「使用说明.txt」。", parent=self)
            return
        try:
            os.startfile(str(manual))  # noqa: S606 - Windows 上交给默认程序打开
        except OSError as error:
            messagebox.showerror(APP_NAME, f"无法打开说明文件：{error}", parent=self)

    def save_drafts(self) -> None:
        paths.save_drafts(self.drafts)

    def _report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        import traceback

        detail = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        try:
            paths.ensure_data_dir()
            with open(paths.DATA_DIR / "last_error.log", "a", encoding="utf-8") as handle:
                handle.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} ===\n{detail}")
        except OSError:
            pass
        try:
            messagebox.showerror(APP_NAME, f"刚才的操作出错了，已记录到 data\\last_error.log：\n\n{exc_value}", parent=self)
        except tk.TclError:
            pass

    def on_close(self) -> None:
        self.free_tab.panel.persist_draft()
        self.review_tab.panel.persist_draft()
        self.save_drafts()
        self.db.close()
        self.destroy()


class PracticePanel(tk.Frame):
    """两种模式共用的练习区：显示情境 → 输入回答 → 提交 AI 对比 → 展示结果 → 继续。"""

    def __init__(self, parent, app: FlashcardApp, session, on_next, on_skip=None, on_exit=None, on_committed=None):
        super().__init__(parent, bg=BG)
        self.app = app
        self.session = session
        self.mode = session.mode
        self.on_next = on_next
        self.on_skip = on_skip
        self.on_exit = on_exit
        self.on_committed = on_committed

        self.card = None
        self.attempt = None
        self.analysis = None
        self.phase = "answer"
        self.grade = None
        self.preview_scheduling = None

        self.columnconfigure(0, weight=1)
        self.bind("<Configure>", self._on_panel_configure, add="+")
        self._build_head()
        self._build_front()
        self._build_answer()
        self._build_banner()
        self._build_result()
        self._build_confirm()
        self._build_actions()

    # ---------- 界面搭建 ----------
    def _on_panel_configure(self, event) -> None:
        """窗口缩放时，让长文本跟着宽度换行。"""
        width = max(200, event.width - 44)
        for label in (self.front_label, self.meta_label, self.banner_label):
            label.configure(wraplength=width)
        self.confirm_label.configure(wraplength=width - 24)

    def _build_head(self):
        head = tk.Frame(self, bg=BG)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.columnconfigure(1, weight=1)
        self.mode_chip = tk.Label(
            head,
            text="算法复习" if self.mode == "review" else "自由练习",
            bg="#eef0fe" if self.mode == "review" else "#eef7f1",
            fg=ACCENT_DARK if self.mode == "review" else OK,
            font=(FONT, 9, "bold"),
            padx=9,
            pady=3,
        )
        self.position_var = tk.StringVar(value="")

        rule = "确认后会更新这张卡的 FSRS 复习计划" if self.mode == "review" else "不更新复习计划，也不计入正式复习次数"
        self.rule_label = tk.Label(head, text=rule, bg=BG, fg=MUTED, font=(FONT, 9), justify="right")
        self.position_label = tk.Label(head, textvariable=self.position_var, bg=BG, fg=MUTED, font=(FONT, 10))
        self.mode_chip.grid(row=0, column=0, sticky="w")
        self.position_label.grid(row=0, column=1, sticky="w", padx=(10, 8))
        self.rule_label.grid(row=0, column=2, sticky="e")

        def on_head_configure(event):
            # 窗口够宽时说明文字放在同一行右侧；变窄时自动挪到第二行，避免挤掉位置文字
            if event.width < 560:
                self.rule_label.configure(wraplength=max(150, event.width - 20), justify="left")
                self.rule_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
            else:
                self.rule_label.configure(wraplength=max(150, event.width - 320), justify="right")
                self.rule_label.grid(row=0, column=2, sticky="e")

        head.bind("<Configure>", on_head_configure, add="+")

    def _build_front(self):
        card = tk.Frame(self, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=1, column=0, sticky="ew")
        tk.Frame(card, bg=ACCENT, width=3).pack(side="left", fill="y")
        inner = tk.Frame(card, bg=CARD_BG)
        inner.pack(side="left", fill="both", expand=True, padx=18, pady=14)

        tk.Label(inner, text="情 境 / 问 题", bg=CARD_BG, fg=MUTED, font=(FONT, 8)).pack(anchor="w")
        self.front_label = tk.Label(inner, text="", bg=CARD_BG, fg=TEXT, font=(FONT, 16, "bold"), justify="left", anchor="w")
        self.front_label.pack(anchor="w", fill="x", pady=(5, 7))
        self.meta_var = tk.StringVar(value="")
        self.meta_label = tk.Label(
            inner, textvariable=self.meta_var, bg=CARD_BG, fg=MUTED, font=(FONT, 9), justify="left", anchor="w"
        )
        self.meta_label.pack(anchor="w", fill="x")

    def _build_answer(self):
        wrapper = tk.Frame(self, bg=BG)
        wrapper.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        wrapper.columnconfigure(0, weight=1)
        wrapper.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=1, minsize=70)

        tk.Label(wrapper, text="先写下你的回答（Ctrl + Enter 提交）", bg=BG, fg=MUTED, font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        box = tk.Frame(wrapper, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        box.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.answer_text = tk.Text(
            box,
            height=5,
            wrap="word",
            font=(FONT, 12),
            bg=CARD_BG,
            fg=TEXT,
            relief="flat",
            insertbackground=TEXT,
            padx=10,
            pady=8,
            undo=True,
        )
        self.answer_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.answer_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.answer_text.configure(yscrollcommand=scroll.set)
        self.answer_text.bind("<Control-Return>", lambda _event: (self.submit(), "break")[1])
        self.answer_text.bind("<Control-KP_Enter>", lambda _event: (self.submit(), "break")[1])

    def _build_banner(self):
        self.banner_var = tk.StringVar(value="")
        self.banner_label = tk.Label(
            self,
            textvariable=self.banner_var,
            bg=BG,
            fg=MUTED,
            font=(FONT, 10),
            justify="left",
            anchor="w",
            wraplength=800,
        )

    def _build_result(self):
        # 匹配度卡片：大号数字 + 圆角进度条
        score_card = tk.Frame(self, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        self.score_frame = score_card
        score_inner = tk.Frame(score_card, bg=CARD_BG)
        score_inner.pack(fill="x", padx=18, pady=(14, 12))
        head = tk.Frame(score_inner, bg=CARD_BG)
        head.pack(fill="x")
        tk.Label(head, text="匹配程度", bg=CARD_BG, fg=MUTED, font=(FONT, 10)).pack(side="left")
        self.score_var = tk.StringVar(value="—")
        tk.Label(head, textvariable=self.score_var, bg=CARD_BG, fg=TEXT, font=(FONT, 24, "bold")).pack(side="right")
        self.score_bar = ScoreBar(score_inner, height=9)
        self.score_bar.pack(fill="x", pady=(6, 8))
        self.score_hint = tk.Label(
            score_inner,
            text="这个百分比只表示「我的回答」与「这张卡的标准回复」的接近程度，不代表对你个人的评价，也不是社交能力评分。",
            bg=CARD_BG,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
            anchor="w",
            wraplength=800,
        )
        self.score_hint.pack(anchor="w", fill="x")
        adaptive_wrap(self.score_hint, score_inner, 60)

        box = tk.Frame(self, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        self.result_box = box
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.result_text = tk.Text(
            box,
            height=12,
            wrap="word",
            font=(FONT, 10),
            bg=CARD_BG,
            fg=TEXT,
            relief="flat",
            padx=16,
            pady=12,
            state="disabled",
            cursor="arrow",
        )
        self.result_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.result_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.result_text.configure(yscrollcommand=scroll.set)

        self.result_text.tag_configure("h1", font=(FONT, 9, "bold"), foreground=MUTED, spacing1=12, spacing3=4)
        self.result_text.tag_configure("body", font=(FONT, 11), foreground=TEXT, spacing3=2)
        self.result_text.tag_configure("ok", font=(FONT, 11), foreground=OK, spacing3=2, lmargin1=14, lmargin2=22)
        self.result_text.tag_configure("warn", font=(FONT, 11), foreground=WARN, spacing3=2, lmargin1=14, lmargin2=22)
        self.result_text.tag_configure("diff", font=(FONT, 11), foreground=DANGER, spacing3=2, lmargin1=14, lmargin2=22)
        self.result_text.tag_configure("muted", font=(FONT, 9), foreground=MUTED, spacing1=6)
        self.result_text.tag_configure("back", font=(FONT, 12), foreground=TEXT, spacing3=2)

    def _build_confirm(self):
        frame = tk.Frame(self, bg=ACCENT_SOFT, highlightbackground="#cfcffa", highlightthickness=1)
        self.confirm_frame = frame
        inner = tk.Frame(frame, bg=ACCENT_SOFT)
        inner.pack(fill="x", padx=16, pady=12)
        self.confirm_var = tk.StringVar(value="")
        self.confirm_label = tk.Label(
            inner,
            textvariable=self.confirm_var,
            bg=ACCENT_SOFT,
            fg=TEXT,
            font=(FONT, 10),
            justify="left",
            anchor="w",
            wraplength=800,
        )
        self.confirm_label.pack(anchor="w", fill="x")
        tk.Label(
            inner,
            text="在你点击确认之前，不会写入任何 FSRS 调度数据。",
            bg=ACCENT_SOFT,
            fg=MUTED,
            font=(FONT, 9),
        ).pack(anchor="w", pady=(4, 0))

    def _build_actions(self):
        bar = FlowBar(self)
        self.actions_bar = bar
        self.actions_bar.grid(row=8, column=0, sticky="ew", pady=(12, 0))

        self.btn_submit = ttk.Button(bar, text="提交回答", style="Primary.TButton", command=self.submit)
        self.btn_retry = ttk.Button(bar, text="重试分析", style="Primary.TButton", command=self.retry)
        self.btn_confirm = ttk.Button(bar, text="确认并更新复习计划", style="Primary.TButton", command=self.confirm)
        self.btn_next = ttk.Button(bar, text="下一张", style="Primary.TButton", command=self.go_next)
        self.btn_skip = ttk.Button(bar, text="跳过此卡（不更新）" if self.mode == "review" else "换一张", command=self.go_skip)
        self.btn_redo = ttk.Button(bar, text="重新作答", command=self.redo)
        self.btn_exit = ttk.Button(bar, text="结束本次复习", command=self.go_exit)

    # ---------- 状态 ----------
    def persist_draft(self) -> None:
        if self.card is None:
            return
        key = f"{self.mode}:{self.card['id']}"
        text = self.answer_text.get("1.0", "end-1c")
        if text.strip():
            self.app.drafts[key] = text
        else:
            self.app.drafts.pop(key, None)

    def _set_banner(self, text: str, kind: str = "info") -> None:
        colors = {"info": MUTED, "error": DANGER, "ok": OK}
        backgrounds = {"info": BG, "error": DANGER_SOFT, "ok": OK_SOFT}
        self.banner_var.set(text)
        self.banner_label.configure(fg=colors.get(kind, MUTED), bg=backgrounds.get(kind, BG))
        if text:
            self.banner_label.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        else:
            self.banner_label.grid_remove()

    def _show_result_area(self, show: bool) -> None:
        # 匹配度卡片与结果卡片常驻显示：没提交时给一段提示，避免空白和高度跳动
        self.score_frame.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self.result_box.grid(row=6, column=0, sticky="nsew", pady=(10, 0))
        self.rowconfigure(5, weight=0)
        self.rowconfigure(6, weight=3, minsize=110)
        # 出结果后把输入框收窄一点，让对比结果占更多空间
        self.answer_text.configure(height=3 if show else 5)
        if not show:
            self._render_placeholder()

    def _render_placeholder(self) -> None:
        self.score_var.set("—")
        self.score_bar.set(0)
        widget = self.result_text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", "还没有提交\n", "h1")
        widget.insert(
            "end",
            "在上面写下你会说的那句话，点「提交回答」，这里会显示 AI 的对比结果：\n"
            "你答到了哪些关键意思、漏掉了什么、和标准回复的实质差异，以及标准回复全文。",
            "muted",
        )
        widget.configure(state="disabled")

    def _refresh_actions(self):
        buttons = [
            self.btn_submit,
            self.btn_retry,
            self.btn_confirm,
            self.btn_next,
            self.btn_skip,
            self.btn_redo,
            self.btn_exit,
        ]
        for button in buttons:
            button.state(["!disabled"])

        phase = self.phase
        visible: list[tk.Widget] = []
        if phase in ("answer", "error"):
            # 失败时只给「重试分析」：回答没改动会复用同一条记录，改了就当作新的作答提交。
            visible.append(self.btn_retry if phase == "error" else self.btn_submit)
            if self.on_skip:
                visible.append(self.btn_skip)
            if self.on_exit:
                visible.append(self.btn_exit)
        elif phase == "analyzing":
            visible.append(self.btn_submit)
            self.btn_submit.state(["disabled"])
            if self.on_skip:
                visible.append(self.btn_skip)
                self.btn_skip.state(["disabled"])
            if self.on_exit:
                visible.append(self.btn_exit)
                self.btn_exit.state(["disabled"])
        elif phase == "result":
            if self.mode == "review":
                visible.append(self.btn_confirm)
                if self.on_skip:
                    visible.append(self.btn_skip)
                visible.append(self.btn_redo)
                if self.on_exit:
                    visible.append(self.btn_exit)
            else:
                visible.extend([self.btn_next, self.btn_redo, self.btn_skip])
        elif phase == "applied":
            visible.append(self.btn_next)
            if self.on_exit:
                visible.append(self.btn_exit)
        self.actions_bar.show(visible)

    def load(self, card: dict, position_text: str = "") -> None:
        self.persist_draft()
        self.app.save_drafts()
        self.card = card
        self.attempt = None
        self.analysis = None
        self.grade = None
        self.preview_scheduling = None
        self.phase = "answer"

        self.position_var.set(position_text)
        self.front_label.configure(text=card["front"])
        if not card.get("formal_reps"):
            self.meta_var.set("新卡 · 尚未做过正式复习")
        else:
            self.meta_var.set(
                f"上次正式复习 {format_datetime(card.get('last_formal_review_at'))} · "
                f"到期 {format_datetime(card.get('due'))}（{describe_due(card.get('due'), now_ms())}） · "
                f"正式复习 {card.get('formal_reps')} 次 · 状态 {STATE_LABELS.get(card.get('state'), card.get('state'))}"
            )

        draft = self.app.drafts.get(f"{self.mode}:{card['id']}", "")
        self.answer_text.delete("1.0", "end")
        self.answer_text.insert("1.0", draft)
        self.answer_text.configure(state="normal")
        self._set_banner("")
        self._show_result_area(False)
        self.confirm_frame.grid_remove()
        self._refresh_actions()
        self.answer_text.focus_set()

    def clear(self) -> None:
        """没有卡片可练时清空面板。"""
        self.card = None
        self.attempt = None
        self.analysis = None
        self.phase = "answer"
        self.front_label.configure(text="")
        self.meta_var.set("")
        self.position_var.set("")
        self.answer_text.delete("1.0", "end")
        self._show_result_area(False)
        self.confirm_frame.grid_remove()
        self._set_banner("")
        self._refresh_actions()

    # ---------- 流程 ----------
    def submit(self):
        if self.card is None or self.phase == "analyzing":
            return
        answer = self.answer_text.get("1.0", "end-1c").strip()
        if not answer:
            self._set_banner("请先输入你的回答，再提交。", "error")
            self.bell()
            return
        try:
            attempt = self.session.start(self.card, answer)
        except Exception as error:  # noqa: BLE001
            self._set_banner(f"保存回答失败：{error}", "error")
            return
        self.attempt = attempt
        self._run_analysis(attempt["id"], answer)
        return "break"

    def retry(self):
        """AI 失败后重试：回答没变就复用同一条作答记录，避免重复计数。"""
        if self.phase == "analyzing":
            return
        answer = self.answer_text.get("1.0", "end-1c").strip()
        if not answer:
            self._set_banner("请先输入你的回答，再提交。", "error")
            return
        if self.attempt is not None and self.attempt["user_answer"] == answer:
            self._run_analysis(self.attempt["id"], answer)
        else:
            self.submit()

    def _run_analysis(self, attempt_id: str, answer: str):
        self.phase = "analyzing"
        self.analysis = None
        self.grade = None
        self.preview_scheduling = None
        self._show_result_area(False)
        self.confirm_frame.grid_remove()
        self._refresh_actions()
        self._set_banner("正在把你的回答与标准回复做对比…（已提交，请勿重复点击）", "info")

        card = self.card
        api_key = paths.effective_api_key(self.app.settings)
        model = self.app.settings["model"]
        thinking = self.app.settings["thinking"]

        def work():
            return ai.analyze(card["front"], card["back"], answer, api_key, model=model, thinking=thinking)

        def ok(outcome):
            # 任何意外都不能把界面卡在「正在对比」状态：出错也要把状态切回来并说清楚
            try:
                attempt = self.session.attach_result(attempt_id, outcome)
                self.attempt = attempt
                self.analysis = outcome["analysis"]
                self.grade = self.session.grade_for(self.analysis)
                self.phase = "result"
                self._render_result()
                self._render_confirm()
                self._show_result_area(True)
                self._refresh_actions()
                self._set_banner("")
                self.app.drafts.pop(f"{self.mode}:{self.card['id']}", None)
                self.app.save_drafts()
            except Exception as error:  # noqa: BLE001
                self.session.attach_error(attempt_id, f"结果保存失败：{error}")
                self.phase = "error"
                self._refresh_actions()
                self._set_banner(f"拿到了分析结果但保存失败：{error}", "error")

        def fail(error):
            message = error.message if isinstance(error, ai.AiError) else str(error)
            try:
                self.session.attach_error(attempt_id, message)
            except Exception as db_error:  # noqa: BLE001
                message = f"{message}（另外，失败原因也没能保存：{db_error}）"
            self.phase = "error"
            self._refresh_actions()
            self._set_banner(f"分析失败：{message}", "error")

        self.app.run_async(work, ok, fail)

    def confirm(self):
        if self.phase != "result" or self.attempt is None or not self.grade:
            return
        self.btn_confirm.state(["disabled"])
        try:
            result = self.session.confirm(self.attempt["id"], self.grade)
        except Exception as error:  # noqa: BLE001
            self._set_banner(f"更新复习计划失败：{error}", "error")
            self._refresh_actions()
            return

        scheduling = result["scheduling"]
        self.preview_scheduling = scheduling
        self.phase = "applied"
        self.confirm_frame.grid_remove()
        self._refresh_actions()
        self._set_banner(
            f"已更新复习计划：下次复习 {format_datetime(scheduling['due'])}"
            f"（{describe_due(scheduling['due'], now_ms())}） · 状态 {STATE_LABELS.get(scheduling['state'], scheduling['state'])}"
            f" · 正式复习 {scheduling['formal_reps']} 次",
            "ok",
        )
        if self.on_committed:
            self.on_committed()
        fresh = self.app.card_by_id(self.card["id"])
        if fresh:
            self.card = fresh

    def redo(self):
        if self.card is None:
            return
        self.attempt = None
        self.analysis = None
        self.grade = None
        self.preview_scheduling = None
        self.phase = "answer"
        self.answer_text.delete("1.0", "end")
        self.answer_text.configure(state="normal")
        self._set_banner("")
        self._show_result_area(False)
        self.confirm_frame.grid_remove()
        self._refresh_actions()
        self.answer_text.focus_set()

    def go_next(self):
        self.persist_draft()
        self.app.save_drafts()
        if self.on_next:
            self.on_next()

    def go_skip(self):
        self.persist_draft()
        self.app.save_drafts()
        if self.on_skip:
            self.on_skip()

    def go_exit(self):
        self.persist_draft()
        self.app.save_drafts()
        if self.on_exit:
            self.on_exit()

    # ---------- 结果展示 ----------
    def _render_result(self):
        if not self.analysis:
            return
        analysis = self.analysis
        widget = self.result_text
        self.score_var.set(f"{analysis['match_score']}%")
        self.score_bar.set(analysis["match_score"])
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", "我的回答\n", "h1")
        widget.insert("end", f"{(self.attempt or {}).get('user_answer', '')}\n", "body")

        widget.insert("end", "● 与标准回复意思一致的部分\n", "h1")
        if analysis["matched_points"]:
            for item in analysis["matched_points"]:
                widget.insert("end", f"✓ {item}\n", "ok")
        else:
            widget.insert("end", "✓ （无）\n", "muted")

        widget.insert("end", "● 标准回复里你没有表达出来的关键点\n", "h1")
        if analysis["missing_points"]:
            for item in analysis["missing_points"]:
                widget.insert("end", f"! {item}\n", "warn")
        else:
            widget.insert("end", "! （无）\n", "muted")

        widget.insert("end", "● 与标准回复的实质性差异\n", "h1")
        if analysis["differences"]:
            for item in analysis["differences"]:
                widget.insert("end", f"× {item}\n", "diff")
        else:
            widget.insert("end", "× （无）\n", "muted")

        widget.insert("end", "● 分数依据\n", "h1")
        widget.insert("end", f"{analysis['explanation']}\n", "body")

        widget.insert("end", "● 标准回复\n", "h1")
        widget.insert("end", f"{self.card['back']}\n", "back")

        model = (self.attempt or {}).get("analysis_model") or ""
        if model:
            widget.insert("end", f"分析模型：{model}\n", "muted")

        widget.configure(state="disabled")
        widget.yview_moveto(0)

    def _render_confirm(self):
        if self.mode != "review" or not self.analysis or not self.grade:
            self.confirm_frame.grid_remove()
            return
        try:
            next_record, _ = self.session.preview(self.card["id"], self.grade, self.analysis, self.attempt["id"])
        except Exception as error:  # noqa: BLE001
            self.confirm_var.set(f"无法预览复习计划：{error}")
            self.confirm_frame.grid(row=7, column=0, sticky="ew", pady=(10, 0))
            return
        self.preview_scheduling = next_record
        self.confirm_var.set(
            f"匹配程度 {self.analysis['match_score']}% → 按固定映射规则评为 {grade_label(self.grade)}。"
            f"确认后这张卡的下次复习时间将变为 约 {describe_due(next_record['due'], now_ms())}"
            f"（{format_datetime(next_record['due'])}）。"
        )
        self.confirm_frame.grid(row=7, column=0, sticky="ew", pady=(10, 0))



class ReviewTab(tk.Frame):
    """今日复习：只练 FSRS 判定到期的卡片，确认后才更新复习计划。"""

    def __init__(self, parent, app: FlashcardApp):
        super().__init__(parent, bg=BG)
        self.app = app
        self.session = ReviewSession(app.db)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = tk.Frame(self, bg=BG)
        top.grid(row=0, column=0, sticky="ew", pady=(10, 6))
        # 右侧按钮先占位，窗口变窄时不会被文字挤掉
        ttk.Button(top, text="重新开始本轮", command=self.restart).pack(side="right")
        self.progress_var = tk.StringVar(value="")
        self.progress_label = tk.Label(top, textvariable=self.progress_var, bg=BG, fg=TEXT, font=(FONT, 10, "bold"))
        self.progress_label.pack(side="left")
        self.progress_hint = tk.Label(
            top,
            text="只出现 FSRS 判定到期的卡片（含新卡）。匹配分析成功并确认后，才写入复习计划。",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
        )
        self.progress_hint.pack(side="left", padx=12)

        def on_top_configure(event):
            self.progress_hint.configure(wraplength=max(140, event.width - 260))

        top.bind("<Configure>", on_top_configure, add="+")

        self.rowconfigure(0, weight=0)

        self.panel = PracticePanel(
            self,
            app,
            self.session,
            on_next=self.next_card,
            on_skip=self.skip_card,
            on_exit=self.finish,
            on_committed=app.refresh_all,
        )
        self.panel.grid(row=1, column=0, sticky="nsew")

        self.notice_frame = tk.Frame(self, bg=BG)
        self.notice_inner = tk.Frame(self.notice_frame, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        self.notice_inner.pack(fill="x", pady=(0, 10))
        self.notice_var = tk.StringVar(value="")
        self.notice_label = tk.Label(
            self.notice_inner,
            textvariable=self.notice_var,
            bg=CARD_BG,
            fg=TEXT,
            font=(FONT, 11),
            justify="left",
            anchor="w",
            wraplength=800,
            padx=16,
            pady=14,
        )
        self.notice_label.pack(anchor="w", fill="x")
        adaptive_wrap(self.notice_label, self.notice_inner, 60)
        self.notice_actions = FlowBar(self.notice_inner, bg=CARD_BG)
        self.notice_actions.pack(anchor="w", padx=16, pady=(0, 14))
        self.notice_buttons = [
            ttk.Button(self.notice_actions, text="去自由练习", command=lambda: self.app.show_page(1)),
            ttk.Button(self.notice_actions, text="查看全部闪卡", command=lambda: self.app.show_page(2)),
        ]
        self.notice_actions.show(self.notice_buttons)
        self.notice_frame.grid(row=1, column=0, sticky="nsew")
        self.notice_frame.grid_remove()

    def ensure_queue(self):
        if self.session.finished or self.session.total == 0:
            self.session.build_queue()
        self._render()

    def on_data_changed(self):
        if self.session.total == 0:
            self.session.build_queue()
            self._render()

    def restart(self):
        self.session.build_queue()
        self._render()

    def _render(self):
        card_id = self.session.current_card_id()
        if card_id is None:
            self._render_notice()
            return
        card = self.app.card_by_id(card_id)
        if card is None:
            self.session.index += 1
            self._render()
            return
        self.notice_frame.grid_remove()
        self.panel.grid()
        self.progress_var.set(f"第 {self.session.index + 1} / {self.session.total} 张")
        self.panel.load(card, f"到期时间 {format_datetime(card['due'])}")

    def _render_notice(self):
        self.panel.grid_remove()
        self.notice_frame.grid()
        if self.session.total == 0:
            self.notice_var.set("今天没有需要复习的卡片。\n所有卡片都还没到 FSRS 安排的复习时间；想练的话随时可以去自由练习，那不会影响复习计划。")
        else:
            self.notice_var.set(
                f"本轮复习结束：已更新复习计划 {self.session.updated} 张，跳过 {self.session.skipped} 张。\n"
                "跳过的卡片没有写入任何 FSRS 数据，仍然是到期状态。可以点「重新开始本轮」再检查一次，"
                "或去自由练习继续练。"
            )

    def next_card(self):
        self.session.advance(True)
        self._render()

    def skip_card(self):
        self.session.skip()
        self._render()

    def finish(self):
        self.session.index = self.session.total
        self._render()
        self.app.refresh_all()


class FreeTab(tk.Frame):
    """自由练习：左侧选卡，右侧练习。任何卡片都能练，重复练、随时换都可以。"""

    def __init__(self, parent, app: FlashcardApp):
        super().__init__(parent, bg=BG)
        self.app = app
        self.session = FreePracticeSession(app.db)
        self.current_id = None
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.grid(row=0, column=0, sticky="nsew", pady=(10, 0))

        left = tk.Frame(paned, bg=BG)
        right = tk.Frame(paned, bg=BG)
        paned.add(left, weight=1)
        paned.add(right, weight=3)

        size_state = {"last_width": None}

        def keep_right_usable(event):
            """只在窗口变窄时收窄左侧列表，保证右侧练习区仍然放得下；不会打断手动拖动分隔条。"""
            last_width = size_state["last_width"]
            size_state["last_width"] = event.width
            if last_width is None or event.width >= last_width:
                return
            try:
                if event.width - paned.sashpos(0) < int(event.width * 0.45):
                    paned.sashpos(0, max(160, int(event.width * 0.25)))
            except tk.TclError:
                pass

        paned.bind("<Configure>", keep_right_usable, add="+")

        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        tk.Label(
            left,
            text="任意卡片都能练，可以重复练同一张。\n不要求到期，也不会改动复习计划。",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.search_var = tk.StringVar(value="")
        self.search_var.trace_add("write", lambda *_args: self.reload())
        ttk.Entry(left, textvariable=self.search_var).grid(row=1, column=0, sticky="ew", pady=(0, 8))

        tree_wrap = tk.Frame(left, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        tree_wrap.grid(row=2, column=0, sticky="nsew")
        tree_wrap.columnconfigure(0, weight=1)
        tree_wrap.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_wrap, columns=("front",), show="tree", selectmode="browse")
        self.tree.column("#0", width=260)
        self.tree.grid(row=0, column=0, sticky="nsew")

        def resize_tree(event):
            self.tree.column("#0", width=max(150, event.width - 26))

        tree_wrap.bind("<Configure>", resize_tree, add="+")
        scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _event: self.practice_selected())

        buttons = FlowBar(left)
        buttons.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        buttons.show(
            [
                ttk.Button(buttons, text="随机抽一张", style="Primary.TButton", command=self.random_card),
                ttk.Button(buttons, text="练这一张", command=self.practice_selected),
            ]
        )

        self.panel = PracticePanel(
            right,
            app,
            self.session,
            on_next=self.next_random,
            on_skip=self.next_random,
            on_exit=None,
            on_committed=app.refresh_all,
        )
        self.panel.pack(fill="both", expand=True)
        self._show_empty_hint()

    def _show_empty_hint(self):
        self.panel.clear()
        self.panel.position_var.set("左侧选一张卡片，或点「随机抽一张」")
        if not self.app.cards:
            self.panel._set_banner("还没有卡片。请到「全部闪卡」页新建一张，或导入一份 JSON 备份。", "error")
        else:
            self.panel._set_banner("")

    def reload(self):
        cards = query_cards(self.app.cards, self.search_var.get(), "all", "created_desc")
        selected = self.current_id
        self.tree.delete(*self.tree.get_children())
        for card in cards:
            label = f"{truncate(card['front'], 34)}"
            if card["status"] == "new":
                suffix = "（新卡）"
            elif card["status"] == "due":
                suffix = "（已到期）"
            else:
                suffix = ""
            self.tree.insert("", "end", iid=card["id"], text=f"{label}{suffix}")
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
        # 没有在练的卡片时，重新判断空状态提示（避免残留「还没有卡片」）
        if self.current_id is None or self.app.card_by_id(self.current_id) is None:
            self._show_empty_hint()

    def _on_select(self, _event=None):
        selection = self.tree.selection()
        if selection:
            self.current_id = selection[0]

    def practice_selected(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "请先在左侧选择一张卡片。", parent=self)
            return
        self.practice(selection[0])

    def practice(self, card_id: str):
        card = self.app.card_by_id(card_id)
        if card is None:
            self.reload()
            return
        self.current_id = card_id
        if self.tree.exists(card_id):
            self.tree.selection_set(card_id)
            self.tree.see(card_id)
        status = {"new": "新卡", "due": "已到期", "scheduled": "未到期"}[card["status"]]
        self.panel.load(card, f"{status} · 正式复习 {card['formal_reps']} 次")
        self.app.set_status(f"自由练习：正在练「{truncate(card['front'], 30)}」。这次练习不会影响复习计划。")

    def random_card(self):
        cards = self.app.cards
        if not cards:
            messagebox.showinfo(APP_NAME, "还没有卡片，请先新建一张。", parent=self)
            return
        pool = [card for card in cards if card["id"] != self.current_id] or cards
        card = pool[random_index(len(pool))]
        self.practice(card["id"])

    def next_random(self):
        self.random_card()


def random_index(length: int) -> int:
    import random

    return random.randrange(length)


class CardsTab(tk.Frame):
    """全部闪卡：浏览、搜索、筛选、新建、编辑、删除、导入导出。"""

    FILTERS = (
        ("全部", "all"),
        ("已到期 / 新卡", "due"),
        ("仅新卡", "new"),
        ("已正式复习过", "learned"),
    )
    SORTS = (
        ("新建时间（新→旧）", "created_desc"),
        ("新建时间（旧→新）", "created_asc"),
        ("下次到期时间", "due_asc"),
        ("正式复习次数", "reps_desc"),
    )

    def __init__(self, parent, app: FlashcardApp):
        super().__init__(parent, bg=BG)
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        toolbar = FlowBar(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(10, 6))

        self.search_var = tk.StringVar(value="")
        self.search_var.trace_add("write", lambda *_args: self.reload())
        search_entry = ttk.Entry(toolbar, textvariable=self.search_var, width=24)

        self.filter_var = tk.StringVar(value=self.FILTERS[0][0])
        filter_box = ttk.Combobox(toolbar, textvariable=self.filter_var, values=[label for label, _ in self.FILTERS], width=13, state="readonly")
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self.reload())

        self.sort_var = tk.StringVar(value=self.SORTS[0][0])
        sort_box = ttk.Combobox(toolbar, textvariable=self.sort_var, values=[label for label, _ in self.SORTS], width=16, state="readonly")
        sort_box.bind("<<ComboboxSelected>>", lambda _event: self.reload())

        toolbar.show(
            [
                search_entry,
                filter_box,
                sort_box,
                ttk.Button(toolbar, text="新建卡片", style="Primary.TButton", command=self.create_card),
                ttk.Button(toolbar, text="编辑", command=self.edit_selected),
                ttk.Button(toolbar, text="删除", style="Danger.TButton", command=self.delete_selected),
                ttk.Button(toolbar, text="导入 JSON", command=self.import_backup),
                ttk.Button(toolbar, text="导出 JSON", command=self.export_backup),
            ]
        )

        self.count_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.count_var, bg=BG, fg=MUTED, font=(FONT, 9)).grid(row=1, column=0, sticky="w", pady=(0, 6))

        table = tk.Frame(self, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)

        columns = ("front", "back", "status", "due", "reps")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        headings = {
            "front": ("正面（情境 / 问题）", 300),
            "back": ("标准回复", 300),
            "status": ("状态", 90),
            "due": ("下次到期", 150),
            "reps": ("正式复习", 80),
        }
        for key, (label, width) in headings.items():
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w" if key in ("front", "back") else "center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        hscroll = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        hscroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.configure(xscrollcommand=hscroll.set)
        self.tree.bind("<Double-1>", lambda _event: self.edit_selected())

        def resize_columns(event):
            """列宽按比例跟着窗口走，窗口拉宽拉窄都不需要手动调整。"""
            total = max(520, event.width - 40)
            for key, ratio in (("front", 0.32), ("back", 0.30), ("status", 0.09), ("due", 0.20), ("reps", 0.09)):
                self.tree.column(key, width=max(70, int(total * ratio)))

        table.bind("<Configure>", resize_columns, add="+")

        hint = tk.Frame(self, bg=BG)
        hint.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.hint_label = tk.Label(
            hint,
            text=f"匹配分数 → 复习计划映射：{describe_bands()}。"
            "修改标准回复不会重置这张卡的复习历史；删除卡片会同时清理它的调度状态、复习日志与作答记录。",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
            wraplength=900,
        )
        self.hint_label.pack(anchor="w")
        adaptive_wrap(self.hint_label, hint, 20)

    # ---------- 列表 ----------
    def _current_query(self):
        card_filter = dict(self.FILTERS)[self.filter_var.get()]
        sort = dict(self.SORTS)[self.sort_var.get()]
        return self.search_var.get(), card_filter, sort

    def reload(self):
        search, card_filter, sort = self._current_query()
        cards = query_cards(self.app.cards, search, card_filter, sort)
        self.tree.delete(*self.tree.get_children())
        for row_index, card in enumerate(cards):
            status = {"new": "新卡", "due": "已到期", "scheduled": "未到期"}[card["status"]]
            self.tree.insert(
                "",
                "end",
                iid=card["id"],
                values=(
                    truncate(card["front"], 60),
                    truncate(card["back"], 60),
                    status,
                    f"{compact_datetime(card['due'])} · {describe_due(card['due'], now_ms())}",
                    card["formal_reps"],
                ),
                # 斑马纹底色 + 状态颜色：两种标签各自负责背景与文字颜色
                tags=(f"parity{row_index % 2}", card["status"]),
            )
        self.tree.tag_configure("parity0", background=CARD_BG)
        self.tree.tag_configure("parity1", background="#f6f7fb")
        self.tree.tag_configure("new", foreground=ACCENT_DARK)
        self.tree.tag_configure("due", foreground=WARN)
        self.tree.tag_configure("scheduled", foreground="#4a4d55")
        self.count_var.set(f"共 {len(self.app.cards)} 张卡片，当前显示 {len(cards)} 张。双击一行可以直接编辑。")

    def _selected_card(self):
        selection = self.tree.selection()
        if not selection:
            return None
        return self.app.db.get_card(selection[0])

    # ---------- 增删改 ----------
    def create_card(self):
        CardEditorDialog(self, self.app, None)

    def edit_selected(self):
        card = self._selected_card()
        if card is None:
            messagebox.showinfo(APP_NAME, "请先选择一张卡片。", parent=self)
            return
        CardEditorDialog(self, self.app, card)

    def delete_selected(self):
        card = self._selected_card()
        if card is None:
            messagebox.showinfo(APP_NAME, "请先选择一张卡片。", parent=self)
            return
        related = self.app.db.count_related(card["id"])
        confirmed = messagebox.askyesno(
            "确认删除这张卡片？",
            f"{truncate(card['front'], 60)}\n\n"
            f"删除后会同时清理这张卡的关联数据：调度状态 {related['scheduling']} 条、"
            f"正式复习日志 {related['review_logs']} 条、作答记录 {related['attempts']} 条。\n\n此操作不可撤销，确定删除吗？",
            parent=self,
            icon="warning",
            default="no",
        )
        if not confirmed:
            return
        removed = self.app.db.delete_card(card["id"])
        self.app.refresh_all()
        self.app.set_status(
            f"已删除卡片，同时清理了调度状态 {removed['scheduling']} 条、"
            f"正式复习日志 {removed['review_logs']} 条、作答记录 {removed['attempts']} 条。"
        )

    # ---------- 导入导出 ----------
    def export_backup(self):
        default_name = f"社交直觉闪卡-备份-{format_datetime(now_ms()).replace(':', '').replace(' ', '-')}.json"
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出备份",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        data = build_backup(self.app.db)
        try:
            import json

            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
        except OSError as error:
            messagebox.showerror(APP_NAME, f"导出失败：{error}", parent=self)
            return
        messagebox.showinfo(
            APP_NAME,
            f"已导出 {data['counts']['cards']} 张卡片（含调度状态、正式复习日志与作答记录）。",
            parent=self,
        )

    def import_backup(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="选择 JSON 备份文件",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as error:
            messagebox.showerror(APP_NAME, f"读取文件失败：{error}", parent=self)
            return

        data, issues = validate_backup(raw)
        if data is None:
            messagebox.showerror(
                APP_NAME,
                "文件格式校验未通过，本地数据没有任何改动：\n\n" + "\n".join(f"· {issue}" for issue in issues[:12]),
                parent=self,
            )
            return

        dialog = ImportDialog(self, data, summarize_backup(data))
        self.wait_window(dialog)
        if not dialog.result:
            return
        try:
            outcome = apply_backup(self.app.db, data, dialog.result)
        except Exception as error:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"导入失败：{error}", parent=self)
            return
        self.app.refresh_all()
        mode_text = "合并" if outcome["mode"] == "merge" else "覆盖"
        messagebox.showinfo(
            APP_NAME,
            f"导入完成（{mode_text}）：卡片 {outcome['cards']} 张、调度状态 {outcome['scheduling']} 条、"
            f"正式复习日志 {outcome['review_logs']} 条、作答记录 {outcome['attempts']} 条。",
            parent=self,
        )


class CardEditorDialog(tk.Toplevel):
    """新建 / 编辑卡片。修改内容不会重置 FSRS 复习历史。"""

    def __init__(self, parent, app: FlashcardApp, card: dict | None):
        super().__init__(parent)
        self.app = app
        self.card = card
        self.title("编辑卡片" if card else "新建卡片")
        self.configure(bg=CARD_BG)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        body = tk.Frame(self, bg=CARD_BG)
        body.pack(fill="both", expand=True, padx=18, pady=16)

        tk.Label(body, text="正面（情境 / 问题）", bg=CARD_BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        self.front_text = tk.Text(
            body, height=4, wrap="word", font=(FONT, 11), bg=CARD_BG, relief="flat",
            highlightthickness=1, highlightbackground=BORDER_STRONG, highlightcolor=ACCENT, padx=10, pady=8,
        )
        self.front_text.pack(fill="x", pady=(4, 12))

        tk.Label(body, text="背面（标准回复）", bg=CARD_BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        self.back_text = tk.Text(
            body, height=7, wrap="word", font=(FONT, 11), bg=CARD_BG, relief="flat",
            highlightthickness=1, highlightbackground=BORDER_STRONG, highlightcolor=ACCENT, padx=10, pady=8,
        )
        self.back_text.pack(fill="both", expand=True, pady=(4, 8))

        self.hint_label = tk.Label(
            body,
            text="修改标准回复只影响卡面内容，不会重置这张卡的 FSRS 复习历史与下次复习时间。",
            bg=CARD_BG,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
            wraplength=520,
        )
        self.hint_label.pack(anchor="w")
        adaptive_wrap(self.hint_label, body, 30)

        self.error_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.error_var, bg=CARD_BG, fg=DANGER, font=(FONT, 9)).pack(anchor="w", pady=(6, 0))

        actions = tk.Frame(body, bg=CARD_BG)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="保存", style="Primary.TButton", command=self.save).pack(side="right")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))

        if card:
            self.front_text.insert("1.0", card["front"])
            self.back_text.insert("1.0", card["back"])
        self.front_text.focus_set()
        self.bind("<Escape>", lambda _event: self.destroy())
        self.update_idletasks()
        width = max(560, parent.winfo_width() - 200)
        height = 520
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + 80
        self.geometry(f"{width}x{height}+{x}+{y}")

    def save(self):
        front = self.front_text.get("1.0", "end-1c").strip()
        back = self.back_text.get("1.0", "end-1c").strip()
        if not front or not back:
            self.error_var.set("正面和标准回复都不能为空。")
            return
        try:
            if self.card:
                self.app.db.update_card(self.card["id"], front, back)
            else:
                self.app.db.create_card(front, back)
        except Exception as error:  # noqa: BLE001
            self.error_var.set(f"保存失败：{error}")
            return
        self.app.refresh_all()
        self.app.set_status("已保存修改，复习历史与下次复习时间保持不变。" if self.card else "已新建卡片。")
        self.destroy()


class SettingsDialog(tk.Toplevel):
    """API 设置：密钥只保存在程序文件夹内的 data/settings.json。"""

    def __init__(self, parent, app: FlashcardApp):
        super().__init__(parent)
        self.app = app
        self.title("API 设置")
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=14)

        tk.Label(body, text="DeepSeek API 密钥", bg=BG, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        self.key_var = tk.StringVar(value=app.settings.get("api_key", ""))
        entry = ttk.Entry(body, textvariable=self.key_var, width=52, show="•")
        entry.pack(anchor="w", pady=(4, 4))

        source = paths.api_key_source(app.settings)
        current = paths.effective_api_key(app.settings)
        if source == "env":
            note = f"当前正在使用系统环境变量 DEEPSEEK_API_KEY（{paths.mask_key(current)}）。留空即继续使用它。"
        elif source == "settings":
            note = f"当前使用这里保存的密钥（{paths.mask_key(current)}）。"
        else:
            note = "还没有可用的密钥。可以在这里填写，或设置系统环境变量 DEEPSEEK_API_KEY。"
        tk.Label(body, text=note, bg=BG, fg=MUTED, font=(FONT, 9), justify="left", wraplength=520).pack(anchor="w")

        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(12, 0))
        tk.Label(row, text="模型", bg=BG, fg=MUTED, font=(FONT, 9)).pack(side="left")
        self.model_var = tk.StringVar(value=app.settings.get("model", paths.DEFAULT_MODEL))
        ttk.Combobox(row, textvariable=self.model_var, values=list(paths.AVAILABLE_MODELS), width=20, state="readonly").pack(side="left", padx=(8, 16))

        self.thinking_var = tk.BooleanVar(value=app.settings.get("thinking") == "enabled")
        ttk.Checkbutton(row, text="开启思考模式（更慢但更细）", variable=self.thinking_var).pack(side="left")

        tk.Label(
            body,
            text=f"密钥保存在：{paths.SETTINGS_PATH}\n"
            "程序只把它用于调用 DeepSeek，不会写入源码、不会上传到其它服务。"
            "练习内容（情境、标准回复、你的回答）会发送给 DeepSeek 做对比分析。",
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            justify="left",
            wraplength=520,
        ).pack(anchor="w", pady=(12, 0))

        self.status_var = tk.StringVar(value="")
        tk.Label(body, textvariable=self.status_var, bg=BG, fg=MUTED, font=(FONT, 9), justify="left", wraplength=520).pack(anchor="w", pady=(8, 0))

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="测试连接", command=self.test_connection).pack(side="left")
        ttk.Button(actions, text="保存", style="Primary.TButton", command=self.save).pack(side="right")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _event: self.destroy())
        self.geometry(f"600x420+{parent.winfo_rootx() + 120}+{parent.winfo_rooty() + 120}")

    def _collect(self) -> dict:
        return {
            "api_key": self.key_var.get().strip(),
            "model": self.model_var.get().strip() or paths.DEFAULT_MODEL,
            "thinking": "enabled" if self.thinking_var.get() else "disabled",
        }

    def save(self):
        settings = self._collect()
        try:
            paths.save_settings(settings)
        except OSError as error:
            self.status_var.set(f"保存失败：{error}")
            return
        self.app.settings.update(settings)
        self.app.refresh_ai_status()
        self.app.set_status(f"已保存 API 设置（{settings['model']}）。")
        self.destroy()

    def test_connection(self):
        settings = self._collect()
        api_key = settings["api_key"] or paths.effective_api_key({"api_key": ""})
        if not api_key:
            messagebox.showwarning(APP_NAME, "请先填写 API 密钥，或设置系统环境变量 DEEPSEEK_API_KEY。", parent=self)
            return
        self.status_var.set("正在测试连接…")

        def work():
            return ai.check_connection(api_key, model=settings["model"], thinking=settings["thinking"])

        def ok(outcome):
            analysis = outcome["analysis"]
            self.status_var.set(f"连接成功：示例卡片匹配度 {analysis['match_score']}%。")

        def fail(error):
            message = error.message if isinstance(error, ai.AiError) else str(error)
            self.status_var.set(f"连接失败：{message}")

        self.app.run_async(work, ok, fail)


class ImportDialog(tk.Toplevel):
    """导入方式确认：合并还是覆盖（覆盖需要额外勾选确认）。"""

    def __init__(self, parent, data: dict, summary: str):
        super().__init__(parent)
        self.title("导入 JSON 备份")
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        self.result = None

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=14)

        tk.Label(body, text="文件校验通过：", bg=BG, fg=OK, font=(FONT, 10, "bold")).pack(anchor="w")
        self.summary_label = tk.Label(body, text=summary, bg=BG, fg=TEXT, font=(FONT, 10), justify="left", wraplength=520)
        self.summary_label.pack(anchor="w", fill="x", pady=(4, 12))
        adaptive_wrap(self.summary_label, body, 30)

        self.mode_var = tk.StringVar(value="merge")
        ttk.Radiobutton(
            body,
            text="合并：按 ID 更新同名卡片，保留本地其它卡片和数据（推荐）",
            variable=self.mode_var,
            value="merge",
            command=self._on_mode,
        ).pack(anchor="w")
        ttk.Radiobutton(
            body,
            text="覆盖：先清空本地全部卡片、调度状态与作答记录，再导入文件内容",
            variable=self.mode_var,
            value="replace",
            command=self._on_mode,
        ).pack(anchor="w", pady=(6, 0))

        self.ack_var = tk.BooleanVar(value=False)
        self.ack_check = ttk.Checkbutton(
            body,
            text="我明白这会清空本地现有的全部数据，并且无法撤销。",
            variable=self.ack_var,
            command=self._refresh,
        )
        self.ack_check.pack(anchor="w", pady=(12, 0))

        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(16, 0))
        self.confirm_button = ttk.Button(actions, text="确认合并", style="Primary.TButton", command=self._confirm)
        self.confirm_button.pack(side="right")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _event: self.destroy())
        self.geometry(f"600x400+{parent.winfo_rootx() + 100}+{parent.winfo_rooty() + 100}")
        self._refresh()

    def _on_mode(self):
        self._refresh()

    def _refresh(self):
        if self.mode_var.get() == "replace":
            self.confirm_button.configure(text="确认覆盖本地数据")
            self.confirm_button.state(["!disabled"] if self.ack_var.get() else ["disabled"])
            self.ack_check.state(["!disabled"])
        else:
            self.confirm_button.configure(text="确认合并")
            self.confirm_button.state(["!disabled"])
            self.ack_var.set(False)
            self.ack_check.state(["!disabled"])

    def _confirm(self):
        self.result = self.mode_var.get()
        self.destroy()


INSTANCE_LOCK_PORT = 51788


def _acquire_single_instance():
    """用本地端口当锁：程序已经在运行时，不再让第二个窗口同时打开数据库。"""
    import socket

    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", INSTANCE_LOCK_PORT))
        lock.listen(1)
    except OSError:
        lock.close()
        return None
    return lock


def _focus_existing_window() -> None:
    """把已经打开的窗口切到最前面，并提示一句。"""
    hwnd = 0
    if os.name == "nt":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, f"{APP_NAME} {APP_VERSION}")
        except Exception:  # noqa: BLE001
            hwnd = 0
    if hwnd:
        try:
            import ctypes

            user32 = ctypes.windll.user32
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(APP_NAME, "程序已经在运行了，窗口应该在任务栏里。")
    root.destroy()


def main() -> None:
    paths.ensure_data_dir()
    instance_lock = _acquire_single_instance()
    if instance_lock is None:
        _focus_existing_window()
        return
    db = Database(paths.DB_PATH)
    db.initialize()
    db.seed_if_needed(SEED_CARDS)
    settings = paths.load_settings()
    app = FlashcardApp(db, settings)
    try:
        app.mainloop()
    finally:
        db.close()
        instance_lock.close()
