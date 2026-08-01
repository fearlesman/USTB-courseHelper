"""定时抢课的开始时间计算。"""

from datetime import datetime, time


def get_scheduled_start(now: datetime, target_time: time) -> datetime:
    """
    计算定时抢课应开始轮询的本地时间。

    Args:
        now: 当前本地日期时间。
        target_time: 用户输入的每日定时抢课时刻。

    Returns:
        当天尚未到达目标时返回当天目标时刻；目标已到或已过时返回当前时间。
    """
    scheduled_start = datetime.combine(now.date(), target_time)
    return scheduled_start if scheduled_start > now else now
