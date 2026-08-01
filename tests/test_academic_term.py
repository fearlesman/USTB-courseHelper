"""学年学期计算规则的单元测试。"""

from datetime import date

import pytest

from academic_term import get_current_academic_term


@pytest.mark.parametrize(
    ("current_date", "expected"),
    [
        (date(2025, 1, 1), "2024-2025-2"),
        (date(2025, 5, 31), "2024-2025-2"),
        (date(2025, 6, 1), "2024-2025-3"),
        (date(2025, 7, 27), "2024-2025-3"),
        (date(2025, 7, 28), "2025-2026-1"),
        (date(2025, 12, 31), "2025-2026-1"),
    ],
)
def test_get_current_academic_term_uses_ustb_boundaries(
    current_date: date, expected: str
) -> None:
    """
    验证学年学期按北京科技大学的日期边界计算。

    Args:
        current_date: 需要计算学年学期的公历日期。
        expected: 该日期应得到的学年学期字符串。

    Returns:
        None: 测试仅通过断言验证结果。
    """
    assert get_current_academic_term(current_date) == expected
