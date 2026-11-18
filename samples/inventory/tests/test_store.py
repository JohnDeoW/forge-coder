import pytest

from inventory.models import Product
from inventory.store import Inventory


@pytest.fixture
def inv():
    store = Inventory()
    store.add_product(Product("A001", "机械键盘", "外设", 399.0, 12))
    return store


def test_restock(inv):
    assert inv.restock("A001", 3) == 15


def test_remove_stock(inv):
    assert inv.remove_stock("A001", 2) == 10


def test_remove_stock_insufficient(inv):
    with pytest.raises(ValueError):
        inv.remove_stock("A001", 20)
    assert inv.get("A001").stock == 12


def test_duplicate_sku(inv):
    with pytest.raises(ValueError):
        inv.add_product(Product("A001", "重复", "外设", 1.0))
