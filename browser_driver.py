"""Chrome 浏览器驱动的自动匹配与启动封装。"""

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.remote.webdriver import WebDriver


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
