"""逾期罚金：逾期天数 × 每日罚金 × 等级折扣，不超过上限。"""

from datetime import date

from . import policy
from .dates import days_overdue


def overdue_fee(due: date, returned: date, rate: float = 1.0) -> float:
    days = days_overdue(due, returned)
    fee = days * policy.DAILY_FEE * rate
    return round(min(fee, policy.FEE_CAP), 2)
