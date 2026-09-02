"""选课任务联合查询的请求构造、空值清理与展示字段映射。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from bs4 import BeautifulSoup


COURSE_TYPE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("素质扩展课", "sztzk-b-b"),
    ("专业扩展课", "zytzk-b-b"),
    ("MOOC", "mooc-b-b"),
    ("必修课", "bx-b-b"),
    ("体育III", "bx-b-b-ty3"),
)


DISPLAY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("display_name", "展示名称"),
    ("course_code", "课程代码"),
    ("course_name", "课程名称"),
    ("course_nature", "课程性质"),
    ("course_category", "课程类别"),
    ("teaching_language", "授课语言"),
    ("scoring_method", "计分方式"),
    ("credit", "学分"),
    ("class_hours", "学时"),
    ("schedule", "上课信息"),
    ("capacity_selected", "容量/已选"),
    ("offering_college", "开课学院"),
    ("campus", "校区"),
)


@dataclass(frozen=True)
class CourseSearchCriteria:
    """课程联合查询的可选筛选条件。"""

    course_code: str
    course_name: str

    def has_condition(self) -> bool:
        """
        判断是否至少提供了课程代码或课程名称筛选条件。

        Args:
            None.

        Returns:
            True 表示课程代码或课程名称中至少有一项非空。
        """
        return any((self.course_code, self.course_name))


@dataclass(frozen=True)
class CourseSearchResult:
    """课程检索结果在界面展示与加入课程列表所需的数据。"""

    task_id: str
    category_code: str
    display_name: str
    course_code: str
    course_name: str
    course_nature: str
    course_category: str
    teaching_language: str
    scoring_method: str
    credit: str
    class_hours: str
    schedule: str
    capacity_selected: str
    offering_college: str
    campus: str
    teacher: str

    def display_values(self) -> tuple[str, ...]:
        """
        按课程结果表的列顺序返回展示值。

        Args:
            None.

        Returns:
            与 DISPLAY_COLUMNS 顺序一致的字符串元组。
        """
        return tuple(getattr(self, field_name) for field_name, _ in DISPLAY_COLUMNS)


def remove_empty_values(value: object) -> object:
    """
    递归移除 JSON 数据中的空值、空文本、空列表和空对象。

    Args:
        value: 需要清理的 JSON 兼容对象。

    Returns:
        保留有效数据的新对象，数值 0 和布尔值 False 不会被移除。
    """
    if isinstance(value, Mapping):
        cleaned_mapping = {
            str(key): cleaned_value
            for key, item in value.items()
            if not _is_empty(cleaned_value := remove_empty_values(item))
        }
        return cleaned_mapping

    if isinstance(value, list):
        cleaned_list = [
            cleaned_value
            for item in value
            if not _is_empty(cleaned_value := remove_empty_values(item))
        ]
        return cleaned_list

    return value


def build_course_query_payload(
    semester: str,
    course_type_code: str,
    criteria: CourseSearchCriteria,
    page_number: int = 1,
    page_size: int = 100,
) -> dict[str, str]:
    """
    构造选课任务接口的联合查询负载。

    Args:
        semester: `YYYY-YYYY-N` 格式的学年学期。
        course_type_code: 选课方式代码，例如 `bx-b-b`。
        criteria: 课程代码和名称筛选条件。
        page_number: 请求页码，必须大于零。
        page_size: 单页数量，必须大于零。

    Returns:
        可直接作为 form-urlencoded 数据发送的非空参数字典。

    Raises:
        ValueError: 当学期格式错误、没有筛选条件或分页参数无效时抛出。
    """
    semester_parts = semester.split("-")
    if len(semester_parts) != 3 or not all(semester_parts):
        raise ValueError("学期格式错误，请使用 YYYY-YYYY-N 格式")
    if not criteria.has_condition():
        raise ValueError("请至少填写课程代码或课程名称之一")
    if page_number < 1 or page_size < 1:
        raise ValueError("页码和每页数量必须为正整数")

    academic_year = "-".join(semester_parts[:2])
    term = semester_parts[2]
    payload = {
        "cxsfmt": "1",
        "p_pylx": "1",
        "mxpylx": "1",
        "p_sfgldjr": "0",
        "p_sfredis": "0",
        "p_sfsyxkgwc": "0",
        "p_xktjz": "rwtjzyx",
        "p_xn": academic_year,
        "p_xq": term,
        "p_xnxq": f"{academic_year}{term}",
        "p_dqxn": academic_year,
        "p_dqxq": term,
        "p_dqxnxq": f"{academic_year}{term}",
        "p_xkfsdm": course_type_code,
        "p_sfhlctkc": "0",
        "p_sfhllrlkc": "0",
        "p_sfxsgwckb": "1",
        "pageNum": str(page_number),
        "pageSize": str(page_size),
    }
    optional_payload = {
        "p_kcdm_cxrw": criteria.course_code,
        "p_kcdm_cxrw_zckc": criteria.course_code,
        "p_gjz": criteria.course_name,
    }
    payload.update(
        {key: value for key, value in optional_payload.items() if value.strip()}
    )
    return payload


def extract_course_search_results(
    response_payload: Mapping[str, object],
) -> list[CourseSearchResult]:
    """
    清理接口响应并提取课程检索结果的展示字段。

    Args:
        response_payload: 选课任务接口返回的 JSON 对象。

    Returns:
        按响应顺序排列的课程检索结果列表；无有效课程时返回空列表。
    """
    cleaned_payload = remove_empty_values(response_payload)
    if not isinstance(cleaned_payload, dict):
        return []
    task_list = cleaned_payload.get("kxrwList")
    if not isinstance(task_list, dict):
        return []
    courses = task_list.get("list")
    if not isinstance(courses, list):
        return []

    return [
        _map_course_result(course)
        for course in courses
        if isinstance(course, Mapping) and _value(course, "id")
    ]


def _is_empty(value: object) -> bool:
    """
    判断值是否应在清理 JSON 时移除。

    Args:
        value: 待判断的对象。

    Returns:
        True 表示值为 None、空白文本、空列表或空对象。
    """
    return value is None or (isinstance(value, str) and not value.strip()) or value in (
        [],
        {},
    )


def _value(course: Mapping[str, object], field_name: str) -> str:
    """
    读取课程字段并统一为空缺展示符号。

    Args:
        course: 单条课程任务的字段映射。
        field_name: 需要读取的接口字段名。

    Returns:
        去除首尾空白后的字段文本；字段不存在或为空时返回 `—`。
    """
    value = course.get(field_name)
    if value is None:
        return "—"
    text = str(value).strip()
    return text if text else "—"


def _map_course_result(course: Mapping[str, object]) -> CourseSearchResult:
    """
    将一条选课任务映射为课程查询结果。

    Args:
        course: 已剔除空值的选课任务字段映射。

    Returns:
        可供界面展示和加入课程列表的课程结果对象。
    """
    display_name = _value(course, "rwmc")
    course_name = _value(course, "kcmc")
    if display_name == "—":
        display_name = course_name

    capacity = f"{_value(course, 'zrl')}/{_value(course, 'yxzrs')}"
    scoring_method = _value(course, "jfzlbmc")
    if scoring_method == "—":
        scoring_method = _value(course, "jfxs")

    return CourseSearchResult(
        task_id=_value(course, "id"),
        category_code=_value(course, "kclb"),
        display_name=display_name,
        course_code=_value(course, "kcdm"),
        course_name=course_name,
        course_nature=_value(course, "kcxzmc"),
        course_category=_value(course, "kclbmc"),
        teaching_language=_value(course, "skyymc"),
        scoring_method=scoring_method,
        credit=_value(course, "xf"),
        class_hours=_value(course, "zxs"),
        schedule=_extract_schedule(_value(course, "kcxx")),
        capacity_selected=capacity,
        offering_college=_value(course, "kkyxmc"),
        campus=_value(course, "xiaoqumc"),
        teacher=_value(course, "dgjsmc"),
    )


def _extract_schedule(course_html: str) -> str:
    """
    从课程信息 HTML 中提取所有青色标签的上课时间与地点。

    Args:
        course_html: 接口 `kcxx` 字段中的 HTML 片段。

    Returns:
        使用换行拼接的上课信息；不存在时返回 `—`。
    """
    if course_html == "—":
        return "—"
    document = BeautifulSoup(course_html, "html.parser")
    schedule_tags = document.select(".ivu-tag-cyan .ivu-tag-text")
    schedules = [tag.get_text(" ", strip=True) for tag in schedule_tags]
    return "\n".join(schedule for schedule in schedules if schedule) or "—"
