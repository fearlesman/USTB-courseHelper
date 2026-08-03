"""主程序多用户查询、任务与会话隔离测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from multi_user_runtime import MultiUserRuntime, UserLoginState, UserTaskState
from user_profiles import UserProfileStore


class FakeRoot:
    """提供立即执行 Tk 回调的根窗口替身。"""

    def after(self, delay: int, callback: Any) -> None:
        """立即执行计划到主线程的回调。

        Args:
            delay: 模拟的延迟毫秒数。
            callback: 需要执行的回调。

        Returns:
            None: 回调在当前线程立即执行。
        """
        callback()


class FakeVariable:
    """提供 Tk 变量的最小读写行为。"""

    def __init__(self, value: Any = "") -> None:
        """保存初始值。

        Args:
            value: 初始变量值。

        Returns:
            None: 值保存在实例中。
        """
        self.value = value

    def get(self) -> Any:
        """返回当前值。

        Args:
            None.

        Returns:
            当前保存的变量值。
        """
        return self.value

    def set(self, value: Any) -> None:
        """替换当前值。

        Args:
            value: 新变量值。

        Returns:
            None: 值保存在实例中。
        """
        self.value = value


class FakeLayoutWidget:
    """记录 Tk 网格面板显示与隐藏调用。"""

    def __init__(self) -> None:
        """初始化空的布局调用记录。

        Args:
            None.

        Returns:
            None: 调用记录保存在实例属性中。
        """
        self.calls: list[str] = []

    def grid(self, **kwargs: Any) -> None:
        """记录面板显示调用。

        Args:
            **kwargs: Tk 网格布局参数。

        Returns:
            None: 调用名称写入记录。
        """
        self.calls.append("grid")

    def grid_remove(self) -> None:
        """记录面板隐藏调用。

        Args:
            None.

        Returns:
            None: 调用名称写入记录。
        """
        self.calls.append("grid_remove")

@pytest.fixture(scope="module")
def app_module() -> ModuleType:
    """加载桌面应用入口模块。

    Args:
        None.

    Returns:
        已加载的课程助手模块。

    Raises:
        RuntimeError: 无法创建入口模块加载器时抛出。
    """
    module_path = Path(__file__).parents[1] / "USTB-courseHelper.py"
    spec = importlib.util.spec_from_file_location("ustb_multi_user_app", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载课程助手入口模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_app(
    app_module: ModuleType, tmp_path: Path
) -> tuple[Any, str, str]:
    """创建包含两个登录用户的无界面应用实例。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。

    Returns:
        应用实例和两个用户 UUID。
    """
    store = UserProfileStore(tmp_path)
    first = store.create("[[USER_A]]")
    second = store.create("[[USER_B]]")
    runtime = MultiUserRuntime(store)
    runtime.set_cookies(first.id, {"SESSION": "[[COOKIE_A]]"})
    runtime.set_cookies(second.id, {"SESSION": "[[COOKIE_B]]"})
    runtime.select_profile(first.id)
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.profile_store = store
    app.runtime = runtime
    app.root = FakeRoot()
    app.student_name_var = FakeVariable(first.alias)
    app.current_student_name = first.alias
    app.current_student_display_var = FakeVariable(first.alias)
    app.rush_mode_var = FakeVariable("轮询模式")
    app.rush_time_var = FakeVariable("10:00:00")
    app.stop_on_success_var = FakeVariable(True)
    app.retry_full_var = FakeVariable(True)
    app.search_results_by_task_id = {}
    app.search_result_course_types_by_task_id = {}
    app.status_var = FakeVariable()
    return app, first.id, second.id


def test_query_result_remains_bound_to_origin_user(
    app_module: ModuleType, tmp_path: Path
) -> None:
    """验证切换用户后查询响应仍写回发起用户。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。

    Returns:
        None: 通过断言验证查询缓存没有串用户。
    """
    app, first_id, second_id = _make_app(app_module, tmp_path)
    result = type("Result", (), {"task_id": "[[TASK_ID]]"})()
    rendered: list[str] = []
    app.render_course_search_results = lambda results: rendered.append(results[0].task_id)

    app.runtime.select_profile(second_id)
    app.show_course_search_results(first_id, [result], {result.task_id: "[[TYPE]]"})

    first = app.runtime.require_context(first_id)
    second = app.runtime.require_context(second_id)
    assert first.search_results_by_task_id == {result.task_id: result}
    assert second.search_results_by_task_id == {}
    assert rendered == []


def test_switch_user_persists_and_loads_runtime_settings(
    app_module: ModuleType, tmp_path: Path
) -> None:
    """验证切换用户时保存旧设置并载入新用户设置。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。

    Returns:
        None: 通过断言验证每用户设置相互隔离。
    """
    app, first_id, second_id = _make_app(app_module, tmp_path)
    second = app.runtime.require_context(second_id)
    second.rush_mode = "定时抢课"
    second.rush_time = "12:34:56"
    second.stop_on_success = False
    second.retry_full = False
    app.rush_mode_var.set("轮询模式")
    app.rush_time_var.set("09:00:00")
    app.stop_on_success_var.set(True)
    app.retry_full_var.set(True)
    app.student_name_var.set(second.profile.alias)
    app.load_named_state_from_context = lambda context: None
    app.update_course_list = lambda: None
    app.refresh_user_views = lambda: None
    app.on_mode_change = lambda: None

    app.select_user_by_alias()

    first = app.runtime.require_context(first_id)
    assert (first.rush_mode, first.rush_time) == ("轮询模式", "09:00:00")
    assert app.rush_mode_var.get() == "定时抢课"
    assert app.rush_time_var.get() == "12:34:56"
    assert app.stop_on_success_var.get() is False
    assert app.retry_full_var.get() is False


def test_switch_user_renders_empty_cache_without_query_popup(
    app_module: ModuleType, tmp_path: Path
) -> None:
    """验证切换到无查询缓存用户时使用静默空状态。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。

    Returns:
        None: 通过断言验证用户切换不会模拟一次查询失败。
    """
    app, _, second_id = _make_app(app_module, tmp_path)
    second = app.runtime.require_context(second_id)
    app.student_name_var.set(second.profile.alias)
    app.course_result_tree = object()
    render_calls: list[tuple[list[object], bool]] = []
    app.render_course_search_results = (
        lambda results, notify_empty=True: render_calls.append(
            (list(results), notify_empty)
        )
    )
    app.load_named_state_from_context = lambda context: None
    app.update_course_list = lambda: None
    app.refresh_user_views = lambda: None
    app.on_mode_change = lambda: None

    app.select_user_by_alias()

    assert render_calls == [([], False)]


def test_expired_qr_login_releases_lock_for_another_user(
    app_module: ModuleType, tmp_path: Path
) -> None:
    """验证二维码过期后另一用户可以立即取得扫码锁。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。

    Returns:
        None: 通过断言验证失败状态、界面收尾和扫码锁释放。
    """
    app, first_id, second_id = _make_app(app_module, tmp_path)
    assert app.runtime.begin_login(first_id) is True
    logs: list[str] = []
    finished: list[tuple[str, bool, str]] = []
    app.user_log = lambda profile_id, message: logs.append(message)
    app.finish_login_ui = (
        lambda profile_id, succeeded, message: finished.append(
            (profile_id, succeeded, message)
        )
    )

    app.handle_qr_login_expired(first_id)

    first = app.runtime.require_context(first_id)
    assert first.login_state is UserLoginState.ERROR
    assert app.runtime.begin_login(second_id) is True
    assert finished == [(first_id, False, "二维码已过期，请重新扫码登录")]
    assert logs == ["二维码已过期，扫码登录已结束"]


def test_stop_only_affects_active_user(app_module: ModuleType, tmp_path: Path) -> None:
    """验证停止操作只设置当前用户的停止事件。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。

    Returns:
        None: 通过断言验证另一用户任务继续运行。
    """
    app, first_id, second_id = _make_app(app_module, tmp_path)
    app.runtime.mark_task_started(first_id, waiting=False)
    app.runtime.mark_task_started(second_id, waiting=False)
    app.runtime.select_profile(first_id)
    app.apply_active_user_control_state = lambda: None
    app.refresh_user_views = lambda: None

    app.stop_auto_selection()

    assert app.runtime.require_context(first_id).stop_event.is_set()
    assert app.runtime.require_context(first_id).task_state is UserTaskState.STOPPING
    assert not app.runtime.require_context(second_id).stop_event.is_set()
    assert app.runtime.require_context(second_id).task_state is UserTaskState.RUNNING


def test_expired_session_isolated_to_target_user(
    app_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证会话过期只清理指定用户并停止其任务。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。
        monkeypatch: pytest 运行时替换工具。

    Returns:
        None: 通过断言验证另一用户的 Cookie 和任务不变。
    """
    app, first_id, second_id = _make_app(app_module, tmp_path)
    app.runtime.mark_task_started(first_id, waiting=False)
    app.runtime.mark_task_started(second_id, waiting=False)
    app.refresh_user_views = lambda: None
    app.apply_active_user_control_state = lambda: None
    warnings: list[str] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showwarning",
        lambda title, message: warnings.append(message),
    )

    app.handle_session_expired(first_id)

    first = app.runtime.require_context(first_id)
    second = app.runtime.require_context(second_id)
    assert first.login_state is UserLoginState.EXPIRED
    assert first.stop_event.is_set()
    assert second.login_state is UserLoginState.LOGGED_IN
    assert second.cookies == {"SESSION": "[[COOKIE_B]]"}
    assert second.task_state is UserTaskState.RUNNING
    assert warnings


def test_quick_login_creates_default_profile_without_opening_management(
    app_module: ModuleType, tmp_path: Path
) -> None:
    """验证首次扫码入口创建默认用户且不暴露账号管理面板。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。

    Returns:
        None: 通过断言验证默认别名和登录目标 UUID。
    """
    store = UserProfileStore(tmp_path)
    runtime = MultiUserRuntime(store)
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.profile_store = store
    app.runtime = runtime
    app._quick_login_active = False
    app._quick_profile_id = None
    management_opened: list[bool] = []
    app.show_login_management = lambda: management_opened.append(True)
    app.refresh_user_views = lambda: None
    started: list[str] = []
    app._start_login_for_profile = lambda profile_id: started.append(profile_id)

    app.start_quick_login()

    profiles = store.list_all()
    assert len(profiles) == 1
    assert profiles[0].alias == "默认用户"
    assert started == [profiles[0].id]
    assert app._quick_login_active is True
    assert management_opened == []


def test_quick_login_reuses_inactive_default_profile(
    app_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证首次扫码会恢复并复用遗留的停用默认用户。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 通过断言验证不会重复创建同名用户。
    """
    store = UserProfileStore(tmp_path)
    existing = store.create("默认用户")
    store.deactivate(existing.id)
    runtime = MultiUserRuntime(store)
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.profile_store = store
    app.runtime = runtime
    app._quick_login_active = False
    app._quick_profile_id = None
    app.show_login_management = lambda: None
    app.refresh_user_views = lambda: None
    started: list[str] = []
    errors: list[str] = []
    app._start_login_for_profile = lambda profile_id: started.append(profile_id)
    monkeypatch.setattr(
        app_module.messagebox,
        "showerror",
        lambda title, message: errors.append(message),
    )

    app.start_quick_login()

    profiles = store.list_all(include_inactive=True)
    assert len(profiles) == 1
    assert profiles[0].id == existing.id
    assert profiles[0].active is True
    assert started == [existing.id]
    assert errors == []


def test_failed_added_account_login_restores_previous_account(
    app_module: ModuleType, tmp_path: Path
) -> None:
    """验证新增账号登录失败后恢复原当前账号。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。

    Returns:
        None: 通过断言验证原账号状态不受新增账号失败影响。
    """
    app, first_id, second_id = _make_app(app_module, tmp_path)
    app.runtime.select_profile(second_id)
    app._login_return_profile_id = first_id
    app._pending_added_profile_id = second_id
    selected: list[str] = []

    def select_profile(profile_id: str) -> None:
        """记录并切换测试账号。

        Args:
            profile_id: 目标账号 UUID。

        Returns:
            None: 目标账号成为当前账号。
        """
        selected.append(profile_id)
        app.runtime.select_profile(profile_id)

    app.select_user_by_id = select_profile

    app.restore_profile_after_failed_add(second_id)

    assert app.runtime.active_profile_id == first_id
    assert selected == [first_id]
    assert app._login_return_profile_id is None
    assert app._pending_added_profile_id is None


def test_successful_login_enters_workspace_without_info_dialog(
    app_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证登录成功后直接进入工作区且不弹出说明窗口。

    Args:
        app_module: 已加载的应用入口模块。
        tmp_path: pytest 临时目录。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 通过断言验证页面跳转和无成功弹窗行为。
    """
    class FakeButton:
        """提供按钮状态配置的最小替身。"""

        def config(self, **options: object) -> None:
            """接收按钮配置但不执行界面操作。

            Args:
                self: 当前按钮替身实例。
                **options: 待忽略的按钮配置项。

            Returns:
                None: 配置项不需要保存。
            """
            return None

    store = UserProfileStore(tmp_path)
    profile = store.create("默认用户")
    runtime = MultiUserRuntime(store)
    runtime.set_cookies(profile.id, {"SESSION": "[[COOKIE_A]]"})
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.profile_store = store
    app.runtime = runtime
    app.login_btn = FakeButton()
    app.status_var = FakeVariable()
    app._quick_login_active = True
    app._quick_profile_id = profile.id
    app._first_launch = True
    app.refresh_user_views = lambda: None
    app.current_profile_id = lambda: profile.id
    entered: list[bool] = []
    app.enable_workspace_tabs = lambda: entered.append(True)
    info_dialogs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_module.messagebox,
        "showinfo",
        lambda title, message: info_dialogs.append((title, message)),
    )

    app.finish_login_ui(profile.id, True, "登录成功")

    assert entered == [True]
    assert info_dialogs == []
    assert app._first_launch is False


def test_automatic_login_starts_only_once_after_driver_ready(
    app_module: ModuleType,
) -> None:
    """验证驱动就绪后的自动登录只启动一次且不需要确认按钮。

    Args:
        app_module: 已加载的课程助手入口模块。

    Returns:
        None: 通过断言验证重复回调不会重复启动登录。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app._automatic_login_started = False
    app._automatic_login_scheduled = True
    app._automatic_login_suppressed = False
    started: list[bool] = []
    app.start_login = lambda: started.append(True)

    app.start_automatic_login()
    app.start_automatic_login()

    assert started == [True]
    assert app._automatic_login_started is True
    assert app._automatic_login_scheduled is False


def test_offline_workspace_suppresses_pending_automatic_login(
    app_module: ModuleType,
) -> None:
    """验证离线进入会阻止尚未执行的后台自动登录。

    Args:
        app_module: 已加载的课程助手入口模块。

    Returns:
        None: 通过断言验证工作区正常打开且扫码不会启动。
    """
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app._automatic_login_started = False
    app._automatic_login_scheduled = True
    app._automatic_login_suppressed = False
    app.active_user_context = lambda: object()
    entered: list[bool] = []
    started: list[bool] = []
    app.enable_workspace_tabs = lambda: entered.append(True)
    app.start_login = lambda: started.append(True)

    app.enter_offline_workspace()
    app.start_automatic_login()

    assert app._automatic_login_suppressed is True
    assert entered == [True]
    assert started == []


def test_failed_login_restores_retry_action(
    app_module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证二维码失败后允许用户直接重新生成二维码。

    Args:
        app_module: 已加载的课程助手入口模块。
        tmp_path: pytest 临时目录。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 通过断言验证自动登录状态重置且重试按钮显示。
    """
    class FakeButton:
        """记录按钮配置与显示调用。"""

        def __init__(self) -> None:
            """初始化按钮调用记录。

            Args:
                None.

            Returns:
                None: 调用记录保存在实例属性中。
            """
            self.configured: dict[str, object] = {}
            self.shown = False

        def config(self, **options: object) -> None:
            """记录按钮配置参数。

            Args:
                **options: 按钮配置项。

            Returns:
                None: 配置项写入记录。
            """
            self.configured.update(options)

        def grid(self, **options: object) -> None:
            """记录按钮重新显示。

            Args:
                **options: 网格布局参数。

            Returns:
                None: 显示状态写入记录。
            """
            self.shown = True

    store = UserProfileStore(tmp_path)
    profile = store.create("默认用户")
    runtime = MultiUserRuntime(store)
    app = app_module.CourseSelectionApp.__new__(app_module.CourseSelectionApp)
    app.profile_store = store
    app.runtime = runtime
    app.login_btn = FakeButton()
    app.quick_login_btn = app.login_btn
    app.status_var = FakeVariable()
    app._automatic_login_started = True
    app._automatic_login_scheduled = False
    app._automatic_login_suppressed = False
    app._quick_login_active = False
    app._quick_profile_id = None
    app.refresh_user_views = lambda: None
    app.current_profile_id = lambda: profile.id
    app.reset_qr_display = lambda message: None
    app.restore_profile_after_failed_add = lambda profile_id: None
    app.show_quick_login = lambda: None
    monkeypatch.setattr(app_module.messagebox, "showerror", lambda *args: None)

    app.finish_login_ui(profile.id, False, "二维码已过期")

    assert app._automatic_login_started is False
    assert app.quick_login_btn.shown is True
    assert app.status_var.get() == "二维码已过期"
