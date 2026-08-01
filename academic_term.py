"""北京科技大学选课系统的学年学期计算规则。"""

from datetime import date


def get_current_academic_term(current_date: date) -> str:
    """
    根据日期计算当前学年学期的 xnxq 值。

    Args:
        current_date: 用于计算的公历日期。

    Returns:
        符合 ``YYYY-YYYY-N`` 格式的学年学期字符串，其中 N 为 1、2 或 3。

    """
    year = current_date.year

    if current_date.month <= 5:
        return f"{year - 1}-{year}-2"

    if current_date.month == 6 or (
        current_date.month == 7 and current_date.day < 28
    ):
        return f"{year - 1}-{year}-3"

    return f"{year}-{year + 1}-1"
