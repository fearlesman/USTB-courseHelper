"""多用户运行时会话、任务和日志隔离测试。"""

from __future__ import annotations

from pathlib import Path

from multi_user_runtime import MultiUserRuntime, UserLoginState, UserTaskState
from user_profiles import UserProfileStore


def _runtime(tmp_path: Path) -> tuple[MultiUserRuntime, str, str]:
    """创建包含两名用户的运行时管理器。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        运行时管理器及两名用户 UUID。
    """
    profile_store = UserProfileStore(tmp_path)
    first = profile_store.create("[[USER_A]]")
    second = profile_store.create("[[USER_B]]")
    return MultiUserRuntime(profile_store), first.id, second.id


def test_cookies_and_logs_are_isolated(tmp_path: Path) -> None:
    """验证不同用户的 Cookie 与日志不会串用。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证运行时隔离。
    """
    runtime, first_id, second_id = _runtime(tmp_path)
    runtime.set_cookies(first_id, {"SESSION": "[[TOKEN_A]]"})
    runtime.set_cookies(second_id, {"SESSION": "[[TOKEN_B]]"})
    runtime.append_log(first_id, "[[LOG_A]]")
    runtime.append_log(second_id, "[[LOG_B]]")

    assert runtime.require_context(first_id).cookies == {"SESSION": "[[TOKEN_A]]"}
    assert runtime.require_context(second_id).cookies == {"SESSION": "[[TOKEN_B]]"}
    assert runtime.logs_for(first_id) == ["[[LOG_A]]"]
    assert runtime.logs_for(second_id) == ["[[LOG_B]]"]


def test_only_one_qr_login_can_run_at_a_time(tmp_path: Path) -> None:
    """验证 Selenium 扫码登录流程使用全局串行锁。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证登录锁。
    """
    runtime, first_id, second_id = _runtime(tmp_path)

    assert runtime.begin_login(first_id) is True
    assert runtime.begin_login(second_id) is False
    runtime.finish_login(first_id, {"SESSION": "[[TOKEN_A]]"})
    assert runtime.begin_login(second_id) is True


def test_expiring_one_session_does_not_change_another(tmp_path: Path) -> None:
    """验证单用户会话过期只停止该用户任务。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证会话和停止事件隔离。
    """
    runtime, first_id, second_id = _runtime(tmp_path)
    runtime.set_cookies(first_id, {"SESSION": "[[TOKEN_A]]"})
    runtime.set_cookies(second_id, {"SESSION": "[[TOKEN_B]]"})
    runtime.mark_task_started(first_id, waiting=False)
    runtime.mark_task_started(second_id, waiting=False)

    runtime.expire_session(first_id)

    first = runtime.require_context(first_id)
    second = runtime.require_context(second_id)
    assert first.login_state is UserLoginState.EXPIRED
    assert first.stop_event.is_set()
    assert second.login_state is UserLoginState.LOGGED_IN
    assert second.task_state is UserTaskState.RUNNING
    assert not second.stop_event.is_set()


def test_tasks_start_and_stop_independently(tmp_path: Path) -> None:
    """验证每名用户最多一个任务且停止操作互不影响。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证任务状态机。
    """
    runtime, first_id, second_id = _runtime(tmp_path)
    runtime.mark_task_started(first_id, waiting=True)
    runtime.mark_task_started(second_id, waiting=False)

    runtime.request_stop(first_id)

    assert runtime.require_context(first_id).task_state is UserTaskState.STOPPING
    assert runtime.require_context(second_id).task_state is UserTaskState.RUNNING
    assert runtime.require_context(first_id).stop_event.is_set()
    assert not runtime.require_context(second_id).stop_event.is_set()


def test_stop_all_sets_every_active_stop_event(tmp_path: Path) -> None:
    """验证关闭程序时可统一停止全部活动任务。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证全部停止事件。
    """
    runtime, first_id, second_id = _runtime(tmp_path)
    runtime.mark_task_started(first_id, waiting=False)
    runtime.mark_task_started(second_id, waiting=False)

    runtime.stop_all()

    assert runtime.require_context(first_id).stop_event.is_set()
    assert runtime.require_context(second_id).stop_event.is_set()


def test_context_queries_filter_enabled_and_active_tasks(tmp_path: Path) -> None:
    """验证界面可通过只读快照查询启用账号和活动任务。

    Args:
        tmp_path: pytest 临时目录。

    Returns:
        None: 通过断言验证停用账号过滤和活动状态筛选。
    """
    store = UserProfileStore(tmp_path)
    first = store.create("[[USER_A]]")
    second = store.create("[[USER_B]]")
    inactive = store.create("[[USER_C]]")
    store.deactivate(inactive.id)
    runtime = MultiUserRuntime(store)
    runtime.mark_task_started(second.id, waiting=True)

    enabled = runtime.enabled_contexts()
    active_tasks = runtime.active_task_contexts(exclude_profile_id=first.id)

    assert isinstance(enabled, tuple)
    assert [context.profile.id for context in enabled] == [first.id, second.id]
    assert [context.profile.id for context in active_tasks] == [second.id]
