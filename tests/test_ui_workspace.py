"""全局用户栏、双页工作区导航与课程添加前置校验测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from multi_user_runtime import MultiUserRuntime
from user_profiles import UserProfileStore


class FakeNotebook:
    """记录 Notebook 页签状态与选中页面。"""

    def __init__(self) -> None:
        """初始化空的页签调用记录。

        Args:
            None.

        Returns:
            None: 初始化结果保存在实例属性中。
        """
        self.states: dict[object, str] = {}
        self.selected: object | None = None

    def tab(self, page: object, state: str) -> None:
        """记录页面状态变更。

        Args:
            page: 被修改的页面对象。
            state: 页面目标状态。

        Returns:
            None: 状态写入调用记录。
        """
        self.states[page] = state

    def select(self, page: object) -> None:
        """记录当前选中的页面对象。

        Args:
            page: 需要显示的页面对象。

        Returns:
            None: 页面对象写入调用记录。
        """
        self.selected = page


class FakeTree:
    """提供课程结果选择项的最小 Treeview 替身。"""

    def __init__(self, task_id: str = "[[COURSE_TASK_ID]]") -> None:
        """初始化指定教学班 ID 的选择替身。

        Args:
            task_id: 选择结果返回的合成教学班 ID。

        Returns:
            None: 教学班 ID 保存在实例属性中。
        """
        self.task_id = task_id

    def selection(self) -> tuple[str, ...]:
        """返回一条模拟的课程任务选择。

        Args:
            None.

        Returns:
            包含模拟任务 ID 的元组。
        """
        return (self.task_id,)


class FakeWidget:
    """记录 Tkinter 控件状态变更的最小替身。"""

    def __init__(self) -> None:
        """初始化空的控件状态映射。

        Args:
            None.

        Returns:
            None: 状态映射保存在实例中。
        """
        self.options: dict[str, object] = {}

    def config(self, **options: object) -> None:
        """记录控件配置参数。

        Args:
            **options: 需要写入控件的配置项。

        Returns:
            None: 配置项写入实例状态。
        """
        self.options.update(options)

    def grid(self, **options: object) -> None:
        """记录控件显示调用。

        Args:
            **options: 网格布局参数。

        Returns:
            None: 控件显示状态写入实例。
        """
        self.options["manager"] = "grid"
        self.options.update(options)

    def grid_remove(self) -> None:
        """记录控件隐藏调用。

        Args:
            None.

        Returns:
            None: 控件标记为隐藏。
        """
        self.options["manager"] = ""


class FakeVariable:
    """提供 Tkinter StringVar 的最小赋值行为。"""

    def __init__(self) -> None:
        """初始化空文本值。

        Args:
            None.

        Returns:
            None: 文本值保存在实例中。
        """
        self.value = ""

    def set(self, value: str) -> None:
        """记录变量文本。

        Args:
            value: 需要保存的文本。

        Returns:
            None: 文本写入实例状态。
        """
        self.value = value

    def get(self) -> str:
        """返回当前保存的文本。

        Args:
            None.

        Returns:
            当前变量文本。
        """
        return self.value


class FakeThread:
    """阻止测试启动真实后台线程。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        """记录线程是否被启动。

        Args:
            *args: 被隔离的线程位置参数。
            **kwargs: 被隔离的线程关键字参数。

        Returns:
            None: 初始化结果保存在实例中。
        """
        self.started = False

    def start(self) -> None:
        """记录线程启动调用但不执行目标函数。

        Args:
            None.

        Returns:
            None: 启动状态写入实例。
        """
        self.started = True


@pytest.fixture(scope="module")
def app_module() -> ModuleType:
    """加载带连字符文件名的应用入口模块。

    Args:
        None.

    Returns:
        已加载的课程助手模块。

    Raises:
        RuntimeError: 无法创建入口模块加载器时抛出。
    """
    module_path = Path(__file__).parents[1] / "USTB-courseHelper.py"
    spec = importlib.util.spec_from_file_location("ustb_course_helper_ui", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载课程助手入口模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_navigation_app(app_module: ModuleType) -> Any:
    """创建只包含工作区导航依赖的应用实例。

    Args:
        app_module: 已加载的课程助手入口模块。

    Returns:
        绕过 Tkinter 初始化的课程助手实例。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.tab_control = FakeNotebook()
    app.course_search_tab = object()
    app.rush_tab = object()
    app.show_login_view = lambda: setattr(app, "login_view_shown", True)
    app.show_workspace = lambda: setattr(app, "workspace_shown", True)
    return app


def test_workspace_tabs_enable_and_default_to_rush_page(
    app_module: ModuleType,
) -> None:
    """验证登录后启用两个工作页并默认显示抢课任务。

    Args:
        app_module: 已加载的课程助手入口模块。

    Returns:
        None: 测试仅通过断言验证页签状态。
    """
    app = _make_navigation_app(app_module)

    app.enable_workspace_tabs()

    assert app.tab_control.states == {
        app.course_search_tab: "normal",
        app.rush_tab: "normal",
    }
    assert app.tab_control.selected is app.rush_tab


def test_workspace_tabs_disable_and_show_standalone_login_view(
    app_module: ModuleType,
) -> None:
    """验证无用户时禁用两个工作页并显示独立登录视图。

    Args:
        app_module: 已加载的课程助手入口模块。

    Returns:
        None: 测试仅通过断言验证页签状态。
    """
    app = _make_navigation_app(app_module)

    app.disable_workspace_tabs()

    assert app.tab_control.states == {
        app.course_search_tab: "disabled",
        app.rush_tab: "disabled",
    }
    assert app.login_view_shown is True


def test_add_selected_courses_requires_current_student(
    app_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证未设置人员时不向待抢列表写入课程。

    Args:
        app_module: 已加载的课程助手入口模块。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 测试仅通过断言验证课程数据和页面跳转。
    """
    app = _make_navigation_app(app_module)
    app.current_student_name = ""
    app.course_result_tree = FakeTree()
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showerror",
        lambda title, message: messages.append((title, message)),
    )
    monkeypatch.setattr(app_module, "course_data_list", [])

    app.add_selected_courses()

    assert app_module.course_data_list == []
    assert app.tab_control.selected is app.rush_tab
    assert messages == [("错误", "请先在抢课任务页设置抢课人员")]


def test_add_selected_courses_stays_on_course_search_page(
    app_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """验证成功添加课程后继续停留在课程查询页。

    Args:
        app_module: 已加载的课程助手入口模块。
        monkeypatch: pytest 提供的运行时替换工具。
        tmp_path: pytest 临时目录。

    Returns:
        None: 通过断言验证课程已加入且页面没有自动跳转。
    """
    store = UserProfileStore(tmp_path)
    profile = store.create("[[USER_A]]")
    app = _make_navigation_app(app_module)
    app.profile_store = store
    app.runtime = MultiUserRuntime(store)
    app.runtime.select_profile(profile.id)
    app.tab_control.selected = app.course_search_tab
    app.course_result_tree = FakeTree("[[SPORTS_TASK_ID]]")
    app.priority_var = FakeVariable()
    app.priority_var.set("1")
    app.semester_var = FakeVariable()
    app.semester_var.set("2026-2027-1")
    result = type(
        "CourseResult",
        (),
        {
            "task_id": "[[SPORTS_TASK_ID]]",
            "category_code": "19",
            "display_name": "体育III(乒乓球)",
            "course_name": "体育III",
            "teacher": "[[TEACHER_NAME]]",
            "course_code": "11101013",
            "schedule": "1-16周,星期三第3-4节 体育馆",
        },
    )()
    app.search_results_by_task_id = {result.task_id: result}
    app.search_result_course_types_by_task_id = {
        result.task_id: "bx-b-b-ty3"
    }
    app.status_var = FakeVariable()
    app.course_cache = {}
    app.cache_file = str(tmp_path / "course_cache.json")
    app.mark_current_list_dirty = lambda: None
    app.save_course_list = lambda: True
    app.update_course_list = lambda: None
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showinfo",
        lambda title, message: messages.append((title, message)),
    )

    app.add_selected_courses()

    courses = app.runtime.require_context(profile.id).courses
    assert len(courses) == 1
    assert courses[0]["name"] == "体育III(乒乓球)"
    assert courses[0]["data"] == {
        "p_xktjz": "rwtjzyx",
        "p_xn": "2026-2027",
        "p_xq": "1",
        "p_xkfsdm": "bx-b-b-ty3",
        "p_kclb": "19",
        "p_id": "[[SPORTS_TASK_ID]]",
    }
    assert app.course_cache["2026-20271_11101013"][0]["name"] == "体育III(乒乓球)"
    assert app.tab_control.selected is app.course_search_tab
    assert messages == [("成功", "已添加 1 门课程")]


def test_start_auto_selection_opens_rush_page_without_numeric_index(
    app_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证启动轮询时通过页面对象显示抢课任务页。

    Args:
        app_module: 已加载的课程助手入口模块。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 测试仅通过断言验证页面切换和控件锁定。
    """
    app = _make_navigation_app(app_module)
    app.current_student_name = "[[STUDENT_NAME]]"
    app.add_course_btn = FakeWidget()
    app.add_selected_course_btn = FakeWidget()
    app.start_auto_btn = FakeWidget()
    app.remove_course_btn = FakeWidget()
    app.stop_auto_btn = FakeWidget()
    app.student_name_var = FakeVariable()
    app.auto_selection_process = lambda: None
    control_states: list[bool] = []
    app._set_rush_controls_enabled = control_states.append
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args: True)
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(app_module, "final_cookies_dict", {"SESSION": "[[TOKEN]]"})
    monkeypatch.setattr(
        app_module,
        "course_data_list",
        [
            {
                "priority": 1,
                "name": "[[COURSE_NAME]]",
                "teacher": "[[TEACHER_NAME]]",
            }
        ],
    )
    monkeypatch.setattr(app_module, "selection_running", False)
    monkeypatch.setattr(app_module, "stop_selection", False)

    app.start_auto_selection()

    assert app.tab_control.selected is app.rush_tab
    assert control_states == [False]
    assert app.stop_auto_btn.options["state"] == app_module.tk.NORMAL


def test_tk_workspace_builds_progressive_account_controls_and_two_pages(
    app_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """验证真实 Tkinter 工作区按账号数量渐进显示账号控件。

    Args:
        app_module: 已加载的课程助手入口模块。
        monkeypatch: pytest 提供的运行时替换工具。
        tmp_path: pytest 临时目录。

    Returns:
        None: 测试仅通过断言验证 Tkinter 控件结构。
    """
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    root = app_module.tk.Tk()
    root.withdraw()
    try:
        app = app_module.CourseSelectionApp(root)
        root.update_idletasks()

        assert app.browser_driver_warmup_thread.started is True
        tab_names = [
            app.tab_control.tab(index, "text")
            for index in range(app.tab_control.index("end"))
        ]
        assert tab_names == ["课程查询", "抢课任务"]
        assert "体育III" in app.course_type_combo.cget("values")
        assert not hasattr(app, "global_user_combo")
        assert app.global_login_state_var.get()
        assert app.global_task_state_var.get() == ""
        assert app.account_menu_btn.winfo_exists()
        assert not hasattr(app, "manage_users_btn")
        assert not hasattr(app, "login_workspace")
        assert app.quick_login_btn.cget("text") == "重新生成二维码"
        assert str(app.quick_login_btn.cget("state")) == "disabled"
        assert app.quick_login_btn.winfo_manager() == ""
        assert str(app.global_login_btn.cget("state")) == "disabled"
        assert app.status_var.get() == "浏览器驱动准备中..."
        app.open_user_manager()
        root.update_idletasks()
        assert str(app.add_user_btn.cget("state")) == "disabled"
        assert str(app.manager_login_btn.cget("state")) == "disabled"
        app.close_user_manager()
        assert app.quick_login_frame.winfo_exists()
        assert app.quick_qr_frame.winfo_manager() == "grid"
        assert app.quick_qr_frame.grid_info()["column"] == 0
        assert app.quick_qr_frame.winfo_reqwidth() >= 400
        assert app.quick_qr_frame.winfo_reqheight() >= 430
        assert set(app.quick_qr_label.grid_info()["sticky"]) == set("nsew")
        assert app.quick_qr_label.cget("text") == "浏览器驱动准备中..."
        assert not hasattr(app, "quick_login_copy")
        assert app.course_result_tree.winfo_manager() == "grid"
        assert app.course_tree.winfo_manager() == "grid"
        assert app.course_tree["columns"] == (
            "id",
            "priority",
            "name",
            "teacher",
            "course_id",
            "schedule",
            "attempt_status",
            "last_response",
        )
        expected_empty_manager = "" if app.current_courses() else "place"
        assert app.task_empty_label.winfo_manager() == expected_empty_manager
        assert not hasattr(app, "console_output")
        assert app.current_list_name_var.get() == "默认列表"
        assert app.current_list_dirty_var.get() == "自动保存"
        assert app.save_rush_list_btn.winfo_exists()
        assert app.manage_rush_lists_btn.winfo_exists()
        assert app.search_priority_help_label.cget("text") == (
            "优先级说明：数字越小越先尝试；相同数字按列表顺序执行。"
        )
        assert app.task_priority_help_label.cget("text") == (
            "优先级说明：数字越小越先尝试；相同数字按列表顺序执行。"
        )
        assert not hasattr(app, "clear_log_btn")

        class CompletedWarmup:
            """提供已完成浏览器驱动预匹配状态的测试替身。"""

            def status(self) -> tuple[bool, str | None]:
                """返回预匹配成功状态。

                Args:
                    self: 当前预热替身实例。

                Returns:
                    已完成且无错误的状态元组。
                """
                return True, None

        app.browser_driver_warmup = CompletedWarmup()
        app.refresh_browser_driver_status()
        assert app.status_var.get() == "正在生成登录二维码..."
        assert app.quick_qr_label.cget("text") == "正在生成登录二维码..."
        assert app._automatic_login_scheduled is True
        assert app.quick_login_btn.winfo_manager() == ""
        assert str(app.global_login_btn.cget("state")) == "normal"

        class FailedWarmup:
            """提供浏览器驱动预匹配失败状态的测试替身。"""

            def status(self) -> tuple[bool, str | None]:
                """返回带原始原因的预匹配失败状态。

                Args:
                    self: 当前预热替身实例。

                Returns:
                    已完成且包含失败原因的状态元组。
                """
                return True, "[[DRIVER_MATCH_ERROR]]"

        app.browser_driver_warmup = FailedWarmup()
        app._automatic_login_scheduled = False
        app.refresh_browser_driver_status()
        assert app.status_var.get() == "浏览器驱动准备失败，请重试"
        assert str(app.quick_login_btn.cget("state")) == "normal"
        assert app.quick_login_btn.winfo_manager() == "grid"
        assert str(app.global_login_btn.cget("state")) == "normal"

        store = UserProfileStore(tmp_path)
        first = store.create("默认用户")
        app.profile_store = store
        app.runtime = MultiUserRuntime(store)
        app.refresh_user_views()
        root.update_idletasks()
        assert app.login_target_frame.winfo_manager() == ""
        assert app.offline_workspace_btn.winfo_manager() == "grid"
        assert app.account_menu_btn.cget("text") == "账号"
        assert app.global_task_state_label.winfo_manager() == ""
        account_labels = [
            app.account_menu.entrycget(index, "label")
            for index in range(app.account_menu.index("end") + 1)
            if app.account_menu.type(index) == "command"
        ]
        assert all("默认用户" not in label for label in account_labels)

        second = store.create("[[USER_B]]")
        app.runtime.sync_profiles()
        app.runtime.mark_task_started(second.id, waiting=False)
        app.refresh_user_views()
        root.update_idletasks()
        assert app.login_target_frame.winfo_manager() == "grid"
        assert app.account_menu_btn.cget("text") == first.alias
        assert app.background_task_btn.winfo_manager() == "pack"
        assert "1 个账号正在运行" in app.background_task_btn.cget("text")
        for window_width, window_height in ((1280, 860), (1120, 720)):
            root.geometry(f"{window_width}x{window_height}")
            root.update_idletasks()
            assert app.quick_login_frame.winfo_reqwidth() <= window_width - 48
            assert app.quick_login_frame.winfo_reqheight() <= window_height - 180
            assert app.quick_qr_frame.winfo_reqwidth() <= window_width
            assert app.quick_qr_frame.winfo_reqheight() <= window_height

        app.open_user_manager()
        root.update_idletasks()
        assert app.user_manager_window.winfo_exists()
        assert app.user_profile_tree["columns"] == (
            "alias",
            "login",
            "task",
            "result",
        )
        app.close_user_manager()

        app.rush_mode_var.set("定时抢课")
        app.on_mode_change()
        assert app.rush_time_frame.winfo_manager() == "grid"
        app.rush_mode_var.set("轮询模式")
        app.on_mode_change()
        assert app.rush_time_frame.winfo_manager() == ""
    finally:
        root.destroy()


def test_existing_account_startup_stays_on_login_view(
    app_module: ModuleType,
) -> None:
    """验证已有账号再次启动时先显示扫码页而不是直接进入工作区。

    Args:
        app_module: 已加载的课程助手入口模块。

    Returns:
        None: 通过断言验证工作页已启用但初始保持隐藏。
    """
    class RuntimeWithAccount:
        """提供已有活动账号的最小运行时替身。"""

        def active_context(self) -> object:
            """返回一个非空活动账号上下文。

            Args:
                self: 当前运行时替身实例。

            Returns:
                用于表示已有账号的占位对象。
            """
            return object()

    app = _make_navigation_app(app_module)
    app.runtime = RuntimeWithAccount()

    app.show_initial_view()

    assert app.tab_control.states == {
        app.course_search_tab: "normal",
        app.rush_tab: "normal",
    }
    assert app.tab_control.selected is app.rush_tab
    assert app.login_view_shown is True


def test_course_changes_mark_named_list_dirty(app_module: ModuleType) -> None:
    """验证课程变化后当前命名列表标记为未保存。

    Args:
        app_module: 已加载的课程助手入口模块。

    Returns:
        None: 通过断言验证状态文本。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.current_saved_list_id = "[[LIST_ID]]"
    app.current_saved_list_name = "[[LIST_NAME]]"
    app.current_saved_list_note = "[[LIST_NOTE]]"
    app.current_list_name_var = FakeVariable()
    app.current_list_note_var = FakeVariable()
    app.current_list_dirty_var = FakeVariable()

    app.mark_current_list_dirty()

    assert app.current_list_name_var.get() == "[[LIST_NAME]]"
    assert app.current_list_dirty_var.get() == "有未保存更改"


def test_default_list_is_presented_as_automatically_saved(
    app_module: ModuleType,
) -> None:
    """验证未加载命名列表时使用无需手动保存的默认列表。

    Args:
        app_module: 已加载的课程助手入口模块。

    Returns:
        None: 通过断言验证默认列表名称、说明和保存状态。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.current_saved_list_id = None
    app.current_saved_list_name = ""
    app.current_saved_list_note = ""
    app.current_saved_list_dirty = True
    app.current_list_name_var = FakeVariable()
    app.current_list_note_var = FakeVariable()
    app.current_list_dirty_var = FakeVariable()

    app.refresh_current_list_status()

    assert app.current_list_name_var.get() == "默认列表"
    assert app.current_list_note_var.get() == "自动保存当前待抢课程"
    assert app.current_list_dirty_var.get() == "自动保存"


def test_student_switch_resets_named_list_state(
    app_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证切换人员时重置当前命名列表状态。

    Args:
        app_module: 已加载的课程助手入口模块。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 通过断言验证人员隔离状态。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.switch_timer = object()
    app.current_student_name = "[[STUDENT_A]]"
    app.student_switch_lock = False
    app.student_name_var = FakeVariable()
    app.student_name_var.set("[[STUDENT_B]]")
    app.status_var = FakeVariable()
    app.save_course_list = lambda student_name=None: None
    app.load_saved_course_list = lambda student_name=None: None
    reset_calls: list[bool] = []
    app.reset_current_saved_list_state = lambda: reset_calls.append(True)
    monkeypatch.setattr(app_module, "selection_running", False)

    app.process_student_switch()

    assert app.current_student_name == "[[STUDENT_B]]"
    assert reset_calls == [True]


def test_loading_saved_list_cancel_keeps_current_courses(
    app_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证取消加载确认时当前课程和命名状态保持不变。

    Args:
        app_module: 已加载的课程助手入口模块。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 通过断言验证无副作用。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    original = [{"id": 1, "name": "[[CURRENT_COURSE]]"}]
    monkeypatch.setattr(app_module, "course_data_list", original)
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args: False)
    saved = type(
        "SavedList",
        (),
        {"id": "[[LIST_ID]]", "name": "[[LIST_NAME]]", "note": "", "courses": [{}]},
    )()

    app.load_named_rush_list(saved)

    assert app_module.course_data_list is original


def test_empty_course_list_cannot_open_save_dialog(
    app_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证空课程列表不会创建保存对话框或文件记录。

    Args:
        app_module: 已加载的课程助手入口模块。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 通过断言验证中文提示和无副作用。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.current_student_name = "[[STUDENT_NAME]]"
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(app_module, "course_data_list", [])
    monkeypatch.setattr(app_module, "selection_running", False)
    monkeypatch.setattr(
        app_module.messagebox,
        "showerror",
        lambda title, message: messages.append((title, message)),
    )

    app.open_save_rush_list_dialog()

    assert messages == [("错误", "请至少添加一门课程后再保存列表")]


def test_loading_saved_list_replaces_draft_and_marks_clean(
    app_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证确认加载后完整替换草稿并将命名状态设为干净。

    Args:
        app_module: 已加载的课程助手入口模块。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 通过断言验证替换、保存和状态同步。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    saved = type(
        "SavedList",
        (),
        {
            "id": "[[LIST_ID]]",
            "name": "[[LIST_NAME]]",
            "note": "[[LIST_NOTE]]",
            "courses": [{"name": "[[SAVED_COURSE]]"}],
        },
    )()
    replacement = [{"id": 1, "name": "[[SAVED_COURSE]]"}]
    app.rush_list_store = type(
        "FakeStore",
        (),
        {"courses_for_loading": lambda self, item: replacement},
    )()
    save_calls: list[bool] = []
    update_calls: list[bool] = []
    current_calls: list[tuple[object, bool]] = []
    app.save_course_list = lambda student_name=None: save_calls.append(True) or True
    app.update_course_list = lambda: update_calls.append(True)
    app.set_current_saved_list = (
        lambda item, dirty=False: current_calls.append((item, dirty))
    )
    app.status_var = FakeVariable()
    monkeypatch.setattr(app_module, "selection_running", False)
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args: True)
    monkeypatch.setattr(
        app_module, "course_data_list", [{"id": 1, "name": "[[CURRENT_COURSE]]"}]
    )
    monkeypatch.setattr(app_module, "course_id_count", 1)

    result = app.load_named_rush_list(saved)

    assert result is True
    assert app_module.course_data_list is replacement
    assert save_calls == [True]
    assert update_calls == [True]
    assert current_calls == [(saved, False)]


def test_rush_controls_lock_named_list_operations(app_module: ModuleType) -> None:
    """验证抢课运行期间命名列表与任务编辑操作一并锁定。

    Args:
        app_module: 已加载的课程助手入口模块。

    Returns:
        None: 通过断言验证关键控件状态。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.student_name_entry = FakeWidget()
    app.stop_on_success_check = FakeWidget()
    app.retry_full_check = FakeWidget()
    app.save_rush_list_btn = FakeWidget()
    app.manage_rush_lists_btn = FakeWidget()
    app.task_add_course_btn = FakeWidget()
    app.task_empty_add_btn = FakeWidget()
    app.rush_mode_buttons = [FakeWidget(), FakeWidget()]
    app.rush_time_entry = FakeWidget()
    app.rush_mode_var = FakeVariable()
    app.rush_mode_var.set("轮询模式")

    app._set_rush_controls_enabled(False)

    assert app.save_rush_list_btn.options["state"] == app_module.tk.DISABLED
    assert app.manage_rush_lists_btn.options["state"] == app_module.tk.DISABLED
    assert app.task_add_course_btn.options["state"] == app_module.tk.DISABLED


def test_deleting_current_named_list_keeps_courses_and_resets_state(
    app_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证删除当前命名列表后保留课程并恢复未命名状态。

    Args:
        app_module: 已加载的课程助手入口模块。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 通过断言验证课程未变且状态变脏。
    """
    saved = type(
        "SavedList",
        (),
        {"id": "[[COURSE_TASK_ID]]", "name": "[[LIST_NAME]]"},
    )()
    deleted: list[str] = []
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.current_student_name = "[[STUDENT_NAME]]"
    app.current_saved_list_id = saved.id
    app.current_saved_list_name = saved.name
    app.current_saved_list_note = "[[LIST_NOTE]]"
    app.current_saved_list_dirty = False
    app.current_list_name_var = FakeVariable()
    app.current_list_note_var = FakeVariable()
    app.current_list_dirty_var = FakeVariable()
    app.rush_list_store = type(
        "FakeStore",
        (),
        {
            "get": lambda self, student, list_id: saved,
            "delete": lambda self, student, list_id: deleted.append(list_id),
        },
    )()
    app.refresh_rush_list_manager = lambda tree: None
    courses = [{"id": 1, "name": "[[CURRENT_COURSE]]"}]
    monkeypatch.setattr(app_module, "course_data_list", courses)
    monkeypatch.setattr(app_module, "selection_running", False)
    monkeypatch.setattr(app_module.messagebox, "askyesno", lambda *args: True)

    app.delete_selected_named_rush_list(FakeTree())

    assert app_module.course_data_list is courses
    assert deleted == [saved.id]
    assert app.current_saved_list_id is None
    assert app.current_saved_list_dirty is True
    assert app.current_list_name_var.get() == "默认列表"
    assert app.current_list_dirty_var.get() == "自动保存"


def test_empty_profile_state_prefers_direct_login_page(
    app_module: ModuleType,
) -> None:
    """验证首次打开时显示直登入口而不是要求先创建用户。

    Args:
        app_module: 已加载的课程助手模块。

    Returns:
        None: 通过断言验证用户管理区域在无档案时隐藏。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)

    class EmptyProfileStore:
        """提供无用户档案的最小存储替身。"""

        def list_all(self, include_inactive: bool = False) -> list[object]:
            """返回空用户档案列表。

            Args:
                include_inactive: 是否包含停用档案。

            Returns:
                空列表。
            """
            return []

    class EmptyRuntime:
        """提供无活动用户的最小运行时替身。"""

        def enabled_contexts(self) -> tuple[object, ...]:
            """返回空的启用账号上下文快照。

            Args:
                None.

            Returns:
                空元组。
            """
            return ()

        def active_task_contexts(
            self, exclude_profile_id: str | None = None
        ) -> tuple[object, ...]:
            """返回空的活动任务上下文快照。

            Args:
                exclude_profile_id: 需要排除的账号 UUID。

            Returns:
                空元组。
            """
            return ()

        def active_context(self) -> None:
            """返回空活动上下文。

            Args:
                None.

            Returns:
                None: 当前没有用户。
            """
            return None

    app.profile_store = EmptyProfileStore()
    app.runtime = EmptyRuntime()
    app.quick_login_frame = FakeWidget()
    app.login_target_frame = FakeWidget()
    app.offline_workspace_btn = FakeWidget()
    app.account_menu_btn = FakeWidget()
    app.global_task_state_var = FakeVariable()
    app.global_login_state_var = FakeVariable()
    app.student_name_var = FakeVariable()
    app.current_student_display_var = FakeVariable()
    app.current_student_name = ""

    app.refresh_user_views()

    assert app.quick_login_frame.options["manager"] == "grid"
    assert app.login_target_frame.options["manager"] == ""
    assert app.offline_workspace_btn.options["manager"] == ""
