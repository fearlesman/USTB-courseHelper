"""高级添加特殊课程的手动参数构建与校验测试。"""

from __future__ import annotations

from typing import Any

import pytest

from manual_course import (
    COURSE_TYPE_LABELS,
    DEFAULT_CATEGORY_CODE,
    build_manual_course,
    build_manual_rush_payload,
    course_type_code_for,
    is_manual_course,
    split_semester,
)


def test_course_type_code_for_maps_all_labels() -> None:
    """验证界面课程类型文本全部映射为选课方式代码。

    Args:
        None.

    Returns:
        None: 通过断言验证映射表完整且统一。
    """
    expected_codes = {
        "素质扩展课": "sztzk-b-b",
        "专业扩展课": "zytzk-b-b",
        "MOOC": "mooc-b-b",
        "必修课": "bx-b-b",
    }
    assert dict(COURSE_TYPE_LABELS) == expected_codes
    for label, code in expected_codes.items():
        assert course_type_code_for(label) == code
        assert course_type_code_for(f"  {label}  ") == code


def test_course_type_code_for_rejects_unknown_label() -> None:
    """验证未知课程类型文本抛出中文错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常信息包含可选值。
    """
    with pytest.raises(ValueError, match="课程类型无效"):
        course_type_code_for("不存在的类型")


def test_split_semester_parses_valid_term() -> None:
    """验证合法学期拆分为学年和学期号。

    Args:
        None.

    Returns:
        None: 通过断言验证拆分的二元组。
    """
    assert split_semester("2026-2027-1") == ("2026-2027", "1")
    assert split_semester("2025-2026-2") == ("2025-2026", "2")


@pytest.mark.parametrize(
    "semester",
    ["", "2026-2027", "2026-2027-1-2", "abc-def-1"],
)
def test_split_semester_rejects_invalid_terms(semester: str) -> None:
    """验证非法学期格式抛出中文错误。

    Args:
        semester: 非法的学期文本。

    Returns:
        None: 参数化场景全部应抛出 ValueError。
    """
    with pytest.raises(ValueError, match="学期格式错误"):
        split_semester(semester)


def test_manual_rush_payload_matches_query_added_shape() -> None:
    """验证手动负载与查询添加课程的请求参数完全一致。

    Args:
        None.

    Returns:
        None: 通过断言验证六个字段及默认选课条件。
    """
    payload = build_manual_rush_payload(
        semester="2026-2027-1",
        course_type_label="专业扩展课",
        category_code="2301",
        task_id="[[TASK_ID]]",
    )
    assert payload == {
        "p_xktjz": "rwtjzyx",
        "p_xn": "2026-2027",
        "p_xq": "1",
        "p_xkfsdm": "zytzk-b-b",
        "p_kclb": "2301",
        "p_id": "[[TASK_ID]]",
    }


def test_manual_rush_payload_rejects_empty_task_or_category() -> None:
    """验证空任务 ID 与空类别代码抛出中文错误。

    Args:
        None.

    Returns:
        None: 两种空值场景应分别给出对应提示。
    """
    with pytest.raises(ValueError, match="课程任务 ID 不能为空"):
        build_manual_rush_payload("2026-2027-1", "必修课", "2301", "  ")
    with pytest.raises(ValueError, match="课程类别代码不能为空"):
        build_manual_rush_payload("2026-2027-1", "必修课", "  ", "[[TASK]]")


def test_build_manual_course_returns_complete_course_record() -> None:
    """验证手动课程记录与查询课程结构混用且标记来源。

    Args:
        None.

    Returns:
        None: 通过断言验证记录字段、优先级和来源标记。
    """
    course = build_manual_course(
        semester="2026-2027-1",
        course_type_label="MOOC",
        category_code="2301",
        task_id="[[TASK_ID]]",
        name=" 特殊课程 ",
        priority="3",
        teacher=" 王老师 ",
        course_code="[[CODE]]",
        schedule="[[SCHEDULE]]",
    )
    assert isinstance(course, dict)
    assert course["priority"] == 3
    assert course["name"] == "特殊课程"
    assert course["teacher"] == "王老师"
    assert course["course_id"] == "[[CODE]]"
    assert course["schedule"] == "[[SCHEDULE]]"
    assert course["source"] == "manual"
    assert course["data"]["p_id"] == "[[TASK_ID]]"
    assert course["data"]["p_xkfsdm"] == "mooc-b-b"
    assert is_manual_course(course)


def test_build_manual_course_defaults_for_optional_display_fields() -> None:
    """验证可选展示字段为空时使用占位文本。

    Args:
        None.

    Returns:
        None: 通过断言验证课程代码与上课安排的默认值。
    """
    course = build_manual_course(
        semester="2026-2027-1",
        course_type_label="素质扩展课",
        category_code=DEFAULT_CATEGORY_CODE,
        task_id="[[TASK_ID]]",
        name="课程",
        priority=1,
    )
    assert course["teacher"] == ""
    assert course["course_id"] == ""
    assert course["schedule"] == "—"


def test_build_manual_course_rejects_empty_name() -> None:
    """验证空课程名称抛出中文错误。

    Args:
        None.

    Returns:
        None: 通过断言验证异常信息。
    """
    with pytest.raises(ValueError, match="课程名称不能为空"):
        build_manual_course(
            semester="2026-2027-1",
            course_type_label="必修课",
            category_code="2301",
            task_id="[[TASK_ID]]",
            name="   ",
            priority=1,
        )


@pytest.mark.parametrize(
    ("priority", "message"),
    [
        ("0", "优先级必须是 1-99 的整数"),
        ("100", "优先级必须是 1-99 的整数"),
        ("abc", "优先级必须是 1-99 的整数"),
        ("-1", "优先级必须是 1-99 的整数"),
    ],
)
def test_build_manual_course_rejects_invalid_priority(
    priority: str, message: str
) -> None:
    """验证非法优先级抛出中文错误。

    Args:
        priority: 非法的优先级输入。
        message: 期望的异常信息片段。

    Returns:
        None: 参数化场景全部应抛出 ValueError。
    """
    with pytest.raises(ValueError, match=message):
        build_manual_course(
            semester="2026-2027-1",
            course_type_label="必修课",
            category_code="2301",
            task_id="[[TASK_ID]]",
            name="课程",
            priority=priority,
        )


def test_manual_course_is_distinguishable_from_query_courses() -> None:
    """验证来源标记只存在于手动添加的课程记录。

    Args:
        None.

    Returns:
        None: 通过断言验证普通课程记录不会被误判。
    """
    query_course: dict[str, Any] = {
        "priority": 1,
        "data": {"p_id": "[[TASK]]"},
        "name": "查询课程",
    }
    manual_course = build_manual_course(
        semester="2026-2027-1",
        course_type_label="必修课",
        category_code="2301",
        task_id="[[TASK]]",
        name="手动课程",
        priority=2,
    )
    assert is_manual_course(manual_course) is True
    assert is_manual_course(query_course) is False