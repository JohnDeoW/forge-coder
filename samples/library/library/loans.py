"""借还流程：借书时检查借阅上限与可借副本，还书时计算逾期罚金。"""

from dataclasses import dataclass
from datetime import date
from itertools import count

from .catalog import Catalog
from .dates import add_days
from .fees import overdue_fee
from .members import Member, borrow_limit, fee_rate, loan_days


@dataclass
class Loan:
    id: int
    isbn: str
    member_id: str
    start: date
    due: date
    returned: date | None = None
    fee: float = 0.0

    @property
    def open(self) -> bool:
        return self.returned is None


class LoanService:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.members: dict[str, Member] = {}
        self.loans: list[Loan] = []
        self._ids = count(1)

    def add_member(self, member: Member) -> None:
        self.members[member.id] = member

    def member(self, member_id: str) -> Member:
        if member_id not in self.members:
            raise ValueError(f"没有编号为 {member_id} 的会员")
        return self.members[member_id]

    def open_loans(self, member_id: str | None = None) -> list[Loan]:
        return [l for l in self.loans if l.open and (member_id is None or l.member_id == member_id)]

    def borrow(self, member_id: str, isbn: str, today: date) -> Loan:
        member = self.member(member_id)
        if len(self.open_loans(member_id)) >= borrow_limit(member):
            raise ValueError(f"{member.name} 已达到借阅上限")
        if self.catalog.available_copies(isbn, self.loans) <= 0:
            raise ValueError(f"《{self.catalog.get(isbn).title}》暂无可借副本")
        loan = Loan(next(self._ids), isbn, member_id, today, add_days(today, loan_days(member)))
        self.loans.append(loan)
        return loan

    def return_book(self, loan_id: int, today: date) -> float:
        """还书并返回罚金。"""
        loan = next((l for l in self.loans if l.id == loan_id), None)
        if loan is None or not loan.open:
            raise ValueError(f"没有未归还的借阅记录 {loan_id}")
        loan.returned = today
        loan.fee = overdue_fee(loan.due, today, fee_rate(self.member(loan.member_id)))
        return loan.fee
