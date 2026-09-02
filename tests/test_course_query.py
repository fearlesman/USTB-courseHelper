"""课程联合查询与响应字段映射的单元测试。"""

from course_query import (
    COURSE_TYPE_DEFINITIONS,
    CourseSearchCriteria,
    build_course_query_payload,
    extract_course_search_results,
    remove_empty_values,
)


def test_remove_empty_values_removes_empty_values_recursively() -> None:
    """
    验证递归剔除空值时保留零值和布尔假值。

    Args:
        None.

    Returns:
        None: 测试仅通过断言验证清理后的结构。
    """
    raw_payload = {
        "null_value": None,
        "empty_text": "",
        "blank_text": "  ",
        "empty_list": [],
        "nested": {"empty": None, "zero": 0, "false": False},
        "items": [None, "", {"value": "保留"}, 0],
    }

    cleaned_payload = remove_empty_values(raw_payload)

    assert cleaned_payload == {
        "nested": {"zero": 0, "false": False},
        "items": [{"value": "保留"}, 0],
    }


def test_build_course_query_payload_combines_code_and_name_criteria() -> None:
    """
    验证课程代码和名称可同时写入查询负载。

    Args:
        None.

    Returns:
        None: 测试仅通过断言验证请求参数。
    """
    criteria = CourseSearchCriteria(
        course_code="1029005",
        course_name="世界科技文明史",
    )

    payload = build_course_query_payload(
        semester="2026-2027-1",
        course_type_code="bx-b-b",
        criteria=criteria,
    )

    assert payload["p_kcdm_cxrw"] == "1029005"
    assert payload["p_kcdm_cxrw_zckc"] == "1029005"
    assert payload["p_gjz"] == "世界科技文明史"
    assert "p_kc_gjz" not in payload
    assert "p_kclb" not in payload
    assert payload["p_xnxq"] == "2026-20271"
    assert payload["pageSize"] == "100"
    assert all(value != "" for value in payload.values())


def test_extract_course_search_results_maps_requested_columns() -> None:
    """
    验证课程响应映射为界面所需字段并清理空值。

    Args:
        None.

    Returns:
        None: 测试仅通过断言验证映射结果。
    """
    response_payload = {
        "message": None,
        "kxrwList": {
            "list": [
                {
                    "id": "course-task-id",
                    "rwmc": "",
                    "kcdm": "1029005",
                    "kcmc": "世界科技文明史",
                    "kcxzmc": "任选",
                    "kclb": "2305",
                    "kclbmc": "素质拓展-人文素养(素质拓展)",
                    "skyymc": "中文",
                    "jfzlbmc": "百分制",
                    "xf": "2.0",
                    "zxs": "32.0",
                    "kcxx": (
                        "<div class='ivu-tag-cyan'><span class='ivu-tag-text'>"
                        "1-16周,星期三第11-12节 逸夫楼202</span></div>"
                    ),
                    "zrl": "70",
                    "yxzrs": "108",
                    "kkyxmc": "人文素质教育中心",
                    "xiaoqumc": "校本部",
                    "unused": None,
                }
            ]
        },
    }

    results = extract_course_search_results(response_payload)

    assert len(results) == 1
    result = results[0]
    assert result.display_name == "世界科技文明史"
    assert result.course_code == "1029005"
    assert result.course_name == "世界科技文明史"
    assert result.course_nature == "任选"
    assert result.course_category == "素质拓展-人文素养(素质拓展)"
    assert result.teaching_language == "中文"
    assert result.scoring_method == "百分制"
    assert result.credit == "2.0"
    assert result.class_hours == "32.0"
    assert result.schedule == "1-16周,星期三第11-12节 逸夫楼202"
    assert result.capacity_selected == "70/108"
    assert result.offering_college == "人文素质教育中心"
    assert result.campus == "校本部"


def test_sports_iii_course_type_definition_and_query_payload() -> None:
    """
    验证体育III课程类型代码及其查询学期字段。

    Args:
        None.

    Returns:
        None: 断言共享课程类型配置和查询负载值。
    """
    course_type_codes = dict(COURSE_TYPE_DEFINITIONS)

    assert course_type_codes["体育III"] == "bx-b-b-ty3"

    payload = build_course_query_payload(
        semester="2026-2027-1",
        course_type_code=course_type_codes["体育III"],
        criteria=CourseSearchCriteria(course_code="11101013", course_name="体育III"),
    )

    assert {
        key: payload[key]
        for key in (
            "p_xn",
            "p_xq",
            "p_xnxq",
            "p_dqxn",
            "p_dqxq",
            "p_dqxnxq",
            "p_xkfsdm",
        )
    } == {
        "p_xn": "2026-2027",
        "p_xq": "1",
        "p_xnxq": "2026-20271",
        "p_dqxn": "2026-2027",
        "p_dqxq": "1",
        "p_dqxnxq": "2026-20271",
        "p_xkfsdm": "bx-b-b-ty3",
    }


def test_extract_course_search_results_maps_sports_iii_response() -> None:
    """
    验证脱敏后的体育III教学班响应能够映射到查询结果。

    Args:
        None.

    Returns:
        None: 断言体育III关键字段及 kclb=19 的映射结果。
    """
    response_payload = {
        "kxrwList": {
            "list": [
                {
                    "id": "[[SPORTS_TASK_ID]]",
                    "rwmc": "体育III(乒乓球)",
                    "kcdm": "11101013",
                    "kcmc": "体育III",
                    "kclb": "19",
                    "kclbmc": "通识课程",
                    "dgjsmc": "[[SPORTS_TEACHER]]",
                    "zrl": "34",
                    "yxzrs": "0",
                    "kkyxmc": "体育部",
                    "kcxx": (
                        "<div class='ivu-tag-cyan'><span class='ivu-tag-text'>"
                        "1-16周,星期三第3-4节 体育馆"
                        "</span></div>"
                    ),
                    "xiaoqumc": "校本部",
                }
            ]
        }
    }

    results = extract_course_search_results(response_payload)

    assert len(results) == 1
    result = results[0]
    assert result.task_id == "[[SPORTS_TASK_ID]]"
    assert result.display_name == "体育III(乒乓球)"
    assert result.course_code == "11101013"
    assert result.course_name == "体育III"
    assert result.category_code == "19"
    assert result.course_category == "通识课程"
    assert result.teacher == "[[SPORTS_TEACHER]]"
    assert result.capacity_selected == "34/0"
    assert result.schedule == "1-16周,星期三第3-4节 体育馆"
    assert result.campus == "校本部"
