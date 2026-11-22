"""日期工具：解析 YYYY-MM-DD，计算借期与逾期天数。"""

from datetime import date, timedelta


def parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise ValueError(f"日期格式应为 YYYY-MM-DD：{text}") from None


def add_days(start: date, days: int) -> date:
    if days < 0:
        raise ValueError("天数不能为负数")
    return start + timedelta(days=days)


def days_overdue(due: date, returned: date) -> int:
    """逾期天数：在到期日当天或之前归还为 0，到期日次日归还为 1，依此类推。"""
    if returned <= due:
        return 0
    return (returned - due).days + 1
