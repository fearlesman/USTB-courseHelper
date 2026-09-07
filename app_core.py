"""主应用类聚合模块。

CourseSelectionApp 通过 Mixin 组合各功能模块的方法；本文件只保留类
定义与初始化逻辑，并统一完成文件日志的终端重定向。
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk

from file_logging import DailyLogWriter

# 将 print 输出按日写入 logs，同时保留源码运行时的终端回显。
if not isinstance(sys.stdout, DailyLogWriter):
    sys.stdout = DailyLogWriter(os.path.dirname(__file__), terminal=sys.stdout)

from user_profiles import UserProfileStore
from multi_user_runtime import MultiUserRuntime
from rush_list_store import RushListStore
from ui_theme import ThemeMixin
from ui_workspace import WorkspaceMixin
from ui_student_data import StudentDataMixin
from ui_login import LoginMixin
from ui_user_manager import UserManagerMixin
from ui_course_query import CourseQueryMixin
from ui_named_lists import NamedListMixin
from ui_rush_task import RushTaskMixin, UserRushPage


class CourseSelectionApp(
    ThemeMixin,
    WorkspaceMixin,
    StudentDataMixin,
    LoginMixin,
    UserManagerMixin,
    CourseQueryMixin,
    NamedListMixin,
    RushTaskMixin,
):

    def __init__(
        self: "CourseSelectionApp",
        root: tk.Tk,
        data_directory: str | None = None,
    ) -> None:
        """
        初始化课程助手窗口、工作区页面与后台任务。

        Args:
            root: Tkinter 根窗口。
            data_directory: 用户档案与列表数据所在目录；为 None 时使用项目目录。

        Returns:
            None: 界面与应用状态直接绑定到当前实例。
        """
        self.target_text = ""
        self._data_directory = (
            data_directory if data_directory is not None else os.path.dirname(__file__)
        )
        self.root = root
        self.root.title("北京科技大学选课助手-zby")
        self.root.geometry("1280x860")
        self.root.minsize(1120, 720)
        self.root.configure(bg="#f3f6f9")

        self.course_cache = {}
        self.cache_file = os.path.join(self._data_directory, "course_cache.json")
        # 移除单一课程列表文件，改为按人员保存
        # self.course_list_file = os.path.join(os.path.dirname(__file__), "course_list.json")
        self.switch_timer = None

        self.profile_store = UserProfileStore(self._data_directory)
        self.runtime = MultiUserRuntime(self.profile_store)
        self.rush_list_store = RushListStore(
            self._data_directory, path_resolver=self.profile_store.named_lists_path
        )
        active_context = self.runtime.active_context()
        active_alias = active_context.profile.alias if active_context else ""
        self.current_student_name = active_alias
        self.student_name_var = tk.StringVar(value=active_alias)
        self.current_student_display_var = tk.StringVar(value=active_alias or "未设置")
        self.student_switch_lock = False
        self.current_saved_list_id = (
            active_context.current_saved_list_id if active_context else None
        )
        self.current_saved_list_name = (
            active_context.current_saved_list_name if active_context else ""
        )
        self.current_saved_list_note = (
            active_context.current_saved_list_note if active_context else ""
        )
        self.current_saved_list_dirty = (
            active_context.current_saved_list_dirty if active_context else False
        )
        self._user_alias_to_id: dict[str, str] = {}
        self._quick_login_active = False
        self._quick_profile_id: str | None = None
        self._login_return_profile_id: str | None = None
        self._pending_added_profile_id: str | None = None
        self._first_launch = not bool(self.profile_store.list_all())
        self._automatic_login_started = False
        self._automatic_login_scheduled = False
        self._automatic_login_suppressed = False

        # 加载课程缓存
        self.load_course_cache()

        # 配置样式
        self.style = ttk.Style()
        self.configure_visual_theme()

        # 创建全局用户栏、独立登录视图和双页工作区
        self.app_shell = ttk.Frame(root, style="App.TFrame")
        self.app_shell.pack(fill="both", expand=True)
        self.setup_global_user_bar()
        self.content_host = ttk.Frame(self.app_shell, style="App.TFrame")
        self.content_host.pack(fill="both", expand=True)
        self.content_host.columnconfigure(0, weight=1)
        self.content_host.rowconfigure(0, weight=1)

        self.login_view = ttk.Frame(self.content_host, style="TFrame")
        self.login_tab = self.login_view
        self.login_view.grid(row=0, column=0, sticky="nsew")
        self.setup_login_tab()

        self.workspace_frame = ttk.Frame(self.content_host, style="App.TFrame")
        self.workspace_frame.grid(row=0, column=0, sticky="nsew")
        self.tab_control = ttk.Notebook(self.workspace_frame)
        self.course_search_tab = ttk.Frame(self.tab_control, style="TFrame")
        self.tab_control.add(self.course_search_tab, text="课程查询")
        self.setup_course_search_tab()

        self.rush_tab = None
        self.rush_views: dict[str | None, UserRushPage] = {}
        self.rush_tab_frames: dict[str | None, tk.Widget] = {}
        self.current_rush_view: UserRushPage | None = None
        self._rush_tabs_signature: tuple[tuple[str, str], ...] | None = None
        self._workspace_enabled = False
        self._syncing_tab = False
        self.build_rush_tabs()

        self.tab_control.pack(expand=1, fill="both")
        self.tab_control.bind("<<NotebookTabChanged>>", self.on_rush_tab_changed)
        self.show_initial_view()
        self.refresh_user_views()

        # 设置窗口关闭事件处理
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # 启动自动保存线程
        self.auto_save_thread = threading.Thread(target=self.auto_save_course_list, daemon=True)
        self.auto_save_thread.start()

        self.window_minimized = False
        self.last_window_state = None
        self.window_state_debounce_id = None

        # 绑定窗口状态变化事件
        self.root.bind("<Configure>", self.on_window_configure)
        self.root.bind("<Unmap>", self.on_window_minimize)
        self.root.bind("<Map>", self.on_window_restore)

