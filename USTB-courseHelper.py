import copy
import os
import orjson
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from PIL import Image, ImageTk
from io import BytesIO
import requests
import threading
import time
from datetime import datetime, time as clock_time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import sys
from academic_term import get_current_academic_term
from browser_driver import (
    ChromeDriverWarmup,
    create_chrome_driver,
    is_qr_login_expired,
)
from course_query import (
    DISPLAY_COLUMNS,
    CourseSearchCriteria,
    CourseSearchResult,
    build_course_query_payload,
    extract_course_search_results,
)
from rush_schedule import get_scheduled_start
from rush_list_store import RushListStore, RushListStoreError, SavedRushList
from multi_user_runtime import (
    MultiUserRuntime,
    UserLoginState,
    UserRuntimeContext,
    UserTaskState,
)
from user_profiles import UserProfile, UserProfileStore, UserProfileStoreError
from file_logging import DailyLogWriter
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
from collections import defaultdict

# 旧版测试兼容状态；真实应用路径使用 MultiUserRuntime 中的用户上下文。
qr_image_url = None
current_img_data = None
stop_display = False
login_success = False
final_cookies_dict = {}  # 存储提取的 cookies
course_data_list = []  # 存储课程数据（含优先级）
course_id_count=0
selection_running = False  # 是否正在抢课
stop_selection = False     # 是否请求停止
online_thread_running = False  # 是否正在运行online线程gio

SELECTION_RESULT_CODE_STATUSES: dict[str, str] = {
    "XKGL.OPERATE.RESULT_YCGDWRL": "课程容量已满",
    "XKGL.OPERATE.RESULT_YCGZRL": "课程容量已满",
    "XKGL.OPERATE.RESULT_XKSJCTDQRWHCTRWH": "不符合选课要求",
}
SELECTION_MESSAGE_STATUSES: tuple[tuple[str, str], ...] = (
    ("不在设定的选课时间范围内", "不在设定的选课时间范围内"),
    ("选课成功", "选课成功"),
    ("课程容量已满", "课程容量已满"),
    ("不符合选课要求", "不符合选课要求"),
    ("对外容量已满", "课程容量已满"),
    ("总容量已满", "课程容量已满"),
    ("容量已满", "课程容量已满"),
    ("上课时间冲突", "不符合选课要求"),
)


def classify_selection_response(response_text: str) -> str:
    """按结果码和消息文本归一抢课接口的业务状态。

    Args:
        response_text: 抢课接口返回的原始文本。

    Returns:
        四种已知业务状态之一；无法识别时返回“未知响应”。
    """
    normalized = response_text.strip()
    message_text = normalized
    try:
        parsed_response: object = orjson.loads(normalized)
    except orjson.JSONDecodeError:
        parsed_response = None
    if isinstance(parsed_response, dict):
        result_code = parsed_response.get("gjhczztm")
        if isinstance(result_code, str):
            mapped_status = SELECTION_RESULT_CODE_STATUSES.get(result_code.strip())
            if mapped_status is not None:
                return mapped_status
        response_message = parsed_response.get("message")
        if isinstance(response_message, str):
            message_text = response_message.strip()
    for message_fragment, business_status in SELECTION_MESSAGE_STATUSES:
        if message_fragment in message_text:
            return business_status
    return "未知响应"

# 将 print 输出按日写入 logs，同时保留源码运行时的终端回显。
if not isinstance(sys.stdout, DailyLogWriter):
    sys.stdout = DailyLogWriter(os.path.dirname(__file__), terminal=sys.stdout)

# 主应用类
class CourseSelectionApp:
    def __init__(self: "CourseSelectionApp", root: tk.Tk) -> None:
        """
        初始化课程助手窗口、工作区页面与后台任务。

        Args:
            root: Tkinter 根窗口。

        Returns:
            None: 界面与应用状态直接绑定到当前实例。
        """
        self.target_text = ""
        self.root = root
        self.root.title("北京科技大学选课助手-zby")
        self.root.geometry("1280x860")
        self.root.minsize(1120, 720)
        self.root.configure(bg="#f3f6f9")

        self.course_cache = {}
        self.cache_file = os.path.join(os.path.dirname(__file__), "course_cache.json")
        # 移除单一课程列表文件，改为按人员保存
        # self.course_list_file = os.path.join(os.path.dirname(__file__), "course_list.json")
        self.switch_timer = None

        self.profile_store = UserProfileStore(os.path.dirname(__file__))
        self.runtime = MultiUserRuntime(self.profile_store)
        self.rush_list_store = RushListStore(
            os.path.dirname(__file__), path_resolver=self.profile_store.named_lists_path
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

        # 尽早启动 Selenium Manager，界面构建与驱动匹配并行执行。
        self.configure_browser()
        self.browser_driver_warmup = ChromeDriverWarmup()
        self.start_browser_driver_warmup()

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

        self.rush_tab = ttk.Frame(self.tab_control, style="TFrame")
        self.tab_control.add(self.rush_tab, text="抢课任务")
        self.setup_rush_tab()

        self.tab_control.pack(expand=1, fill="both")
        self.show_initial_view()
        self.refresh_user_views()

         # 设置窗口关闭事件处理
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # 启动自动保存线程
        self.auto_save_thread = threading.Thread(target=self.auto_save_course_list, daemon=True)
        self.auto_save_thread.start()

        self.window_minimized = False
        self.window_resizing = False
        self.last_window_state = None
        self.window_state_debounce_id = None

        # 绑定窗口状态变化事件
        self.root.bind("<Configure>", self.on_window_configure)
        self.root.bind("<Unmap>", self.on_window_minimize)
        self.root.bind("<Map>", self.on_window_restore)

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

    def toggle_user_management(self: "CourseSelectionApp") -> None:
        """打开独立账号管理窗口。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 管理窗口存在时将其提升到前台。
        """
        self.open_user_manager()

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

    def start_current_user_login(self: "CourseSelectionApp") -> None:
        """从全局顶栏为当前用户启动扫码登录。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 无当前用户时进入首次扫码视图。
        """
        profile_id = self.current_profile_id()
        if profile_id is None:
            self.show_login_view()
            self.start_quick_login()
            return
        self._start_login_for_profile(profile_id)

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
        self.tab_control.tab(self.course_search_tab, state="normal")
        self.tab_control.tab(self.rush_tab, state="normal")
        self.tab_control.select(self.rush_tab)
        self.show_login_view()

    def enable_workspace_tabs(self: "CourseSelectionApp") -> None:
        """
        启用登录后的两个工作区页面并显示抢课任务页。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 页签状态直接写入 Notebook 控件。
        """
        self.tab_control.tab(self.course_search_tab, state="normal")
        self.tab_control.tab(self.rush_tab, state="normal")
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
        self.tab_control.tab(self.course_search_tab, state="disabled")
        self.tab_control.tab(self.rush_tab, state="disabled")
        self.show_login_view()

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

    def show_rush_tab(self: "CourseSelectionApp") -> None:
        """
        显示抢课任务页面。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: Notebook 当前页面切换为抢课任务页。
        """
        self.show_workspace()
        self.tab_control.select(self.rush_tab)

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
        return context.profile.id if context is not None else None

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
        return course_data_list

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
        return dict(final_cookies_dict)

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
            return bool(selection_running)
        return context.task_state in {
            UserTaskState.WAITING,
            UserTaskState.RUNNING,
            UserTaskState.STOPPING,
        }

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
        contexts = self.runtime.enabled_contexts()
        active_profiles = [context.profile for context in contexts]
        warmup = getattr(self, "browser_driver_warmup", None)
        driver_completed = warmup.status()[0] if warmup is not None else True
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
                if getattr(self, "_quick_login_active", False) or not driver_completed
                else tk.NORMAL
            )
        for widget_name in ("add_user_btn", "manager_login_btn"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.config(
                    state=tk.NORMAL if driver_completed else tk.DISABLED
                )
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
                        or not driver_completed
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
                    state=tk.NORMAL if driver_completed else tk.DISABLED,
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
            warmup = getattr(self, "browser_driver_warmup", None)
            driver_completed = warmup.status()[0] if warmup is not None else True
            self.account_menu.add_separator()
            self.account_menu.add_command(
                label="添加其他账号",
                command=self.add_user_profile,
                state=tk.NORMAL if driver_completed else tk.DISABLED,
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
        running = self.is_context_task_active(context)
        logged_in = context.login_state is UserLoginState.LOGGED_IN and bool(context.cookies)
        self._set_rush_controls_enabled(not running)
        self.start_auto_btn.config(state=tk.NORMAL if logged_in and not running else tk.DISABLED)
        self.stop_auto_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.add_course_btn.config(state=tk.NORMAL if logged_in else tk.DISABLED)
        self.add_selected_course_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.remove_course_btn.config(state=tk.DISABLED if running else tk.NORMAL)

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

    def configure_visual_theme(self: "CourseSelectionApp") -> None:
        """
        配置课程助手统一的颜色、字体和控件视觉层级。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 样式直接注册到 Tkinter ttk 主题系统。
        """
        self.style.theme_use("clam")
        self.style.configure(".", font=("Microsoft YaHei UI", 10))
        self.style.configure("TFrame", background="#ffffff")
        self.style.configure("App.TFrame", background="#f3f6f9")
        self.style.configure("TLabel", background="#ffffff", foreground="#1f2937")
        self.style.configure(
            "Title.TLabel",
            background="#f3f6f9",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 20, "bold"),
        )
        self.style.configure(
            "LoginTitle.TLabel",
            background="#ffffff",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 22, "bold"),
        )
        self.style.configure(
            "Section.TLabel",
            background="#ffffff",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.style.configure(
            "SurfaceMuted.TLabel",
            background="#ffffff",
            foreground="#64748b",
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "ListName.TLabel",
            background="#ffffff",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.style.configure(
            "Dirty.TLabel",
            background="#fff7ed",
            foreground="#c2410c",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(8, 4),
        )
        self.style.configure(
            "Clean.TLabel",
            background="#ecfdf5",
            foreground="#047857",
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=(8, 4),
        )
        self.style.configure(
            "Header.TLabel",
            background="#f3f6f9",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        self.style.configure(
            "Muted.TLabel",
            background="#f3f6f9",
            foreground="#64748b",
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "Status.TLabel",
            background="#e0f2fe",
            foreground="#075985",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(12, 8),
        )
        self.style.configure(
            "GuideNumber.TLabel",
            background="#ecfdf5",
            foreground="#0f766e",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(8, 5),
        )
        self.style.configure(
            "GuideTitle.TLabel",
            background="#ffffff",
            foreground="#102a43",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.configure(
            "GuideBody.TLabel",
            background="#ffffff",
            foreground="#64748b",
            font=("Microsoft YaHei UI", 9),
        )
        self.style.configure(
            "GuideHint.TLabel",
            background="#f0fdfa",
            foreground="#115e59",
            font=("Microsoft YaHei UI", 9),
            padding=(10, 8),
        )
        self.style.configure(
            "TLabelframe",
            background="#ffffff",
            borderwidth=1,
            relief="solid",
            bordercolor="#dbe4ee",
        )
        self.style.configure(
            "TLabelframe.Label",
            background="#ffffff",
            foreground="#0f766e",
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.style.configure(
            "TNotebook",
            background="#f3f6f9",
            borderwidth=0,
            tabmargins=(16, 12, 16, 0),
        )
        self.style.configure(
            "TNotebook.Tab",
            background="#e5edf5",
            foreground="#52616f",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(20, 11),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", "#ffffff"), ("active", "#dbeafe")],
            foreground=[("selected", "#0f766e"), ("active", "#0f172a")],
        )
        self.style.configure(
            "TEntry",
            fieldbackground="#ffffff",
            foreground="#1f2937",
            bordercolor="#cbd5e1",
            lightcolor="#cbd5e1",
            darkcolor="#cbd5e1",
            padding=(8, 6),
        )
        self.style.configure(
            "TCombobox",
            fieldbackground="#ffffff",
            foreground="#1f2937",
            bordercolor="#cbd5e1",
            padding=(6, 4),
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#ffffff")],
            foreground=[("readonly", "#1f2937")],
        )
        self.style.configure(
            "Primary.TButton",
            background="#0f766e",
            foreground="#ffffff",
            borderwidth=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(14, 8),
        )
        self.style.map(
            "Primary.TButton",
            background=[("active", "#115e59"), ("disabled", "#94a3b8")],
            foreground=[("disabled", "#e2e8f0")],
        )
        self.style.configure(
            "Secondary.TButton",
            background="#ffffff",
            foreground="#0f766e",
            borderwidth=1,
            relief="solid",
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(14, 8),
        )
        self.style.map(
            "Secondary.TButton",
            background=[("active", "#ccfbf1"), ("disabled", "#f1f5f9")],
            foreground=[("disabled", "#94a3b8")],
        )
        self.style.configure(
            "Danger.TButton",
            background="#dc2626",
            foreground="#ffffff",
            borderwidth=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            padding=(14, 8),
        )
        self.style.map(
            "Danger.TButton",
            background=[("active", "#b91c1c"), ("disabled", "#fca5a5")],
            foreground=[("disabled", "#fef2f2")],
        )
        self.style.configure(
            "Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#243447",
            rowheight=32,
            bordercolor="#dbe4ee",
            relief="solid",
        )
        self.style.map(
            "Treeview",
            background=[("selected", "#ccfbf1")],
            foreground=[("selected", "#134e4a")],
        )
        self.style.configure(
            "Treeview.Heading",
            background="#eaf0f6",
            foreground="#334155",
            font=("Microsoft YaHei UI", 9, "bold"),
            relief="flat",
            padding=(8, 8),
        )
        self.style.configure(
            "Mode.TRadiobutton",
            background="#ffffff",
            foreground="#334155",
            padding=(6, 4),
        )
        self.style.map(
            "Mode.TRadiobutton",
            foreground=[("selected", "#0f766e"), ("disabled", "#94a3b8")],
        )

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
        except Exception as e:
            print(f"检查窗口状态时出错: {e}")

    def _schedule_treeview_refresh(self):
        """计划Treeview刷新，避免频繁重绘"""
        if hasattr(self, '_treeview_refresh_id'):
            self.root.after_cancel(self._treeview_refresh_id)

        self._treeview_refresh_id = self.root.after(50, self._refresh_treeview)

    def _refresh_treeview(self):
        """刷新Treeview显示"""
        try:
            if hasattr(self, 'course_tree'):
                self.course_tree.update_idletasks()

                width = self.course_tree.winfo_width()
                if width > 100:
                    col_width = max(50, width // 6 - 10)
                    for col in self.course_tree["columns"]:
                        self.course_tree.column(col, width=col_width)
        except Exception as e:
            print(f"刷新Treeview时出错: {e}")

    def load_course_cache(self):
        """加载课程缓存"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.course_cache = orjson.loads(f.read())
                print(f"✅ 已加载 {len(self.course_cache)} 条课程缓存")
        except Exception as e:
            print(f"⚠️ 加载课程缓存失败: {e}")
            self.course_cache = {}

    def save_course_cache(self):
        """保存课程缓存"""
        try:
            with open(self.cache_file, 'wb') as f:
                f.write(orjson.dumps(self.course_cache, option=orjson.OPT_INDENT_2))
            print(f"💾 已保存 {len(self.course_cache)} 条课程缓存")
        except Exception as e:
            print(f"⚠️ 保存课程缓存失败: {e}")

    def get_cached_course(self, course_id, semester):
        """获取缓存的课程信息 - 修改版：返回课程列表"""
        cache_key = f"{semester}_{course_id}"
        cached_data = self.course_cache.get(cache_key)

        # 兼容旧版本：如果缓存是单个dict，转换为list
        if cached_data and isinstance(cached_data, dict):
            return [cached_data]

        return cached_data  # 返回列表或None

    def cache_course_info(self, course_id, semester, course_info):
        """缓存课程信息 - 修改版：支持同一课程ID的多门课"""
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

    def get_student_course_file(self, student_name):
        """获取指定学生的课程列表文件路径"""
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
        global course_data_list, course_id_count
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
            course_data_list = []
            course_id_count = 0
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
                            course_data_list = valid_courses
                            # 设置计数器为最大ID
                            course_id_count = max(course["id"] for course in valid_courses)
                            self.update_course_list()
                            print(f"✅ 已加载 {student_name} 的 {len(course_data_list)} 门课程")
                        else:
                            print(f"⚠️ {student_name} 的课程列表数据无效，已清空")
                            course_data_list = []
                            course_id_count = 0
                            self.update_course_list()
                    else:
                        # 空列表处理
                        print(f"ℹ️ {student_name} 暂无保存的课程")
                        course_data_list = []
                        course_id_count = 0
                        self.update_course_list()
            else:
                print(f"ℹ️ {student_name} 是新用户，暂无课程记录")
                course_data_list = []
                course_id_count = 0
                self.update_course_list()
        except Exception as e:
            print(f"⚠️ 加载 {student_name} 的课程列表失败: {e}")
            course_data_list = []
            course_id_count = 0
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
        global course_data_list
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
            for course in course_data_list:
                save_course = course.copy()
                if "id" in save_course:
                    del save_course["id"]
                save_list.append(save_course)

            with open(course_file, 'wb') as f:
                f.write(orjson.dumps(save_list, option=orjson.OPT_INDENT_2))
            print(f"💾 已保存 {student_name} 的 {len(course_data_list)} 门课程")
            return True
        except Exception as e:
            print(f"⚠️ 保存 {student_name} 的课程列表失败: {e}")
            return False

    # def on_student_name_change(self, *args):
    #     """学生姓名变化时的回调函数"""
    #     if self.student_switch_lock:
    #         return

    #     new_name = self.student_name_var.get().strip()

    #     # 如果名字没有实质性变化，不处理
    #     if new_name == self.current_student_name:
    #         return

    #     # 如果正在抢课，不允许切换
    #     if selection_running:
    #         messagebox.showwarning("警告", "正在抢课中，无法切换人员！")
    #         self.student_switch_lock = True
    #         self.student_name_var.set(self.current_student_name)
    #         self.student_switch_lock = False
    #         return

    #     # 保存当前人员的课程列表（如果有）
    #     if self.current_student_name:
    #         print(f"💾 切换人员：保存 {self.current_student_name} 的课程列表")
    #         self.save_course_list(self.current_student_name)

    #     # 更新当前人员
    #     old_name = self.current_student_name
    #     self.current_student_name = new_name

    #     # 加载新人员的课程列表
    #     if new_name:
    #         print(f"📂 切换人员：加载 {new_name} 的课程列表")
    #         self.load_saved_course_list(new_name)
    #         self.status_var.set(f"已切换到 {new_name}")
    #     else:
    #         # 如果清空了姓名，也清空课程列表
    #         global course_data_list, course_id_count
    #         course_data_list = []
    #         course_id_count = 0
    #         self.update_course_list()
    #         self.status_var.set("请输入抢课人员姓名")
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
        if selection_running:
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
            global course_data_list, course_id_count
            course_data_list = []
            course_id_count = 0
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
            if selection_running or stop_selection:
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
        global stop_display, stop_selection
        stop_display = True
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

        if selection_running:
            stop_selection = True
            print("🛑 正在停止选课进程...")
            time.sleep(1)

        self.stop_online_keepalive()

        # 保存当前人员的课程列表
        if self.current_student_name:
            self.save_course_list()
        print("👋 程序即将关闭，已保存数据")
        self.root.destroy()

    def configure_browser(self: "CourseSelectionApp") -> None:
        """配置后台预热和扫码登录共用的 Chrome 启动选项。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: Chrome 选项保存在当前应用实例中。
        """
        self.chrome_options = Options()
        self.chrome_options.add_argument("--headless=new")
        self.chrome_options.add_argument("--disable-gpu")
        self.chrome_options.add_argument("--no-sandbox")
        self.chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        self.chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])

    def start_browser_driver_warmup(self: "CourseSelectionApp") -> None:
        """在应用启动时并行预匹配 ChromeDriver。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 后台线程引用保存在当前应用实例中。
        """
        self.browser_driver_warmup_thread = threading.Thread(
            target=self._run_browser_driver_warmup,
            daemon=True,
        )
        self.browser_driver_warmup_thread.start()

    def _run_browser_driver_warmup(self: "CourseSelectionApp") -> None:
        """执行后台驱动预热并记录结果，且不阻塞界面线程。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 预热结果保存在 ChromeDriverWarmup 中。
        """
        print("正在后台匹配 ChromeDriver，程序界面可继续使用...")
        self.browser_driver_warmup.run(self.chrome_options)
        try:
            self.browser_driver_warmup.wait()
        except RuntimeError as error:
            print(f"后台匹配 ChromeDriver 失败：{error}")
        else:
            print("ChromeDriver 后台匹配完成，后续扫码登录将复用缓存")

    def refresh_browser_driver_status(self: "CourseSelectionApp") -> None:
        """在主线程刷新登录页的浏览器驱动准备状态。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 未完成时继续轮询，完成后更新提示和登录按钮状态。
        """
        completed, error_message = self.browser_driver_warmup.status()
        if not completed:
            self.status_var.set("浏览器驱动准备中...")
            self.reset_qr_display("浏览器驱动准备中...")
            self.quick_login_btn.config(state=tk.DISABLED)
            self.global_login_btn.config(state=tk.DISABLED)
            self._browser_driver_status_after_id = self.root.after(
                100, self.refresh_browser_driver_status
            )
            return
        self._browser_driver_status_after_id = None
        if error_message is None:
            self.status_var.set("正在生成登录二维码...")
            self.reset_qr_display("正在生成登录二维码...")
            self.quick_login_btn.grid_remove()
            self.schedule_automatic_login()
        else:
            self.status_var.set("浏览器驱动准备失败，请重试")
            self.reset_qr_display("浏览器驱动准备失败，请重试")
            self.quick_login_btn.grid(row=0, column=0)
        self.refresh_user_views()

    def schedule_automatic_login(self: "CourseSelectionApp") -> None:
        """在界面构建完成后安排一次自动扫码登录。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 已安排、已启动或选择离线时不会重复加入回调。
        """
        if (
            self._automatic_login_scheduled
            or self._automatic_login_started
            or self._automatic_login_suppressed
        ):
            return
        self._automatic_login_scheduled = True
        self.root.after(0, self.start_automatic_login)

    def start_automatic_login(self: "CourseSelectionApp") -> None:
        """无需确认步骤地启动当前登录页目标账号的扫码流程。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 同一轮自动登录最多启动一次，离线模式下不启动。
        """
        self._automatic_login_scheduled = False
        if self._automatic_login_started or self._automatic_login_suppressed:
            return
        self._automatic_login_started = True
        self.start_login()

    def setup_login_tab(self: "CourseSelectionApp") -> None:
        """
        构建单账号优先的串行二维码登录页面。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 多账号选择仅在存在多个启用账号时显示。
        """
        page = ttk.Frame(self.login_tab, style="App.TFrame", padding=(24, 18))
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)

        header = ttk.Frame(page, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="扫码登录", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.status_var = tk.StringVar(value="浏览器驱动准备中...")
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(
            row=0, column=1, sticky="e"
        )

        self.login_target_frame = ttk.LabelFrame(
            page, text="选择登录账号", padding=(12, 10)
        )
        self.login_target_frame.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.login_target_frame.columnconfigure(1, weight=1)
        ttk.Label(self.login_target_frame, text="账号").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.login_target_var = self.student_name_var
        self.login_target_combo = ttk.Combobox(
            self.login_target_frame,
            textvariable=self.login_target_var,
            state="readonly",
            width=24,
        )
        self.login_target_combo.grid(row=0, column=1, sticky="w")
        self.login_target_combo.bind(
            "<<ComboboxSelected>>", self.select_user_by_alias
        )

        self.quick_login_frame = ttk.Frame(page, style="App.TFrame", padding=(8, 8))
        self.quick_login_frame.grid(row=2, column=0, sticky="nsew")
        self.quick_login_frame.columnconfigure(0, weight=1)
        self.quick_login_frame.rowconfigure(0, weight=1)
        self.quick_qr_frame = ttk.LabelFrame(
            self.quick_login_frame, text="扫码登录", padding=(24, 24)
        )
        self.quick_qr_frame.grid(row=0, column=0)
        self.quick_qr_frame.configure(width=400, height=430)
        self.quick_qr_frame.grid_propagate(False)
        self.quick_qr_frame.columnconfigure(0, weight=1)
        self.quick_qr_frame.rowconfigure(0, weight=1)
        self.quick_qr_label = ttk.Label(
            self.quick_qr_frame,
            text="浏览器驱动准备中...",
            anchor="center",
            justify=tk.CENTER,
            style="SurfaceMuted.TLabel",
        )
        self.quick_qr_label.grid(row=0, column=0, sticky="nsew")
        self.qr_label = self.quick_qr_label

        login_actions = ttk.Frame(self.quick_login_frame, style="App.TFrame")
        login_actions.grid(row=1, column=0, pady=(16, 0))
        self.quick_login_btn = ttk.Button(
            login_actions,
            text="重新生成二维码",
            command=self.start_login,
            style="Primary.TButton",
        )
        self.quick_login_btn.grid(row=0, column=0)
        self.quick_login_btn.grid_remove()
        self.login_btn = self.quick_login_btn
        self.offline_workspace_btn = ttk.Button(
            login_actions,
            text="暂不登录，离线进入",
            command=self.enter_offline_workspace,
            style="Secondary.TButton",
        )
        self.offline_workspace_btn.grid(row=0, column=1, padx=(10, 0))
        self.login_target_frame.grid_remove()
        self.refresh_browser_driver_status()

    def show_login_management(self: "CourseSelectionApp") -> None:
        """打开独立账号管理窗口。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 已存在的管理窗口会被提升到前台。
        """
        self.open_user_manager()

    def show_quick_login(self: "CourseSelectionApp") -> None:
        """显示精简扫码页面。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 工作区隐藏且扫码主区域保持显示。
        """
        self.show_login_view()
        self.quick_login_frame.grid(row=2, column=0, sticky="nsew")

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
        warmup = getattr(self, "browser_driver_warmup", None)
        if warmup is not None and not warmup.status()[0]:
            self.status_var.set("浏览器驱动准备中...")
            return
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

    def deactivate_user_profile(self: "CourseSelectionApp") -> None:
        """软删除选中用户并保留课程数据。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 用户会从默认选择器隐藏但可恢复。
        """
        profile_id = self.selected_profile_id_from_table()
        if profile_id is None:
            return
        context = self.runtime.require_context(profile_id)
        if self.is_context_task_active(context):
            messagebox.showwarning("操作已锁定", "请先停止该用户的抢课任务")
            return
        if not messagebox.askyesno("确认停用", f"停用用户“{context.profile.alias}”并保留课程数据吗？"):
            return
        try:
            self.profile_store.deactivate(profile_id)
            context.cookies.clear()
            context.login_state = UserLoginState.NOT_LOGGED_IN
            self.runtime.sync_profiles()
        except UserProfileStoreError as error:
            messagebox.showerror("停用失败", str(error))
            return
        self.refresh_user_views()
        active_context = self.runtime.active_context()
        if active_context is None:
            self.disable_workspace_tabs()
        else:
            self.student_name_var.set(active_context.profile.alias)
            self.select_user_by_alias()

    def restore_user_profile(self: "CourseSelectionApp") -> None:
        """恢复选中的已停用用户档案。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 用户恢复后重新出现在选择器中。
        """
        profile_id = self.selected_profile_id_from_table()
        if profile_id is None:
            return
        try:
            self.profile_store.restore(profile_id)
            self.runtime.sync_profiles()
        except UserProfileStoreError as error:
            messagebox.showerror("恢复失败", str(error))
            return
        self.refresh_user_views()
        active_context = self.runtime.active_context()
        if active_context is not None:
            self.student_name_var.set(active_context.profile.alias)
            self.select_user_by_alias()
            self.enable_workspace_tabs()

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

    def setup_course_search_tab(self: "CourseSelectionApp") -> None:
        """
        构建课程条件查询与结果选择页面。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 查询控件与结果表直接创建在课程查询页中。
        """
        course_frame = ttk.Frame(
            self.course_search_tab,
            style="App.TFrame",
            padding=(28, 20),
        )
        course_frame.pack(fill="both", expand=True)
        course_frame.columnconfigure(0, weight=1)
        course_frame.rowconfigure(2, weight=1)

        header = ttk.Frame(course_frame, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="课程查询", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            header,
            text="查看抢课任务",
            command=self.show_rush_tab,
            style="Secondary.TButton",
        ).grid(row=0, column=1, sticky="e")

        input_frame = ttk.LabelFrame(course_frame, text="查询条件", padding=12)
        input_frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        for column_index in range(5):
            input_frame.columnconfigure(column_index, weight=1)

        self.course_type_var = tk.StringVar(value="所有")
        self.course_id_var = tk.StringVar()
        self.course_name_var = tk.StringVar()
        self.semester_var = tk.StringVar(
            value=get_current_academic_term(datetime.now().date())
        )
        self.priority_var = tk.StringVar(value="1")

        ttk.Label(input_frame, text="课程类型").grid(row=0, column=0, sticky="w")
        self.course_type_combo = ttk.Combobox(
            input_frame,
            textvariable=self.course_type_var,
            values=["所有", "素质扩展课", "专业扩展课", "MOOC", "必修课"],
            state="readonly",
            width=16,
        )
        self.course_type_combo.grid(
            row=1, column=0, sticky="ew", padx=(0, 12), pady=(5, 0)
        )
        ttk.Label(input_frame, text="课程代码").grid(row=0, column=1, sticky="w")
        ttk.Entry(input_frame, textvariable=self.course_id_var).grid(
            row=1, column=1, sticky="ew", padx=(0, 12), pady=(5, 0)
        )
        ttk.Label(input_frame, text="课程名称").grid(row=0, column=2, sticky="w")
        ttk.Entry(input_frame, textvariable=self.course_name_var).grid(
            row=1, column=2, sticky="ew", padx=(0, 12), pady=(5, 0)
        )
        ttk.Label(input_frame, text="学年学期").grid(row=0, column=3, sticky="w")
        ttk.Entry(input_frame, textvariable=self.semester_var).grid(
            row=1, column=3, sticky="ew", padx=(0, 12), pady=(5, 0)
        )
        self.add_course_btn = ttk.Button(
            input_frame,
            text="查询课程",
            command=self.search_courses,
            style="Primary.TButton",
        )
        self.add_course_btn.grid(row=1, column=4, sticky="ew", pady=(5, 0))

        result_frame = ttk.LabelFrame(course_frame, text="课程查询结果", padding=8)
        result_frame.grid(row=2, column=0, sticky="nsew")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(1, weight=1)

        result_toolbar = ttk.Frame(result_frame, style="TFrame")
        result_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        result_toolbar.columnconfigure(0, weight=1)
        self.search_count_var = tk.StringVar(value="尚未查询")
        self.search_type_summary_var = tk.StringVar(value="")
        ttk.Label(
            result_toolbar,
            textvariable=self.search_count_var,
            style="SurfaceMuted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            result_toolbar,
            textvariable=self.search_type_summary_var,
            style="SurfaceMuted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(result_toolbar, text="优先级").grid(
            row=0, column=1, padx=(12, 6)
        )
        ttk.Spinbox(
            result_toolbar,
            from_=1,
            to=99,
            textvariable=self.priority_var,
            width=5,
        ).grid(row=0, column=2)
        self.add_selected_course_btn = ttk.Button(
            result_toolbar,
            text="添加选中课程",
            command=self.add_selected_courses,
            style="Secondary.TButton",
        )
        self.add_selected_course_btn.grid(row=0, column=3, padx=(12, 0))
        self.search_priority_help_label = ttk.Label(
            result_toolbar,
            text="优先级说明：数字越小越先尝试；相同数字按列表顺序执行。",
            style="SurfaceMuted.TLabel",
        )
        self.search_priority_help_label.grid(
            row=1, column=1, columnspan=3, sticky="e", pady=(5, 0)
        )

        result_columns = tuple(field_name for field_name, _ in DISPLAY_COLUMNS)
        self.course_result_tree = ttk.Treeview(
            result_frame,
            columns=result_columns,
            show="headings",
            selectmode="extended",
        )
        column_widths = {
            "display_name": 180,
            "course_code": 110,
            "course_name": 180,
            "course_nature": 90,
            "course_category": 230,
            "teaching_language": 90,
            "scoring_method": 90,
            "credit": 65,
            "class_hours": 65,
            "schedule": 280,
            "capacity_selected": 100,
            "offering_college": 180,
            "campus": 90,
        }
        for field_name, column_title in DISPLAY_COLUMNS:
            self.course_result_tree.heading(field_name, text=column_title)
            self.course_result_tree.column(
                field_name,
                width=column_widths[field_name],
                minwidth=60,
                stretch=False,
            )
        self.search_results_by_task_id: dict[str, CourseSearchResult] = {}
        self.search_result_course_types_by_task_id: dict[str, str] = {}
        result_scrollbar_y = ttk.Scrollbar(
            result_frame, orient="vertical", command=self.course_result_tree.yview
        )
        result_scrollbar_x = ttk.Scrollbar(
            result_frame, orient="horizontal", command=self.course_result_tree.xview
        )
        self.course_result_tree.configure(
            yscrollcommand=result_scrollbar_y.set,
            xscrollcommand=result_scrollbar_x.set,
        )
        self.course_result_tree.grid(row=1, column=0, sticky="nsew")
        result_scrollbar_y.grid(row=1, column=1, sticky="ns")
        result_scrollbar_x.grid(row=2, column=0, sticky="ew")

    def setup_rush_tab(self: "CourseSelectionApp") -> None:
        """
        构建人员配置、待抢课程与任务操作页面。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 抢课相关控件直接创建在抢课任务页中。
        """
        page = ttk.Frame(self.rush_tab, style="App.TFrame", padding=(22, 14))
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(3, weight=3)

        header = ttk.Frame(page, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="抢课任务", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(
            row=0, column=1, sticky="e"
        )

        current_list_bar = ttk.Frame(page, style="TFrame", padding=(12, 9))
        current_list_bar.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        current_list_bar.columnconfigure(1, weight=1)
        ttk.Label(current_list_bar, text="当前列表", style="SurfaceMuted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
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
            command=self.begin_current_list_rename,
            style="Secondary.TButton",
        )
        self.edit_current_list_name_btn.grid(row=0, column=2, padx=(8, 0))
        self.inline_list_name_var = tk.StringVar(value="")
        self.inline_list_name_frame = ttk.Frame(current_list_bar, style="TFrame")
        ttk.Entry(
            self.inline_list_name_frame,
            textvariable=self.inline_list_name_var,
            width=24,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            self.inline_list_name_frame,
            text="确认",
            command=self.confirm_current_list_rename,
            style="Primary.TButton",
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            self.inline_list_name_frame,
            text="取消",
            command=self.cancel_current_list_rename,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT)
        ttk.Label(
            current_list_bar,
            textvariable=self.current_list_note_var,
            style="SurfaceMuted.TLabel",
        ).grid(row=1, column=1, sticky="w", pady=(3, 0))
        self.current_list_dirty_label = ttk.Label(
            current_list_bar,
            textvariable=self.current_list_dirty_var,
            style="Dirty.TLabel",
        )
        self.current_list_dirty_label.grid(row=0, column=3, rowspan=2, padx=(12, 10))
        self.save_rush_list_btn = ttk.Button(
            current_list_bar,
            text="保存列表",
            command=self.open_save_rush_list_dialog,
            style="Primary.TButton",
        )
        self.save_rush_list_btn.grid(row=0, column=4, rowspan=2, padx=(0, 8))
        self.manage_rush_lists_btn = ttk.Button(
            current_list_bar,
            text="管理列表",
            command=self.open_rush_list_manager,
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
                command=self.on_mode_change,
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
        self.rush_time_frame.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
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
            command=self.show_course_search_tab,
            style="Secondary.TButton",
        )
        self.task_add_course_btn.grid(row=0, column=1, padx=(0, 8))
        self.remove_course_btn = ttk.Button(
            list_toolbar,
            text="删除选中课程",
            command=self.remove_course,
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
        self.task_empty_label = ttk.Label(
            table_body,
            text="暂无待抢课程",
            style="SurfaceMuted.TLabel",
        )
        self.task_empty_add_btn = ttk.Button(
            table_body,
            text="添加课程",
            command=self.show_course_search_tab,
            style="Primary.TButton",
        )

        actions = ttk.Frame(page, style="App.TFrame")
        actions.grid(row=4, column=0, sticky="ew", pady=(8, 8))
        actions.columnconfigure(0, weight=1)
        self.start_auto_btn = ttk.Button(
            actions,
            text="开始轮询",
            command=self.start_selection,
            style="Primary.TButton",
        )
        self.start_auto_btn.grid(row=0, column=1, padx=(8, 8))
        self.stop_auto_btn = ttk.Button(
            actions,
            text="停止抢课",
            command=self.stop_auto_selection,
            state=tk.DISABLED,
            style="Danger.TButton",
        )
        self.stop_auto_btn.grid(row=0, column=2)

        self.refresh_current_list_status()
        self.update_course_list()

    def refresh_current_list_status(self: "CourseSelectionApp") -> None:
        """刷新当前命名列表的名称、备注摘要和保存状态。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 状态条变量和样式会同步更新。
        """
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

    def select_user_from_search_combo(
        self: "CourseSelectionApp", event: object | None = None
    ) -> None:
        """从课程查询页选择器切换当前用户。

        Args:
            self: 当前课程助手应用实例。
            event: Tkinter 下拉选择事件；直接调用时为 None。

        Returns:
            None: 两个工作区用户选择器会保持同步。
        """
        alias = self.current_student_display_var.get().strip()
        if alias:
            self.student_name_var.set(alias)
            self.select_user_by_alias(event)

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
        self.current_saved_list_dirty = bool(course_data_list)
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
        global course_data_list, course_id_count
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
            original_courses = course_data_list
            original_count = course_id_count
            course_data_list = replacement
            course_id_count = len(replacement)
        if not self.save_course_list():
            if context is not None:
                context.courses = original_courses
            else:
                course_data_list = original_courses
                course_id_count = original_count
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

    def start_quick_login(self: "CourseSelectionApp") -> None:
        """为首次使用者创建或复用默认档案并启动扫码登录。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 登录成功后自动进入课程工作区。
        """
        if getattr(self, "_quick_login_active", False):
            return
        profile_id = getattr(self, "_quick_profile_id", None)
        if profile_id is None:
            try:
                profiles = self.profile_store.list_all(include_inactive=True)
                profile = next(
                    (
                        candidate
                        for candidate in profiles
                        if candidate.alias.casefold() == "默认用户".casefold()
                    ),
                    None,
                )
                if profile is None:
                    profile = self.profile_store.create("默认用户")
                elif not profile.active:
                    profile = self.profile_store.restore(profile.id)
                self.runtime.sync_profiles()
                self.runtime.select_profile(profile.id)
                profile_id = profile.id
                self._quick_profile_id = profile_id
            except UserProfileStoreError as error:
                messagebox.showerror("登录准备失败", str(error))
                return
        self._quick_login_active = True
        self.refresh_user_views()
        self._start_login_for_profile(profile_id)

    def _start_login_for_profile(
        self: "CourseSelectionApp", profile_id: str
    ) -> None:
        """为指定用户取得扫码锁并启动后台登录线程。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 登录目标用户 UUID。

        Returns:
            None: 已有其他扫码流程时显示提示并返回。
        """
        warmup = getattr(self, "browser_driver_warmup", None)
        if warmup is not None and not warmup.status()[0]:
            self._automatic_login_started = False
            self.status_var.set("浏览器驱动准备中...")
            self.restore_profile_after_failed_add(profile_id)
            return
        context = self.runtime.require_context(profile_id)
        if not self.runtime.begin_login(profile_id):
            self._automatic_login_started = False
            self.restore_profile_after_failed_add(profile_id)
            messagebox.showwarning("登录进行中", "已有其他账号正在扫码登录，请稍后再试")
            return
        self._automatic_login_started = True
        self._automatic_login_suppressed = False
        self.show_login_view()
        self.reset_qr_display("正在生成登录二维码...")
        self.login_btn.config(state=tk.DISABLED)
        if hasattr(self, "quick_login_btn"):
            self.quick_login_btn.config(state=tk.DISABLED)
            self.quick_login_btn.grid_remove()
        status = (
            f"正在为 {context.profile.alias} 启动登录..."
            if len(self.runtime.enabled_contexts()) > 1
            else "正在启动登录..."
        )
        self.status_var.set(status)
        self.refresh_user_views()
        login_thread = threading.Thread(
            target=self.login_process, args=(profile_id,), daemon=True
        )
        login_thread.start()

    def start_login(self: "CourseSelectionApp") -> None:
        """为登录页当前目标账号启动串行扫码登录流程。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 无账号时先创建或复用内部默认档案。
        """
        if not self.runtime.enabled_contexts():
            self.start_quick_login()
            return
        alias = self.login_target_var.get().strip() if hasattr(self, "login_target_var") else ""
        profile_id = self._user_alias_to_id.get(alias) or self.current_profile_id()
        if profile_id is None:
            messagebox.showerror("错误", "没有可登录的账号")
            return
        if self.current_profile_id() != profile_id:
            self.select_user_by_id(profile_id)
        self._start_login_for_profile(profile_id)

    def login_process(
        self: "CourseSelectionApp", profile_id: str | None = None
    ) -> None:
        """启动浏览器并将提取的 Cookie 绑定到指定用户。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 登录目标用户 UUID；为 None 时使用当前用户。

        Returns:
            None: 登录结果写入目标用户运行时上下文。
        """
        target_id = profile_id or self.current_profile_id()
        if target_id is None:
            return
        context = self.runtime.require_context(target_id)
        browser = None
        last_qr_url = ""
        try:
            self.user_log(target_id, "正在确认后台 ChromeDriver 预匹配结果")
            self.root.after(
                0,
                lambda: self.status_var.set(
                    f"正在为 {context.profile.alias} 准备浏览器驱动"
                ),
            )
            warmup = getattr(self, "browser_driver_warmup", None)
            if warmup is not None:
                warmup.wait()
                self.user_log(target_id, "ChromeDriver 预匹配完成，正在启动登录浏览器")
            browser = create_chrome_driver(self.chrome_options)
            browser.get("https://byyt.ustb.edu.cn/oauth/login/code")
            self.user_log(target_id, "已进入统一身份认证登录页面")
            qr_generated_at = time.monotonic()
            while not stop_display:
                qr_page_text = ""
                try:
                    iframe = browser.find_element(By.TAG_NAME, "iframe")
                    browser.switch_to.frame(iframe)
                    qr_page_text = browser.find_element(By.TAG_NAME, "body").text
                    qr_image = browser.find_element(By.ID, "qrimg")
                    qr_url = qr_image.get_attribute("src") or ""
                    if qr_url and qr_url != last_qr_url:
                        last_qr_url = qr_url
                        qr_generated_at = time.monotonic()
                        response = requests.get(
                            qr_url,
                            headers={"Referer": browser.current_url},
                            timeout=5,
                        )
                        if response.status_code == 200:
                            image = Image.open(BytesIO(response.content))
                            image = image.resize(
                                (300, 300), Image.Resampling.NEAREST
                            )
                            tk_image = ImageTk.PhotoImage(image)
                            self.root.after(
                                0,
                                lambda value=tk_image: self.update_qr_image(
                                    value, context.profile.alias
                                ),
                            )
                    browser.switch_to.default_content()
                except Exception:
                    try:
                        browser.switch_to.default_content()
                    except Exception:
                        pass
                if is_qr_login_expired(qr_page_text, qr_generated_at):
                    self.handle_qr_login_expired(target_id)
                    return
                if "https://byyt.ustb.edu.cn/authentication/main" in browser.current_url:
                    cookies = {
                        item["name"]: item["value"] for item in browser.get_cookies()
                    }
                    self.runtime.finish_login(target_id, cookies)
                    self.user_log(target_id, f"登录成功，已获取 {len(cookies)} 个 Cookie")
                    self.start_online_keepalive()
                    self.root.after(0, lambda: self.finish_login_ui(target_id, True, "登录成功"))
                    return
                time.sleep(0.2)
            self.runtime.fail_login(target_id, "登录已取消")
            self.root.after(0, lambda: self.finish_login_ui(target_id, False, "登录已取消"))
        except Exception as error:
            error_message = f"登录出错：{error}"
            self.runtime.fail_login(target_id, error_message)
            self.user_log(target_id, error_message)
            self.root.after(
                0, lambda message=error_message: self.finish_login_ui(target_id, False, message)
            )
        finally:
            if browser is not None:
                try:
                    browser.quit()
                except Exception:
                    pass

    def handle_qr_login_expired(
        self: "CourseSelectionApp", profile_id: str
    ) -> None:
        """结束失效二维码对应的登录流程并释放全局扫码锁。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 二维码已经失效的用户 UUID。

        Returns:
            None: 用户状态、日志和界面收尾任务会同步更新。
        """
        message = "二维码已过期，请重新扫码登录"
        self.runtime.fail_login(profile_id, message)
        self.user_log(profile_id, "二维码已过期，扫码登录已结束")
        self.root.after(
            0,
            lambda: self.finish_login_ui(profile_id, False, message),
        )

    def reset_qr_display(
        self: "CourseSelectionApp", message: str = "点击扫码登录后显示二维码"
    ) -> None:
        """清除失效二维码图片并恢复可操作提示文本。

        Args:
            self: 当前课程助手应用实例。
            message: 二维码区域需要显示的提示文本。

        Returns:
            None: 已创建的扫码区域会清除图片引用并显示提示。
        """
        for widget_name in ("qr_label", "quick_qr_label"):
            label = getattr(self, widget_name, None)
            if label is None:
                continue
            label.config(image="", text=message)
            label.image = None

    def update_qr_image(
        self: "CourseSelectionApp", image: ImageTk.PhotoImage, alias: str = ""
    ) -> None:
        """在主线程更新当前串行登录流程的二维码。

        Args:
            self: 当前课程助手应用实例。
            image: 已转换的 Tkinter 二维码图片。
            alias: 正在登录的用户别名。

        Returns:
            None: 二维码和状态提示会直接更新。
        """
        self.qr_label.config(image=image, text="")
        self.qr_label.image = image
        if hasattr(self, "quick_qr_label"):
            self.quick_qr_label.config(image=image, text="")
            self.quick_qr_label.image = image
        status = (
            f"请为 {alias} 扫描二维码"
            if len(self.runtime.enabled_contexts()) > 1
            else "请使用手机扫描二维码"
        )
        self.status_var.set(status)

    def restore_profile_after_failed_add(
        self: "CourseSelectionApp", profile_id: str
    ) -> None:
        """在新增账号登录失败时恢复添加前的当前账号。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 本次登录失败的新增账号 UUID。

        Returns:
            None: 非新增账号失败或原账号不存在时只清理临时标记。
        """
        pending_id = getattr(self, "_pending_added_profile_id", None)
        return_id = getattr(self, "_login_return_profile_id", None)
        if pending_id == profile_id and return_id is not None:
            try:
                self.profile_store.get(return_id)
            except UserProfileStoreError:
                pass
            else:
                self.select_user_by_id(return_id)
        self._login_return_profile_id = None
        self._pending_added_profile_id = None

    def finish_login_ui(
        self: "CourseSelectionApp", profile_id: str, succeeded: bool, message: str
    ) -> None:
        """在主线程收尾单个用户登录界面。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 登录目标用户 UUID。
            succeeded: True 表示登录成功。
            message: 登录结果摘要。

        Returns:
            None: 用户表、操作状态和提示会同步更新。
        """
        self.login_btn.config(state=tk.NORMAL)
        if hasattr(self, "quick_login_btn"):
            self.quick_login_btn.config(state=tk.NORMAL)
        self.refresh_user_views()
        context = self.runtime.require_context(profile_id)
        if self.current_profile_id() == profile_id:
            status = (
                f"{context.profile.alias}：{message}"
                if len(self.runtime.enabled_contexts()) > 1
                else message
            )
            self.status_var.set(status)
        if succeeded:
            self._automatic_login_started = False
            self._quick_login_active = False
            self._quick_profile_id = None
            self._login_return_profile_id = None
            self._pending_added_profile_id = None
            self._first_launch = False
            self.refresh_user_views()
            self.enable_workspace_tabs()
        else:
            self._automatic_login_started = False
            self.reset_qr_display(message)
            if hasattr(self, "quick_login_btn"):
                self.quick_login_btn.config(state=tk.NORMAL)
                self.quick_login_btn.grid(row=0, column=0)
            if getattr(self, "_quick_login_active", False):
                self._quick_login_active = False
                self._quick_profile_id = None
            self.restore_profile_after_failed_add(profile_id)
            self.show_quick_login()
            self.refresh_user_views()
            self.status_var.set(message)
            messagebox.showerror("登录失败", message)

    def search_courses(self: "CourseSelectionApp") -> None:
        """
        读取联合筛选条件并在后台查询可选课程。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 查询结果会异步写入课程查询结果表。
        """
        context = self.active_user_context(show_error=True)
        if context is None:
            return
        cookies = dict(context.cookies)
        if context.login_state is not UserLoginState.LOGGED_IN or not cookies:
            messagebox.showerror("错误", "请先登录")
            return

        course_type_codes = {
            "素质扩展课": "sztzk-b-b",
            "专业扩展课": "zytzk-b-b",
            "MOOC": "mooc-b-b",
            "必修课": "bx-b-b",
        }
        selected_course_type = self.course_type_var.get()
        if selected_course_type not in {"所有", *course_type_codes}:
            messagebox.showerror("错误", "课程类型无效")
            return

        criteria = CourseSearchCriteria(
            course_code=self.course_id_var.get().strip(),
            course_name=self.course_name_var.get().strip(),
        )
        try:
            target_course_types = (
                course_type_codes.items()
                if selected_course_type == "所有"
                else ((selected_course_type, course_type_codes[selected_course_type]),)
            )
            payloads = [
                (
                    course_type_code,
                    build_course_query_payload(
                        semester=self.semester_var.get().strip(),
                        course_type_code=course_type_code,
                        criteria=criteria,
                    ),
                )
                for _, course_type_code in target_course_types
            ]
        except ValueError as error:
            messagebox.showerror("错误", str(error))
            return

        self.add_course_btn.config(state=tk.DISABLED)
        self.status_var.set("正在查询课程...")
        threading.Thread(
            target=self.query_course_results,
            args=(context.profile.id, cookies, payloads),
            daemon=True,
        ).start()

    def query_course_results(
        self: "CourseSelectionApp",
        profile_id: str,
        cookies: dict[str, str],
        payloads: list[tuple[str, dict[str, str]]],
    ) -> None:
        """
        使用当前登录会话请求课程结果并安排界面刷新。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 发起查询的用户 UUID。
            cookies: 查询开始时捕获的用户 Cookie 副本。
            payloads: 选课方式代码与其对应的非空查询参数列表。

        Returns:
            None: 结果或错误信息通过 Tkinter 主线程展示。
        """
        try:
            session = requests.Session()
            session.cookies.update(cookies)
            session.headers.update({
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://byyt.ustb.edu.cn",
                "Referer": "https://byyt.ustb.edu.cn/Xsxk/query/1",
                "RoleCode": "null",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0",
                "X-Requested-With": "XMLHttpRequest",
                "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Microsoft Edge";v="150"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            })
            result_by_task_id: dict[str, CourseSearchResult] = {}
            course_type_by_task_id: dict[str, str] = {}
            failed_type_codes: list[str] = []
            successful_type_count = 0
            course_type_counts = {
                course_type_code: 0 for course_type_code, _ in payloads
            }
            for course_type_code, payload in payloads:
                page_size = max(int(payload.get("pageSize", "100")), 1)
                page_number = 1
                type_task_ids: set[str] = set()
                type_had_success = False
                while page_number <= 100:
                    page_payload = dict(payload)
                    page_payload["pageNum"] = str(page_number)
                    page_payload["pageSize"] = str(page_size)
                    try:
                        response = session.post(
                            "https://byyt.ustb.edu.cn/Xsxk/queryKxrw",
                            data=page_payload,
                            timeout=30,
                        )
                        response.raise_for_status()
                        response_results = extract_course_search_results(
                            orjson.loads(response.content)
                        )
                    except Exception as error:
                        failed_type_codes.append(course_type_code)
                        self.user_log(
                            profile_id,
                            f"课程类型 {course_type_code} 第 {page_number} 页查询失败：{error}",
                        )
                        break
                    type_had_success = True
                    new_task_count = 0
                    for result in response_results:
                        if result.task_id not in type_task_ids:
                            type_task_ids.add(result.task_id)
                            new_task_count += 1
                        result_by_task_id.setdefault(result.task_id, result)
                        course_type_by_task_id.setdefault(
                            result.task_id, course_type_code
                        )
                    if (
                        len(response_results) < page_size
                        or new_task_count == 0
                    ):
                        break
                    page_number += 1
                if type_had_success:
                    successful_type_count += 1
                course_type_counts[course_type_code] = len(type_task_ids)
            if successful_type_count == 0:
                failed_summary = "、".join(failed_type_codes)
                raise RuntimeError(f"所有课程类型查询均失败：{failed_summary}")
            results = list(result_by_task_id.values())
            self.root.after(
                0,
                lambda: self.show_course_search_results(
                    profile_id,
                    results,
                    course_type_by_task_id,
                    len(failed_type_codes),
                    course_type_counts,
                ),
            )
        except Exception as error:
            error_message = f"查询课程时出错：{error}"
            self.user_log(profile_id, f"查询失败：{error}")
            self.root.after(0, lambda: messagebox.showerror("错误", error_message))
        finally:
            self.root.after(0, self.apply_active_user_control_state)

    def show_course_search_results(
        self: "CourseSelectionApp",
        profile_id: str,
        results: list[CourseSearchResult],
        course_type_by_task_id: dict[str, str],
        failed_type_count: int = 0,
        course_type_counts: dict[str, int] | None = None,
    ) -> None:
        """
        清空并填充课程查询结果表。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 发起查询的用户 UUID。
            results: 已完成空值清理和字段映射的查询结果。
            course_type_by_task_id: 每条结果对应的选课方式代码。
            failed_type_count: 本次汇总中请求失败的课程类型数量。
            course_type_counts: 每种选课方式在去重前返回的课程数量。

        Returns:
            None: 结果直接渲染到 Treeview 控件。
        """
        context = self.runtime.require_context(profile_id)
        context.search_results_by_task_id = {
            result.task_id: result for result in results
        }
        context.search_result_course_types_by_task_id = dict(course_type_by_task_id)
        if self.current_profile_id() != profile_id:
            result_message = f"查询完成，共 {len(results)} 门课程"
            if failed_type_count:
                result_message += f"，{failed_type_count} 个课程类型查询失败"
            self.user_log(profile_id, result_message)
            self.refresh_user_views()
            return
        self.search_results_by_task_id = dict(context.search_results_by_task_id)
        self.search_result_course_types_by_task_id = dict(
            context.search_result_course_types_by_task_id
        )
        self.render_course_search_results(
            results, notify_empty=failed_type_count == 0
        )
        if course_type_counts is not None and hasattr(
            self, "search_type_summary_var"
        ):
            type_labels = {
                "sztzk-b-b": "素质扩展课",
                "zytzk-b-b": "专业扩展课",
                "mooc-b-b": "MOOC",
                "bx-b-b": "必修课",
            }
            summary_parts = [
                f"{type_labels.get(code, code)} {count}"
                for code, count in course_type_counts.items()
            ]
            self.search_type_summary_var.set(" / ".join(summary_parts))
        if failed_type_count:
            self.search_count_var.set(
                f"共汇总 {len(results)} 门课程（{failed_type_count} 个类型查询失败）"
            )
            self.status_var.set(
                f"已汇总 {len(results)} 门课程；{failed_type_count} 个课程类型查询失败"
            )

    def render_course_search_results(
        self: "CourseSelectionApp",
        results: list[CourseSearchResult],
        notify_empty: bool = True,
    ) -> None:
        """将当前用户的课程查询结果渲染到表格。

        Args:
            self: 当前课程助手应用实例。
            results: 当前用户需要显示的课程结果。
            notify_empty: True 表示真实查询为空时弹出提示；用户切换时为 False。

        Returns:
            None: 结果表、数量和状态文本会同步更新。
        """
        for item_id in self.course_result_tree.get_children():
            self.course_result_tree.delete(item_id)
        for result in results:
            self.course_result_tree.insert(
                "", "end", iid=result.task_id, values=result.display_values()
            )

        if results:
            self.search_count_var.set(f"共查询到 {len(results)} 门课程")
            self.status_var.set(f"查询到 {len(results)} 门课程，请选择后添加")
        elif notify_empty:
            self.search_count_var.set("未找到符合条件的课程")
            self.status_var.set("未找到符合条件的课程")
            messagebox.showinfo("查询结果", "未找到符合条件的课程")
        else:
            self.search_count_var.set("当前用户暂无查询结果")

    def add_selected_courses(self: "CourseSelectionApp") -> None:
        """
        将查询结果表中选中的课程加入当前抢课列表。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 选中课程会保存到当前人员的课程列表并刷新界面。
        """
        context = self.active_user_context()
        if context is None:
            messagebox.showerror("错误", "请先在抢课任务页设置抢课人员")
            self.show_rush_tab()
            return
        courses = context.courses

        selected_task_ids = self.course_result_tree.selection()
        if not selected_task_ids:
            messagebox.showwarning("警告", "请先选择要添加的课程")
            return

        try:
            priority = int(self.priority_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "优先级必须是整数")
            return

        semester = self.semester_var.get().strip()
        semester_parts = semester.split("-")
        if len(semester_parts) != 3 or not all(semester_parts):
            messagebox.showerror("错误", "学期格式错误，请使用 YYYY-YYYY-N 格式")
            return

        academic_year = "-".join(semester_parts[:2])
        term = semester_parts[2]
        added_count = 0
        for task_id in selected_task_ids:
            result = self.search_results_by_task_id.get(task_id)
            course_type_code = self.search_result_course_types_by_task_id.get(task_id)
            if (
                result is None
                or result.category_code == "—"
                or course_type_code is None
            ):
                continue
            next_id = len(courses) + 1
            courses.append({
                "priority": priority,
                "data": {
                    "p_xktjz": "rwtjzyx",
                    "p_xn": academic_year,
                    "p_xq": term,
                    "p_xkfsdm": course_type_code,
                    "p_kclb": result.category_code,
                    "p_id": result.task_id,
                },
                "name": result.course_name,
                "teacher": result.teacher,
                "course_id": result.course_code,
                "schedule": result.schedule,
                "id": next_id,
            })
            self.cache_course_info(
                result.course_code,
                f"{academic_year}{term}",
                {
                    "name": result.course_name,
                    "teacher": result.teacher,
                    "p_id": result.task_id,
                    "p_kclb": result.category_code,
                    "schedule": result.schedule,
                },
            )
            added_count += 1

        if not added_count:
            messagebox.showerror("错误", "选中课程缺少课程类别编码，无法添加")
            return
        self.mark_current_list_dirty()
        self.save_course_list()
        self.update_course_list()
        self.status_var.set(f"已添加 {added_count} 门课程")
        messagebox.showinfo("成功", f"已添加 {added_count} 门课程")

    def add_course(self):
        global final_cookies_dict, course_data_list

        # 检查是否已输入抢课人员
        if not self.current_student_name:
            messagebox.showerror("错误", "请先输入抢课人员姓名")
            return

        if not final_cookies_dict:
            messagebox.showerror("错误", "请先登录")
            return

        course_type_text = self.course_type_var.get()
        if course_type_text == "素质扩展课":
            p_xkfsdm = "sztzk-b-b"
        elif course_type_text == "专业扩展课":
            p_xkfsdm = "zytzk-b-b"
        elif course_type_text == "MOOC":
            p_xkfsdm = "mooc-b-b"
        elif course_type_text == "必修课":
            p_xkfsdm = "bx-b-b"
        else:
            messagebox.showerror("错误", "课程类型无效")
            return

        course_id = self.course_id_var.get().strip()
        if not course_id:
            messagebox.showerror("错误", "请输入课程ID")
            return

        try:
            priority = int(self.priority_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "优先级必须是整数")
            return

        p_xn_xq = self.semester_var.get().strip()
        if not p_xn_xq or p_xn_xq.count("-") != 2:
            messagebox.showerror("错误", "学期格式错误，请使用 YYYY-YYYY-N 格式")
            return

        course_time = p_xn_xq.split("-")
        p_xn = f"{course_time[0]}-{course_time[1]}"
        p_xq = course_time[2]
        p_xnxq = p_xn + p_xq
        p_dqxn = p_xn
        p_dqxq = p_xq
        p_dqxnxq = p_xnxq

        print(f"🔍 正在查询课程 {course_id} 的信息...")
        self.status_var.set(f"正在查询课程 {course_id}...")

        query_thread = threading.Thread(
            target=self.query_course_info,
            args=(course_id, p_xn, p_xq, p_xnxq, p_dqxn, p_dqxq, p_dqxnxq, p_xkfsdm, priority),
            daemon=True
        )
        query_thread.start()

    def query_course_info(self, course_id, p_xn, p_xq, p_xnxq, p_dqxn, p_dqxq, p_dqxnxq, p_xkfsdm, priority):
        global final_cookies_dict, course_data_list

        semester = f"{p_xn}{p_xq}"
        cache_key = f"{semester}_{course_id}"

        cached_courses = self.get_cached_course(course_id, semester)
        if cached_courses:
            print(f"ℹ️ 从缓存中获取课程 {course_id} 的信息（共 {len(cached_courses)} 门课）")

            # 获取全局变量
            global course_id_count

            added_count = 0
            for cached_course in cached_courses:
                course_name = cached_course["name"]
                teacher = cached_course["teacher"]
                p_id = cached_course["p_id"]
                p_kclb = cached_course["p_kclb"]
                course_schedule = cached_course["schedule"]

                # 使用 course_id_count 生成新的ID
                new_id = course_id_count + 1
                course_id_count = new_id

                course_data = {
                    "priority": priority,
                    "data": {
                        "p_xktjz": "rwtjzyx",
                        "p_xn": p_xn,
                        "p_xq": p_xq,
                        "p_xkfsdm": p_xkfsdm,
                        "p_kclb": p_kclb,
                        "p_id": p_id
                    },
                    "name": course_name,
                    "teacher": teacher,
                    "course_id": course_id,
                    "schedule": course_schedule,
                    "id": new_id
                }
                course_data_list.append(course_data)
                added_count += 1
                print(f"✅ 已添加：{course_name} | 教师：{teacher} | 时间：{course_schedule}")

            # 添加课程后立即保存
            self.save_course_list()
            self.root.after(0, lambda: self.update_course_list())
            self.root.after(0, self.mark_current_list_dirty)
            self.root.after(0, lambda ac=added_count: messagebox.showinfo("成功", f"已从缓存添加 {ac} 门课程"))
            self.root.after(0, lambda: self.status_var.set("课程添加成功"))
            return

        try:
            session = requests.Session()
            session.cookies.update(final_cookies_dict)
            session.headers.update({
                "accept": "*/*",
                "accept-encoding": "gzip, deflate, br, zstd",
                "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                "cache-control": "no-cache",
                "connection": "keep-alive",
                "content-length": "537",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "host": "byyt.ustb.edu.cn",
                "origin": "https://byyt.ustb.edu.cn",
                "pragma": "no-cache",
                "referer": "https://byyt.ustb.edu.cn/Xsxk/query/1",
                "rolecode": "null",
                "sec-ch-ua": '"Not;A=Brand";v="99", "Microsoft Edge";v="139", "Chromium";v="139"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36 Edg/139.0.0.0",
                "x-requested-with": "XMLHttpRequest"
            })

            qurl = "https://byyt.ustb.edu.cn/Xsxk/queryKxrw"
            qdata = {
                'cxsfmt': "1",
                'p_pylx': "1",
                'mxpylx': "1",
                'p_xn': p_xn,
                'p_xq': p_xq,
                'p_xnxq': p_xnxq,
                'p_dqxn': p_dqxn,
                'p_dqxq': p_dqxq,
                'p_dqxnxq': p_dqxnxq,
                'p_xkfsdm': p_xkfsdm,
                'p_kcdm_cxrw': course_id,
                'p_kcdm_cxrw_zckc': course_id,
                "p_sfxsgwckb": "1",
                "p_sfgldjr":"0",
                "p_sfredis":"0",
                "p_sfsyxkgwc":"0",
                "p_sfhlctkc":"0",
                "p_sfhllrlkc":"0",
                'pageNum': "1",
                'pageSize': "100"
            }
            print(f"🔍 正在查询课程 {course_id} 的信息...")
            response = session.post(qurl, data=qdata)
            if response.status_code != 200:
                error_msg = f"查询失败，状态码：{response.status_code}"
                print(f"⚠️ {error_msg}")
                self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
                return

            coursedata = orjson.loads(response.content)
            if not coursedata['kxrwList']['list']:
                error_msg = f"未找到课程 {course_id}"
                print(f"⚠️ {error_msg}")
                self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
                return

            course_total = coursedata['kxrwList']['total']
            course_info = coursedata['kxrwList']['list']

            added_count = 0

            for course in course_info:
                course_id_count += 1
                course_name = course["kcmc"]
                teacher = course["dgjsmc"]
                p_id = course["id"]
                p_kclb = course.get("kclbdm", "2301")
                kcxx_html = course["kcxx"]
                soup = BeautifulSoup(kcxx_html, 'html.parser')
                tag_cyan = soup.find('div', class_='ivu-tag-cyan')
                schedule = "未知时间"
                if tag_cyan:
                    tag_text = tag_cyan.find('span', class_='ivu-tag-text')
                    if tag_text:
                        schedule = tag_text.get_text(strip=True)
                course_schedule = schedule
                print(f"✅ 找到课程：{course_name} | 教师：{teacher} | ID：{p_id} | 课程安排：{course_schedule}")

                course_data = {
                    "priority": priority,
                    "data": {
                        "p_xktjz": "rwtjzyx",
                        "p_xn": p_xn,
                        "p_xq": p_xq,
                        "p_xkfsdm": p_xkfsdm,
                        "p_kclb": p_kclb,
                        "p_id": p_id
                    },
                    "name": course_name,
                    "teacher": teacher,
                    "course_id": course_id,
                    "schedule": course_schedule,
                    "id": course_id_count
                }
                course_data_list.append(course_data)
                added_count += 1

                self.cache_course_info(
                    course_id,
                    semester,
                    {
                        "name": course_name,
                        "teacher": teacher,
                        "p_id": p_id,
                        "p_kclb": p_kclb,
                        "schedule": course_schedule
                    }
                )

            # 添加课程后立即保存
            self.save_course_list()

            self.root.after(0, lambda: self.update_course_list())
            self.root.after(0, self.mark_current_list_dirty)
            self.root.after(0, lambda ac=added_count: messagebox.showinfo("成功", f"已添加 {ac} 门课程"))
            self.root.after(0, lambda: self.status_var.set("课程添加成功"))


        except Exception as e:
            error_msg = f"查询课程 {course_id} 时出错：{e}"
            print(f"❌ {error_msg}")
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))

    def update_course_list(self: "CourseSelectionApp") -> None:
        """
        排序并刷新当前人员的待抢课程表与空状态。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 全局课程顺序和界面表格会被同步更新。
        """
        global course_data_list, course_id_count
        context = self.active_user_context()
        courses = context.courses if context is not None else course_data_list
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
        course_id_count = len(sorted_courses)
        if context is not None:
            context.courses[:] = sorted_courses
            courses = context.courses
        else:
            course_data_list = sorted_courses
            courses = course_data_list

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
                course["schedule"],
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
        """在抢课状态变化后刷新当前可见用户的待抢表。

        Args:
            self: 当前课程助手应用实例。
            profile_id: 状态发生变化的用户 UUID。

        Returns:
            None: 非当前用户或界面尚未创建时不刷新表格。
        """
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
        global course_data_list, course_id_count
        context = self.active_user_context()
        courses = context.courses if context is not None else course_data_list
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

        # 从 course_data_list 中移除对应课程
        remaining_courses = [c for c in courses if c["id"] not in ids_to_remove]

        # 重置所有课程的ID为连续序号
        for i, course in enumerate(remaining_courses):
            course["id"] = i + 1

        # 更新全局计数器
        course_id_count = len(remaining_courses)
        if context is not None:
            context.courses[:] = remaining_courses
        else:
            course_data_list = remaining_courses

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
        global course_data_list, final_cookies_dict, selection_running, stop_selection
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

        if not course_data_list:
            messagebox.showerror("错误", "尚未添加任何课程")
            return

        if not final_cookies_dict:
            messagebox.showerror("错误", "请先登录")
            return

        # 检查是否选择了人员（虽然add时检查过，但防止清空）
        if not self.current_student_name:
            messagebox.showerror("错误", "请确认抢课人员姓名")
            return

        course_data_list.sort(key=lambda x: x["priority"])

        msg = f"即将开始为【{self.current_student_name}】自动选课，课程如下：\n\n"
        for i, course in enumerate(course_data_list):
            msg += f"{i+1}. [优先级 {course['priority']}] {course['name']} ({course['teacher']})\n"
        msg += "\n是否继续？"

        if not messagebox.askyesno("确认", msg):
            return

        # === 设置状态 ===
        selection_running = True
        stop_selection = False

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
        self: "CourseSelectionApp", enabled: bool
    ) -> None:
        """
        统一设置抢课人员、模式和策略控件的可用状态。

        Args:
            self: 当前课程助手应用实例。
            enabled: True 表示允许编辑，False 表示锁定设置。

        Returns:
            None: 控件状态直接写入 Tkinter 组件。
        """
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
        根据抢课模式切换抢课时间输入框的可见性。

        Args:
            self: 当前课程助手应用实例。
            event: Tkinter 模式切换事件；直接调用时为 None。

        Returns:
            None: 直接更新界面控件状态。
        """
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
        global selection_running, stop_selection, final_cookies_dict, course_data_list
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

        if selection_running:
            messagebox.showwarning("警告", "已有抢课任务正在运行")
            return

        if not final_cookies_dict:
            messagebox.showerror("错误", "请先登录")
            return

        if not course_data_list:
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

        selection_running = True
        stop_selection = False

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
        global selection_running, stop_selection
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
                while not stop_selection:
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

            if stop_selection:
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
                selection_running = False
                stop_selection = False

    def _timed_rush_worker(self, target_time):
        """定时抢课工作线程"""
        import time
        from datetime import datetime
        import concurrent.futures
        global selection_running, stop_selection, final_cookies_dict, course_data_list

        try:
            # 等待到指定时间
            print(f"⏰ 定时抢课模式启动，目标时间：{target_time}")
            self.root.after(0, lambda: self.status_var.set(f"等待抢课时间：{target_time}"))

            while True:
                now = datetime.now().time()

                if stop_selection:
                    print("🛑 用户请求停止定时抢课")
                    return

                # 检查是否到达抢课时间（精确到秒）
                if now.hour == target_time.hour and now.minute == target_time.minute and now.second == target_time.second:
                    break

                # 显示倒计时
                target_datetime = datetime.combine(datetime.today(), target_time)
                now_datetime = datetime.now()
                if target_datetime < now_datetime:
                    # 如果目标时间已过，提示错误
                    error_msg = f"抢课时间 {target_time} 已过，请重新设置"
                    print(f"❌ {error_msg}")
                    self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
                    return

                time_diff = (target_datetime - now_datetime).total_seconds()
                hours = int(time_diff // 3600)
                minutes = int((time_diff % 3600) // 60)
                seconds = int(time_diff % 60)
                countdown_text = f"距离抢课时间还有：{hours:02d}:{minutes:02d}:{seconds:02d}"
                self.root.after(0, lambda ct=countdown_text: self.status_var.set(ct))

                # 每秒检查一次
                time.sleep(1)

            print(f"🚀 抢课时间到达！开始抢课...")
            self.root.after(0, lambda: self.status_var.set("正在抢课中..."))

            # 按优先级分组课程
            from collections import defaultdict
            courses_by_priority = defaultdict(list)
            for course in course_data_list:
                courses_by_priority[course["priority"]].append(course)

            sorted_priorities = sorted(courses_by_priority.keys())

            # 成功标志
            success = False

            # 使用线程池进行异步请求
            for priority in sorted_priorities:
                if success or stop_selection:
                    break

                courses = courses_by_priority[priority]
                print(f"\n📌 开始抢优先级 {priority} 的课程（共 {len(courses)} 门）")

                # 对当前优先级的所有课程进行定时抢课
                for course in courses:
                    if success or stop_selection:
                        break

                    course_name = course["name"]
                    print(f"\n🎯 正在抢课：{course_name}")

                    # 每3.5秒发送一次请求，直到成功或满足停止条件
                    request_count = 0
                    while not success and not stop_selection:
                        request_count += 1

                        try:
                            # 异步发送请求，不等待完整响应
                            future = self._send_rush_request_async(course)

                            # 等待最多3.5秒
                            try:
                                result = future.result(timeout=3.5)

                                if result:
                                    status, text = result

                                    # 检查响应内容
                                    if "成功" in text or "success" in text.lower():
                                        print(f"✅ 抢课成功！课程：{course_name}")
                                        self.root.after(0, lambda cn=course_name: messagebox.showinfo("成功", f"抢课成功：{cn}"))
                                        self.root.after(0, lambda: self.status_var.set("抢课成功！"))
                                        success = True
                                        break

                                    elif "已选择" in text or "already selected" in text.lower():
                                        print(f"ℹ️ 已选择该课程：{course_name}，停止抢课")
                                        success = True
                                        break

                                    elif "已满" in text or "full" in text.lower():
                                        print(f"🚫 课程已满：{course_name}，停止抢课")
                                        break  # 停止抢这门课，继续下一门

                                    else:
                                        print(f"[{request_count}] {course_name}: {text[:100]}")

                            except concurrent.futures.TimeoutError:
                                # 超时，继续下一次请求
                                print(f"[{request_count}] {course_name}: 请求超时（3.5秒），继续...")

                        except Exception as e:
                            print(f"[{request_count}] 请求异常：{e}")

                        # 等待3.5秒再发送下一次请求
                        if not success and not stop_selection:
                            time.sleep(3.5)

            if not success:
                print("🔚 所有课程抢课完成")
                self.root.after(0, lambda: self.status_var.set("抢课结束"))

        except Exception as e:
            error_msg = f"定时抢课出错：{e}"
            print(f"❌ {error_msg}")
            self.root.after(0, lambda: messagebox.showerror("错误", error_msg))
            self.root.after(0, lambda: self.status_var.set("抢课失败"))

        finally:
            self.root.after(0, self.restore_buttons)
            selection_running = False
            stop_selection = False

    def _send_rush_request_async(self, course):
        """
        异步发送抢课请求
        返回 Future 对象
        """
        import concurrent.futures

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._send_rush_request, course)
        return future

    def _send_rush_request(self, course):
        """
        发送单次抢课请求
        返回: (status_code, response_text) 或 None
        """
        import requests
        global final_cookies_dict

        try:
            url = "https://byyt.ustb.edu.cn/Xsxk/addGouwuche"

            session = requests.Session()
            session.cookies.update(final_cookies_dict)
            session.headers.update({
                "accept": "application/json, text/javascript, */*; q=0.01",
                "accept-encoding": "gzip, deflate, br, zstd",
                "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                "cache-control": "no-cache",
                "connection": "keep-alive",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "host": "byyt.ustb.edu.cn",
                "origin": "https://byyt.ustb.edu.cn",
                "pragma": "no-cache",
                "referer": "https://byyt.ustb.edu.cn/Xsxk/query/1",
                "sec-ch-ua": '"Not;A=Brand";v="99", "Microsoft Edge";v="139", "Chromium";v="139"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36 Edg/139.0.0.0",
                "x-requested-with": "XMLHttpRequest"
            })

            # 发送请求，timeout设置为3.5秒
            response = session.post(url, data=course["data"], timeout=3.5)

            return (response.status_code, response.text)

        except requests.Timeout:
            # 超时异常，返回None让外层处理
            return None
        except Exception as e:
            print(f"请求异常：{e}")
            return None

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
        global course_data_list, final_cookies_dict, selection_running, stop_selection

        print(f"\n🚀 开始为 {self.current_student_name} 自动选课...")
        self.status_var.set("自动选课已启动")

        session = requests.Session()
        session.cookies.update(final_cookies_dict)
        session.headers.update({
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
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36 Edg/139.0.0.0",
            "X-Requested-With": "XMLHttpRequest"
        })

        url = "https://byyt.ustb.edu.cn/Xsxk/addGouwuche"
        count = 0
        success = False

        try:
            # 按优先级分组
            priority_groups = defaultdict(list)
            for course in sorted(course_data_list, key=lambda x: x["priority"]):
                priority_groups[course["priority"]].append(course)

            # 记录已因“冲突”或“已满+不重试”而放弃的课程 ID
            failed_course_ids = set()

            # 按优先级从高到低处理
            for priority in sorted(priority_groups.keys()):
                courses = priority_groups[priority]
                print(f"🎯 开始抢优先级 {priority} 的课程，共 {len(courses)} 门：")
                for c in courses:
                    print(f"   → {c['name']} ({c['teacher']})")

                if stop_selection:
                    break

                while not success and not stop_selection:
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
                        if stop_selection:
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

                if success or stop_selection:
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
            selection_running = False
            stop_selection = False

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
        request_count = 0
        try:
            for priority in sorted(priority_groups):
                priority_courses = priority_groups[priority]
                self.user_log(
                    profile_id,
                    f"开始抢优先级 {priority} 的课程，共 {len(priority_courses)} 门",
                )
                while not stop_event.is_set() and not stop_after_success:
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
        global selection_running, stop_selection
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
        if not selection_running:
            return

        stop_selection = True
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
        while not self.keepalive_stop_event.wait(1.0):
            now = time.time()
            logged_in_ids = [
                context.profile.id
                for context in self.runtime.enabled_contexts()
                if context.login_state is UserLoginState.LOGGED_IN and context.cookies
            ]
            if now - last_business_time >= 360:
                for profile_id in logged_in_ids:
                    self.send_business_keepalive(profile_id)
                last_business_time = now
            if now - last_online_time >= 600:
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
        global selection_running, stop_selection
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
        if selection_running:
            stop_selection = True
            self.root.after(0, self.restore_buttons)
        self.root.after(0, self.disable_workspace_tabs)

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

# 启动应用
if __name__ == "__main__":
    root = tk.Tk()
    app = CourseSelectionApp(root)
    root.mainloop()
