"""课程联合查询与响应字段映射的单元测试。"""

from course_query import (
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
