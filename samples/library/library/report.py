"""逾期报表。"""

from datetime import date

from .dates import days_overdue
from .loans import LoanService


def overdue_report(service: LoanService, today: date) -> list[str]:
    lines = []
    for loan in service.open_loans():
        days = days_overdue(loan.due, today)
        if days:
            member = service.member(loan.member_id)
            title = service.catalog.get(loan.isbn).title
            lines.append(f"{member.name}《{title}》逾期 {days} 天")
    return lines
