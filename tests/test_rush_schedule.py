"""定时抢课开始时间计算的单元测试。"""

from datetime import datetime, time

from rush_schedule import get_scheduled_start


def test_get_scheduled_start_uses_today_for_future_time() -> None:
    """
    验证当天未来的定时时间会保留为当天目标时刻。

    Args:
        None.

    Returns:
        None: 测试仅通过断言验证目标时间。
    """
    now = datetime(2026, 8, 1, 9, 30, 0)

    scheduled_start = get_scheduled_start(now, time(10, 0, 0))

    assert scheduled_start == datetime(2026, 8, 1, 10, 0, 0)


def test_get_scheduled_start_starts_immediately_for_past_time() -> None:
    """
    验证目标时间已过时立即进入轮询而不是等待到次日。

    Args:
        None.

    Returns:
        None: 测试仅通过断言验证开始时间。
    """
    now = datetime(2026, 8, 1, 10, 0, 1)

    scheduled_start = get_scheduled_start(now, time(10, 0, 0))

    assert scheduled_start == now
