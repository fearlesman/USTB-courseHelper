"""抢课任务页与轮询逻辑。"""

import threading
import time
from datetime import datetime, time as clock_time
import tkinter as tk
from tkinter import ttk, messagebox
from collections import defaultdict
import requests
from rush_schedule import get_scheduled_start
from course_query import format_schedule_text
from ui_workspace import open_schedule_popup
from user_profiles import UserProfileStoreError
from multi_user_runtime import UserLoginState, UserRuntimeContext, UserTaskState
from selection_codes import classify_selection_response, is_environment_request_error
import app_legacy

# 抢课接口请求共用的浏览器伪装头；新旧路径共享同一份定义。
_RUSH_HEADERS: dict[str, str] = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Host": "byyt.ustb.edu.cn",
    "Origin": "https://byyt.ustb.edu.cn",
    "Pragma": "no-cache",
    "Referer": "https://byyt.ustb.edu.cn/Xsxk/query/1",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 "
        "Mobile Safari/537.36 Edg/139.0.0.0"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

# 保活线程周期：心跳轮询间隔、业务保活间隔与底层在线间隔（秒）。
_KEEPALIVE_TICK_SECONDS = 1.0
_BUSINESS_KEEPALIVE_INTERVAL_SECONDS = 360
_ONLINE_KEEPALIVE_INTERVAL_SECONDS = 600


class UserRushPage:
    """一名用户独立的抢课任务页签控件集合。

    Attributes:
        profile_id: 该页签对应用户 UUID；无启用用户时为 None。
        frame: 页签承载的顶层 Frame。
        status_var: 页头任务状态文本。
        current_list_name_var: 当前列表名称变量。
        current_list_note_var: 当前列表备注摘要变量。
        current_list_dirty_var: 当前列表保存状态变量。
        current_list_dirty_label: 保存状态标签。
        current_list_name_label: 只读名称标签。
        edit_current_list_name_btn: 行内编辑入口按钮。
        inline_list_name_var: 行内编辑名称变量。
        inline_list_name_entry: 行内编辑输入框。
        inline_list_name_frame: 行内编辑控件容器。
        inline_confirm_btn: 行内编辑确认按钮。
        inline_cancel_btn: 行内编辑取消按钮。
        save_rush_list_btn: 保存命名列表按钮。
        manage_rush_lists_btn: 打开列表管理按钮。
        rush_mode_var: 当前抢课模式变量。
        rush_mode_buttons: 模式单选项列表。
        stop_on_success_var: 选到一门后停止策略变量。
        stop_on_success_check: 停止策略单选框。
        retry_full_var: 满课持续重试策略变量。
        retry_full_check: 满课策略单选框。
        rush_time_frame: 定时开始时间输入容器。
        rush_time_var: 定时开始时间文本变量。
        rush_time_entry: 定时开始时间输入框。
        task_count_var: 待抢课程数量文本变量。
        task_add_course_btn: 跳转课程查询的添加按钮。
        remove_course_btn: 删除选中课程按钮。
        task_priority_help_label: 优先级说明标签。
        course_tree: 待抢课程表。
        empty_label: 空列表提示标签。
        empty_add_btn: 空列表添加课程按钮。
        start_auto_btn: 开始抢课按钮。
        stop_auto_btn: 停止抢课按钮。
    """

    def __init__(
        self: "UserRushPage",
        parent: tk.Widget,
        profile_id: str | None,
        title: str,
    ) -> None:
        """创建用户抢课页的全部控件。

        Args:
            parent: 页签承载父控件。
            profile_id: 该页签对应用户 UUID；无启用用户时为 None。
            title: 页内标题文本。

        Returns:
            None: 控件直接创建在 parent 中。
        """
        self.profile_id = profile_id
        page = ttk.Frame(parent, style="App.TFrame", padding=(22, 14))
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(3, weight=3)
        self.frame = page

        header = ttk.Frame(page, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=title, style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.status_var = tk.StringVar(value="空闲")
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(
            row=0, column=1, sticky="e"
        )

        current_list_bar = ttk.Frame(page, style="TFrame", padding=(12, 9))
        current_list_bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        current_list_bar.columnconfigure(1, weight=1)
        ttk.Label(
            current_list_bar, text="当前列表", style="SurfaceMuted.TLabel"
        ).grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.current_list_name_var = tk.StringVar(value="默认列表")
        self.current_list_note_var = tk.StringVar(value="自动保存当前待抢课程")
        self.current_list_dirty_var = tk.StringVar(value="自动保存")
        self.current_list_name_label = ttk.Label(
            current_list_bar,
            textvariable=self.current_list_name_var,
            style="ListName.TLabel",
        )
        self.current_list_name_label.grid(row=0, column=1, sticky="w")
        self.edit_current_list_name_btn = ttk.Button(
            current_list_bar,
            text="修改名称",
            style="Secondary.TButton",
        )
        self.edit_current_list_name_btn.grid(row=0, column=2, padx=(8, 0))
        self.inline_list_name_var = tk.StringVar(value="")
        self.inline_list_name_frame = ttk.Frame(current_list_bar, style="TFrame")
        self.inline_list_name_entry = ttk.Entry(
            self.inline_list_name_frame,
            textvariable=self.inline_list_name_var,
            width=24,
        )
        self.inline_list_name_entry.pack(side=tk.LEFT, padx=(0, 6))
        self.inline_confirm_btn = ttk.Button(
            self.inline_list_name_frame,
            text="确认",
            style="Primary.TButton",
        )
        self.inline_confirm_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.inline_cancel_btn = ttk.Button(
            self.inline_list_name_frame,
            text="取消",
            style="Secondary.TButton",
        )
        self.inline_cancel_btn.pack(side=tk.LEFT)
        ttk.Label(
            current_list_bar,
            textvariable=self.current_list_note_var,
            style="SurfaceMuted.TLabel",
        ).grid(row=1, column=1, sticky="w", pady=(3, 0))
        self.current_list_dirty_label = ttk.Label(
            current_list_bar,
            textvariable=self.current_list_dirty_var,
            style="Clean.TLabel",
        )
        self.current_list_dirty_label.grid(row=0, column=3, rowspan=2, padx=(12, 10))
        self.save_rush_list_btn = ttk.Button(
            current_list_bar,
            text="保存列表",
            style="Primary.TButton",
        )
        self.save_rush_list_btn.grid(row=0, column=4, rowspan=2, padx=(0, 8))
        self.manage_rush_lists_btn = ttk.Button(
            current_list_bar,
            text="管理列表",
            style="Secondary.TButton",
        )
        self.manage_rush_lists_btn.grid(row=0, column=5, rowspan=2)

        settings = ttk.LabelFrame(page, text="任务设置", padding=(10, 8))
        settings.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for column_index in range(4):
            settings.columnconfigure(column_index, weight=1)

        self.rush_mode_var = tk.StringVar(value="轮询模式")
        mode_frame = ttk.Frame(settings, style="TFrame")
        mode_frame.grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Label(mode_frame, text="模式").pack(side=tk.LEFT, padx=(0, 6))
        self.rush_mode_buttons: list[ttk.Radiobutton] = []
        for mode_name in ("轮询模式", "定时抢课"):
            mode_button = ttk.Radiobutton(
                mode_frame,
                text=mode_name,
                value=mode_name,
                variable=self.rush_mode_var,
                style="Mode.TRadiobutton",
            )
            mode_button.pack(side=tk.LEFT, padx=(0, 4))
            self.rush_mode_buttons.append(mode_button)

        self.stop_on_success_var = tk.BooleanVar(value=True)
        self.retry_full_var = tk.BooleanVar(value=True)
        stop_frame = ttk.Frame(settings, style="TFrame")
        stop_frame.grid(row=0, column=1, sticky="w", padx=(0, 12))
        ttk.Label(stop_frame, text="停止策略").pack(side=tk.LEFT, padx=(0, 6))
        self.stop_on_success_check = ttk.Checkbutton(
            stop_frame,
            text="选到一门后停止",
            variable=self.stop_on_success_var,
        )
        self.stop_on_success_check.pack(side=tk.LEFT)
        full_frame = ttk.Frame(settings, style="TFrame")
        full_frame.grid(row=0, column=2, sticky="w")
        ttk.Label(full_frame, text="满课策略").pack(side=tk.LEFT, padx=(0, 6))
        self.retry_full_check = ttk.Checkbutton(
            full_frame,
            text="持续重试",
            variable=self.retry_full_var,
        )
        self.retry_full_check.pack(side=tk.LEFT)

        self.rush_time_frame = ttk.Frame(settings, style="TFrame")
        self.rush_time_frame.grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(self.rush_time_frame, text="定时开始").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        self.rush_time_var = tk.StringVar(value="10:00:00")
        self.rush_time_entry = ttk.Entry(
            self.rush_time_frame,
            textvariable=self.rush_time_var,
            width=12,
        )
        self.rush_time_entry.pack(side=tk.LEFT)
        self.rush_time_frame.grid_remove()

        list_frame = ttk.LabelFrame(page, text="待抢课程", padding=8)
        list_frame.grid(row=3, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)
        list_toolbar = ttk.Frame(list_frame, style="TFrame")
        list_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        list_toolbar.columnconfigure(0, weight=1)
        self.task_count_var = tk.StringVar(value="共 0 门课程")
        ttk.Label(
            list_toolbar,
            textvariable=self.task_count_var,
            style="SurfaceMuted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.task_add_course_btn = ttk.Button(
            list_toolbar,
            text="添加课程",
            style="Secondary.TButton",
        )
        self.task_add_course_btn.grid(row=0, column=1, padx=(0, 8))
        self.remove_course_btn = ttk.Button(
            list_toolbar,
            text="删除选中课程",
            style="Secondary.TButton",
        )
        self.remove_course_btn.grid(row=0, column=2)
        self.task_priority_help_label = ttk.Label(
            list_toolbar,
            text="优先级说明：数字越小越先尝试；相同数字按列表顺序执行。",
            style="SurfaceMuted.TLabel",
        )
        self.task_priority_help_label.grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(5, 0)
        )

        table_body = ttk.Frame(list_frame, style="TFrame")
        table_body.grid(row=1, column=0, columnspan=2, sticky="nsew")
        table_body.columnconfigure(0, weight=1)
        table_body.rowconfigure(0, weight=1)
        columns = (
            "id",
            "priority",
            "name",
            "teacher",
            "course_id",
            "schedule",
            "attempt_status",
            "last_response",
        )
        self.course_tree = ttk.Treeview(
            table_body,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        headings = {
            "id": "序号",
            "priority": "优先级",
            "name": "课程名称",
            "teacher": "授课教师",
            "course_id": "课程代码",
            "schedule": "上课安排",
            "attempt_status": "抢课状态",
            "last_response": "最近返回",
        }
        widths = {
            "id": 60,
            "priority": 70,
            "name": 220,
            "teacher": 120,
            "course_id": 130,
            "schedule": 280,
            "attempt_status": 180,
            "last_response": 320,
        }
        for column_name in columns:
            self.course_tree.heading(column_name, text=headings[column_name])
            self.course_tree.column(
                column_name,
                width=widths[column_name],
                minwidth=55,
                stretch=column_name == "last_response",
            )
        task_scrollbar_y = ttk.Scrollbar(
            table_body,
            orient="vertical",
            command=self.course_tree.yview,
        )
        task_scrollbar_x = ttk.Scrollbar(
            table_body,
            orient="horizontal",
            command=self.course_tree.xview,
        )
        self.course_tree.configure(
            yscrollcommand=task_scrollbar_y.set,
            xscrollcommand=task_scrollbar_x.set,
        )
        self.course_tree.grid(row=0, column=0, sticky="nsew")
        task_scrollbar_y.grid(row=0, column=1, sticky="ns")
        task_scrollbar_x.grid(row=1, column=0, sticky="ew")
        self.course_tree.bind("<Button-1>", self._on_schedule_cell_click)
        self.empty_label = ttk.Label(
            table_body,
            text="暂无待抢课程",
            style="SurfaceMuted.TLabel",
        )
        self.empty_add_btn = ttk.Button(
            table_body,
            text="添加课程",
            style="Primary.TButton",
        )

        actions = ttk.Frame(page, style="App.TFrame")
        actions.grid(row=4, column=0, sticky="ew", pady=(8, 8))
        actions.columnconfigure(0, weight=1)
        self.start_auto_btn = ttk.Button(
            actions,
            text="开始轮询",
            style="Primary.TButton",
        )
        self.start_auto_btn.grid(row=0, column=1, padx=(8, 8))
        self.stop_auto_btn = ttk.Button(
            actions,
            text="停止抢课",
            state=tk.DISABLED,
            style="Danger.TButton",
        )
        self.stop_auto_btn.grid(row=0, column=2)

    def set_empty_visible(self: "UserRushPage", visible: bool) -> None:
        """显示或隐藏空列表提示与添加按钮。

        Args:
            self: 当前用户抢课页。
            visible: True 表示显示空状态。

        Returns:
            None: 空状态控件布局会直接更新。
        """
        if visible:
            self.empty_label.place(relx=0.5, rely=0.42, anchor="center")
            self.empty_add_btn.place(relx=0.5, rely=0.60, anchor="center")
        else:
            self.empty_label.place_forget()
            self.empty_add_btn.place_forget()

    def _on_schedule_cell_click(
        self: "UserRushPage", event: tk.Event
    ) -> None:
        """点击上课安排单元格时弹出完整安排窗口。

        Args:
            self: 当前用户抢课页。
            event: 表格点击事件。

        Returns:
            None: 点击表头或非上课安排列时静默返回。
        """
        if self.course_tree.identify_region(event.x, event.y) != "cell":
            return
        if self.course_tree.identify_column(event.x) != "#6":
            return
        row_id = self.course_tree.identify_row(event.y)
        if not row_id:
            return
        values = self.course_tree.item(row_id, "values")
        if len(values) < 6:
            return
        open_schedule_popup(
            self.frame.winfo_toplevel(), str(values[5])
        )


class RushTaskMixin:
    def setup_rush_tab(self: "CourseSelectionApp") -> None:
        """为当前启用用户重建一人一页的抢课任务页签。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 页签重建后自动切换到当前用户抢课页。
        """
        self.build_rush_tabs()

    def build_rush_tabs(self: "CourseSelectionApp") -> None:
        """按当前启用账号重建每用户一个的抢课任务页签。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 无启用用户时保留占位空页签。
        """
        self.rebuild_rush_tabs()

    def ensure_rush_tabs(self: "CourseSelectionApp") -> None:
        """在启用用户集合变化时重建抢课任务页签。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 用户或别名未变化时不执行任何操作。
        """
        if not hasattr(self, "tab_control") or not hasattr(self, "runtime"):
            return
        contexts = self.runtime.enabled_contexts()
        signature = tuple(
            (context.profile.id, context.profile.alias) for context in contexts
        )
        if getattr(self, "_rush_tabs_signature", None) == signature:
            return
        self.rebuild_rush_tabs()

    def rebuild_rush_tabs(self: "CourseSelectionApp") -> None:
        """删除旧抢课页签并为每名启用用户重建独立页签。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 重建后恢复当前用户页签选中和控件状态。
        """
        if not hasattr(self, "tab_control"):
            self.rush_views = {}
            self.rush_tab_frames = {}
            return
        previous_id = self.current_profile_id()
        for frame in list(getattr(self, "rush_tab_frames", {}).values()):
            try:
                self.tab_control.forget(frame)
            except tk.TclError:
                pass
        self.rush_views = {}
        self.rush_tab_frames = {}
        self.current_rush_view = None
        contexts = self.runtime.enabled_contexts()
        if not contexts:
            self._build_rush_page_tab(None, None, "抢课任务")
        else:
            multi_user = len(contexts) > 1
            for view_context in contexts:
                title = (
                    f"抢课任务·{view_context.profile.alias}"
                    if multi_user
                    else "抢课任务"
                )
                self._build_rush_page_tab(
                    view_context.profile.id, view_context, title
                )
        self._rush_tabs_signature = tuple(
            (view_context.profile.id, view_context.profile.alias)
            for view_context in contexts
        )
        target_id = previous_id if previous_id in self.rush_views else None
        if target_id is None:
            target_id = self.current_profile_id()
        if target_id is None and contexts:
            target_id = contexts[0].profile.id
        self._sync_active_rush_view(target_id)
        self._apply_tab_states()

    def _build_rush_page_tab(
        self: "CourseSelectionApp",
        profile_id: str | None,
        view_context: UserRuntimeContext | None,
        title: str,
    ) -> None:
        """为指定用户创建单个抢课任务页签。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 目标用户 UUID；None 表示无启用用户的占位页。
            view_context: 目标用户运行时上下文；占位页时为 None。
            title: 页签与页内标题文本。

        Returns:
            None: 页签和控件集合写入应用实例。
        """
        frame = ttk.Frame(self.tab_control, style="TFrame")
        view = UserRushPage(frame, profile_id, title)
        self.rush_views[profile_id] = view
        self.rush_tab_frames[profile_id] = frame
        self.tab_control.add(frame, text=title)
        if profile_id is not None:
            view.edit_current_list_name_btn.config(
                command=self.begin_current_list_rename
            )
            view.inline_confirm_btn.config(command=self.confirm_current_list_rename)
            view.inline_cancel_btn.config(command=self.cancel_current_list_rename)
            view.save_rush_list_btn.config(command=self.open_save_rush_list_dialog)
            view.manage_rush_lists_btn.config(command=self.open_rush_list_manager)
            view.task_add_course_btn.config(command=self.show_course_search_tab)
            view.empty_add_btn.config(command=self.show_course_search_tab)
            view.remove_course_btn.config(command=self.remove_course)
            view.start_auto_btn.config(command=self.start_selection)
            view.stop_auto_btn.config(command=self.stop_auto_selection)
            for mode_button in view.rush_mode_buttons:
                mode_button.config(
                    command=lambda target_view=view: self._on_view_mode_change(
                        target_view
                    )
                )
            self._load_view_settings(view, view_context)
            self._refresh_view_list_status(view, view_context)
            self._on_view_mode_change(view)
        self._refresh_view_controls(view, view_context)
        self._refresh_view_course_table(view, view_context)
        self._refresh_view_header(view, view_context)

    def _load_view_settings(
        self: "CourseSelectionApp",
        view: UserRushPage,
        view_context: UserRuntimeContext,
    ) -> None:
        """将用户运行期的抢课设置载入其页签控件。

        Args:
            self: 当前课程助手应用实例。
            view: 目标用户抢课页。
            view_context: 目标用户运行时上下文。

        Returns:
            None: 模式、定时时间与策略变量会同步更新。
        """
        view.rush_mode_var.set(view_context.rush_mode)
        view.rush_time_var.set(view_context.rush_time)
        view.stop_on_success_var.set(bool(view_context.stop_on_success))
        view.retry_full_var.set(bool(view_context.retry_full))

    def _on_view_mode_change(
        self: "CourseSelectionApp", view: UserRushPage
    ) -> None:
        """根据页签模式切换定时时间输入框的可见性。

        Args:
            self: 当前课程助手应用实例。
            view: 模式发生变化的用户抢课页。

        Returns:
            None: 目标页签的时间输入与开始按钮文本同步更新。
        """
        if view.rush_mode_var.get() == "定时抢课":
            view.rush_time_frame.grid(
                row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
            )
            view.rush_time_entry.config(state=tk.NORMAL)
            view.start_auto_btn.config(text="定时抢课")
        else:
            view.rush_time_frame.grid_remove()
            view.start_auto_btn.config(text="开始轮询")

    def _refresh_view_header(
        self: "CourseSelectionApp",
        view: UserRushPage,
        view_context: UserRuntimeContext | None,
    ) -> None:
        """刷新页签标题右侧的用户任务状态文本。

        Args:
            self: 当前课程助手应用实例。
            view: 目标用户抢课页。
            view_context: 目标用户运行时上下文；占位页时为 None。

        Returns:
            None: 页头状态标签会同步更新。
        """
        if view_context is None:
            view.status_var.set("空闲")
            return
        state_text = view_context.task_state.value
        if (
            view_context.last_result
            and view_context.task_state is not UserTaskState.IDLE
        ):
            state_text = f"{state_text} · {view_context.last_result}"
        view.status_var.set(state_text)

    def _refresh_view_list_status(
        self: "CourseSelectionApp",
        view: UserRushPage,
        view_context: UserRuntimeContext | None,
    ) -> None:
        """将用户上下文的命名列表状态刷新到页签状态条。

        Args:
            self: 当前课程助手应用实例。
            view: 目标用户抢课页。
            view_context: 目标用户运行时上下文；占位页时为 None。

        Returns:
            None: 名称、备注摘要和保存状态变量会同步更新。
        """
        if (
            view_context is None
            or view_context.current_saved_list_id is None
        ):
            view.current_list_name_var.set("默认列表")
            view.current_list_note_var.set("自动保存当前待抢课程")
            view.current_list_dirty_var.set("自动保存")
            view.current_list_dirty_label.config(style="Clean.TLabel")
            return
        view.current_list_name_var.set(view_context.current_saved_list_name)
        normalized_note = " ".join(view_context.current_saved_list_note.split())
        note_summary = normalized_note or "暂无备注"
        if len(note_summary) > 70:
            note_summary = f"{note_summary[:67]}..."
        view.current_list_note_var.set(note_summary)
        if view_context.current_saved_list_dirty:
            view.current_list_dirty_var.set("有未保存更改")
            view.current_list_dirty_label.config(style="Dirty.TLabel")
        else:
            view.current_list_dirty_var.set("已保存")
            view.current_list_dirty_label.config(style="Clean.TLabel")

    def _refresh_view_controls(
        self: "CourseSelectionApp",
        view: UserRushPage,
        view_context: UserRuntimeContext | None,
    ) -> None:
        """按用户登录与任务状态刷新页签控件可用性。

        Args:
            self: 当前课程助手应用实例。
            view: 目标用户抢课页。
            view_context: 目标用户运行时上下文；占位页时为 None。

        Returns:
            None: 开始、停止与列表编辑控件状态会同步更新。
        """
        if view_context is None:
            self._set_rush_controls_enabled(True, view=view)
            view.start_auto_btn.config(state=tk.DISABLED)
            view.stop_auto_btn.config(state=tk.DISABLED)
            view.remove_course_btn.config(state=tk.NORMAL)
            return
        running = self.is_context_task_active(view_context)
        logged_in = (
            view_context.login_state is UserLoginState.LOGGED_IN
            and bool(view_context.cookies)
        )
        self._set_rush_controls_enabled(not running, view=view)
        view.start_auto_btn.config(
            state=tk.NORMAL if logged_in and not running else tk.DISABLED
        )
        view.stop_auto_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        view.remove_course_btn.config(
            state=tk.NORMAL if not running else tk.DISABLED
        )
        view.task_add_course_btn.config(
            state=tk.NORMAL if not running else tk.DISABLED
        )
        view.empty_add_btn.config(state=tk.NORMAL if not running else tk.DISABLED)

    def _refresh_view_course_table(
        self: "CourseSelectionApp",
        view: UserRushPage,
        view_context: UserRuntimeContext | None,
    ) -> None:
        """排序并刷新指定用户抢课页的待抢课程表与空状态。

        Args:
            self: 当前课程助手应用实例。
            view: 目标用户抢课页。
            view_context: 目标用户运行时上下文；占位页时为 None。

        Returns:
            None: 课程顺序、表格行与空状态提示会同步更新。
        """
        for item_id in view.course_tree.get_children():
            view.course_tree.delete(item_id)
        if view_context is None:
            view.task_count_var.set("共 0 门课程")
            view.set_empty_visible(True)
            return
        courses = view_context.courses
        sorted_courses = sorted(
            courses, key=lambda item: (item["priority"], item["id"])
        )
        for new_id, course in enumerate(sorted_courses, start=1):
            course["id"] = new_id
        app_legacy.course_id_count = len(sorted_courses)
        view_context.courses[:] = sorted_courses
        for course in sorted_courses:
            attempt_status = "等待抢课"
            last_response = "尚未开始"
            raw_data = course.get("data")
            task_id = (
                str(raw_data.get("p_id", ""))
                if isinstance(raw_data, dict)
                else ""
            )
            if task_id:
                attempt = self.runtime.course_attempt(
                    view_context.profile.id, task_id
                )
                if attempt is not None:
                    attempt_status = attempt.status
                    last_response = attempt.message
                    if attempt.attempt_count:
                        last_response = (
                            f"第 {attempt.attempt_count} 次 | {last_response}"
                        )
            view.course_tree.insert("", "end", values=(
                course["id"],
                course["priority"],
                course["name"],
                course["teacher"],
                course["course_id"],
                format_schedule_text(course["schedule"]),
                attempt_status,
                last_response,
            ))
        view.task_count_var.set(f"共 {len(sorted_courses)} 门课程")
        view.set_empty_visible(not bool(sorted_courses))

    def refresh_rush_view(self: "CourseSelectionApp", profile_id: str) -> None:
        """完整刷新指定用户抢课页签的标题、列表、课程表与控件。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 需要刷新的用户 UUID。

        Returns:
            None: 无该用户页签或运行时不存在时不执行操作。
        """
        if not hasattr(self, "runtime"):
            return
        view = getattr(self, "rush_views", {}).get(profile_id)
        if view is None:
            return
        try:
            view_context = self.runtime.require_context(profile_id)
        except UserProfileStoreError:
            return
        self._refresh_view_header(view, view_context)
        self._refresh_view_list_status(view, view_context)
        self._refresh_view_controls(view, view_context)
        self._refresh_view_course_table(view, view_context)

    def _sync_active_rush_view(
        self: "CourseSelectionApp", profile_id: str | None
    ) -> None:
        """将当前用户抢课页的控件更新到兼容代理属性。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 当前用户 UUID；无对应页签或占位页时使用占位页。

        Returns:
            None: 旧界面方法引用的代理属性会指向当前用户页签控件。
        """
        rush_views = getattr(self, "rush_views", {})
        view = rush_views.get(profile_id)
        if view is None:
            view = next(iter(rush_views.values()), None)
        self.current_rush_view = view
        self.rush_tab = view.frame if view is not None else None
        if view is None:
            return
        self.course_tree = view.course_tree
        self.task_count_var = view.task_count_var
        self.task_empty_label = view.empty_label
        self.task_empty_add_btn = view.empty_add_btn
        self.task_add_course_btn = view.task_add_course_btn
        self.remove_course_btn = view.remove_course_btn
        self.start_auto_btn = view.start_auto_btn
        self.stop_auto_btn = view.stop_auto_btn
        self.rush_mode_var = view.rush_mode_var
        self.rush_mode_buttons = view.rush_mode_buttons
        self.stop_on_success_var = view.stop_on_success_var
        self.stop_on_success_check = view.stop_on_success_check
        self.retry_full_var = view.retry_full_var
        self.retry_full_check = view.retry_full_check
        self.rush_time_frame = view.rush_time_frame
        self.rush_time_var = view.rush_time_var
        self.rush_time_entry = view.rush_time_entry
        self.current_list_name_var = view.current_list_name_var
        self.current_list_note_var = view.current_list_note_var
        self.current_list_dirty_var = view.current_list_dirty_var
        self.current_list_dirty_label = view.current_list_dirty_label
        self.current_list_name_label = view.current_list_name_label
        self.edit_current_list_name_btn = view.edit_current_list_name_btn
        self.inline_list_name_var = view.inline_list_name_var
        self.inline_list_name_frame = view.inline_list_name_frame
        self.inline_list_name_entry = view.inline_list_name_entry
        self.save_rush_list_btn = view.save_rush_list_btn
        self.manage_rush_lists_btn = view.manage_rush_lists_btn
        self.task_priority_help_label = view.task_priority_help_label

    def refresh_current_list_status(
        self: "CourseSelectionApp", profile_id: str | None = None
    ) -> None:
        """刷新当前或指定用户命名列表的名称、备注摘要和保存状态。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 目标用户 UUID；为 None 时使用当前用户。

        Returns:
            None: 状态条变量和样式会同步更新。
        """
        target_id = profile_id if profile_id is not None else self.current_profile_id()
        rush_views = getattr(self, "rush_views", {})
        if (
            hasattr(self, "runtime")
            and target_id is not None
            and target_id in rush_views
        ):
            try:
                view_context = self.runtime.require_context(target_id)
            except UserProfileStoreError:
                return
            self._refresh_view_list_status(rush_views[target_id], view_context)
            return
        if self.current_saved_list_id is None:
            display_name = "默认列表"
            note_summary = "自动保存当前待抢课程"
            dirty_text = "自动保存"
            dirty_style = "Clean.TLabel"
        else:
            display_name = self.current_saved_list_name
            normalized_note = " ".join(self.current_saved_list_note.split())
            note_summary = normalized_note or "暂无备注"
            if len(note_summary) > 70:
                note_summary = f"{note_summary[:67]}..."
            if self.current_saved_list_dirty:
                dirty_text = "有未保存更改"
                dirty_style = "Dirty.TLabel"
            else:
                dirty_text = "已保存"
                dirty_style = "Clean.TLabel"
        self.current_list_name_var.set(display_name)
        self.current_list_note_var.set(note_summary)
        self.current_list_dirty_var.set(dirty_text)
        if hasattr(self, "current_list_dirty_label"):
            self.current_list_dirty_label.config(style=dirty_style)

    def update_course_list(self: "CourseSelectionApp") -> None:
        """
        排序并刷新当前人员的待抢课程表与空状态。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 当前用户的页签课程表会同步更新。
        """
        if hasattr(self, "runtime"):
            profile_id = self.current_profile_id()
            if (
                profile_id is not None
                and profile_id in getattr(self, "rush_views", {})
            ):
                self.refresh_rush_view(profile_id)
                return
        context = self.active_user_context()
        courses = context.courses if context is not None else app_legacy.course_data_list
        try:
            for item in self.course_tree.get_children():
                self.course_tree.delete(item)
        except Exception as e:
            print(f"清空课程列表时出错：{e}")

        # ✅ 1. 稳定排序：优先级为主，原 id 为辅
        sorted_courses = sorted(courses, key=lambda x: (x["priority"], x["id"]))

        # ✅ 2. 重置 id 为连续序号（1, 2, 3, ...）
        for new_id, course in enumerate(sorted_courses, start=1):
            course["id"] = new_id

        # ✅ 3. 更新全局计数器（用于后续添加课程的初始 id）
        app_legacy.course_id_count = len(sorted_courses)
        if context is not None:
            context.courses[:] = sorted_courses
            courses = context.courses
        else:
            app_legacy.course_data_list = sorted_courses
            courses = app_legacy.course_data_list

        # ✅ 4. 刷新表格
        for course in courses:
            attempt_status = "等待抢课"
            last_response = "尚未开始"
            raw_data = course.get("data")
            task_id = (
                str(raw_data.get("p_id", ""))
                if isinstance(raw_data, dict)
                else ""
            )
            if context is not None and task_id:
                attempt = self.runtime.course_attempt(context.profile.id, task_id)
                if attempt is not None:
                    attempt_status = attempt.status
                    last_response = attempt.message
                    if attempt.attempt_count:
                        last_response = (
                            f"第 {attempt.attempt_count} 次 | {last_response}"
                        )
            self.course_tree.insert("", "end", values=(
                course["id"],
                course["priority"],
                course["name"],
                course["teacher"],
                course["course_id"],
                format_schedule_text(course["schedule"]),
                attempt_status,
                last_response,
            ))

        if hasattr(self, "task_count_var"):
            self.task_count_var.set(f"共 {len(courses)} 门课程")
        if hasattr(self, "task_empty_label"):
            if courses:
                self.task_empty_label.place_forget()
                self.task_empty_add_btn.place_forget()
            else:
                self.task_empty_label.place(relx=0.5, rely=0.42, anchor="center")
                self.task_empty_add_btn.place(relx=0.5, rely=0.60, anchor="center")

    def record_course_attempt(
        self: "CourseSelectionApp",
        profile_id: str,
        task_id: str,
        status: str,
        message: str,
        attempt_count: int,
    ) -> None:
        """记录单门课程的抢课状态并安排可见任务表刷新。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 任务所属用户 UUID。
            task_id: 课程任务 ID。
            status: 当前业务状态或请求状态。
            message: 接口返回或异常摘要。
            attempt_count: 本轮任务中该课程已经尝试的次数。

        Returns:
            None: 状态写入目标用户上下文，当前用户表格会在主线程刷新。
        """
        self.runtime.update_course_attempt(
            profile_id,
            task_id,
            status,
            message,
            attempt_count,
        )
        self.root.after(
            0,
            lambda: self.refresh_course_attempt_table(profile_id),
        )

    def refresh_course_attempt_table(
        self: "CourseSelectionApp", profile_id: str
    ) -> None:
        """在抢课状态变化后刷新对应用户的待抢表。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 状态发生变化的用户 UUID。

        Returns:
            None: 该用户抢课页签会在主线程直接刷新。
        """
        if hasattr(self, "runtime") and profile_id in getattr(self, "rush_views", {}):
            self.refresh_rush_view(profile_id)
            return
        if (
            hasattr(self, "course_tree")
            and self.current_profile_id() == profile_id
        ):
            self.update_course_list()

    def remove_course(self: "CourseSelectionApp") -> None:
        """删除当前用户选中的待抢课程并保存草稿。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 用户取消或未选中课程时不修改草稿。
        """
        context = self.active_user_context()
        courses = context.courses if context is not None else app_legacy.course_data_list
        selected_items = self.course_tree.selection()
        if not selected_items:
            messagebox.showwarning("警告", "请先选择要删除的课程")
            return

        deleted_names = []
        ids_to_remove = []
        for item in selected_items:
            values = self.course_tree.item(item, "values")
            course_id = int(values[0])  # 第一列是 id
            course_name = values[2]     # 第三列是课程名称
            ids_to_remove.append(course_id)
            deleted_names.append(course_name)

        # 弹出确认框
        if len(deleted_names) > 1:
            confirm = messagebox.askyesno("确认删除", f"确定要删除以下 {len(deleted_names)} 门课程吗？\n" + "\n".join(deleted_names))
        else:
            confirm = messagebox.askyesno("确认删除", f"确定要删除课程：{deleted_names[0]} 吗？")

        if not confirm:
            return

        # 从 app_legacy.course_data_list 中移除对应课程
        remaining_courses = [c for c in courses if c["id"] not in ids_to_remove]

        # 重置所有课程的ID为连续序号
        for i, course in enumerate(remaining_courses):
            course["id"] = i + 1

        # 更新全局计数器
        app_legacy.course_id_count = len(remaining_courses)
        if context is not None:
            context.courses[:] = remaining_courses
        else:
            app_legacy.course_data_list = remaining_courses

        # 【核心修改】删除操作后立即保存当前人员的课程列表
        self.mark_current_list_dirty()
        self.save_course_list()

        # 刷新表格
        self.update_course_list()

        # 提示删除成功
        if len(deleted_names) > 1:
            messagebox.showinfo("成功", f"已删除 {len(deleted_names)} 门课程")
        else:
            messagebox.showinfo("成功", f"已删除课程：{deleted_names[0]}")

    def start_auto_selection(self: "CourseSelectionApp") -> None:
        """
        校验当前任务并启动持续轮询抢课线程。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 校验通过后启动后台轮询并锁定任务设置。
        """
        context = self.active_user_context()
        if context is not None:
            courses = self.runtime.course_snapshot(context.profile.id)
            cookies = dict(context.cookies)
            if not courses:
                messagebox.showerror("错误", "尚未添加任何课程")
                return
            if context.login_state is not UserLoginState.LOGGED_IN or not cookies:
                messagebox.showerror("错误", "请先登录当前用户")
                return
            if self.is_context_task_active(context):
                messagebox.showwarning("警告", "当前用户已有抢课任务正在运行")
                return
            courses.sort(key=lambda item: int(item["priority"]))
            message = (
                f"即将为“{context.profile.alias}”启动轮询，共 {len(courses)} 门课程：\n\n"
                + "\n".join(
                    f"{index}. [优先级 {course['priority']}] {course['name']} ({course['teacher']})"
                    for index, course in enumerate(courses, start=1)
                )
                + "\n\n是否继续？"
            )
            if not messagebox.askyesno("确认", message):
                return
            self.sync_task_settings_to_context()
            try:
                self.runtime.mark_task_started(context.profile.id, waiting=False)
            except RuntimeError as error:
                messagebox.showwarning("警告", str(error))
                return
            task_ids = [
                str(course["data"].get("p_id", ""))
                for course in courses
                if isinstance(course.get("data"), dict)
            ]
            self.runtime.reset_course_attempts(context.profile.id, task_ids)
            self.update_course_list()
            worker = threading.Thread(
                target=self.auto_selection_process,
                args=(
                    context.profile.id,
                    cookies,
                    courses,
                    context.stop_event,
                    context.stop_on_success,
                    context.retry_full,
                ),
                daemon=True,
            )
            context.task_thread = worker
            self.user_log(context.profile.id, "轮询抢课任务已启动")
            worker.start()
            self.apply_active_user_control_state()
            self.refresh_user_views()
            self.show_rush_tab()
            return

        if not app_legacy.course_data_list:
            messagebox.showerror("错误", "尚未添加任何课程")
            return

        if not app_legacy.final_cookies_dict:
            messagebox.showerror("错误", "请先登录")
            return

        # 检查是否选择了人员（虽然add时检查过，但防止清空）
        if not self.current_student_name:
            messagebox.showerror("错误", "请确认抢课人员姓名")
            return

        app_legacy.course_data_list.sort(key=lambda x: x["priority"])

        msg = f"即将开始为【{self.current_student_name}】自动选课，课程如下：\n\n"
        for i, course in enumerate(app_legacy.course_data_list):
            msg += f"{i+1}. [优先级 {course['priority']}] {course['name']} ({course['teacher']})\n"
        msg += "\n是否继续？"

        if not messagebox.askyesno("确认", msg):
            return

        # === 设置状态 ===
        app_legacy.selection_running = True
        app_legacy.stop_selection = False

        # === 禁用无关按钮 ===
        self.add_course_btn.config(state=tk.DISABLED)
        self.add_selected_course_btn.config(state=tk.DISABLED)
        self.start_auto_btn.config(state=tk.DISABLED)
        self.remove_course_btn.config(state=tk.DISABLED)
        self.student_name_var.set(self.current_student_name) # 锁定输入框显示
        self.stop_auto_btn.config(state=tk.NORMAL)

        self._set_rush_controls_enabled(False)

        # 锁定人员切换
        self.student_switch_lock = True

        selection_thread = threading.Thread(target=self.auto_selection_process, daemon=True)
        selection_thread.start()
        self.show_rush_tab()

    def restore_buttons(self: "CourseSelectionApp") -> None:
        """
        恢复查询、任务操作和抢课设置控件状态。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 所有相关控件恢复为可操作状态。
        """
        self.add_course_btn.config(state=tk.NORMAL)
        self.add_selected_course_btn.config(state=tk.NORMAL)
        self.start_auto_btn.config(state=tk.NORMAL)
        self.remove_course_btn.config(state=tk.NORMAL)
        self.stop_auto_btn.config(state=tk.DISABLED)

        self._set_rush_controls_enabled(True)
        self.on_mode_change()

        self.student_switch_lock = False # 解锁人员切换
        self.status_var.set("抢课结束，按钮已恢复")

    def _set_rush_controls_enabled(
        self: "CourseSelectionApp",
        enabled: bool,
        view: UserRushPage | None = None,
    ) -> None:
        """
        统一设置抢课人员、模式和策略控件的可用状态。

        Args:
            self: 当前课程助手应用实例。
            enabled: True 表示允许编辑，False 表示锁定设置。
            view: 目标用户抢课页；为 None 时使用当前兼容代理控件。

        Returns:
            None: 控件状态直接写入 Tkinter 组件。
        """
        target_view = view
        if target_view is None:
            target_view = getattr(self, "current_rush_view", None)
        if target_view is not None:
            state = tk.NORMAL if enabled else tk.DISABLED
            target_view.stop_on_success_check.config(state=state)
            target_view.retry_full_check.config(state=state)
            target_view.save_rush_list_btn.config(state=state)
            target_view.manage_rush_lists_btn.config(state=state)
            target_view.edit_current_list_name_btn.config(state=state)
            target_view.inline_list_name_entry.config(state=state)
            target_view.task_add_course_btn.config(state=state)
            target_view.empty_add_btn.config(state=state)
            for mode_button in target_view.rush_mode_buttons:
                mode_button.config(state=state)
            if not enabled and hasattr(self, "rush_list_manager_window"):
                try:
                    self.rush_list_manager_window.destroy()
                except tk.TclError:
                    pass
            if enabled and target_view.rush_mode_var.get() == "定时抢课":
                target_view.rush_time_entry.config(state=tk.NORMAL)
            else:
                target_view.rush_time_entry.config(state=tk.DISABLED)
            return
        state = tk.NORMAL if enabled else tk.DISABLED
        self.stop_on_success_check.config(state=state)
        self.retry_full_check.config(state=state)
        self.save_rush_list_btn.config(state=state)
        self.manage_rush_lists_btn.config(state=state)
        if hasattr(self, "edit_current_list_name_btn"):
            self.edit_current_list_name_btn.config(state=state)
        if hasattr(self, "inline_list_name_entry"):
            self.inline_list_name_entry.config(state=state)
        self.task_add_course_btn.config(state=state)
        self.task_empty_add_btn.config(state=state)
        for mode_button in self.rush_mode_buttons:
            mode_button.config(state=state)
        if not enabled and hasattr(self, "rush_list_manager_window"):
            try:
                self.rush_list_manager_window.destroy()
            except tk.TclError:
                pass
        if enabled and self.rush_mode_var.get() == "定时抢课":
            self.rush_time_entry.config(state=tk.NORMAL)
        else:
            self.rush_time_entry.config(state=tk.DISABLED)

    def on_mode_change(
        self: "CourseSelectionApp", event: object | None = None
    ) -> None:
        """
        根据当前用户抢课模式切换抢课时间输入框的可见性。

        Args:
            self: 当前课程助手应用实例。
            event: Tkinter 模式切换事件；直接调用时为 None。

        Returns:
            None: 直接更新当前用户页签控件状态。
        """
        view = getattr(self, "current_rush_view", None)
        if view is not None:
            self._on_view_mode_change(view)
            return
        mode = self.rush_mode_var.get()

        if mode == "定时抢课":
            self.rush_time_frame.grid(
                row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
            )
            self.rush_time_entry.config(state=tk.NORMAL)
            self.start_auto_btn.config(text="定时抢课")
        else:
            self.rush_time_frame.grid_remove()
            self.start_auto_btn.config(text="开始轮询")

    def start_selection(self: "CourseSelectionApp") -> None:
        """
        根据当前模式立即开始轮询或等待后开始轮询。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 在校验成功后启动相应的后台线程。
        """
        mode = self.rush_mode_var.get()

        if mode == "定时抢课":
            # 调用定时抢课模式
            rush_time = self.rush_time_var.get().strip()
            if not rush_time:
                messagebox.showerror("错误", "请输入抢课时间")
                return
            self.start_timed_rush_mode(rush_time)
        else:
            # 调用原来的轮询模式
            self.start_auto_selection()

    def start_timed_rush_mode(
        self: "CourseSelectionApp", rush_time_str: str
    ) -> None:
        """
        启动定时等待线程，并在目标时刻转入轮询抢课。

        Args:
            self: 当前课程助手应用实例。
            rush_time_str: 抢课时间字符串，格式为 `HH:MM:SS`。

        Returns:
            None: 校验成功后启动后台等待线程。
        """
        context = self.active_user_context()
        if context is not None:
            courses = self.runtime.course_snapshot(context.profile.id)
            cookies = dict(context.cookies)
            if self.is_context_task_active(context):
                messagebox.showwarning("警告", "当前用户已有抢课任务正在运行")
                return
            if context.login_state is not UserLoginState.LOGGED_IN or not cookies:
                messagebox.showerror("错误", "请先登录当前用户")
                return
            if not courses:
                messagebox.showerror("错误", "请先添加课程")
                return
            try:
                rush_hour, rush_minute, rush_second = map(int, rush_time_str.split(":"))
                target_time = clock_time(rush_hour, rush_minute, rush_second)
            except (TypeError, ValueError) as error:
                messagebox.showerror(
                    "错误", f"时间格式错误：{error}\n请使用 HH:MM:SS 格式"
                )
                return
            self.sync_task_settings_to_context()
            try:
                self.runtime.mark_task_started(context.profile.id, waiting=True)
            except RuntimeError as error:
                messagebox.showwarning("警告", str(error))
                return
            task_ids = [
                str(course["data"].get("p_id", ""))
                for course in courses
                if isinstance(course.get("data"), dict)
            ]
            self.runtime.reset_course_attempts(
                context.profile.id,
                task_ids,
                status="等待定时",
                message=f"计划在 {target_time:%H:%M:%S} 开始",
            )
            self.update_course_list()
            worker = threading.Thread(
                target=self._timed_polling_worker,
                args=(
                    context.profile.id,
                    target_time,
                    cookies,
                    courses,
                    context.stop_event,
                    context.stop_on_success,
                    context.retry_full,
                ),
                daemon=True,
            )
            context.task_thread = worker
            self.user_log(
                context.profile.id, f"定时任务已创建，目标时刻 {target_time:%H:%M:%S}"
            )
            worker.start()
            self.apply_active_user_control_state()
            self.refresh_user_views()
            self.show_rush_tab()
            return

        if app_legacy.selection_running:
            messagebox.showwarning("警告", "已有抢课任务正在运行")
            return

        if not app_legacy.final_cookies_dict:
            messagebox.showerror("错误", "请先登录")
            return

        if not app_legacy.course_data_list:
            messagebox.showerror("错误", "请先添加课程")
            return

        if not self.current_student_name:
            messagebox.showerror("错误", "请确认抢课人员姓名")
            return

        # 解析抢课时间
        try:
            rush_hour, rush_minute, rush_second = map(int, rush_time_str.split(':'))
            target_time = clock_time(rush_hour, rush_minute, rush_second)
        except Exception as e:
            messagebox.showerror("错误", f"时间格式错误: {e}\n请使用 HH:MM:SS 格式")
            return

        app_legacy.selection_running = True
        app_legacy.stop_selection = False

        # 禁用相关按钮
        self.add_course_btn.config(state=tk.DISABLED)
        self.add_selected_course_btn.config(state=tk.DISABLED)
        self.start_auto_btn.config(state=tk.DISABLED)
        self.remove_course_btn.config(state=tk.DISABLED)
        self.stop_auto_btn.config(state=tk.NORMAL)
        self._set_rush_controls_enabled(False)
        self.student_switch_lock = True
        self.show_rush_tab()

        rush_thread = threading.Thread(
            target=self._timed_polling_worker,
            args=(target_time,),
            daemon=True
        )
        rush_thread.start()

    def _timed_polling_worker(
        self: "CourseSelectionApp",
        profile_id: str | clock_time,
        target_time: clock_time | None = None,
        cookies: dict[str, str] | None = None,
        courses: list[dict[str, object]] | None = None,
        stop_event: threading.Event | None = None,
        stop_on_success: bool = True,
        retry_full: bool = True,
    ) -> None:
        """
        等待到目标时刻后复用轮询抢课流程。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 用户 UUID；旧测试调用时可传入目标时刻。
            target_time: 用户设定的每日开始轮询时刻。
            cookies: 任务启动时捕获的 Cookie 副本。
            courses: 任务启动时深拷贝的课程列表。
            stop_event: 用户独立停止事件。
            stop_on_success: 选到一门课程后是否停止。
            retry_full: 课程已满时是否继续重试。

        Returns:
            None: 等待取消时恢复界面；开始轮询后由轮询流程负责收尾。
        """
        if isinstance(profile_id, clock_time):
            legacy_target = profile_id
            profile_id = ""
            target_time = legacy_target
        if profile_id:
            if target_time is None or cookies is None or courses is None or stop_event is None:
                raise ValueError("多用户定时任务缺少运行参数")
            try:
                now = datetime.now()
                scheduled_start = get_scheduled_start(now, target_time)
                if scheduled_start > now:
                    self.user_log(
                        profile_id,
                        f"等待定时开始：{scheduled_start:%H:%M:%S}",
                    )
                    while not stop_event.is_set():
                        remaining_seconds = (
                            scheduled_start - datetime.now()
                        ).total_seconds()
                        if remaining_seconds <= 0:
                            break
                        stop_event.wait(min(1.0, remaining_seconds))
                if stop_event.is_set():
                    self.runtime.mark_task_finished(profile_id, False, "用户已停止定时任务")
                    return
                self.runtime.mark_polling(profile_id)
                self.user_log(profile_id, "定时时间已到，开始轮询抢课")
                self.root.after(0, self.refresh_user_views)
                self.auto_selection_process(
                    profile_id,
                    cookies,
                    courses,
                    stop_event,
                    stop_on_success,
                    retry_full,
                )
            except Exception as error:
                self.user_log(profile_id, f"定时抢课出错：{error}")
                self.runtime.mark_task_finished(profile_id, False, f"定时抢课失败：{error}")
                self.root.after(0, self.refresh_user_views)
            finally:
                self.root.after(0, lambda: self.finish_user_task_ui(profile_id))
            return
        if target_time is None:
            raise ValueError("缺少定时抢课目标时刻")
        polling_process_started = False
        try:
            now = datetime.now()
            scheduled_start = get_scheduled_start(now, target_time)
            if scheduled_start > now:
                print(f"⏰ 定时抢课已启动，轮询将在 {scheduled_start:%H:%M:%S} 开始")
                while not app_legacy.stop_selection:
                    remaining_seconds = (scheduled_start - datetime.now()).total_seconds()
                    if remaining_seconds <= 0:
                        break
                    hours, remainder = divmod(int(remaining_seconds), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    countdown_text = f"距离开始轮询还有：{hours:02d}:{minutes:02d}:{seconds:02d}"
                    self.root.after(
                        0, lambda text=countdown_text: self.status_var.set(text)
                    )
                    time.sleep(min(1.0, remaining_seconds))

            if app_legacy.stop_selection:
                print("🛑 用户取消定时抢课")
                return

            print("🚀 定时时间已到，开始轮询抢课")
            self.root.after(0, lambda: self.status_var.set("正在轮询抢课..."))
            polling_process_started = True
            self.auto_selection_process()
        except Exception as error:
            error_message = f"定时抢课出错：{error}"
            print(f"❌ {error_message}")
            self.root.after(0, lambda: messagebox.showerror("错误", error_message))
        finally:
            if not polling_process_started:
                self.root.after(0, self.restore_buttons)
                app_legacy.selection_running = False
                app_legacy.stop_selection = False

    def auto_selection_process(
        self: "CourseSelectionApp",
        profile_id: str | None = None,
        cookies: dict[str, str] | None = None,
        courses: list[dict[str, object]] | None = None,
        stop_event: threading.Event | None = None,
        stop_on_success: bool | None = None,
        retry_full: bool | None = None,
    ) -> None:
        """使用任务启动时冻结的用户会话与课程执行轮询。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 任务所属用户 UUID；为 None 时保留旧测试兼容行为。
            cookies: 任务启动时捕获的 Cookie 副本。
            courses: 任务启动时深拷贝的课程列表。
            stop_event: 该用户独立的停止事件。
            stop_on_success: 选到课程后是否停止。
            retry_full: 课程已满时是否持续重试。

        Returns:
            None: 任务状态和日志写回对应用户上下文。

        Raises:
            ValueError: 多用户任务缺少必要运行参数时抛出。
        """
        if profile_id is not None:
            if cookies is None or courses is None or stop_event is None:
                raise ValueError("多用户抢课任务缺少运行参数")
            self._run_user_selection(
                profile_id,
                cookies,
                courses,
                stop_event,
                bool(stop_on_success),
                bool(retry_full),
            )
            return

        print(f"\n🚀 开始为 {self.current_student_name} 自动选课...")
        self.status_var.set("自动选课已启动")

        session = requests.Session()
        session.cookies.update(app_legacy.final_cookies_dict)
        session.headers.update(_RUSH_HEADERS)

        url = "https://byyt.ustb.edu.cn/Xsxk/addGouwuche"
        count = 0
        success = False

        try:
            # 按优先级分组
            priority_groups = defaultdict(list)
            for course in sorted(app_legacy.course_data_list, key=lambda x: x["priority"]):
                priority_groups[course["priority"]].append(course)

            # 记录已因“冲突”或“已满+不重试”而放弃的课程 ID
            failed_course_ids = set()

            # 按优先级从高到低处理
            for priority in sorted(priority_groups.keys()):
                courses = priority_groups[priority]
                print(f"🎯 开始抢优先级 {priority} 的课程，共 {len(courses)} 门：")
                for c in courses:
                    print(f"   → {c['name']} ({c['teacher']})")

                if app_legacy.stop_selection:
                    break

                while not success and not app_legacy.stop_selection:
                    current_time = datetime.now()
                    minute = current_time.minute
                    if minute == 59:
                        time.sleep(max(58.5-current_time.second,0))
                        continue

                    any_active_in_priority = False  # 当前优先级是否有可抢的课

                    for course in courses:
                        course_id = course["data"]["p_id"]

                        # 如果这门课已经失败过（冲突或已满且不重试），跳过
                        if course_id in failed_course_ids:
                            continue

                        time.sleep(3.5)
                        if app_legacy.stop_selection:
                            break

                        try:
                            response = session.post(url, data=course["data"])
                            count += 1
                            text = response.text.strip()

                            print(f"[{count}] 优先级 {priority} | 课程：{course['name']} | 状态：{response.status_code} | 响应：{text[:160]}...")

                            if "success" in text or "成功" in text:
                                success_msg = f"🎉 选课成功！课程：{course['name']} | 教师：{course['teacher']}"
                                print(success_msg)
                                if self.stop_on_success_var.get():
                                    success = True
                                    self.root.after(0, lambda msg=success_msg: messagebox.showinfo("成功", msg))
                                    self.root.after(0, lambda: self.status_var.set("选课成功！"))
                                    break
                                else:
                                    print("⏩ 选课成功，但将继续尝试其他课程...")
                                    failed_course_ids.add(course_id)
                                    continue

                            elif "冲突" in text:
                                print(f"⛔ 时间冲突，放弃课程：{course['name']}（不再尝试）")
                                failed_course_ids.add(course_id)
                                continue

                            elif "不符合" in text:
                                print(f"⛔ 不符合要求，放弃课程：{course['name']}（不再尝试）")
                                failed_course_ids.add(course_id)
                                continue

                            elif "full" in text or "已满" in text:
                                retry_enabled = self.retry_full_var.get()
                                if not retry_enabled:
                                    print(f"🚫 课程已满且“不重试”，放弃课程：{course['name']}（不再尝试）")
                                    failed_course_ids.add(course_id)
                                else:
                                    print(f"⏸️ 课程已满：{course['name']}，等待下次重试...")
                                    any_active_in_priority = True
                                continue

                            elif "不在设置的时间范围内" in text or "未到选课时间" in text or "尚未开放" in text or "not in the time range" in text.lower():
                                # ⏳ 尚未到选课时间，继续重试（不放弃，保持活跃）
                                print(f"⏳ 选课时间未到：{course['name']}，持续等待中...")
                                any_active_in_priority = True
                                continue

                            else:
                                print(f"⚠️ 未知响应（可能可抢）：{text[:100]}...")
                                any_active_in_priority = True

                        except Exception as e:
                            print(f"[{count}] 请求失败（{course['name']}）：{e}")
                            any_active_in_priority = True

                    # 检查是否当前优先级还有可尝试的课程
                    remaining_courses = [c for c in courses if c["data"]["p_id"] not in failed_course_ids]
                    if not remaining_courses:
                        print(f"⏸️ 优先级 {priority} 所有课程均已失败或放弃，进入下一优先级...")
                        break

                    if not any_active_in_priority and not success:
                        print(f"⏸️ 优先级 {priority} 无活跃课程可抢，进入下一优先级...")
                        break

                if success or app_legacy.stop_selection:
                    break

            if not success:
                print("🔚 所有课程均已满或失败，抢课结束。")
                self.root.after(0, lambda: self.status_var.set("所有课程均已满或失败，抢课结束"))

        except Exception as e:
            error_msg = f"自动选课出错：{e}"
            print(f"❌ {error_msg}")
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
            self.root.after(0, lambda: self.status_var.set("选课失败"))

        finally:
            self.root.after(0, self.restore_buttons)
            app_legacy.selection_running = False
            app_legacy.stop_selection = False

    def _run_user_selection(
        self: "CourseSelectionApp",
        profile_id: str,
        cookies: dict[str, str],
        courses: list[dict[str, object]],
        stop_event: threading.Event,
        stop_on_success: bool,
        retry_full: bool,
    ) -> None:
        """执行与界面当前用户无关的单用户轮询循环。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 任务所属用户 UUID。
            cookies: 启动时捕获的 Cookie 副本。
            courses: 启动时深拷贝的课程列表。
            stop_event: 用户独立停止事件。
            stop_on_success: 选到课程后是否停止。
            retry_full: 课程已满时是否持续重试。

        Returns:
            None: 任务结果写入用户运行时上下文。
        """
        session = requests.Session()
        session.cookies.update(cookies)
        session.headers.update({
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://byyt.ustb.edu.cn",
            "Referer": "https://byyt.ustb.edu.cn/Xsxk/query/1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
        })
        priority_groups: dict[int, list[dict[str, object]]] = defaultdict(list)
        for course in sorted(courses, key=lambda item: int(item["priority"])):
            priority_groups[int(course["priority"])].append(course)
        task_ids = [
            str(course["data"].get("p_id", ""))
            for course in courses
            if isinstance(course.get("data"), dict)
        ]
        self.runtime.reset_course_attempts(profile_id, task_ids)
        self.root.after(0, lambda: self.refresh_course_attempt_table(profile_id))
        failed_course_ids: set[str] = set()
        attempt_counts: dict[str, int] = defaultdict(int)
        any_success = False
        stop_after_success = False
        session_expired = False
        environment_error = ""
        request_count = 0
        try:
            for priority in sorted(priority_groups):
                if environment_error:
                    break
                priority_courses = priority_groups[priority]
                self.user_log(
                    profile_id,
                    f"开始抢优先级 {priority} 的课程，共 {len(priority_courses)} 门",
                )
                while (
                    not stop_event.is_set()
                    and not stop_after_success
                    and not environment_error
                ):
                    current_time = datetime.now()
                    if current_time.minute == 59:
                        stop_event.wait(max(58.5 - current_time.second, 0))
                        continue
                    has_retryable_course = False
                    for course in priority_courses:
                        raw_data = course.get("data")
                        if not isinstance(raw_data, dict):
                            continue
                        course_task_id = str(raw_data.get("p_id", ""))
                        if course_task_id in failed_course_ids:
                            continue
                        if stop_event.wait(3.5):
                            break
                        request_count += 1
                        attempt_counts[course_task_id] += 1
                        attempt_number = attempt_counts[course_task_id]
                        self.record_course_attempt(
                            profile_id,
                            course_task_id,
                            "正在请求",
                            "等待接口返回",
                            attempt_number,
                        )
                        try:
                            response = session.post(
                                "https://byyt.ustb.edu.cn/Xsxk/addGouwuche",
                                data=raw_data,
                                timeout=10,
                            )
                            response_text = response.text.strip()
                            response_summary = (
                                f"HTTP {response.status_code} | "
                                f"{response_text or '空响应'}"
                            )
                            self.user_log(
                                profile_id,
                                f"[{request_count}] {course['name']} | HTTP {response.status_code} | {response_text[:160]}",
                            )
                        except requests.RequestException as error:
                            self.record_course_attempt(
                                profile_id,
                                course_task_id,
                                "请求失败",
                                str(error),
                                attempt_number,
                            )
                            self.user_log(
                                profile_id,
                                f"[{request_count}] 请求失败（{course['name']}）：{error}",
                            )
                            if is_environment_request_error(error):
                                environment_error = str(error)
                                break
                            has_retryable_course = True
                            continue
                        if response.status_code in {401, 403}:
                            self.record_course_attempt(
                                profile_id,
                                course_task_id,
                                "请求失败",
                                response_summary,
                                attempt_number,
                            )
                            session_expired = True
                            stop_event.set()
                            break
                        business_status = classify_selection_response(response_text)
                        self.record_course_attempt(
                            profile_id,
                            course_task_id,
                            business_status,
                            response_summary,
                            attempt_number,
                        )
                        if business_status == "选课成功":
                            any_success = True
                            failed_course_ids.add(course_task_id)
                            success_message = (
                                f"选课成功：{course['name']} | 教师：{course['teacher']}"
                            )
                            self.user_log(profile_id, success_message)
                            if stop_on_success:
                                stop_after_success = True
                                self.root.after(
                                    0,
                                    lambda message=success_message: messagebox.showinfo(
                                        "成功", message
                                    ),
                                )
                                break
                        elif business_status == "不符合选课要求":
                            failed_course_ids.add(course_task_id)
                        elif business_status == "课程容量已满":
                            if retry_full:
                                has_retryable_course = True
                            else:
                                failed_course_ids.add(course_task_id)
                        elif business_status == "不在设定的选课时间范围内":
                            has_retryable_course = True
                        else:
                            has_retryable_course = True
                    remaining_courses = [
                        course
                        for course in priority_courses
                        if isinstance(course.get("data"), dict)
                        and str(course["data"].get("p_id", ""))
                        not in failed_course_ids
                    ]
                    if not remaining_courses or not has_retryable_course:
                        break
                if stop_event.is_set() or stop_after_success:
                    break
            if session_expired:
                self.root.after(0, lambda: self.handle_session_expired(profile_id))
            elif environment_error:
                self.runtime.mark_task_finished(
                    profile_id, False, "网络环境异常，任务已停止"
                )
                self.user_log(
                    profile_id,
                    "检测到网络/代理环境异常，已停止任务。请检查网络或关闭代理后重试",
                )
                self.root.after(
                    0,
                    lambda message=environment_error: self.handle_environment_error(
                        profile_id, message
                    ),
                )
            else:
                result = (
                    "抢课成功"
                    if any_success
                    else "用户已停止"
                    if stop_event.is_set()
                    else "课程均已失败或放弃"
                )
                self.runtime.mark_task_finished(profile_id, any_success, result)
                self.user_log(profile_id, result)
        except Exception as error:
            self.runtime.mark_task_finished(profile_id, False, f"抢课失败：{error}")
            self.user_log(profile_id, f"自动选课出错：{error}")
        finally:
            self.root.after(0, lambda: self.finish_user_task_ui(profile_id))

    def finish_user_task_ui(self: "CourseSelectionApp", profile_id: str) -> None:
        """刷新指定用户任务结束后的可见状态。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 已结束任务的用户 UUID。

        Returns:
            None: 非当前用户只刷新登录页用户表。
        """
        self.refresh_user_views()
        if self.current_profile_id() == profile_id:
            self.apply_active_user_control_state()
            self.status_var.set(self.runtime.require_context(profile_id).last_result)

    def stop_auto_selection(self: "CourseSelectionApp") -> None:
        """仅请求停止当前用户的抢课任务。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 当前用户无活动任务时不执行操作。
        """
        context = self.active_user_context()
        if context is not None:
            if not self.is_context_task_active(context):
                return
            self.runtime.request_stop(context.profile.id)
            self.status_var.set("正在停止当前用户的抢课任务...")
            self.user_log(context.profile.id, "用户请求停止抢课")
            self.apply_active_user_control_state()
            self.refresh_user_views()
            return
        if not app_legacy.selection_running:
            return

        app_legacy.stop_selection = True
        self.status_var.set("正在停止抢课...")
        print("🛑 用户请求停止抢课")

    def start_online_keepalive(self: "CourseSelectionApp") -> None:
        """启动服务于全部已登录用户的单一保活线程。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 已有保活线程运行时不会重复创建。
        """
        existing_thread = getattr(self, "online_thread", None)
        if existing_thread is not None and existing_thread.is_alive():
            return
        self.keepalive_stop_event = threading.Event()
        self.online_thread = threading.Thread(
            target=self.online_keepalive_thread, daemon=True
        )
        self.online_thread.start()

    def stop_online_keepalive(self: "CourseSelectionApp") -> None:
        """停止多用户保活线程。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 最多等待两秒让后台线程退出。
        """
        stop_event = getattr(self, "keepalive_stop_event", None)
        if stop_event is not None:
            stop_event.set()
        online_thread = getattr(self, "online_thread", None)
        if online_thread is not None and online_thread.is_alive():
            online_thread.join(timeout=2.0)

    def online_keepalive_thread(self: "CourseSelectionApp") -> None:
        """按用户分别执行业务保活和底层在线心跳。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 关闭事件设置后线程退出。
        """
        last_business_time = time.time()
        last_online_time = time.time()
        while not self.keepalive_stop_event.wait(_KEEPALIVE_TICK_SECONDS):
            now = time.time()
            logged_in_ids = [
                context.profile.id
                for context in self.runtime.enabled_contexts()
                if context.login_state is UserLoginState.LOGGED_IN and context.cookies
            ]
            if now - last_business_time >= _BUSINESS_KEEPALIVE_INTERVAL_SECONDS:
                for profile_id in logged_in_ids:
                    self.send_business_keepalive(profile_id)
                last_business_time = now
            if now - last_online_time >= _ONLINE_KEEPALIVE_INTERVAL_SECONDS:
                for profile_id in logged_in_ids:
                    self.send_online_request(profile_id)
                last_online_time = now

    def send_online_request(self: "CourseSelectionApp", profile_id: str) -> None:
        """使用指定用户 Cookie 发送底层在线心跳。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 需要保活的用户 UUID。

        Returns:
            None: 失败信息写入该用户日志。
        """
        context = self.runtime.require_context(profile_id)
        if context.login_state is not UserLoginState.LOGGED_IN or not context.cookies:
            return
        try:
            session = requests.Session()
            session.cookies.update(dict(context.cookies))
            response = session.post(
                "https://byyt.ustb.edu.cn/component/online",
                headers={
                    "Accept": "*/*",
                    "Origin": "https://byyt.ustb.edu.cn",
                    "Referer": "https://byyt.ustb.edu.cn/authentication/main",
                },
                timeout=10,
            )
            if response.status_code in {401, 403}:
                self.root.after(0, lambda: self.handle_session_expired(profile_id))
            elif response.status_code != 200:
                self.user_log(profile_id, f"在线心跳失败：HTTP {response.status_code}")
        except requests.RequestException as error:
            self.user_log(profile_id, f"在线心跳异常：{error}")

    def handle_session_expired(
        self: "CourseSelectionApp", profile_id: str | None = None
    ) -> None:
        """仅清理指定用户的失效会话并停止其任务。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 会话失效的用户 UUID；None 用于旧测试兼容路径。

        Returns:
            None: 其他用户的 Cookie、任务和工作区保持不变。
        """
        if profile_id is not None and hasattr(self, "runtime"):
            context = self.runtime.require_context(profile_id)
            self.runtime.expire_session(profile_id)
            self.user_log(profile_id, "检测到登录会话已失效，已停止该用户任务")
            if self.current_profile_id() == profile_id:
                messagebox.showwarning(
                    "会话过期",
                    f"用户“{context.profile.alias}”的登录会话已失效。\n请重新扫码登录。",
                )
                self.status_var.set("当前用户会话已过期，请重新登录")
                self.apply_active_user_control_state()
            self.refresh_user_views()
            return
        if app_legacy.selection_running:
            app_legacy.stop_selection = True
            self.root.after(0, self.restore_buttons)
        self.root.after(0, self.disable_workspace_tabs)

    def handle_environment_error(
        self: "CourseSelectionApp", profile_id: str, error_message: str
    ) -> None:
        """停止指定用户任务并提示检查网络，可选择重新扫码登录。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 网络环境异常对应的用户 UUID。
            error_message: 触发停止的原始异常摘要。

        Returns:
            None: 仅目标用户任务停止，会话与 Cookie 保留以便网络恢复后继续使用。
        """
        context = self.runtime.require_context(profile_id)
        if self.current_profile_id() == profile_id:
            self.status_var.set(
                f"用户“{context.profile.alias}”的任务已停止：网络环境异常"
            )
            self.apply_active_user_control_state()
            should_relogin = messagebox.askyesno(
                "网络环境异常",
                f"检测到网络/代理环境异常，已停止当前抢课任务。\n\n"
                f"错误信息：{error_message}\n\n"
                "请检查网络连接或代理设置（例如关闭 VPN/代理工具）。\n"
                "是否现在重新扫码登录？",
            )
            if should_relogin:
                self.start_login()
        self.refresh_user_views()

    def send_business_keepalive(
        self: "CourseSelectionApp", profile_id: str
    ) -> None:
        """使用指定用户会话访问选课主页并识别会话失效。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 需要业务保活的用户 UUID。

        Returns:
            None: 登录页跳转或 401/403 只使该用户过期。
        """
        context = self.runtime.require_context(profile_id)
        if context.login_state is not UserLoginState.LOGGED_IN or not context.cookies:
            return
        try:
            session = requests.Session()
            session.cookies.update(dict(context.cookies))
            response = session.get(
                "https://byyt.ustb.edu.cn/Xsxk/query/1",
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": "https://byyt.ustb.edu.cn/authentication/main",
                },
                timeout=10,
            )
            expired = (
                response.status_code in {401, 403}
                or "authentication/main" in response.url
                or "login" in response.text.lower()
            )
            if expired:
                self.root.after(0, lambda: self.handle_session_expired(profile_id))
            elif response.status_code != 200:
                self.user_log(profile_id, f"业务保活失败：HTTP {response.status_code}")
        except requests.RequestException as error:
            self.user_log(profile_id, f"业务保活异常：{error}")
