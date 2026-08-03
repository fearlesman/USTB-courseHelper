"""提供不落盘凭据的多用户会话与任务运行时状态。"""

from __future__ import annotations

import copy
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from user_profiles import UserProfile, UserProfileStore, UserProfileStoreError


class UserLoginState(str, Enum):
    """表示单个用户的运行期登录状态。"""

    NOT_LOGGED_IN = "待登录"
    LOGGING_IN = "登录中"
    LOGGED_IN = "已登录"
    EXPIRED = "已过期"
    ERROR = "登录失败"


class UserTaskState(str, Enum):
    """表示单个用户的抢课任务状态。"""

    IDLE = "空闲"
    WAITING = "等待定时"
    RUNNING = "抢课中"
    STOPPING = "正在停止"
    SUCCEEDED = "已成功"
    FAILED = "已失败"
    STOPPED = "已停止"


@dataclass
class UserRuntimeContext:
    """保存一名用户仅在当前进程中存在的运行状态。

    Attributes:
        profile: 对应的持久化用户档案。
        cookies: 仅在内存中的登录 Cookie。
        login_state: 当前登录状态。
        task_state: 当前抢课任务状态。
        courses: 当前草稿课程。
        stop_event: 该用户独立的任务停止事件。
        task_thread: 该用户当前后台任务线程。
        logs: 该用户最近的控制台日志。
        last_result: 最近一次登录或抢课结果摘要。
        rush_mode: 当前运行期抢课模式。
        rush_time: 当前运行期定时时刻文本。
        stop_on_success: 选到课程后是否停止。
        retry_full: 课程已满时是否继续重试。
        current_saved_list_id: 当前加载的命名列表 UUID。
        current_saved_list_name: 当前命名列表名称。
        current_saved_list_note: 当前命名列表备注。
        current_saved_list_dirty: 当前课程是否有未保存变化。
        search_results_by_task_id: 该用户最近一次查询结果映射。
        search_result_course_types_by_task_id: 查询结果的选课方式映射。
    """

    profile: UserProfile
    cookies: dict[str, str] = field(default_factory=dict)
    login_state: UserLoginState = UserLoginState.NOT_LOGGED_IN
    task_state: UserTaskState = UserTaskState.IDLE
    courses: list[dict[str, Any]] = field(default_factory=list)
    stop_event: threading.Event = field(default_factory=threading.Event)
    task_thread: threading.Thread | None = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=500))
    last_result: str = ""
    rush_mode: str = "轮询模式"
    rush_time: str = "10:00:00"
    stop_on_success: bool = True
    retry_full: bool = True
    current_saved_list_id: str | None = None
    current_saved_list_name: str = ""
    current_saved_list_note: str = ""
    current_saved_list_dirty: bool = False
    search_results_by_task_id: dict[str, Any] = field(default_factory=dict)
    search_result_course_types_by_task_id: dict[str, str] = field(default_factory=dict)


class MultiUserRuntime:
    """协调所有用户的内存会话、任务状态和日志。"""

    def __init__(self, profile_store: UserProfileStore) -> None:
        """从持久化档案创建不含凭据的运行时上下文。

        Args:
            profile_store: 用户档案与草稿存储。

        Returns:
            None: 启用和停用用户均会建立运行时上下文。

        Raises:
            UserProfileStoreError: 用户档案或草稿无法读取时抛出。
        """
        self.profile_store = profile_store
        self.contexts: dict[str, UserRuntimeContext] = {}
        self.active_profile_id: str | None = None
        self._login_lock = threading.Lock()
        self._login_profile_id: str | None = None
        self.sync_profiles()

    def sync_profiles(self) -> None:
        """同步用户档案并保留已有运行状态。

        Args:
            self: 当前多用户运行时实例。

        Returns:
            None: 新档案会创建上下文，现有档案会更新元数据。

        Raises:
            UserProfileStoreError: 档案或新用户草稿无法读取时抛出。
        """
        profiles = self.profile_store.list_all(include_inactive=True)
        valid_ids = {profile.id for profile in profiles}
        for profile in profiles:
            if profile.id in self.contexts:
                self.contexts[profile.id].profile = profile
            else:
                self.contexts[profile.id] = UserRuntimeContext(
                    profile=profile,
                    courses=self.profile_store.load_draft(profile.id),
                )
        for profile_id in set(self.contexts) - valid_ids:
            self.contexts.pop(profile_id)
        active_profiles = [profile for profile in profiles if profile.active]
        if self.active_profile_id not in {profile.id for profile in active_profiles}:
            self.active_profile_id = active_profiles[0].id if active_profiles else None

    def require_context(self, profile_id: str) -> UserRuntimeContext:
        """返回指定用户运行时上下文。

        Args:
            profile_id: 用户 UUID。

        Returns:
            对应的运行时上下文。

        Raises:
            UserProfileStoreError: 用户上下文不存在时抛出。
        """
        context = self.contexts.get(profile_id)
        if context is None:
            raise UserProfileStoreError("指定用户运行时上下文不存在")
        return context

    def active_context(self) -> UserRuntimeContext | None:
        """返回当前选择用户的运行时上下文。

        Args:
            None.

        Returns:
            当前用户上下文；尚无启用用户时返回 None。
        """
        if self.active_profile_id is None:
            return None
        return self.require_context(self.active_profile_id)

    def enabled_contexts(self) -> tuple[UserRuntimeContext, ...]:
        """返回按档案创建顺序排列的启用账号上下文快照。

        Args:
            self: 当前多用户运行时实例。

        Returns:
            不暴露内部上下文字典的启用账号上下文元组。

        Raises:
            UserProfileStoreError: 用户档案无法读取时抛出。
        """
        return tuple(
            self.require_context(profile.id)
            for profile in self.profile_store.list_all()
        )

    def active_task_contexts(
        self, exclude_profile_id: str | None = None
    ) -> tuple[UserRuntimeContext, ...]:
        """返回处于等待、抢课或停止阶段的启用账号上下文。

        Args:
            self: 当前多用户运行时实例。
            exclude_profile_id: 需要从结果中排除的账号 UUID；为 None 时不排除。

        Returns:
            按档案创建顺序排列的活动任务上下文元组。

        Raises:
            UserProfileStoreError: 用户档案无法读取时抛出。
        """
        active_states = {
            UserTaskState.WAITING,
            UserTaskState.RUNNING,
            UserTaskState.STOPPING,
        }
        return tuple(
            context
            for context in self.enabled_contexts()
            if context.profile.id != exclude_profile_id
            and context.task_state in active_states
        )

    def select_profile(self, profile_id: str) -> UserRuntimeContext:
        """选择当前活动用户。

        Args:
            profile_id: 必须处于启用状态的用户 UUID。

        Returns:
            新选择的用户上下文。

        Raises:
            UserProfileStoreError: 用户不存在或已停用时抛出。
        """
        context = self.require_context(profile_id)
        if not context.profile.active:
            raise UserProfileStoreError("已停用用户不能设为当前用户")
        self.active_profile_id = profile_id
        return context

    def begin_login(self, profile_id: str) -> bool:
        """尝试为指定用户取得全局扫码登录锁。

        Args:
            profile_id: 准备登录的用户 UUID。

        Returns:
            True 表示成功进入登录流程，False 表示已有其他登录流程。
        """
        context = self.require_context(profile_id)
        if not self._login_lock.acquire(blocking=False):
            return False
        self._login_profile_id = profile_id
        context.login_state = UserLoginState.LOGGING_IN
        context.last_result = "正在扫码登录"
        return True

    def finish_login(self, profile_id: str, cookies: Mapping[str, str]) -> None:
        """保存指定用户运行期 Cookie 并释放登录锁。

        Args:
            profile_id: 完成登录的用户 UUID。
            cookies: Selenium 提取的 Cookie 映射。

        Returns:
            None: Cookie 仅写入内存上下文。
        """
        self.set_cookies(profile_id, cookies)
        self._release_login(profile_id)

    def fail_login(self, profile_id: str, message: str) -> None:
        """记录登录失败并释放扫码登录锁。

        Args:
            profile_id: 登录失败的用户 UUID。
            message: 中文失败摘要。

        Returns:
            None: 用户状态和最后结果会更新。
        """
        context = self.require_context(profile_id)
        context.login_state = UserLoginState.ERROR
        context.last_result = message
        context.cookies.clear()
        self._release_login(profile_id)

    def set_cookies(self, profile_id: str, cookies: Mapping[str, str]) -> None:
        """将 Cookie 独立写入指定用户内存上下文。

        Args:
            profile_id: 用户 UUID。
            cookies: 登录 Cookie 映射。

        Returns:
            None: 用户登录状态会变为已登录。
        """
        context = self.require_context(profile_id)
        context.cookies = {str(key): str(value) for key, value in cookies.items()}
        context.login_state = UserLoginState.LOGGED_IN
        context.last_result = "登录成功"

    def expire_session(self, profile_id: str) -> None:
        """仅使指定用户会话过期并请求停止其任务。

        Args:
            profile_id: 会话失效的用户 UUID。

        Returns:
            None: 其他用户上下文不会变化。
        """
        context = self.require_context(profile_id)
        context.cookies.clear()
        context.login_state = UserLoginState.EXPIRED
        context.last_result = "会话已过期"
        if context.task_state in {UserTaskState.WAITING, UserTaskState.RUNNING}:
            context.stop_event.set()
            context.task_state = UserTaskState.STOPPING

    def mark_task_started(self, profile_id: str, waiting: bool) -> None:
        """初始化用户独立停止事件并标记任务已启动。

        Args:
            profile_id: 用户 UUID。
            waiting: True 表示处于定时等待阶段。

        Returns:
            None: 任务状态会变为等待或抢课中。

        Raises:
            RuntimeError: 该用户已有活动任务时抛出。
        """
        context = self.require_context(profile_id)
        if context.task_state in {
            UserTaskState.WAITING,
            UserTaskState.RUNNING,
            UserTaskState.STOPPING,
        }:
            raise RuntimeError("该用户已有抢课任务正在运行")
        context.stop_event = threading.Event()
        context.task_state = UserTaskState.WAITING if waiting else UserTaskState.RUNNING
        context.last_result = "等待定时" if waiting else "正在抢课"

    def mark_polling(self, profile_id: str) -> None:
        """将用户任务从定时等待切换为轮询中。

        Args:
            profile_id: 用户 UUID。

        Returns:
            None: 任务状态会变为抢课中。
        """
        context = self.require_context(profile_id)
        context.task_state = UserTaskState.RUNNING
        context.last_result = "正在抢课"

    def mark_task_finished(
        self, profile_id: str, succeeded: bool, message: str
    ) -> None:
        """记录用户任务结束结果。

        Args:
            profile_id: 用户 UUID。
            succeeded: True 表示至少一门课程成功。
            message: 最后结果摘要。

        Returns:
            None: 任务状态与结果会同步更新。
        """
        context = self.require_context(profile_id)
        if context.stop_event.is_set() and not succeeded:
            context.task_state = UserTaskState.STOPPED
        else:
            context.task_state = UserTaskState.SUCCEEDED if succeeded else UserTaskState.FAILED
        context.last_result = message
        context.task_thread = None

    def request_stop(self, profile_id: str) -> None:
        """请求停止指定用户的活动任务。

        Args:
            profile_id: 用户 UUID。

        Returns:
            None: 仅设置该用户停止事件。
        """
        context = self.require_context(profile_id)
        context.stop_event.set()
        if context.task_state in {UserTaskState.WAITING, UserTaskState.RUNNING}:
            context.task_state = UserTaskState.STOPPING
            context.last_result = "正在停止"

    def stop_all(self) -> None:
        """请求停止所有活动用户任务。

        Args:
            None.

        Returns:
            None: 每个活动上下文的停止事件都会设置。
        """
        for profile_id, context in self.contexts.items():
            if context.task_state in {UserTaskState.WAITING, UserTaskState.RUNNING}:
                self.request_stop(profile_id)

    def append_log(self, profile_id: str, message: str) -> None:
        """追加一条用户独立日志。

        Args:
            profile_id: 用户 UUID。
            message: 日志文本。

        Returns:
            None: 空文本不会写入日志队列。
        """
        normalized = message.strip()
        if normalized:
            self.require_context(profile_id).logs.append(normalized)

    def logs_for(self, profile_id: str) -> list[str]:
        """返回指定用户日志快照。

        Args:
            profile_id: 用户 UUID。

        Returns:
            不共享内部 deque 的日志列表。
        """
        return list(self.require_context(profile_id).logs)

    def course_snapshot(self, profile_id: str) -> list[dict[str, Any]]:
        """返回用户当前课程的深拷贝。

        Args:
            profile_id: 用户 UUID。

        Returns:
            可供后台任务稳定使用的课程快照。
        """
        return copy.deepcopy(self.require_context(profile_id).courses)

    def _release_login(self, profile_id: str) -> None:
        """在归属匹配时释放扫码登录锁。

        Args:
            profile_id: 尝试释放锁的用户 UUID。

        Returns:
            None: 不匹配的用户不会释放其他人的锁。
        """
        if self._login_profile_id == profile_id:
            self._login_profile_id = None
            if self._login_lock.locked():
                self._login_lock.release()
