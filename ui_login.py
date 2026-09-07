"""微信扫码登录流程、登录页状态与进度显示。

登录通过纯 HTTP 微信扫码完成：程序直接调用统一认证服务器生成二维码并
轮询扫码结果，不依赖 Chrome、ChromeDriver 或 Selenium。界面线程与后台
认证线程通过 ``root.after`` 调度更新，同一时刻仅允许一名用户扫码。
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox
from io import BytesIO
from PIL import Image, ImageTk

from ustb_sso_client import (
    SsoAuthError,
    SsoBadResponseError,
    SsoLoginCanceledError,
    SsoQrExpiredError,
    SsoQrTimeoutError,
    UstbSsoQrLogin,
)
from user_profiles import UserProfileStoreError
import app_legacy


class LoginMixin:
    def is_multi_account(self: "CourseSelectionApp") -> bool:
        """判断是否存在两个及以上启用账号。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            True 表示顶栏与登录页需要显示账号目标选择器。
        """
        return len(self.runtime.enabled_contexts()) > 1

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
        self.status_var = tk.StringVar(value="正在准备登录...")
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
            text="正在准备登录...",
            anchor="center",
            justify=tk.CENTER,
            style="SurfaceMuted.TLabel",
        )
        self.quick_qr_label.grid(row=0, column=0, sticky="nsew")
        self.qr_label = self.quick_qr_label

        self.login_progress_frame = ttk.Frame(
            self.quick_qr_frame, style="App.TFrame", padding=(0, 10)
        )
        self.login_progress_frame.grid(row=1, column=0, sticky="ew")
        self.login_progress_frame.columnconfigure(0, weight=1)
        self.login_progress_bar = ttk.Progressbar(
            self.login_progress_frame, mode="indeterminate", maximum=100
        )
        self.login_progress_bar.grid(row=0, column=0, sticky="ew")
        self.login_progress_label = ttk.Label(
            self.login_progress_frame,
            text="正在连接统一认证...",
            anchor="center",
            style="SurfaceMuted.TLabel",
        )
        self.login_progress_label.grid(row=1, column=0, pady=(6, 0))

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
        self.begin_automatic_login()

    def begin_automatic_login(self: "CourseSelectionApp") -> None:
        """进入自动扫码登录准备状态，无需等待浏览器驱动。

        Args:
            self: 当前课程助手应用实例。

        Returns:
            None: 准备就绪后自动安排一次扫码登录。
        """
        self.status_var.set("正在准备登录...")
        self.reset_qr_display("正在准备登录...")
        self.start_login_loading("正在连接统一认证...")
        self.login_btn.config(state=tk.DISABLED)
        if hasattr(self, "quick_login_btn"):
            self.quick_login_btn.config(state=tk.DISABLED)
            self.quick_login_btn.grid_remove()
        self.schedule_automatic_login()

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
        self.start_login_loading("正在生成登录二维码...")
        self.login_btn.config(state=tk.DISABLED)
        if hasattr(self, "quick_login_btn"):
            self.quick_login_btn.config(state=tk.DISABLED)
            self.quick_login_btn.grid_remove()
        status = (
            f"正在为 {context.profile.alias} 启动登录..."
            if self.is_multi_account()
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

    def _post_login_stage(self: "CourseSelectionApp", stage: str) -> None:
        """在工作线程中把登录阶段文本调度回主线程更新。

        Args:
            self: 当前课程助手应用实例。
            stage: 需要显示的登录阶段描述。

        Returns:
            None: 阶段文本通过 Tkinter 主线程回调更新。
        """
        self.root.after(0, lambda: self.update_login_loading_stage(stage))

    def login_process(
        self: "CourseSelectionApp", profile_id: str | None = None
    ) -> None:
        """通过纯 HTTP 微信扫码完成统一认证并绑定 Cookie 到指定用户。

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
        try:
            self.user_log(target_id, "正在连接统一认证服务器")
            self._post_login_stage("正在连接统一认证...")
            flow = UstbSsoQrLogin()
            flow.open_auth()
            self.user_log(target_id, "已取得统一认证会话，正在生成二维码")
            self._post_login_stage("正在生成登录二维码...")
            flow.prepare_qr_code()
            qr_content = flow.fetch_qr_image()
            image = Image.open(BytesIO(qr_content))
            image = image.resize((300, 300), Image.Resampling.NEAREST)
            tk_image = ImageTk.PhotoImage(image)
            self.root.after(
                0,
                lambda value=tk_image: self.update_qr_image(
                    value, context.profile.alias
                ),
            )
            self.user_log(target_id, "二维码已生成，请使用微信扫码确认")
            pass_code = flow.wait_for_pass_code(
                should_stop=lambda: bool(app_legacy.stop_display)
            )
            self.user_log(target_id, "扫码确认成功，正在完成登录")
            self._post_login_stage("登录确认成功，正在进入系统...")
            cookies = flow.complete_auth(pass_code)
            if not cookies:
                raise SsoBadResponseError("登录响应未返回任何 Cookie")
            self.runtime.finish_login(target_id, cookies)
            self.user_log(target_id, f"登录成功，已获取 {len(cookies)} 个 Cookie")
            self.start_online_keepalive()
            self.root.after(
                0, lambda: self.finish_login_ui(target_id, True, "登录成功")
            )
        except SsoLoginCanceledError:
            message = "登录已取消"
            self.runtime.fail_login(target_id, message)
            self.user_log(target_id, message)
            self.root.after(
                0, lambda message=message: self.finish_login_ui(target_id, False, message)
            )
        except SsoQrExpiredError:
            self.handle_qr_login_expired(target_id)
        except SsoQrTimeoutError:
            message = "二维码等待超时，请重新生成二维码"
            self.runtime.fail_login(target_id, message)
            self.user_log(target_id, message)
            self.root.after(
                0, lambda message=message: self.finish_login_ui(target_id, False, message)
            )
        except SsoAuthError as error:
            error_message = f"登录失败：{error}"
            self.runtime.fail_login(target_id, error_message)
            self.user_log(target_id, error_message)
            self.root.after(
                0,
                lambda message=error_message: self.finish_login_ui(target_id, False, message),
            )
        except Exception as error:
            error_message = f"登录出错：{error}"
            self.runtime.fail_login(target_id, error_message)
            self.user_log(target_id, error_message)
            self.root.after(
                0,
                lambda message=error_message: self.finish_login_ui(target_id, False, message),
            )

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

    def start_login_loading(
        self: "CourseSelectionApp", stage: str = "正在连接统一认证..."
    ) -> None:
        """启动登录加载进度条并设置当前阶段文本。

        Args:
            self: 当前课程助手应用实例。
            stage: 需要显示的加载阶段描述。

        Returns:
            None: 进度条已运行时只更新阶段文本。
        """
        label = getattr(self, "login_progress_label", None)
        if label is not None:
            label.config(text=stage)
        bar = getattr(self, "login_progress_bar", None)
        if bar is None:
            return
        if not getattr(self, "_login_progress_started", False):
            bar.start(80)
            self._login_progress_started = True
        frame = getattr(self, "login_progress_frame", None)
        if frame is not None:
            frame.grid()

    def update_login_loading_stage(self: "CourseSelectionApp", stage: str) -> None:
        """仅更新登录加载阶段文本并保持进度条动画。

        Args:
            self: 当前课程助手应用实例。
            stage: 需要显示的加载阶段描述。

        Returns:
            None: 无阶段标签时静默返回。
        """
        label = getattr(self, "login_progress_label", None)
        if label is not None:
            label.config(text=stage)

    def stop_login_loading(self: "CourseSelectionApp", hidden: bool = True) -> None:
        """停止并可选隐藏登录加载进度条。

        Args:
            self: 当前课程助手应用实例。
            hidden: True 时同时隐藏进度条区域。

        Returns:
            None: 未启动进度条的实例静默返回。
        """
        bar = getattr(self, "login_progress_bar", None)
        if bar is not None and getattr(self, "_login_progress_started", False):
            bar.stop()
            self._login_progress_started = False
        if hidden:
            frame = getattr(self, "login_progress_frame", None)
            if frame is not None:
                frame.grid_remove()

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
        self.stop_login_loading()
        status = (
            f"请为 {alias} 扫描二维码"
            if self.is_multi_account()
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
        self.stop_login_loading()
        self.refresh_user_views()
        context = self.runtime.require_context(profile_id)
        if self.current_profile_id() == profile_id:
            status = (
                f"{context.profile.alias}：{message}"
                if self.is_multi_account()
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
