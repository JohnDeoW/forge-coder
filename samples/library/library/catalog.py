"""馆藏：图书信息与可借副本数。"""

from dataclasses import dataclass
from typing import Iterable


@dataclass
class Book:
    isbn: str
    title: str
    author: str
    copies: int = 1


class Catalog:
    def __init__(self) -> None:
        self._books: dict[str, Book] = {}

    def add_book(self, book: Book) -> None:
        if book.copies <= 0:
            raise ValueError("馆藏副本数必须为正数")
        if book.isbn in self._books:
            self._books[book.isbn].copies += book.copies
        else:
            self._books[book.isbn] = book

    def get(self, isbn: str) -> Book:
        if isbn not in self._books:
            raise ValueError(f"馆藏中没有 ISBN 为 {isbn} 的图书")
        return self._books[isbn]

    def books(self) -> list[Book]:
        return list(self._books.values())

    def available_copies(self, isbn: str, loans: Iterable) -> int:
        """可借副本数 = 馆藏副本数 - 尚未归还的借阅数。"""
        book = self.get(isbn)
        out = sum(1 for loan in loans if loan.isbn == isbn)
        return book.copies - out
