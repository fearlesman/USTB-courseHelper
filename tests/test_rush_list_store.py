"""命名抢课列表存储的持久化与校验测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rush_list_store import RushListStore, RushListStoreError


def _course(name: str = "[[COURSE_NAME]]", course_id: int = 7) -> dict[str, object]:
    """创建带界面序号的最小课程快照。

    Args:
        name: 模拟课程名称。
        course_id: 模拟界面序号。

    Returns:
        可供命名列表保存的课程字典。
    """
    return {
        "id": course_id,
        "priority": 1,
        "name": name,
        "teacher": "[[TEACHER_NAME]]",
        "course_id": "[[COURSE_CODE]]",
        "schedule": "[[SCHEDULE]]",
        "data": {"p_id": "[[COURSE_TASK_ID]]"},
    }


def test_create_multiple_lists_and_isolate_students(tmp_path: Path) -> None:
    """验证每位人员拥有彼此隔离的多个命名列表。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证列表数量和文件隔离。
    """
    store = RushListStore(tmp_path)

    first = store.create("[[STUDENT_A]]", "第一志愿", "上午课程", [_course()])
    second = store.create("[[STUDENT_A]]", "备选", "", [_course("[[COURSE_B]]")])
    store.create("[[STUDENT_B]]", "第一志愿", "另一位人员", [_course()])

    assert [item.id for item in store.list_all("[[STUDENT_A]]")] == [first.id, second.id]
    assert len(store.list_all("[[STUDENT_B]]")) == 1
    assert store.path_for("[[STUDENT_A]]") != store.path_for("[[STUDENT_B]]")


def test_name_validation_uniqueness_update_and_save_as(tmp_path: Path) -> None:
    """验证名称边界、忽略大小写去重、更新和另存为。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证保存语义。
    """
    store = RushListStore(tmp_path)
    saved = store.create("[[STUDENT_NAME]]", " Plan A ", "初始备注", [_course()])

    updated = store.update(
        "[[STUDENT_NAME]]", saved.id, "Plan A", "更新备注", [_course("[[NEW_COURSE]]")]
    )
    copied = store.create("[[STUDENT_NAME]]", "Plan B", "另存备注", updated.courses)

    assert updated.name == "Plan A"
    assert updated.created_at == saved.created_at
    assert updated.updated_at >= saved.updated_at
    assert copied.id != saved.id
    with pytest.raises(RushListStoreError, match="已存在"):
        store.create("[[STUDENT_NAME]]", "plan a", "", [_course()])
    with pytest.raises(RushListStoreError, match="1-40"):
        store.create("[[STUDENT_NAME]]", " ", "", [_course()])
    with pytest.raises(RushListStoreError, match="1-40"):
        store.create("[[STUDENT_NAME]]", "x" * 41, "", [_course()])


def test_note_empty_boundary_and_empty_courses(tmp_path: Path) -> None:
    """验证备注边界和空课程列表禁止保存。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证输入限制。
    """
    store = RushListStore(tmp_path)

    assert store.create("[[STUDENT_NAME]]", "无备注", "", [_course()]).note == ""
    assert len(store.create("[[STUDENT_NAME]]", "边界", "n" * 300, [_course()]).note) == 300
    with pytest.raises(RushListStoreError, match="300"):
        store.create("[[STUDENT_NAME]]", "过长备注", "n" * 301, [_course()])
    with pytest.raises(RushListStoreError, match="至少添加一门课程"):
        store.create("[[STUDENT_NAME]]", "空列表", "", [])


def test_course_snapshot_is_deep_copied_and_ids_are_rebuilt(tmp_path: Path) -> None:
    """验证保存移除序号、隔离嵌套对象并在加载时重建序号。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证快照不与调用方共享引用。
    """
    store = RushListStore(tmp_path)
    source = [_course(course_id=9), _course("[[COURSE_B]]", course_id=12)]

    saved = store.create("[[STUDENT_NAME]]", "快照", "", source)
    source[0]["name"] = "[[MUTATED_NAME]]"
    source[0]["data"]["p_id"] = "[[MUTATED_TASK_ID]]"  # type: ignore[index]
    loaded = store.courses_for_loading(saved)

    assert "id" not in saved.courses[0]
    assert saved.courses[0]["name"] == "[[COURSE_NAME]]"
    assert [course["id"] for course in loaded] == [1, 2]
    loaded[0]["data"]["p_id"] = "[[OTHER_TASK_ID]]"  # type: ignore[index]
    assert saved.courses[0]["data"]["p_id"] == "[[COURSE_TASK_ID]]"  # type: ignore[index]


def test_delete_and_missing_list_diagnostics(tmp_path: Path) -> None:
    """验证删除列表以及不存在列表的中文诊断。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证删除结果。
    """
    store = RushListStore(tmp_path)
    saved = store.create("[[STUDENT_NAME]]", "待删除", "", [_course()])

    store.delete("[[STUDENT_NAME]]", saved.id)

    assert store.list_all("[[STUDENT_NAME]]") == []
    with pytest.raises(RushListStoreError, match="不存在"):
        store.delete("[[STUDENT_NAME]]", saved.id)


def test_atomic_replace_writes_versioned_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证写入通过同目录临时文件和 os.replace 原子替换。

    Args:
        tmp_path: pytest 提供的临时目录。
        monkeypatch: pytest 提供的运行时替换工具。

    Returns:
        None: 通过断言验证替换路径和文件结构。
    """
    store = RushListStore(tmp_path)
    replace_calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        """记录并执行原子替换。

        Args:
            source: 临时文件路径。
            target: 最终文件路径。

        Returns:
            None: 文件由真实 os.replace 写入。
        """
        replace_calls.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", recording_replace)
    store.create("[[STUDENT_NAME]]", "原子写入", "", [_course()])

    assert len(replace_calls) == 1
    source, target = replace_calls[0]
    assert source.parent == target.parent == tmp_path
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert len(document["lists"]) == 1


def test_corrupt_or_invalid_file_has_chinese_diagnostic(tmp_path: Path) -> None:
    """验证损坏文件和非法字段不会被静默接受。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证中文错误信息。
    """
    store = RushListStore(tmp_path)
    path = store.path_for("[[STUDENT_NAME]]")
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(RushListStoreError, match="损坏"):
        store.list_all("[[STUDENT_NAME]]")

    path.write_text(
        json.dumps({"version": 1, "lists": [{"id": "bad"}]}), encoding="utf-8"
    )
    with pytest.raises(RushListStoreError, match="字段非法"):
        store.list_all("[[STUDENT_NAME]]")


def test_rename_preserves_note_courses_and_timestamps(tmp_path: Path) -> None:
    """验证仅重命名不会保存当前脏课程或修改备注。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证元数据更新边界。
    """
    store = RushListStore(tmp_path)
    saved = store.create("[[USER_A]]", "旧名称", "[[LIST_NOTE]]", [_course()])

    renamed = store.rename("[[USER_A]]", saved.id, "新名称")

    assert renamed.name == "新名称"
    assert renamed.note == saved.note
    assert renamed.courses == saved.courses
    assert renamed.created_at == saved.created_at
    assert renamed.updated_at >= saved.updated_at


def test_copy_to_users_creates_independent_snapshots_and_skips_duplicates(
    tmp_path: Path,
) -> None:
    """验证列表分配为独立副本且同名目标不会被覆盖。

    Args:
        tmp_path: pytest 提供的临时目录。

    Returns:
        None: 通过断言验证批量分配结果。
    """
    store = RushListStore(tmp_path)
    source = store.create("[[USER_A]]", "共享计划", "[[LIST_NOTE]]", [_course()])
    store.create("[[USER_C]]", "共享计划", "[[EXISTING_NOTE]]", [_course("[[OLD]]")])

    result = store.copy_to_users(
        "[[USER_A]]", source.id, ["[[USER_B]]", "[[USER_C]]"]
    )
    copied = store.list_all("[[USER_B]]")[0]

    assert result.copied_user_ids == ("[[USER_B]]",)
    assert result.skipped_user_ids == ("[[USER_C]]",)
    assert copied.id != source.id
    assert copied.courses == source.courses
    copied.courses[0]["name"] = "[[MUTATED]]"
    assert store.get("[[USER_A]]", source.id).courses[0]["name"] == "[[COURSE_NAME]]"
