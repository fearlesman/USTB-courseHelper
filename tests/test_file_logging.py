"""文件日志目录创建、按日写入与追加行为测试。"""

from datetime import datetime
from pathlib import Path

from file_logging import DailyLogWriter


def test_daily_log_writer_creates_logs_directory_and_appends_lines(
    tmp_path: Path,
) -> None:
    """验证日志目录自动创建且分段输出合并为完整日志行。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证目录、文件名、时间戳和追加内容。
    """

    def fixed_now() -> datetime:
        """返回固定时间以稳定日志文件名和时间戳。

        Args:
            None.

        Returns:
            用于测试的固定本地时间。
        """
        return datetime(2026, 8, 3, 12, 34, 56)

    writer = DailyLogWriter(tmp_path, terminal=None, now_provider=fixed_now)

    assert (tmp_path / "logs").is_dir()
    writer.write("第一段")
    writer.write("日志\n")
    writer.write("第二行\n")
    writer.flush()

    log_path = tmp_path / "logs" / "application-2026-08-03.log"
    assert log_path.read_text(encoding="utf-8") == (
        "[2026-08-03 12:34:56] 第一段日志\n"
        "[2026-08-03 12:34:56] 第二行\n"
    )


def test_daily_log_writer_appends_without_overwriting_existing_file(
    tmp_path: Path,
) -> None:
    """验证程序再次启动时继续追加当日日志而不是覆盖。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证两次写入均被保留。
    """

    def fixed_now() -> datetime:
        """返回固定日期供多个日志写入器共享文件。

        Args:
            None.

        Returns:
            用于测试的固定本地时间。
        """
        return datetime(2026, 8, 3, 8, 0, 0)

    first_writer = DailyLogWriter(tmp_path, terminal=None, now_provider=fixed_now)
    first_writer.write("[[FIRST_LOG]]\n")
    second_writer = DailyLogWriter(tmp_path, terminal=None, now_provider=fixed_now)
    second_writer.write("[[SECOND_LOG]]\n")

    content = (
        tmp_path / "logs" / "application-2026-08-03.log"
    ).read_text(encoding="utf-8")
    assert "[[FIRST_LOG]]" in content
    assert "[[SECOND_LOG]]" in content
