"""课程缓存、草稿文件与用户切换时的数据同步。"""

import orjson
import os
from tkinter import messagebox
from user_profiles import UserProfileStoreError
from multi_user_runtime import UserRuntimeContext
import app_legacy
import time

class StudentDataMixin:
    def sync_named_state_to_context(self: "CourseSelectionApp") -> None:
        """将界面当前命名列表状态写回当前用户上下文。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 无当前用户时不执行操作。
        """
        context = self.active_user_context()
        if context is None:
            return
        context.current_saved_list_id = getattr(self, "current_saved_list_id", None)
        context.current_saved_list_name = getattr(self, "current_saved_list_name", "")
        context.current_saved_list_note = getattr(self, "current_saved_list_note", "")
        context.current_saved_list_dirty = bool(
            getattr(self, "current_saved_list_dirty", False)
        )

    def load_named_state_from_context(
        self: "CourseSelectionApp", context: UserRuntimeContext
    ) -> None:
        """将用户上下文的命名列表状态载入界面。

        Args:
            self: 当前课程助手应用实例。
            context: 新选择的用户上下文。

        Returns:
            None: 状态条与实例状态会同步更新。
        """
        self.current_saved_list_id = context.current_saved_list_id
        self.current_saved_list_name = context.current_saved_list_name
        self.current_saved_list_note = context.current_saved_list_note
        self.current_saved_list_dirty = context.current_saved_list_dirty
        if hasattr(self, "current_list_name_var"):
            self.refresh_current_list_status()

    def sync_task_settings_to_context(self: "CourseSelectionApp") -> None:
        """将当前可见抢课设置保存到活动用户运行时上下文。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 无活动用户或控件尚未创建时不执行操作。
        """
        context = self.active_user_context()
        if context is None or not hasattr(self, "rush_mode_var"):
            return
        context.rush_mode = str(self.rush_mode_var.get())
        context.rush_time = str(self.rush_time_var.get()).strip()
        context.stop_on_success = bool(self.stop_on_success_var.get())
        context.retry_full = bool(self.retry_full_var.get())

    def load_task_settings_from_context(
        self: "CourseSelectionApp", context: UserRuntimeContext
    ) -> None:
        """将指定用户的运行期抢课设置载入界面。

        Args:
            self: 当前课程助手应用实例。
            context: 新选择的用户运行时上下文。

        Returns:
            None: 控件尚未创建时不执行操作。
        """
        if not hasattr(self, "rush_mode_var"):
            return
        self.rush_mode_var.set(context.rush_mode)
        self.rush_time_var.set(context.rush_time)
        self.stop_on_success_var.set(context.stop_on_success)
        self.retry_full_var.set(context.retry_full)
        self.on_mode_change()

    def load_course_cache(self: "CourseSelectionApp") -> None:
        """从 course_cache.json 加载全局课程缓存。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 缓存加载失败时重置为空字典。
        """
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.course_cache = orjson.loads(f.read())
                print(f"✅ 已加载 {len(self.course_cache)} 条课程缓存")
        except Exception as e:
            print(f"⚠️ 加载课程缓存失败: {e}")
            self.course_cache = {}

    def save_course_cache(self: "CourseSelectionApp") -> None:
        """将内存课程缓存持久化到 course_cache.json。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 写入失败时仅输出提示，不影响界面。
        """
        try:
            with open(self.cache_file, 'wb') as f:
                f.write(orjson.dumps(self.course_cache, option=orjson.OPT_INDENT_2))
            print(f"💾 已保存 {len(self.course_cache)} 条课程缓存")
        except Exception as e:
            print(f"⚠️ 保存课程缓存失败: {e}")

    def get_cached_course(
        self: "CourseSelectionApp",
        course_id: str,
        semester: str,
    ) -> list[dict[str, object]] | None:
        """按学期与课程代码读取缓存课程，并兼容旧版单条字典格式。

        Args:
            self: 当前课程助手应用实例。
            course_id: 课程代码。
            semester: 学期标识（学年与学期拼接）。

        Returns:
            课程列表；旧版单条缓存会转换为列表，无缓存时返回 None。
        """
        cache_key = f"{semester}_{course_id}"
        cached_data = self.course_cache.get(cache_key)

        # 兼容旧版本：如果缓存是单个dict，转换为list
        if cached_data and isinstance(cached_data, dict):
            return [cached_data]

        return cached_data  # 返回列表或None

    def cache_course_info(
        self: "CourseSelectionApp",
        course_id: str,
        semester: str,
        course_info: dict[str, object],
    ) -> None:
        """按学期与课程代码缓存课程并按任务 ID 去重。

        Args:
            self: 当前课程助手应用实例。
            course_id: 课程代码。
            semester: 学期标识（学年与学期拼接）。
            course_info: 单门课程的缓存条目。

        Returns:
            None: 写入完成后立即持久化缓存文件。
        """
        cache_key = f"{semester}_{course_id}"

        # 如果该课程ID已存在缓存，追加到列表；否则创建新列表
        if cache_key in self.course_cache:
            # 检查是否已存在相同的p_id，避免重复
            existing_p_ids = {course['p_id'] for course in self.course_cache[cache_key]}
            if course_info['p_id'] not in existing_p_ids:
                self.course_cache[cache_key].append(course_info)
        else:
            # 新课程ID，创建列表
            self.course_cache[cache_key] = [course_info]

        self.save_course_cache()

    def get_student_course_file(
        self: "CourseSelectionApp", student_name: str
    ) -> str | None:
        """返回旧版按人员命名的课程列表文件路径。

        Args:
            self: 当前课程助手应用实例。
            student_name: 人员名称。

        Returns:
            旧版 course_list_<人员>.json 文件路径；名称为空时返回 None。
        """
        if not student_name or not student_name.strip():
            return None
        # 文件名格式：course_list_学生姓名.json
        safe_name = student_name.strip().replace('/', '_').replace('\\', '_')
        return os.path.join(os.path.dirname(__file__), f"course_list_{safe_name}.json")

    def load_saved_course_list(
        self: "CourseSelectionApp", student_name: str | None = None
    ) -> None:
        """加载指定用户的独立课程草稿。

        Args:
            self: 当前课程助手应用实例。
            student_name: 用户别名；为 None 时使用当前用户。

        Returns:
            None: 用户上下文和当前表格会同步更新。
        """
        if hasattr(self, "runtime"):
            if student_name is None:
                context = self.active_user_context()
            else:
                profile_id = self._user_alias_to_id.get(student_name)
                context = self.runtime.require_context(profile_id) if profile_id else None
            if context is None:
                return
            try:
                context.courses = self.profile_store.load_draft(context.profile.id)
            except UserProfileStoreError as error:
                messagebox.showerror("草稿读取失败", str(error))
                return
            if context is self.active_user_context():
                self.update_course_list()
            return
        if student_name is None:
            student_name = self.current_student_name
        course_file = self.get_student_course_file(student_name)
        if not course_file:
            print("⚠️ 学生姓名为空，无法加载课程列表")
            app_legacy.course_data_list = []
            app_legacy.course_id_count = 0
            self.update_course_list()
            return
        try:
            if os.path.exists(course_file):
                with open(course_file, 'r', encoding='utf-8') as f:
                    saved_list = orjson.loads(f.read())
                    if isinstance(saved_list, list) and len(saved_list) > 0:
                        valid_courses = []
                        for i, course in enumerate(saved_list):
                            if all(key in course for key in ["priority", "data", "name", "teacher", "course_id", "schedule"]):
                                # 保持原有的ID，不重新设置
                                if "id" not in course:
                                    course["id"] = i + 1
                                valid_courses.append(course)

                        if valid_courses:
                            app_legacy.course_data_list = valid_courses
                            # 设置计数器为最大ID
                            app_legacy.course_id_count = max(course["id"] for course in valid_courses)
                            self.update_course_list()
                            print(f"✅ 已加载 {student_name} 的 {len(app_legacy.course_data_list)} 门课程")
                        else:
                            print(f"⚠️ {student_name} 的课程列表数据无效，已清空")
                            app_legacy.course_data_list = []
                            app_legacy.course_id_count = 0
                            self.update_course_list()
                    else:
                        # 空列表处理
                        print(f"ℹ️ {student_name} 暂无保存的课程")
                        app_legacy.course_data_list = []
                        app_legacy.course_id_count = 0
                        self.update_course_list()
            else:
                print(f"ℹ️ {student_name} 是新用户，暂无课程记录")
                app_legacy.course_data_list = []
                app_legacy.course_id_count = 0
                self.update_course_list()
        except Exception as e:
            print(f"⚠️ 加载 {student_name} 的课程列表失败: {e}")
            app_legacy.course_data_list = []
            app_legacy.course_id_count = 0
            self.update_course_list()

    def save_course_list(
        self: "CourseSelectionApp", student_name: str | None = None
    ) -> bool:
        """保存指定人员的当前草稿课程列表。

        Args:
            self: 当前课程助手应用实例。
            student_name: 目标人员名称；为 None 时使用当前人员。

        Returns:
            True 表示草稿写入成功，False 表示人员为空或写入失败。
        """
        if hasattr(self, "runtime"):
            if student_name is None:
                context = self.active_user_context()
            else:
                profile_id = self._user_alias_to_id.get(student_name)
                context = self.runtime.require_context(profile_id) if profile_id else None
            if context is None:
                print("⚠️ 当前用户为空，无法保存课程草稿")
                return False
            try:
                self.profile_store.save_draft(context.profile.id, context.courses)
                return True
            except UserProfileStoreError as error:
                print(f"⚠️ 保存 {context.profile.alias} 的课程草稿失败：{error}")
                return False

        if student_name is None:
            student_name = self.current_student_name

        course_file = self.get_student_course_file(student_name)
        if not course_file:
            print("⚠️ 学生姓名为空，无法保存课程列表")
            return False

        try:
            save_list = []
            for course in app_legacy.course_data_list:
                save_course = course.copy()
                if "id" in save_course:
                    del save_course["id"]
                save_list.append(save_course)

            with open(course_file, 'wb') as f:
                f.write(orjson.dumps(save_list, option=orjson.OPT_INDENT_2))
            print(f"💾 已保存 {student_name} 的 {len(app_legacy.course_data_list)} 门课程")
            return True
        except Exception as e:
            print(f"⚠️ 保存 {student_name} 的课程列表失败: {e}")
            return False

    def on_student_name_change(self: "CourseSelectionApp", *args: object) -> None:
        """在人员输入停止变化后安排实际切换。

        Args:
            self: 当前课程助手应用实例。
            *args: Tkinter 变量追踪回调传入的参数。

        Returns:
            None: 防抖任务会注册到 Tkinter 事件循环。
        """
        if self.student_switch_lock:
            return

        # 如果之前有正在等待执行的任务，先取消它
        if self.switch_timer:
            self.root.after_cancel(self.switch_timer)

        # 开启一个新的定时器，800毫秒后执行 process_student_switch
        self.switch_timer = self.root.after(800, self.process_student_switch)

    def process_student_switch(self: "CourseSelectionApp") -> None:
        """保存旧人员草稿并加载新人员的独立草稿与列表状态。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 当前人员、课程表和命名列表状态会同步更新。
        """
        if hasattr(self, "runtime"):
            self.switch_timer = None
            self.select_user_by_alias()
            return

        # 清空定时器引用
        self.switch_timer = None

        new_name = self.student_name_var.get().strip()

        # 如果名字没有实质性变化，不处理
        if new_name == self.current_student_name:
            return

        # 如果正在抢课，不允许切换
        if app_legacy.selection_running:
            # 注意：因为是延时触发，这里最好不要弹窗打断用户，直接回滚即可
            # 或者仅在日志中提示
            if self.student_switch_lock:
                return
            self.student_switch_lock = True
            self.student_name_var.set(self.current_student_name)
            self.student_switch_lock = False
            print("⚠️ 正在抢课中，忽略人员切换请求")
            return

        # === 以下是原有的切换逻辑 ===

        # 保存当前人员的课程列表（如果有）
        if self.current_student_name:
            print(f"💾 切换人员：保存 {self.current_student_name} 的课程列表")
            self.save_course_list(self.current_student_name)

        # 更新当前人员
        self.current_student_name = new_name
        if hasattr(self, "current_student_display_var"):
            self.current_student_display_var.set(new_name or "未设置")

        # 加载新人员的课程列表
        if new_name:
            print(f"📂 切换人员：加载 {new_name} 的课程列表")
            self.load_saved_course_list(new_name)
            self.status_var.set(f"已切换到 {new_name}")
        else:
            # 如果清空了姓名，也清空课程列表
            app_legacy.course_data_list = []
            app_legacy.course_id_count = 0
            self.update_course_list()
            self.status_var.set("请输入抢课人员姓名")
        self.reset_current_saved_list_state()

    def auto_save_course_list(self: "CourseSelectionApp") -> None:
        """定期保存所有未运行用户的当前课程草稿。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 后台线程会持续运行至进程结束。
        """
        while True:
            time.sleep(30)
            if hasattr(self, "runtime"):
                for context in self.runtime.enabled_contexts():
                    if not self.is_context_task_active(context):
                        try:
                            self.profile_store.save_draft(
                                context.profile.id, context.courses
                            )
                        except UserProfileStoreError as error:
                            self.user_log(
                                context.profile.id, f"自动保存草稿失败：{error}"
                            )
                continue
            if app_legacy.selection_running or app_legacy.stop_selection:
                continue
            if self.current_student_name:
                self.save_course_list()

    def on_closing(self: "CourseSelectionApp") -> None:
        """停止全部用户任务、保存草稿并关闭窗口。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 运行任务会收到停止事件，窗口随后销毁。
        """
        app_legacy.stop_display = True
        if hasattr(self, "runtime"):
            self.runtime.stop_all()
            for context in self.runtime.enabled_contexts():
                try:
                    self.profile_store.save_draft(context.profile.id, context.courses)
                except UserProfileStoreError as error:
                    self.user_log(context.profile.id, f"关闭前保存草稿失败：{error}")
            self.stop_online_keepalive()
            self.root.destroy()
            return

        if app_legacy.selection_running:
            app_legacy.stop_selection = True
            print("🛑 正在停止选课进程...")
            time.sleep(1)

        self.stop_online_keepalive()

        # 保存当前人员的课程列表
        if self.current_student_name:
            self.save_course_list()
        print("👋 程序即将关闭，已保存数据")
        self.root.destroy()
