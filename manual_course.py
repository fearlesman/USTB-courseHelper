"""无法自动查询参数的特殊课程的手动添加参数构建与校验。"""

from __future__ import annotations

from typing import Any, Mapping

COURSE_TYPE_LABELS: tuple[tuple[str, str], ...] = (
    ("素质扩展课", "sztzk-b-b"),
    ("专业扩展课", "zytzk-b-b"),
    ("MOOC", "mooc-b-b"),
    ("必修课", "bx-b-b"),
)

DEFAULT_CATEGORY_CODE = "2301"
DEFAULT_XKTJZ = "rwtjzyx"


def course_type_code_for(label: str) -> str:
    """将界面显示的课程类型文本转换为接口选课方式代码。

    Args:
        label: 界面课程类型文本，例如“素质扩展课”。

    Returns:
        对应的选课方式代码，例如 `sztzk-b-b`。

    Raises:
        ValueError: 课程类型文本不在已知范围内时抛出。
    """
    normalized_label = label.strip()
    for display_label, type_code in COURSE_TYPE_LABELS:
        if display_label == normalized_label:
            return type_code
    raise ValueError(
        f"课程类型无效：{label or '空'}，可选值为"
        + "、".join(display_label for display_label, _ in COURSE_TYPE_LABELS)
    )


def split_semester(semester: str) -> tuple[str, str]:
    """将 `YYYY-YYYY-N` 学期文本拆分为学年与学期号。

    Args:
        semester: 用户输入的学年学期文本。

    Returns:
        (学年, 学期号) 二元组，例如 `(2026-2027, 1)`。

    Raises:
        ValueError: 学期格式不是三段且三段均非空时抛出。
    """
    semester_parts = semester.strip().split("-")
    if len(semester_parts) != 3 or not all(semester_parts):
        raise ValueError("学期格式错误，请使用 YYYY-YYYY-N 格式")
    start_year, end_year, term = semester_parts
    if (
        not (start_year.isdigit() and len(start_year) == 4)
        or not (end_year.isdigit() and len(end_year) == 4)
        or int(end_year) != int(start_year) + 1
    ):
        raise ValueError("学期格式错误，请使用 YYYY-YYYY-N 格式")
    if term not in ("1", "2", "3"):
        raise ValueError("学期格式错误，学期号必须是 1、2 或 3")
    academic_year = "-".join(semester_parts[:2])
    return academic_year, semester_parts[2]


def build_manual_rush_payload(
    semester: str,
    course_type_label: str,
    category_code: str,
    task_id: str,
) -> dict[str, str]:
    """构建手动参数课程加入购物车所需的请求参数。

    Args:
        semester: `YYYY-YYYY-N` 格式的学年学期。
        course_type_label: 界面课程类型文本。
        category_code: 课程类别代码，例如 `2301`。
        task_id: 课程任务 ID，来自接口或手工从页面获取。

    Returns:
        与查询添加课程完全一致的 `addGouwuche` 请求负载。

    Raises:
        ValueError: 学期、课程类型、类别代码或任务 ID 非法时抛出。
    """
    academic_year, term = split_semester(semester)
    if not category_code.strip():
        raise ValueError("课程类别代码不能为空")
    if not task_id.strip():
        raise ValueError("课程任务 ID 不能为空")
    return {
        "p_xktjz": DEFAULT_XKTJZ,
        "p_xn": academic_year,
        "p_xq": term,
        "p_xkfsdm": course_type_code_for(course_type_label),
        "p_kclb": category_code.strip(),
        "p_id": task_id.strip(),
    }


def build_manual_course(
    semester: str,
    course_type_label: str,
    category_code: str,
    task_id: str,
    name: str,
    priority: int,
    teacher: str = "",
    course_code: str = "",
    schedule: str = "",
) -> dict[str, Any]:
    """构建一条可与查询结果课程混用的手动参数课程记录。

    Args:
        semester: `YYYY-YYYY-N` 格式的学年学期。
        course_type_label: 界面课程类型文本。
        category_code: 课程类别代码，例如 `2301`。
        task_id: 课程任务 ID，来自接口或手工从页面获取。
        name: 用于界面展示的课程名称，必须非空。
        priority: 抢课优先级，必须是 1 到 99 的整数。
        teacher: 用于界面展示的授课教师，允许为空。
        course_code: 用于界面展示的课程代码，允许为空。
        schedule: 用于界面展示的上课安排，允许为空。

    Returns:
        与查询结果课程结构一致且不含顶层序号 `id` 的课程字典。

    Raises:
        ValueError: 名称、优先级、学期或参数非法时抛出。
    """
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("课程名称不能为空")
    try:
        numeric_priority = int(priority)
    except (TypeError, ValueError) as error:
        raise ValueError("优先级必须是 1-99 的整数") from error
    if not 1 <= numeric_priority <= 99:
        raise ValueError("优先级必须是 1-99 的整数")
    payload = build_manual_rush_payload(
        semester,
        course_type_label,
        category_code,
        task_id,
    )
    return {
        "priority": numeric_priority,
        "data": payload,
        "name": normalized_name,
        "teacher": teacher.strip(),
        "course_id": course_code.strip(),
        "schedule": schedule.strip() or "—",
        "source": "manual",
    }


def is_manual_course(course: Mapping[str, Any]) -> bool:
    """判断一条课程记录是否来自高级手动添加。

    Args:
        course: 待判断的课程记录字典。

    Returns:
        True 表示该课程是手动参数添加的特殊课程。
    """
    return str(course.get("source", "")) == "manual"