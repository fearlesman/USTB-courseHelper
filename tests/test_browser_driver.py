"""浏览器驱动自动匹配逻辑的单元测试。"""

import threading
from unittest.mock import MagicMock, patch

import pytest
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

from browser_driver import (
    ChromeDriverWarmup,
    create_chrome_driver,
    is_qr_login_expired,
)


@patch("browser_driver.webdriver.Chrome")
def test_create_chrome_driver_uses_selenium_manager(
    mock_chrome: MagicMock,
) -> None:
    """
    验证浏览器启动不指定本地 ChromeDriver 路径。

    Args:
        mock_chrome: 被模拟的 Selenium Chrome 构造器。

    Returns:
        None: 测试仅通过断言验证调用参数。
    """
    chrome_options = Options()
    expected_driver = MagicMock()
    mock_chrome.return_value = expected_driver

    driver = create_chrome_driver(chrome_options)

    assert driver is expected_driver
    mock_chrome.assert_called_once_with(options=chrome_options)


@patch("browser_driver.webdriver.Chrome")
def test_create_chrome_driver_reports_actionable_error(
    mock_chrome: MagicMock,
) -> None:
    """
    验证自动匹配失败时保留原始异常并给出中文诊断信息。

    Args:
        mock_chrome: 被模拟的 Selenium Chrome 构造器。

    Returns:
        None: 测试仅通过断言验证异常内容。
    """
    mock_chrome.side_effect = WebDriverException("模拟的驱动下载失败")

    with pytest.raises(RuntimeError) as error_info:
        create_chrome_driver(Options())

    error_message = str(error_info.value)
    assert "无法自动匹配并启动 ChromeDriver" in error_message
    assert "Chrome for Testing" in error_message
    assert "模拟的驱动下载失败" in error_message


def test_driver_warmup_matches_once_and_closes_temporary_browser() -> None:
    """验证后台预热只执行一次并关闭临时浏览器。

    Args:
        None.

    Returns:
        None: 测试通过断言验证匹配调用和浏览器释放行为。
    """
    chrome_options = Options()
    driver = MagicMock()
    driver_factory = MagicMock(return_value=driver)
    warmup = ChromeDriverWarmup(driver_factory=driver_factory)

    warmup.run(chrome_options)
    warmup.run(chrome_options)
    warmup.wait()

    driver_factory.assert_called_once_with(chrome_options)
    driver.quit.assert_called_once_with()


def test_driver_warmup_propagates_background_failure() -> None:
    """验证登录等待预热时能收到后台匹配的原始失败原因。

    Args:
        None.

    Returns:
        None: 测试通过断言验证中文错误与原始原因。
    """
    driver_factory = MagicMock(side_effect=RuntimeError("[[DRIVER_MATCH_ERROR]]"))
    warmup = ChromeDriverWarmup(driver_factory=driver_factory)

    warmup.run(Options())

    with pytest.raises(RuntimeError) as error_info:
        warmup.wait()

    assert "后台预匹配 ChromeDriver 失败" in str(error_info.value)
    assert "[[DRIVER_MATCH_ERROR]]" in str(error_info.value)


def test_driver_warmup_exposes_non_blocking_status() -> None:
    """验证界面可以无阻塞读取驱动预匹配状态和失败原因。

    Args:
        None.

    Returns:
        None: 通过断言验证未完成、成功和失败三种状态。
    """
    successful = ChromeDriverWarmup(driver_factory=MagicMock(return_value=MagicMock()))
    assert successful.status() == (False, None)

    successful.run(Options())

    assert successful.status() == (True, None)

    failed = ChromeDriverWarmup(
        driver_factory=MagicMock(side_effect=RuntimeError("[[DRIVER_MATCH_ERROR]]"))
    )
    failed.run(Options())
    completed, error_message = failed.status()

    assert completed is True
    assert error_message is not None
    assert "[[DRIVER_MATCH_ERROR]]" in error_message


def test_driver_warmup_waits_for_single_running_match() -> None:
    """验证登录等待正在运行的预热且不会触发第二次匹配。

    Args:
        None.

    Returns:
        None: 测试通过线程事件断言等待和单次执行行为。
    """
    factory_started = threading.Event()
    release_factory = threading.Event()
    wait_finished = threading.Event()
    driver = MagicMock()
    factory_calls: list[Options] = []

    def blocking_factory(chrome_options: Options) -> MagicMock:
        """阻塞模拟驱动创建直到测试允许完成。

        Args:
            chrome_options: 模拟 Selenium 启动选项。

        Returns:
            模拟的临时浏览器驱动。
        """
        factory_calls.append(chrome_options)
        factory_started.set()
        release_factory.wait(timeout=2)
        return driver

    def wait_for_warmup() -> None:
        """等待预热完成并记录等待线程已返回。

        Args:
            None.

        Returns:
            None: 完成事件会在预热成功后设置。
        """
        warmup.wait()
        wait_finished.set()

    options = Options()
    warmup = ChromeDriverWarmup(driver_factory=blocking_factory)
    worker = threading.Thread(target=warmup.run, args=(options,))
    worker.start()
    assert factory_started.wait(timeout=1)

    waiter = threading.Thread(target=wait_for_warmup)
    waiter.start()
    warmup.run(options)
    assert not wait_finished.wait(timeout=0.05)

    release_factory.set()
    worker.join(timeout=2)
    waiter.join(timeout=2)

    assert wait_finished.is_set()
    assert factory_calls == [options]
    driver.quit.assert_called_once_with()


@pytest.mark.parametrize("marker", ["二维码已过期", "二维码已失效", "请刷新二维码"])
def test_qr_expiration_detects_page_markers(marker: str) -> None:
    """验证统一认证页面的常见失效提示可以结束扫码流程。

    Args:
        marker: 模拟认证页面显示的二维码失效文本。

    Returns:
        None: 测试通过断言验证文本失效判定。
    """
    assert is_qr_login_expired(marker, generated_at=10.0, current_time=20.0)


def test_qr_expiration_uses_timeout_fallback() -> None:
    """验证页面未显示失效文本时仍能按超时释放扫码流程。

    Args:
        None.

    Returns:
        None: 测试通过断言验证超时边界。
    """
    assert not is_qr_login_expired(
        "等待扫码", generated_at=10.0, current_time=189.9
    )
    assert is_qr_login_expired(
        "等待扫码", generated_at=10.0, current_time=190.0
    )
