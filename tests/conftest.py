"""测试环境适配：容受限 Windows 沙箱中 `0o700` 目录不可访问的问题。

pytest 的临时目录机制会在 Windows 上以 ``mode=0o700`` 创建目录，
本沙箱环境下该类目录会立即失去访问权限（WinError 5），导致依赖
``tmp_path`` 的测试全部报错。这里在测试进程内将受限模式改回默认权限
并容忍清理阶段的权限错误，仅影响测试运行，不改变被测代码行为。
"""

from typing import Any

import os as _os

import _pytest.pathlib  # type: ignore[import-not-found]
import _pytest.tmpdir  # type: ignore[import-not-found]


_original_mkdir = _os.mkdir
_original_makedirs = _os.makedirs


def _permissive_mkdir(path: Any, mode: int = 0o777, *args: Any, **kwargs: Any) -> None:
    """忽略受限模式创建目录，保证目录在沙箱下可访问。

    Args:
        path: 需要创建的目录。
        mode: 被忽略的 POSIX 权限模式。
        *args: 透传给原始 os.mkdir 的位置参数。
        **kwargs: 透传给原始 os.mkdir 的关键字参数。

    Returns:
        None: 目录以默认权限创建。
    """
    _original_mkdir(path, *args, **kwargs)


def _permissive_makedirs(
    path: Any, mode: int = 0o777, *args: Any, **kwargs: Any
) -> None:
    """忽略受限模式递归创建目录。

    Args:
        path: 需要递归创建的目录。
        mode: 被忽略的 POSIX 权限模式。
        *args: 透传给原始 os.makedirs 的位置参数。
        **kwargs: 透传给原始 os.makedirs 的关键字参数。

    Returns:
        None: 目录以默认权限创建。
    """
    _original_makedirs(path, *args, **kwargs)


_os.mkdir = _permissive_mkdir  # type: ignore[assignment]
_os.makedirs = _permissive_makedirs  # type: ignore[assignment]

# pathlib.Path.mkdir 的 accessor 在模块导入时已捕获原始 os.mkdir，
# 单独补丁 os.mkdir 无法约束 Path.mkdir，因此需要再覆盖 Path.mkdir。
from pathlib import Path as _Path  # noqa: E402


def _permissive_path_mkdir(
    path_object: _Path,
    mode: int = 0o777,
    parents: bool = False,
    exist_ok: bool = False,
) -> None:
    """以默认权限创建 pathlib 目录，避免沙箱拒绝 0o700 目录。

    Args:
        path_object: 需要创建的 pathlib.Path 目录。
        mode: 被忽略的 POSIX 权限模式。
        parents: 是否同时创建缺失的父目录。
        exist_ok: 目录已存在时不抛出异常。

    Returns:
        None: 委托给原始 os.makedirs 完成目录创建。
    """
    _original_makedirs(path_object, mode=0o777, exist_ok=exist_ok)


_Path.mkdir = _permissive_path_mkdir  # type: ignore[assignment]

_original_cleanup = _pytest.pathlib.cleanup_dead_symlinks
_original_rmtree = _pytest.pathlib.rm_rf


def _tolerant_cleanup(root: object) -> None:
    """忽略清理临时目录时的权限错误，避免沙箱环境影响测试结果。

    Args:
        root: 需要清理的临时根目录。

    Returns:
        None: 权限错误会被吞掉，其余错误委托给原实现。
    """
    try:
        _original_cleanup(root)
    except OSError:
        pass


def _tolerant_rmtree(path: Any, onexc: Any | None = None) -> None:
    """忽略删除临时目录时的权限错误。

    Args:
        path: 需要删除的临时目录。
        onexc: 原始删除函数使用的错误回调。

    Returns:
        None: 权限错误会被吞掉，其余错误委托给原实现。
    """
    try:
        _original_rmtree(path, onexc)
    except OSError:
        pass


_pytest.pathlib.cleanup_dead_symlinks = _tolerant_cleanup  # type: ignore[assignment]
_pytest.tmpdir.cleanup_dead_symlinks = _tolerant_cleanup  # type: ignore[assignment]
_pytest.tmpdir.rm_rf = _tolerant_rmtree  # type: ignore[assignment]