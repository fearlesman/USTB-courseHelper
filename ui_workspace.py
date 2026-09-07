"""工作区导航、用户切换与共享上下文辅助。"""

import tkinter as tk
from tkinter import ttk, messagebox
from multi_user_runtime import UserLoginState, UserRuntimeContext, UserTaskState
from user_profiles import UserProfileStoreError
import app_legacy


def open_schedule_popup(master: tk.Misc, schedule_text: str) -> None:
    """以只读小窗口展示完整的多行上课安排。

    Args:
        master: 承载弹窗的根窗口或父控件。
        schedule_text: 分号分隔的课程安排文本。

    Returns:
        None: 弹窗由用户手动关闭，不影响主界面状态。
    """
    display_text = schedule_text.replace("；", "\n")
    popup = tk.Toplevel(master)
    popup.title("上课安排")
    popup.configure(bg="#f3f6f9")
    popup.transient(master)
    popup.geometry("520x320")
    popup.minsize(360, 200)
    content = ttk.Frame(popup, style="App.TFrame", padding=(14, 12))
    content.pack(fill="both", expand=True)
    viewer = tk.Text(
        content,
        wrap="word",
        width=56,
        height=12,
        bg="#ffffff",
        relief=tk.FLAT,
        padx=8,
        pady=8,
    )
    scrollbar = ttk.Scrollbar(content, orient="vertical", command=viewer.yview)
    viewer.configure(yscrollcommand=scrollbar.set)
    viewer.pack(side=tk.LEFT, fill="both", expand=True)
    scrollbar.pack(side=tk.RIGHT, fill="y")
    viewer.insert("1.0", display_text)
    viewer.configure(state="disabled")
    actions = ttk.Frame(content, style="App.TFrame")
    actions.pack(side=tk.BOTTOM, fill="x", pady=(10, 0))
    ttk.Button(
        actions, text="关闭", style="Primary.TButton", command=popup.destroy
    ).pack(side=tk.RIGHT)
    popup.focus_set()


class WorkspaceMixin:
    def setup_global_user_bar(self: "CourseSelectionApp") -> None:
        """构建按账号和任务状态渐进显示的全局顶栏。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 登录操作、异常状态和账号菜单创建在应用根容器中。
        """
        bar = ttk.Frame(self.app_shell, style="TFrame", padding=(18, 9))
        bar.pack(fill="x")
        ttk.Label(bar, text="USTB 选课助手", style="Section.TLabel").pack(
            side=tk.LEFT
        )
        self.global_task_state_var = tk.StringVar(value="")
        self.global_login_state_var = tk.StringVar(value="会话：待登录")
        self.account_menu = tk.Menu(bar, tearoff=False)
        self.account_menu_btn = ttk.Menubutton(
            bar,
            text="账号",
            menu=self.account_menu,
            style="Secondary.TButton",
        )
        self.account_menu_btn.pack(side=tk.RIGHT)
        self.global_login_btn = ttk.Button(
            bar,
            text="扫码登录",
            command=self.start_current_user_login,
            style="Primary.TButton",
        )
        self.global_login_btn.pack(side=tk.RIGHT, padx=(8, 8))
        self.background_task_btn = ttk.Button(
            bar,
            text="",
            command=self.open_user_manager,
            style="Secondary.TButton",
        )
        self.global_task_state_label = ttk.Label(
            bar, textvariable=self.global_task_state_var, style="Muted.TLabel"
        )
        self.global_login_state_label = ttk.Label(
            bar, textvariable=self.global_login_state_var, style="Muted.TLabel"
        )
        self.global_login_state_label.pack(side=tk.RIGHT, padx=(12, 0))

    def show_login_view(self: "CourseSelectionApp") -> None:
        """显示独立扫码登录视图。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: Notebook 工作区会被临时隐藏。
        """
        self.workspace_frame.grid_remove()
        self.login_view.grid(row=0, column=0, sticky="nsew")

    def show_workspace(self: "CourseSelectionApp") -> None:
        """显示课程查询和抢课任务双页工作区。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 没有启用用户时仍停留在登录视图。
        """
        if self.active_user_context() is None:
            self.show_login_view()
            return
        self.login_view.grid_remove()
        self.workspace_frame.grid(row=0, column=0, sticky="nsew")

    def enter_offline_workspace(self: "CourseSelectionApp") -> None:
        """在不登录会话的情况下进入可离线编辑的工作区。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 工作页会启用并默认显示抢课任务页。
        """
        self._automatic_login_suppressed = True
        if self.active_user_context() is None:
            return
        self.enable_workspace_tabs()

    def show_initial_view(self: "CourseSelectionApp") -> None:
        """根据本地账号状态配置启动时显示的页面。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 有账号时启用工作页但先显示登录页，无账号时禁用工作页。
        """
        if self.runtime.active_context() is None:
            self.disable_workspace_tabs()
            return
        self._workspace_enabled = True
        self._apply_tab_states()
        self.show_rush_tab()
        self.show_login_view()

    def enable_workspace_tabs(self: "CourseSelectionApp") -> None:
        """
        启用登录后的工作区页面并显示当前用户的抢课任务页。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 页签状态直接写入 Notebook 控件。
        """
        self._workspace_enabled = True
        self._apply_tab_states()
        self.show_workspace()
        self.show_rush_tab()

    def disable_workspace_tabs(self: "CourseSelectionApp") -> None:
        """
        禁用工作区页面并显示独立登录视图。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 页签状态直接写入 Notebook 控件。
        """
        self._workspace_enabled = False
        self._apply_tab_states()
        self.show_login_view()

    def _apply_tab_states(self: "CourseSelectionApp") -> None:
        """按当前工作区开关状态统一设置全部页签可用性。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 课程查询页与全部抢课页签状态直接写入 Notebook。
        """
        if not hasattr(self, "tab_control") or not hasattr(self, "course_search_tab"):
            return
        state = "normal" if self._workspace_enabled else "disabled"
        try:
            self.tab_control.tab(self.course_search_tab, state=state)
        except (AttributeError, tk.TclError):
            pass
        rush_frames = list(getattr(self, "rush_tab_frames", {}).values())
        if not rush_frames and getattr(self, "rush_tab", None) is not None:
            rush_frames = [self.rush_tab]
        for frame in rush_frames:
            try:
                self.tab_control.tab(frame, state=state)
            except (AttributeError, tk.TclError):
                pass

    def show_course_search_tab(self: "CourseSelectionApp") -> None:
        """
        显示课程查询页面。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: Notebook 当前页面切换为课程查询页。
        """
        self.show_workspace()
        self.tab_control.select(self.course_search_tab)

    def show_rush_tab(self: "CourseSelectionApp", profile_id: str | None = None) -> None:
        """
        显示指定用户的抢课任务页签。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 目标用户 UUID；为 None 时使用当前用户。

        Returns:
            None: Notebook 当前页面切换为目标用户的抢课任务页。
        """
        self.show_workspace()
        target_id = profile_id if profile_id is not None else self.current_profile_id()
        if (
            target_id is not None
            and getattr(self, "rush_tab_frames", {}).get(target_id) is not None
        ):
            if self.current_profile_id() != target_id:
                self.select_user_by_id(target_id)
            self._syncing_tab = True
            try:
                self.tab_control.select(self.rush_tab_frames[target_id])
            finally:
                self._syncing_tab = False
            return
        if getattr(self, "rush_tab", None) is not None:
            try:
                self.tab_control.select(self.rush_tab)
            except (AttributeError, tk.TclError):
                pass

    def on_rush_tab_changed(
        self: "CourseSelectionApp", event: object | None = None
    ) -> None:
        """在用户点击抢课页签时将当前用户切换为该页签对应用户。

        Args:
            self: 当前课程助手应用实例。
            event: Notebook 页签切换事件；直接调用时为 None。

        Returns:
            None: 课程查询页签不会触发用户切换。
        """
        if getattr(self, "_syncing_tab", False):
            return
        if not hasattr(self, "tab_control") or not hasattr(self, "runtime"):
            return
        try:
            selected = self.tab_control.select()
        except tk.TclError:
            return
        for profile_id, frame in getattr(self, "rush_tab_frames", {}).items():
            if profile_id is None:
                continue
            if str(frame) == str(selected):
                self.select_user_by_id(profile_id)
                return

    def active_user_context(
        self: "CourseSelectionApp", show_error: bool = False
    ) -> UserRuntimeContext | None:
        """返回当前用户上下文并按需显示中文提示。

        Args:
            self: 当前课程助手应用实例。
            show_error: True 表示无当前用户时显示提示。

        Returns:
            当前用户上下文；兼容测试或无用户时返回 None。
        """
        runtime = getattr(self, "runtime", None)
        context = runtime.active_context() if runtime is not None else None
        if context is None and show_error:
            messagebox.showerror("错误", "请先在登录页添加并选择用户")
        return context

    def current_profile_id(self: "CourseSelectionApp") -> str | None:
        """返回当前用户 UUID。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            当前用户 UUID；兼容测试或无用户时返回 None。
        """
        context = self.active_user_context()
        if context is None:
            return None
        profile = getattr(context, "profile", None)
        return profile.id if profile is not None else None

    def list_storage_key(self: "CourseSelectionApp") -> str:
        """返回命名列表存储使用的稳定用户标识。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            真实应用返回用户 UUID；旧测试返回人员名称。
        """
        return self.current_profile_id() or self.current_student_name

    def current_courses(self: "CourseSelectionApp") -> list[dict[str, object]]:
        """返回当前用户可编辑课程列表。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            真实应用返回用户上下文课程；旧测试返回兼容全局列表。
        """
        context = self.active_user_context()
        if context is not None:
            return context.courses
        return app_legacy.course_data_list

    def current_cookies(self: "CourseSelectionApp") -> dict[str, str]:
        """返回当前用户仅在内存中的 Cookie。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            当前用户 Cookie 副本；旧测试返回兼容全局 Cookie。
        """
        context = self.active_user_context()
        if context is not None:
            return dict(context.cookies)
        return dict(app_legacy.final_cookies_dict)

    def is_context_task_active(
        self: "CourseSelectionApp", context: UserRuntimeContext | None
    ) -> bool:
        """判断用户上下文是否存在活动抢课任务。

        Args:
            self: 当前课程助手应用实例。
            context: 需要检查的用户运行时上下文。

        Returns:
            True 表示用户处于等待、抢课或停止阶段。
        """
        if context is None:
            return bool(app_legacy.selection_running)
        return context.task_state in {
            UserTaskState.WAITING,
            UserTaskState.RUNNING,
            UserTaskState.STOPPING,
        }

    def select_user_by_alias(
        self: "CourseSelectionApp", event: object | None = None
    ) -> None:
        """根据只读选择器切换当前用户而不中断其他任务。

        Args:
            self: 当前课程助手应用实例。
            event: Tkinter 选择事件；直接调用时为 None。

        Returns:
            None: 当前课程、命名状态、查询结果和日志会切换。
        """
        alias = self.student_name_var.get().strip()
        profile_id = getattr(self, "_user_alias_to_id", {}).get(alias)
        if profile_id is None:
            profile_id = next(
                (
                    profile.id
                    for profile in self.profile_store.list_all()
                    if profile.alias == alias
                ),
                None,
            )
        if not profile_id:
            return
        previous = self.active_user_context()
        if previous is not None and previous.profile.id == profile_id:
            return
        self.sync_named_state_to_context()
        self.sync_task_settings_to_context()
        try:
            context = self.runtime.select_profile(profile_id)
        except UserProfileStoreError as error:
            messagebox.showerror("切换失败", str(error))
            return
        self._sync_active_rush_view(profile_id)
        self.current_student_name = context.profile.alias
        self.current_student_display_var.set(context.profile.alias)
        self.student_name_var.set(context.profile.alias)
        self.load_named_state_from_context(context)
        self.load_task_settings_from_context(context)
        self.search_results_by_task_id = dict(context.search_results_by_task_id)
        self.search_result_course_types_by_task_id = dict(
            context.search_result_course_types_by_task_id
        )
        if hasattr(self, "course_result_tree"):
            self.render_course_search_results(
                list(context.search_results_by_task_id.values()),
                notify_empty=False,
            )
        self.update_course_list()
        self.refresh_user_views()

    def select_user_by_id(self: "CourseSelectionApp", profile_id: str) -> None:
        """通过稳定 UUID 切换当前账号并刷新独立工作状态。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 需要切换到的启用账号 UUID。

        Returns:
            None: 无效或停用账号会显示中文错误并保持当前状态。
        """
        try:
            profile = self.profile_store.get(profile_id)
        except UserProfileStoreError as error:
            messagebox.showerror("切换失败", str(error))
            return
        self.student_name_var.set(profile.alias)
        if hasattr(self, "login_target_var"):
            self.login_target_var.set(profile.alias)
        self.select_user_by_alias()

    def refresh_user_views(self: "CourseSelectionApp") -> None:
        """按启用账号数量刷新登录目标、顶栏菜单和任务提示。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 单账号身份控件会隐藏，多账号切换与异常状态按需显示。
        """
        if not hasattr(self, "runtime"):
            return
        self.ensure_rush_tabs()
        contexts = self.runtime.enabled_contexts()
        active_profiles = [context.profile for context in contexts]
        if hasattr(self, "quick_login_frame"):
            self.quick_login_frame.grid(row=2, column=0, sticky="nsew")
        if hasattr(self, "login_target_frame"):
            if len(active_profiles) > 1:
                self.login_target_frame.grid(row=1, column=0, sticky="ew", pady=(0, 16))
            else:
                self.login_target_frame.grid_remove()
        if hasattr(self, "offline_workspace_btn"):
            if active_profiles:
                self.offline_workspace_btn.grid(row=0, column=1, padx=(10, 0))
            else:
                self.offline_workspace_btn.grid_remove()
        if hasattr(self, "quick_login_btn"):
            self.quick_login_btn.config(
                state=tk.DISABLED
                if getattr(self, "_quick_login_active", False)
                else tk.NORMAL
            )
        for widget_name in ("add_user_btn", "manager_login_btn"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.config(state=tk.NORMAL)
        self._user_alias_to_id = {profile.alias: profile.id for profile in active_profiles}
        aliases = [profile.alias for profile in active_profiles]
        if hasattr(self, "login_target_combo"):
            self.login_target_combo.config(values=aliases)
        context = self.runtime.active_context()
        if context is not None:
            self.student_name_var.set(context.profile.alias)
            self.current_student_name = context.profile.alias
            self.current_student_display_var.set(context.profile.alias)
            if hasattr(self, "login_target_var"):
                self.login_target_var.set(context.profile.alias)
            if hasattr(self, "global_login_state_var"):
                self.global_login_state_var.set(
                    f"会话：{context.login_state.value}"
                )
                task_text = (
                    ""
                    if context.task_state is UserTaskState.IDLE
                    else f"任务：{context.task_state.value}"
                )
                self.global_task_state_var.set(task_text)
                self._set_packed_widget_visible(
                    self.global_task_state_label,
                    bool(task_text),
                    padx=(12, 0),
                )
            if hasattr(self, "global_login_btn"):
                logged_in = context.login_state is UserLoginState.LOGGED_IN
                self.global_login_btn.config(
                    text="重新登录" if context.login_state is UserLoginState.EXPIRED else "扫码登录",
                    state=(
                        tk.DISABLED
                        if context.login_state is UserLoginState.LOGGING_IN
                        else tk.NORMAL
                    ),
                )
                self._set_packed_widget_visible(
                    self.global_login_btn,
                    not logged_in,
                    padx=(8, 8),
                )
        else:
            self.student_name_var.set("")
            self.current_student_name = ""
            self.current_student_display_var.set("未设置")
            if hasattr(self, "login_target_var"):
                self.login_target_var.set("")
            if hasattr(self, "global_login_state_var"):
                self.global_login_state_var.set("会话：待登录")
                self.global_task_state_var.set("")
            if hasattr(self, "global_login_btn"):
                self.global_login_btn.config(
                    text="直接扫码",
                    state=tk.NORMAL,
                )
                self._set_packed_widget_visible(
                    self.global_login_btn,
                    True,
                    padx=(8, 8),
                )
            if hasattr(self, "global_task_state_label"):
                self.global_task_state_label.pack_forget()
        if hasattr(self, "account_menu_btn"):
            self.account_menu_btn.config(
                text=context.profile.alias if context is not None and len(contexts) > 1 else "账号",
                state=tk.NORMAL if contexts else tk.DISABLED,
            )
        if hasattr(self, "account_menu"):
            self.rebuild_account_menu(contexts)
        if hasattr(self, "background_task_btn"):
            active_tasks = self.runtime.active_task_contexts(
                exclude_profile_id=context.profile.id if context is not None else None
            )
            if active_tasks:
                self.background_task_btn.config(
                    text=f"{len(active_tasks)} 个账号正在运行"
                )
                self._set_packed_widget_visible(
                    self.background_task_btn,
                    True,
                    padx=(8, 0),
                )
            else:
                self.background_task_btn.pack_forget()
        tree = getattr(self, "user_profile_tree", None)
        if tree is not None:
            for item_id in tree.get_children():
                tree.delete(item_id)
            for profile in active_profiles:
                user_context = self.runtime.require_context(profile.id)
                tree.insert(
                    "",
                    "end",
                    iid=profile.id,
                    values=(
                        profile.alias,
                        user_context.login_state.value,
                        user_context.task_state.value,
                        user_context.last_result or "—",
                    ),
                )
                if context is not None and profile.id == context.profile.id:
                    tree.selection_set(profile.id)
                    tree.focus(profile.id)
        for view_context in self.runtime.enabled_contexts():
            rush_views = getattr(self, "rush_views", {})
            if view_context.profile.id in rush_views:
                self.refresh_rush_view(view_context.profile.id)
        self._sync_active_rush_view(
            context.profile.id if context is not None else None
        )
        if context is not None and hasattr(self, "start_auto_btn"):
            self.apply_active_user_control_state()

    def _set_packed_widget_visible(
        self: "CourseSelectionApp",
        widget: tk.Widget,
        visible: bool,
        **pack_options: object,
    ) -> None:
        """按需显示或隐藏一个使用 pack 布局的顶栏控件。

        Args:
            self: 当前课程助手应用实例。
            widget: 需要调整可见性的 Tkinter 控件。
            visible: True 表示显示，False 表示隐藏。
            **pack_options: 恢复显示时传递给 pack 的布局参数。

        Returns:
            None: 控件布局状态会直接更新。
        """
        if visible:
            widget.pack(side=tk.RIGHT, **pack_options)
        else:
            widget.pack_forget()

    def rebuild_account_menu(
        self: "CourseSelectionApp", contexts: tuple[UserRuntimeContext, ...]
    ) -> None:
        """根据启用账号上下文重建顶栏账号菜单。

        Args:
            self: 当前课程助手应用实例。
            contexts: 按创建顺序排列的启用账号上下文快照。

        Returns:
            None: 菜单项会替换为最新登录和任务状态。
        """
        self.account_menu.delete(0, tk.END)
        current_id = self.current_profile_id()
        for user_context in contexts:
            selected = "✓ " if user_context.profile.id == current_id else ""
            display_name = (
                "当前账号"
                if len(contexts) == 1
                else user_context.profile.alias
            )
            task_suffix = (
                f" · {user_context.task_state.value}"
                if user_context.task_state is not UserTaskState.IDLE
                else ""
            )
            label = (
                f"{selected}{display_name} · "
                f"{user_context.login_state.value}{task_suffix}"
            )
            self.account_menu.add_command(
                label=label,
                command=lambda profile_id=user_context.profile.id: self.select_user_by_id(
                    profile_id
                ),
            )
        if contexts:
            self.account_menu.add_separator()
            self.account_menu.add_command(
                label="添加其他账号",
                command=self.add_user_profile,
                state=tk.NORMAL,
            )
            self.account_menu.add_command(
                label="管理账号", command=self.open_user_manager
            )

    def apply_active_user_control_state(self: "CourseSelectionApp") -> None:
        """根据当前用户登录和任务状态更新操作可用性。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 只影响当前用户对应的界面操作。
        """
        context = self.active_user_context()
        if context is None:
            return
        self._sync_active_rush_view(context.profile.id)
        running = self.is_context_task_active(context)
        logged_in = (
            context.login_state is UserLoginState.LOGGED_IN and bool(context.cookies)
        )
        self.add_course_btn.config(state=tk.NORMAL if logged_in else tk.DISABLED)
        self.add_selected_course_btn.config(
            state=tk.DISABLED if running else tk.NORMAL
        )
        self.remove_course_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        if hasattr(self, "advanced_add_btn"):
            self.advanced_add_btn.config(
                state=tk.NORMAL if logged_in else tk.DISABLED
            )
        for view_context in self.runtime.enabled_contexts():
            rush_views = getattr(self, "rush_views", {})
            if view_context.profile.id in rush_views:
                self._refresh_view_controls(
                    rush_views[view_context.profile.id], view_context
                )

    def user_log(self: "CourseSelectionApp", profile_id: str, message: str) -> None:
        """写入用户独立日志并保留终端输出。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 日志所属用户 UUID。
            message: 日志文本。

        Returns:
            None: 日志写入用户上下文并输出到终端。
        """
        self.runtime.append_log(profile_id, message)
        alias = self.runtime.require_context(profile_id).profile.alias
        print(f"[{alias}] {message}")

    def on_window_configure(self: "CourseSelectionApp", event: object) -> None:
        """对窗口尺寸变化执行防抖状态检查。

        Args:
            self: 当前课程助手应用实例。
            event: Tkinter 窗口配置事件。

        Returns:
            None: 延迟检查任务会注册到 Tkinter 事件循环。
        """
        if self.window_state_debounce_id:
            self.root.after_cancel(self.window_state_debounce_id)

        self.window_state_debounce_id = self.root.after(100, self.check_window_state)

    def on_window_minimize(self: "CourseSelectionApp", event: object) -> None:
        """记录窗口已最小化但不写入用户控制台。

        Args:
            self: 当前课程助手应用实例。
            event: Tkinter 窗口隐藏事件。

        Returns:
            None: 仅更新窗口状态。
        """
        self.set_window_minimized(True)

    def on_window_restore(self: "CourseSelectionApp", event: object) -> None:
        """记录窗口已恢复但不写入用户控制台。

        Args:
            self: 当前课程助手应用实例。
            event: Tkinter 窗口显示事件。

        Returns:
            None: 仅更新窗口状态。
        """
        self.set_window_minimized(False)

    def set_window_minimized(
        self: "CourseSelectionApp", is_minimized: bool
    ) -> None:
        """设置窗口最小化状态并避免重复处理。

        Args:
            self: 当前课程助手应用实例。
            is_minimized: True 表示窗口已最小化。

        Returns:
            None: 状态仅保存在实例属性中。
        """
        if self.window_minimized == is_minimized:
            return

        self.window_minimized = is_minimized

    def check_window_state(self: "CourseSelectionApp") -> None:
        """读取窗口状态并同步最小化标记。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 当前窗口状态会写入实例属性。
        """
        try:
            current_state = self.root.state()
            is_minimized = (current_state == 'iconic')

            if self.window_minimized != is_minimized:
                self.set_window_minimized(is_minimized)
        except tk.TclError:
            pass
