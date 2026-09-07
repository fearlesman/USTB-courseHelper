"""命名抢课列表的保存、管理、加载与编辑。"""

import tkinter as tk
from tkinter import ttk, messagebox
from rush_list_store import RushListStoreError, SavedRushList
from user_profiles import UserProfile
import app_legacy

class NamedListMixin:
    def begin_current_list_rename(self: "CourseSelectionApp") -> None:
        """在状态条中进入当前列表名称行内编辑模式。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 名称标签会切换为输入与确认控件。
        """
        context = self.active_user_context()
        if context is None:
            return
        if self.is_context_task_active(context):
            messagebox.showwarning("操作已锁定", "该用户抢课运行期间不能修改列表名称")
            return
        self.inline_list_name_var.set(self.current_saved_list_name)
        self.current_list_name_label.grid_remove()
        self.edit_current_list_name_btn.grid_remove()
        self.inline_list_name_frame.grid(row=0, column=1, columnspan=2, sticky="w")

    def cancel_current_list_rename(self: "CourseSelectionApp") -> None:
        """取消当前列表名称行内编辑。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 状态条恢复只读名称显示。
        """
        self.inline_list_name_frame.grid_remove()
        self.current_list_name_label.grid(row=0, column=1, sticky="w")
        self.edit_current_list_name_btn.grid(row=0, column=2, padx=(8, 0))

    def confirm_current_list_rename(self: "CourseSelectionApp") -> None:
        """确认行内名称并重命名或创建当前命名列表。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 成功时更新当前状态，失败时保持原列表不变。
        """
        context = self.active_user_context(show_error=True)
        if context is None:
            return
        if self.is_context_task_active(context):
            messagebox.showwarning("操作已锁定", "该用户抢课运行期间不能修改列表名称")
            return
        name = self.inline_list_name_var.get()
        previous_dirty = self.current_saved_list_dirty
        try:
            if self.current_saved_list_id:
                saved = self.rush_list_store.rename(
                    context.profile.id, self.current_saved_list_id, name
                )
                self.set_current_saved_list(saved, dirty=previous_dirty)
            else:
                if not context.courses:
                    messagebox.showerror("修改失败", "请至少添加一门课程后再命名当前列表")
                    return
                saved = self.rush_list_store.create(
                    context.profile.id,
                    name,
                    self.current_saved_list_note,
                    context.courses,
                )
                self.set_current_saved_list(saved, dirty=False)
        except RushListStoreError as error:
            messagebox.showerror("修改失败", str(error))
            return
        self.cancel_current_list_rename()
        self.status_var.set(f"当前列表名称已更新：{saved.name}")

    def reset_current_saved_list_state(self: "CourseSelectionApp") -> None:
        """重置当前命名列表并根据草稿内容设置未保存状态。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 当前列表恢复为未命名状态。
        """
        self.current_saved_list_id = None
        self.current_saved_list_name = ""
        self.current_saved_list_note = ""
        self.current_saved_list_dirty = bool(app_legacy.course_data_list)
        context = self.active_user_context()
        if context is not None:
            self.current_saved_list_dirty = bool(context.courses)
        self.sync_named_state_to_context()
        if hasattr(self, "current_list_name_var"):
            self.refresh_current_list_status()

    def mark_current_list_dirty(self: "CourseSelectionApp") -> None:
        """将当前命名列表标记为存在未保存课程变化。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 状态条会立即显示未保存更改。
        """
        self.current_saved_list_dirty = True
        self.sync_named_state_to_context()
        self.refresh_current_list_status()

    def set_current_saved_list(
        self: "CourseSelectionApp", saved_list: SavedRushList, dirty: bool = False
    ) -> None:
        """将指定命名列表设为当前列表并刷新状态条。

        Args:
            self: 当前课程助手应用实例。
            saved_list: 新的当前命名列表。
            dirty: 当前课程是否相对保存快照发生变化。

        Returns:
            None: 当前列表标识与显示状态会同步更新。
        """
        self.current_saved_list_id = saved_list.id
        self.current_saved_list_name = saved_list.name
        self.current_saved_list_note = saved_list.note
        self.current_saved_list_dirty = dirty
        self.sync_named_state_to_context()
        self.refresh_current_list_status()

    def open_save_rush_list_dialog(self: "CourseSelectionApp") -> None:
        """打开创建、更新或另存命名抢课列表的对话框。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 校验成功后创建模态 Tkinter 窗口。
        """
        if self.is_context_task_active(self.active_user_context()):
            messagebox.showwarning("操作已锁定", "抢课运行期间不能保存或管理列表")
            return
        context = self.active_user_context()
        if context is None and not self.current_student_name:
            messagebox.showerror("错误", "请先设置抢课人员")
            return
        courses = context.courses if context is not None else self.current_courses()
        if not courses:
            messagebox.showerror("错误", "请至少添加一门课程后再保存列表")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("保存抢课列表")
        dialog.geometry("520x360")
        dialog.minsize(460, 330)
        dialog.transient(self.root)
        dialog.grab_set()
        content = ttk.Frame(dialog, style="TFrame", padding=20)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(3, weight=1)
        ttk.Label(content, text="保存抢课列表", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 14)
        )
        ttk.Label(content, text="列表名称（1-40 个字符）").grid(
            row=1, column=0, sticky="w"
        )
        name_var = tk.StringVar(value=self.current_saved_list_name)
        name_entry = ttk.Entry(content, textvariable=name_var)
        name_entry.grid(row=2, column=0, sticky="ew", pady=(5, 12))
        ttk.Label(content, text="列表备注（最多 300 个字符）").grid(
            row=3, column=0, sticky="nw"
        )
        note_text = tk.Text(
            content,
            height=6,
            wrap=tk.WORD,
            relief="solid",
            borderwidth=1,
            font=("Microsoft YaHei UI", 10),
        )
        note_text.grid(row=4, column=0, sticky="nsew", pady=(5, 14))
        note_text.insert("1.0", self.current_saved_list_note)
        actions = ttk.Frame(content, style="TFrame")
        actions.grid(row=5, column=0, sticky="e")
        ttk.Button(
            actions,
            text="取消",
            command=dialog.destroy,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(0, 8))
        if self.current_saved_list_id:
            ttk.Button(
                actions,
                text="另存为",
                command=lambda: self.submit_rush_list_save(
                    dialog, name_var, note_text, save_as=True
                ),
                style="Secondary.TButton",
            ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            actions,
            text="保存",
            command=lambda: self.submit_rush_list_save(
                dialog, name_var, note_text, save_as=False
            ),
            style="Primary.TButton",
        ).pack(side=tk.LEFT)
        name_entry.focus_set()

    def submit_rush_list_save(
        self: "CourseSelectionApp",
        dialog: tk.Toplevel,
        name_var: tk.StringVar,
        note_text: tk.Text,
        save_as: bool,
    ) -> None:
        """提交保存对话框并创建或更新命名列表。

        Args:
            self: 当前课程助手应用实例。
            dialog: 当前保存对话框。
            name_var: 用户输入的列表名称变量。
            note_text: 用户输入备注的多行文本控件。
            save_as: True 表示始终创建独立快照。

        Returns:
            None: 成功时更新当前列表状态并关闭对话框。
        """
        if self.is_context_task_active(self.active_user_context()):
            messagebox.showwarning("操作已锁定", "抢课运行期间不能保存列表")
            return
        context = self.active_user_context()
        courses = context.courses if context is not None else self.current_courses()
        if not courses:
            messagebox.showerror("保存失败", "请先设置用户并添加课程")
            return
        try:
            name = name_var.get()
            note = note_text.get("1.0", "end-1c")
            if self.current_saved_list_id and not save_as:
                saved = self.rush_list_store.update(
                    self.list_storage_key(),
                    self.current_saved_list_id,
                    name,
                    note,
                    courses,
                )
            else:
                saved = self.rush_list_store.create(
                    self.list_storage_key(),
                    name,
                    note,
                    courses,
                )
        except RushListStoreError as error:
            messagebox.showerror("保存失败", str(error))
            return
        self.set_current_saved_list(saved)
        dialog.destroy()
        self.status_var.set(f"已保存命名列表：{saved.name}")
        messagebox.showinfo("保存成功", f"列表“{saved.name}”已保存")

    def open_rush_list_manager(self: "CourseSelectionApp") -> None:
        """打开当前人员的命名列表管理窗口。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 校验成功后创建模态管理窗口。
        """
        if self.is_context_task_active(self.active_user_context()):
            messagebox.showwarning("操作已锁定", "抢课运行期间不能保存或管理列表")
            return
        if not self.current_student_name:
            messagebox.showerror("错误", "请先设置抢课人员")
            return
        manager = tk.Toplevel(self.root)
        manager.title(f"管理抢课列表 - {self.current_student_name}")
        manager.geometry("760x460")
        manager.minsize(680, 400)
        manager.transient(self.root)
        manager.grab_set()
        self.rush_list_manager_window = manager
        content = ttk.Frame(manager, style="TFrame", padding=18)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)
        ttk.Label(content, text="已保存的抢课列表", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        columns = ("name", "note", "course_count", "updated_at")
        tree = ttk.Treeview(content, columns=columns, show="headings", selectmode="browse")
        headings = {
            "name": "名称",
            "note": "备注摘要",
            "course_count": "课程数",
            "updated_at": "更新时间",
        }
        widths = {"name": 170, "note": 280, "course_count": 75, "updated_at": 180}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], minwidth=60, stretch=column == "note")
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        actions = ttk.Frame(content, style="TFrame")
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions.columnconfigure(4, weight=1)
        ttk.Button(
            actions,
            text="加载",
            command=lambda: self.load_selected_named_rush_list(tree, manager),
            style="Primary.TButton",
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            actions,
            text="编辑",
            command=lambda: self.edit_selected_named_rush_list(tree),
            style="Secondary.TButton",
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(
            actions,
            text="删除",
            command=lambda: self.delete_selected_named_rush_list(tree),
            style="Secondary.TButton",
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(
            actions,
            text="分配给用户",
            command=lambda: self.open_assign_rush_list_dialog(tree),
            style="Secondary.TButton",
        ).grid(row=0, column=3)
        ttk.Button(
            actions,
            text="关闭",
            command=manager.destroy,
            style="Secondary.TButton",
        ).grid(row=0, column=5)
        self.refresh_rush_list_manager(tree)

    def open_assign_rush_list_dialog(
        self: "CourseSelectionApp", tree: ttk.Treeview
    ) -> None:
        """打开将选中列表复制给其他启用用户的对话框。

        Args:
            self: 当前课程助手应用实例。
            tree: 命名列表管理表。

        Returns:
            None: 存在目标用户时创建模态多选窗口。
        """
        context = self.active_user_context(show_error=True)
        if context is None:
            return
        if self.is_context_task_active(context):
            messagebox.showwarning("操作已锁定", "该用户抢课运行期间不能分配列表")
            return
        saved = self.get_manager_selection(tree)
        if saved is None:
            return
        targets = [
            profile
            for profile in self.profile_store.list_all()
            if profile.id != context.profile.id
        ]
        if not targets:
            messagebox.showinfo("暂无目标", "请先添加至少一名其他启用用户")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(f"分配列表 - {saved.name}")
        dialog.geometry("440x380")
        dialog.transient(self.root)
        dialog.grab_set()
        content = ttk.Frame(dialog, style="TFrame", padding=18)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)
        ttk.Label(content, text="选择目标用户", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        user_list = tk.Listbox(
            content,
            selectmode=tk.EXTENDED,
            exportselection=False,
            font=("Microsoft YaHei UI", 10),
            relief="solid",
            borderwidth=1,
        )
        user_list.grid(row=1, column=0, sticky="nsew")
        for profile in targets:
            target_context = self.runtime.require_context(profile.id)
            user_list.insert(
                tk.END,
                f"{profile.alias}    {target_context.login_state.value}",
            )
        actions = ttk.Frame(content, style="TFrame")
        actions.grid(row=2, column=0, sticky="e", pady=(12, 0))
        ttk.Button(
            actions, text="取消", command=dialog.destroy, style="Secondary.TButton"
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            actions,
            text="创建独立副本",
            command=lambda: self.submit_rush_list_assignment(
                dialog, user_list, saved, targets
            ),
            style="Primary.TButton",
        ).pack(side=tk.LEFT)

    def submit_rush_list_assignment(
        self: "CourseSelectionApp",
        dialog: tk.Toplevel,
        user_list: tk.Listbox,
        saved_list: SavedRushList,
        targets: list[UserProfile],
    ) -> None:
        """将命名列表深拷贝给对话框选中的目标用户。

        Args:
            self: 当前课程助手应用实例。
            dialog: 当前分配对话框。
            user_list: 用户多选列表控件。
            saved_list: 准备分配的源列表。
            targets: 与列表索引对应的目标用户档案。

        Returns:
            None: 完成后显示逐用户结果且不替换目标草稿。
        """
        context = self.active_user_context(show_error=True)
        if context is None:
            return
        selected_indices = user_list.curselection()
        if not selected_indices:
            messagebox.showwarning("提示", "请至少选择一名目标用户")
            return
        target_profiles = [targets[index] for index in selected_indices]
        result = self.rush_list_store.copy_to_users(
            context.profile.id,
            saved_list.id,
            [profile.id for profile in target_profiles],
        )
        alias_by_id = {profile.id: profile.alias for profile in target_profiles}
        copied_aliases = [alias_by_id[user_id] for user_id in result.copied_user_ids]
        skipped_aliases = [alias_by_id[user_id] for user_id in result.skipped_user_ids]
        lines = [f"成功：{', '.join(copied_aliases) or '无'}"]
        if skipped_aliases:
            lines.append(f"跳过同名或异常用户：{', '.join(skipped_aliases)}")
        dialog.destroy()
        messagebox.showinfo("分配结果", "\n".join(lines))

    def refresh_rush_list_manager(
        self: "CourseSelectionApp", tree: ttk.Treeview
    ) -> None:
        """刷新管理窗口中的当前人员命名列表。

        Args:
            self: 当前课程助手应用实例。
            tree: 管理窗口的列表表格。

        Returns:
            None: 表格内容会被完整替换。
        """
        try:
            saved_lists = self.rush_list_store.list_all(self.list_storage_key())
        except RushListStoreError as error:
            messagebox.showerror("读取失败", str(error))
            return
        for item_id in tree.get_children():
            tree.delete(item_id)
        for saved in saved_lists:
            note_summary = " ".join(saved.note.split()) or "暂无备注"
            if len(note_summary) > 42:
                note_summary = f"{note_summary[:39]}..."
            updated = saved.updated_at.replace("T", " ")[:19]
            tree.insert(
                "",
                "end",
                iid=saved.id,
                values=(saved.name, note_summary, f"{len(saved.courses)} 门", updated),
            )

    def get_manager_selection(
        self: "CourseSelectionApp", tree: ttk.Treeview
    ) -> SavedRushList | None:
        """读取管理窗口当前选中的命名列表。

        Args:
            self: 当前课程助手应用实例。
            tree: 管理窗口的列表表格。

        Returns:
            选中的命名列表；未选择或读取失败时返回 None。
        """
        selection = tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择一个命名列表")
            return None
        try:
            return self.rush_list_store.get(self.list_storage_key(), selection[0])
        except RushListStoreError as error:
            messagebox.showerror("读取失败", str(error))
            return None

    def load_selected_named_rush_list(
        self: "CourseSelectionApp", tree: ttk.Treeview, manager: tk.Toplevel
    ) -> None:
        """从管理窗口加载选中列表并关闭窗口。

        Args:
            self: 当前课程助手应用实例。
            tree: 管理窗口的列表表格。
            manager: 当前管理窗口。

        Returns:
            None: 加载成功时管理窗口会关闭。
        """
        saved = self.get_manager_selection(tree)
        if saved is None:
            return
        if self.load_named_rush_list(saved):
            manager.destroy()

    def load_named_rush_list(
        self: "CourseSelectionApp", saved_list: SavedRushList
    ) -> bool:
        """确认后用命名快照完整替换当前草稿。

        Args:
            self: 当前课程助手应用实例。
            saved_list: 准备加载的命名列表。

        Returns:
            True 表示替换并保存草稿成功，False 表示取消或失败。
        """
        if self.is_context_task_active(self.active_user_context()):
            messagebox.showwarning("操作已锁定", "抢课运行期间不能加载列表")
            return False
        confirmed = messagebox.askyesno(
            "确认加载",
            f"加载“{saved_list.name}”将用其中 {len(saved_list.courses)} 门课程完整替换当前待抢列表。\n是否继续？",
        )
        if not confirmed:
            return False
        try:
            replacement = self.rush_list_store.courses_for_loading(saved_list)
        except RushListStoreError as error:
            messagebox.showerror("加载失败", str(error))
            return False
        context = self.active_user_context()
        if context is not None:
            original_courses = context.courses
            context.courses = replacement
            original_count = len(original_courses)
        else:
            original_courses = app_legacy.course_data_list
            original_count = app_legacy.course_id_count
            app_legacy.course_data_list = replacement
            app_legacy.course_id_count = len(replacement)
        if not self.save_course_list():
            if context is not None:
                context.courses = original_courses
            else:
                app_legacy.course_data_list = original_courses
                app_legacy.course_id_count = original_count
            messagebox.showerror("加载失败", "当前草稿写入失败，原待抢课程保持不变")
            return False
        self.update_course_list()
        self.set_current_saved_list(saved_list)
        self.status_var.set(f"已加载命名列表：{saved_list.name}")
        return True

    def edit_selected_named_rush_list(
        self: "CourseSelectionApp", tree: ttk.Treeview
    ) -> None:
        """打开选中命名列表的名称与备注编辑对话框。

        Args:
            self: 当前课程助手应用实例。
            tree: 管理窗口的列表表格。

        Returns:
            None: 校验成功后创建模态编辑窗口。
        """
        if self.is_context_task_active(self.active_user_context()):
            messagebox.showwarning("操作已锁定", "抢课运行期间不能编辑列表")
            return
        saved = self.get_manager_selection(tree)
        if saved is None:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("编辑列表信息")
        dialog.geometry("500x340")
        dialog.transient(self.root)
        dialog.grab_set()
        content = ttk.Frame(dialog, style="TFrame", padding=20)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        ttk.Label(content, text="列表名称").grid(row=0, column=0, sticky="w")
        name_var = tk.StringVar(value=saved.name)
        ttk.Entry(content, textvariable=name_var).grid(
            row=1, column=0, sticky="ew", pady=(5, 12)
        )
        ttk.Label(content, text="列表备注").grid(row=2, column=0, sticky="w")
        note_text = tk.Text(content, height=7, wrap=tk.WORD, font=("Microsoft YaHei UI", 10))
        note_text.grid(row=3, column=0, sticky="nsew", pady=(5, 14))
        note_text.insert("1.0", saved.note)
        actions = ttk.Frame(content, style="TFrame")
        actions.grid(row=4, column=0, sticky="e")
        ttk.Button(
            actions, text="取消", command=dialog.destroy, style="Secondary.TButton"
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            actions,
            text="保存修改",
            command=lambda: self.submit_saved_list_metadata_edit(
                dialog, tree, saved, name_var, note_text
            ),
            style="Primary.TButton",
        ).pack(side=tk.LEFT)

    def submit_saved_list_metadata_edit(
        self: "CourseSelectionApp",
        dialog: tk.Toplevel,
        tree: ttk.Treeview,
        saved_list: SavedRushList,
        name_var: tk.StringVar,
        note_text: tk.Text,
    ) -> None:
        """提交管理窗口中的列表名称与备注修改。

        Args:
            self: 当前课程助手应用实例。
            dialog: 当前编辑对话框。
            tree: 需要刷新的管理列表表格。
            saved_list: 修改前的命名列表快照。
            name_var: 用户输入的列表名称变量。
            note_text: 用户输入备注的多行文本控件。

        Returns:
            None: 成功时刷新管理表格和当前列表状态。
        """
        if self.is_context_task_active(self.active_user_context()):
            messagebox.showwarning("操作已锁定", "抢课运行期间不能编辑列表")
            return
        try:
            updated = self.rush_list_store.update(
                self.list_storage_key(),
                saved_list.id,
                name_var.get(),
                note_text.get("1.0", "end-1c"),
                saved_list.courses,
            )
        except RushListStoreError as error:
            messagebox.showerror("编辑失败", str(error))
            return
        if self.current_saved_list_id == updated.id:
            was_dirty = self.current_saved_list_dirty
            self.set_current_saved_list(updated, dirty=was_dirty)
        dialog.destroy()
        self.refresh_rush_list_manager(tree)

    def delete_selected_named_rush_list(
        self: "CourseSelectionApp", tree: ttk.Treeview
    ) -> None:
        """确认后删除管理窗口选中的命名列表。

        Args:
            self: 当前课程助手应用实例。
            tree: 管理窗口的列表表格。

        Returns:
            None: 删除成功后刷新表格；屏幕课程保持不变。
        """
        if self.is_context_task_active(self.active_user_context()):
            messagebox.showwarning("操作已锁定", "抢课运行期间不能删除列表")
            return
        saved = self.get_manager_selection(tree)
        if saved is None:
            return
        if not messagebox.askyesno("确认删除", f"确定删除命名列表“{saved.name}”吗？"):
            return
        try:
            self.rush_list_store.delete(self.list_storage_key(), saved.id)
        except RushListStoreError as error:
            messagebox.showerror("删除失败", str(error))
            return
        if self.current_saved_list_id == saved.id:
            self.current_saved_list_id = None
            self.current_saved_list_name = ""
            self.current_saved_list_note = ""
            self.current_saved_list_dirty = True
            self.sync_named_state_to_context()
            self.refresh_current_list_status()
        self.refresh_rush_list_manager(tree)
