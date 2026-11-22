"""命令行演示：python -m library.cli"""

from .catalog import Book, Catalog
from .dates import parse_date
from .loans import LoanService
from .members import Member
from .report import overdue_report


def demo_service() -> LoanService:
    catalog = Catalog()
    catalog.add_book(Book("978-7-111", "算法导论", "Cormen", copies=2))
    catalog.add_book(Book("978-7-115", "流畅的 Python", "Ramalho", copies=1))
    service = LoanService(catalog)
    service.add_member(Member.from_record({"id": "m1", "name": "张三", "level": "standard"}))
    service.add_member(Member.from_record({"id": "m2", "name": "李四", "level": "Premium"}))
    return service


def main() -> None:
    service = demo_service()
    loan = service.borrow("m1", "978-7-111", parse_date("2024-03-01"))
    service.borrow("m2", "978-7-115", parse_date("2024-03-02"))
    print(f"张三借书，到期日 {loan.due}")
    print("逾期报表：", overdue_report(service, parse_date("2024-03-20")) or "无")
    fee = service.return_book(loan.id, parse_date("2024-03-17"))
    print(f"张三还书，罚金 {fee} 元")


if __name__ == "__main__":
    main()
