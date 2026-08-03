"""为桌面应用提供线程安全的按日文件日志输出流。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TextIO


class DailyLogWriter:
    """将标准输出按行追加到项目 logs 目录中的每日日志文件。"""

    def __init__(
        self,
        base_directory: str | Path,
        terminal: TextIO | None,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        """初始化日志目录、终端回显和线程安全写入状态。

        Args:
            base_directory: 应用所在目录，日志会写入其 `logs` 子目录。
            terminal: 可选的原标准输出流；为 None 时只写日志文件。
            now_provider: 返回当前本地时间的函数，用于按日轮转和时间戳。

        Returns:
            None: 日志目录会立即创建，写入状态保存在实例中。

        Raises:
            OSError: 日志目录无法创建时抛出。
        """
        self.logs_directory = Path(base_directory) / "logs"
        self.logs_directory.mkdir(parents=True, exist_ok=True)
        self.terminal = terminal
        self._now_provider = now_provider
        self._write_lock = threading.Lock()
        self._pending_text = ""

    def current_log_path(self) -> Path:
        """返回当前日期对应的应用日志文件路径。

        Args:
            None.

        Returns:
            `logs/application-YYYY-MM-DD.log` 路径。
        """
        current_date = self._now_provider().strftime("%Y-%m-%d")
        return self.logs_directory / f"application-{current_date}.log"

    def write(self, message: str) -> int:
        """回显文本并将其中的完整行线程安全地追加到日志文件。

        Args:
            message: 标准输出传入的文本片段。

        Returns:
            接收到的文本字符数。

        Raises:
            OSError: 日志目录或日志文件无法写入时抛出。
        """
        self._write_to_terminal(message)
        with self._write_lock:
            self._pending_text += message
            while "\n" in self._pending_text:
                line, self._pending_text = self._pending_text.split("\n", 1)
                if line:
                    self._append_line(line)
        return len(message)

    def flush(self) -> None:
        """刷新终端，并把尚未换行的日志片段写入文件。

        Args:
            None.

        Returns:
            None: 缓冲文本写入后会被清空。

        Raises:
            OSError: 日志文件无法写入时抛出。
        """
        if self.terminal is not None:
            self.terminal.flush()
        with self._write_lock:
            if self._pending_text:
                self._append_line(self._pending_text)
                self._pending_text = ""

    def _write_to_terminal(self, message: str) -> None:
        """将日志文本回显到原终端并兼容不支持 Unicode 的编码。

        Args:
            message: 需要回显的日志文本。

        Returns:
            None: 无终端时直接返回。
        """
        if self.terminal is None:
            return
        try:
            self.terminal.write(message)
        except UnicodeEncodeError:
            encoded_message = message.encode(
                getattr(self.terminal, "encoding", None) or "utf-8",
                errors="replace",
            )
            buffer = getattr(self.terminal, "buffer", None)
            if buffer is not None:
                buffer.write(encoded_message)
            else:
                self.terminal.write(encoded_message.decode("utf-8", errors="replace"))

    def _append_line(self, line: str) -> None:
        """为单行文本添加时间戳并追加到当前日期日志文件。

        Args:
            line: 不包含换行符的日志文本。

        Returns:
            None: 文本以 UTF-8 编码追加写入。

        Raises:
            OSError: 日志目录或文件无法写入时抛出。
        """
        self.logs_directory.mkdir(parents=True, exist_ok=True)
        current_time = self._now_provider()
        timestamp = current_time.strftime("%Y-%m-%d %H:%M:%S")
        log_path = self.logs_directory / f"application-{current_time:%Y-%m-%d}.log"
        with log_path.open("a", encoding="utf-8", newline="\n") as log_file:
            log_file.write(f"[{timestamp}] {line}\n")
