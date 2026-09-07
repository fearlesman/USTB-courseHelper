"""独立账号管理窗口的用户档案维护。"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from user_profiles import UserProfileStoreError

class UserManagerMixin:
    def open_user_manager(self: "CourseSelectionApp") -> None:
        """创建或提升独立账号管理窗口。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 管理窗口允许添加、重命名、切换和登录账号。
        """
        existing = getattr(self, "user_manager_window", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    return
            except tk.TclError:
                pass
        window = tk.Toplevel(self.root)
        self.user_manager_window = window
        window.title("账号管理")
        window.geometry("820x420")
        window.minsize(720, 360)
        window.transient(self.root)
        window.configure(bg="#f3f6f9")
        window.protocol("WM_DELETE_WINDOW", self.close_user_manager)

        page = ttk.Frame(window, style="App.TFrame", padding=(18, 16))
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        ttk.Label(page, text="账号管理", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 12)
        )

        table_frame = ttk.Frame(page, style="TFrame")
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("alias", "login", "task", "result")
        self.user_profile_tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "alias": "账号名称",
            "login": "登录状态",
            "task": "任务状态",
            "result": "最后结果",
        }
        widths = {"alias": 170, "login": 110, "task": 110, "result": 320}
        for column in columns:
            self.user_profile_tree.heading(column, text=headings[column])
            self.user_profile_tree.column(
                column,
                width=widths[column],
                minwidth=80,
                stretch=column == "result",
            )
        user_scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.user_profile_tree.yview
        )
        self.user_profile_tree.configure(yscrollcommand=user_scrollbar.set)
        self.user_profile_tree.grid(row=0, column=0, sticky="nsew")
        user_scrollbar.grid(row=0, column=1, sticky="ns")
        self.user_profile_tree.bind(
            "<Double-1>", lambda event: self.select_user_from_table()
        )

        actions = ttk.Frame(page, style="TFrame", padding=(0, 12, 0, 0))
        actions.grid(row=2, column=0, sticky="ew")
        self.add_user_btn = ttk.Button(
            actions,
            text="添加账号",
            command=self.add_user_profile,
            style="Primary.TButton",
        )
        self.add_user_btn.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            actions,
            text="修改名称",
            command=self.rename_user_profile,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            actions,
            text="切换到所选账号",
            command=self.select_user_from_table,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(0, 8))
        self.manager_login_btn = ttk.Button(
            actions,
            text="登录所选账号",
            command=self.login_selected_user,
            style="Secondary.TButton",
        )
        self.manager_login_btn.pack(side=tk.LEFT)
        ttk.Button(
            actions,
            text="关闭",
            command=self.close_user_manager,
            style="Secondary.TButton",
        ).pack(side=tk.RIGHT)
        self.refresh_user_views()

    def close_user_manager(self: "CourseSelectionApp") -> None:
        """关闭账号管理窗口并清理窗口控件引用。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 后台账号任务和登录流程不受影响。
        """
        window = getattr(self, "user_manager_window", None)
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass
        self.user_manager_window = None
        self.user_profile_tree = None
        self.add_user_btn = None
        self.manager_login_btn = None

    def selected_profile_id_from_table(self: "CourseSelectionApp") -> str | None:
        """返回用户管理表当前选中的用户 UUID。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            选中用户 UUID；未选择时显示提示并返回 None。
        """
        selection = self.user_profile_tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择一个用户档案")
            return None
        return selection[0]

    def add_user_profile(self: "CourseSelectionApp") -> None:
        """创建其他账号并立即进入该账号扫码登录流程。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 登录失败时可恢复添加前的当前账号。
        """
        alias = simpledialog.askstring(
            "添加账号", "请输入账号名称（1-40 个字符）：", parent=self.root
        )
        if alias is None:
            return
        previous_profile_id = self.current_profile_id()
        try:
            profile = self.profile_store.create(alias)
            self.runtime.sync_profiles()
        except UserProfileStoreError as error:
            messagebox.showerror("添加失败", str(error))
            return
        self._login_return_profile_id = previous_profile_id
        self._pending_added_profile_id = profile.id
        self.select_user_by_id(profile.id)
        if getattr(self, "user_manager_window", None) is not None:
            self.close_user_manager()
        self.show_login_view()
        self.refresh_user_views()
        self._start_login_for_profile(profile.id)

    def login_selected_user(self: "CourseSelectionApp") -> None:
        """切换到管理表所选账号并启动扫码登录。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 未选择账号时只显示中文提示。
        """
        profile_id = self.selected_profile_id_from_table()
        if profile_id is None:
            return
        self.select_user_by_id(profile_id)
        self.close_user_manager()
        self.show_login_view()
        self._start_login_for_profile(profile_id)

    def rename_user_profile(self: "CourseSelectionApp") -> None:
        """修改选中用户别名并保持用户数据归属不变。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 重命名成功后刷新所有用户选择器。
        """
        profile_id = self.selected_profile_id_from_table()
        if profile_id is None:
            return
        context = self.runtime.require_context(profile_id)
        if self.is_context_task_active(context):
            messagebox.showwarning("操作已锁定", "该用户抢课运行期间不能修改别名")
            return
        alias = simpledialog.askstring(
            "修改别名", "请输入新的用户别名：", initialvalue=context.profile.alias, parent=self.root
        )
        if alias is None:
            return
        try:
            self.profile_store.rename(profile_id, alias)
            self.runtime.sync_profiles()
        except UserProfileStoreError as error:
            messagebox.showerror("修改失败", str(error))
            return
        self.refresh_user_views()

    def select_user_from_table(self: "CourseSelectionApp") -> None:
        """将用户表选中的启用用户设为当前用户。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 工作区切换到该用户本地状态。
        """
        profile_id = self.selected_profile_id_from_table()
        if profile_id is None:
            return
        try:
            profile = self.profile_store.get(profile_id)
        except UserProfileStoreError as error:
            messagebox.showerror("选择失败", str(error))
            return
        if not profile.active:
            messagebox.showwarning("提示", "请先恢复该用户档案")
            return
        self.student_name_var.set(profile.alias)
        self.select_user_by_alias()
        self.close_user_manager()
        self.show_rush_tab()
