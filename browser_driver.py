"""Chrome 浏览器驱动的自动匹配与启动封装。"""

import threading
import time
from collections.abc import Callable

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.remote.webdriver import WebDriver


QR_LOGIN_TIMEOUT_SECONDS = 180.0
_QR_EXPIRED_MARKERS = (
    "二维码已过期",
    "二维码已失效",
    "二维码失效",
    "请刷新二维码",
)


def create_chrome_driver(chrome_options: Options) -> WebDriver:
    """
    使用 Selenium Manager 自动匹配并启动本机 Chrome 的驱动。

    Args:
        chrome_options: 启动 Chrome 时使用的 Selenium 配置项。

    Returns:
        已启动且可供调用的 Selenium WebDriver 实例。

    Raises:
        RuntimeError: 当 Chrome 未安装、驱动无法自动下载或浏览器无法启动时抛出。
    """
    try:
        return webdriver.Chrome(options=chrome_options)
    except WebDriverException as error:
        raise RuntimeError(
            "无法自动匹配并启动 ChromeDriver。请确认 Chrome 已安装，并检查网络或代理"
            "是否可访问 Chrome for Testing 下载源。原始错误："
            f"{error}"
        ) from error


def is_qr_login_expired(
    page_text: str,
    generated_at: float,
    current_time: float | None = None,
    timeout_seconds: float = QR_LOGIN_TIMEOUT_SECONDS,
) -> bool:
    """判断统一认证二维码是否已失效或超过等待时限。

    Args:
        page_text: 二维码页面当前可见文本。
        generated_at: 当前二维码生成时的单调时钟秒数。
        current_time: 检查时的单调时钟秒数；为 None 时读取系统时钟。
        timeout_seconds: 未检测到失效文本时使用的兜底超时秒数。

    Returns:
        True 表示二维码已失效，应结束当前扫码流程并释放登录锁。

    Raises:
        ValueError: 超时时间不大于零时抛出。
    """
    if timeout_seconds <= 0:
        raise ValueError("二维码登录超时时间必须大于零")
    normalized_text = page_text.casefold()
    if any(marker.casefold() in normalized_text for marker in _QR_EXPIRED_MARKERS):
        return True
    checked_at = time.monotonic() if current_time is None else current_time
    return checked_at - generated_at >= timeout_seconds


class ChromeDriverWarmup:
    """协调启动期 ChromeDriver 预匹配及登录期结果复用。"""

    def __init__(
        self,
        driver_factory: Callable[[Options], WebDriver] = create_chrome_driver,
    ) -> None:
        """初始化单次执行的浏览器驱动预热状态。

        Args:
            driver_factory: 使用 Selenium 配置创建临时 WebDriver 的函数。

        Returns:
            None: 初始化结果保存在当前协调器中。
        """
        self._driver_factory = driver_factory
        self._state_lock = threading.Lock()
        self._completed = threading.Event()
        self._started = False
        self._error: Exception | None = None

    def run(self, chrome_options: Options) -> None:
        """执行一次驱动匹配并立即关闭用于预热的临时浏览器。

        Args:
            chrome_options: 启动临时 Chrome 时使用的 Selenium 配置项。

        Returns:
            None: 匹配结果或异常会保存并通知所有等待者。
        """
        with self._state_lock:
            if self._started:
                return
            self._started = True
        driver: WebDriver | None = None
        try:
            driver = self._driver_factory(chrome_options)
        except Exception as error:
            with self._state_lock:
                self._error = error
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            self._completed.set()

    def status(self) -> tuple[bool, str | None]:
        """返回不会阻塞调用线程的预匹配状态快照。

        Args:
            self: 当前浏览器驱动预热协调器。

        Returns:
            二元组第一项表示预匹配是否结束，第二项为原始失败原因文本；
            未结束或成功时失败原因均为 None。
        """
        completed = self._completed.is_set()
        with self._state_lock:
            error_message = str(self._error) if self._error is not None else None
        return completed, error_message

    def wait(self) -> None:
        """等待后台预匹配完成，并传播可操作的失败信息。

        Args:
            None.

        Returns:
            None: 匹配成功时立即返回。

        Raises:
            RuntimeError: 后台匹配或临时浏览器启动失败时抛出。
        """
        self._completed.wait()
        if self._error is not None:
            raise RuntimeError(
                f"后台预匹配 ChromeDriver 失败：{self._error}"
            ) from self._error
