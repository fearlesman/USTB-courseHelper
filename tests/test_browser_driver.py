"""浏览器驱动自动匹配逻辑的单元测试。"""

from unittest.mock import MagicMock, patch

import pytest
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

from browser_driver import create_chrome_driver


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
