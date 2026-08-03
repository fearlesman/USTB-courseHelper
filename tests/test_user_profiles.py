"""多用户档案持久化、迁移与安全边界测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from user_profiles import UserProfileStore, UserProfileStoreError


def test_create_rename_soft_delete_and_restore_profile(tmp_path: Path) -> None:
    """验证用户 UUID 稳定且软删除不会删除用户数据。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证完整档案生命周期。
    """
    store = UserProfileStore(tmp_path)
    profile = store.create(" [[USER_A]] ")
    draft_path = store.draft_path(profile.id)
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text("[]", encoding="utf-8")

    renamed = store.rename(profile.id, "[[RENAMED_USER]]")
    disabled = store.deactivate(profile.id)
    restored = store.restore(profile.id)

    assert renamed.id == profile.id
    assert renamed.alias == "[[RENAMED_USER]]"
    assert disabled.active is False
    assert restored.active is True
    assert draft_path.exists()


def test_alias_is_casefold_unique_across_inactive_profiles(tmp_path: Path) -> None:
    """验证启用和停用档案共同参与别名唯一性校验。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证重复名称被拒绝。
    """
    store = UserProfileStore(tmp_path)
    profile = store.create("User A")
    store.deactivate(profile.id)

    with pytest.raises(UserProfileStoreError, match="已存在"):
        store.create("user a")
    with pytest.raises(UserProfileStoreError, match="1-40"):
        store.create("x" * 41)


def test_profile_document_never_contains_credentials(tmp_path: Path) -> None:
    """验证档案文件只保存非敏感身份元数据。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证凭据字段未落盘。
    """
    store = UserProfileStore(tmp_path)
    store.create("[[USER_A]]")

    document_text = store.profile_file.read_text(encoding="utf-8")
    lowered = document_text.casefold()

    assert "cookie" not in lowered
    assert "token" not in lowered
    assert "session" not in lowered


def test_profile_writes_use_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证用户档案通过同目录临时文件原子写入。

    Args:
        tmp_path: pytest 提供的临时目录。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 通过断言验证 os.replace 调用。
    """
    store = UserProfileStore(tmp_path)
    calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def record_replace(source: os.PathLike[str], target: os.PathLike[str]) -> None:
        """记录并执行真实原子替换。

        Args:
            source: 临时文件路径。
            target: 最终档案路径。

        Returns:
            None: 文件由真实替换操作写入。
        """
        calls.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", record_replace)
    store.create("[[USER_A]]")

    assert len(calls) == 1
    assert calls[0][0].parent == calls[0][1].parent == tmp_path


def test_corrupt_profile_file_has_chinese_diagnostic(tmp_path: Path) -> None:
    """验证损坏档案文件不会被静默忽略。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证中文诊断异常。
    """
    store = UserProfileStore(tmp_path)
    store.profile_file.write_text("{broken", encoding="utf-8")

    with pytest.raises(UserProfileStoreError, match="损坏"):
        store.list_all(include_inactive=True)


def test_legacy_draft_and_named_lists_are_copied_on_creation(tmp_path: Path) -> None:
    """验证创建同名用户时导入旧版课程文件且保留源文件。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证兼容导入结果。
    """
    legacy_draft = tmp_path / "course_list_[[USER_A]].json"
    legacy_lists = tmp_path / "saved_rush_lists_[[USER_A]].json"
    legacy_draft.write_text('[{"name": "[[COURSE_NAME]]"}]', encoding="utf-8")
    legacy_lists.write_text(
        json.dumps({"version": 1, "lists": []}), encoding="utf-8"
    )
    store = UserProfileStore(tmp_path)

    profile = store.create("[[USER_A]]")

    assert store.draft_path(profile.id).read_bytes() == legacy_draft.read_bytes()
    assert store.named_lists_path(profile.id).read_bytes() == legacy_lists.read_bytes()
    assert legacy_draft.exists()
    assert legacy_lists.exists()


def test_draft_round_trip_is_isolated_by_profile(tmp_path: Path) -> None:
    """验证每名用户的草稿课程互不共享引用或文件。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证草稿隔离。
    """
    store = UserProfileStore(tmp_path)
    first = store.create("[[USER_A]]")
    second = store.create("[[USER_B]]")
    courses = [{"id": 9, "name": "[[COURSE_A]]", "data": {"p_id": "[[TASK_A]]"}}]

    store.save_draft(first.id, courses)
    loaded = store.load_draft(first.id)
    loaded[0]["data"]["p_id"] = "[[MUTATED_TASK]]"  # type: ignore[index]

    assert store.load_draft(first.id)[0]["id"] == 1
    assert store.load_draft(first.id)[0]["data"]["p_id"] == "[[TASK_A]]"  # type: ignore[index]
    assert store.load_draft(second.id) == []
