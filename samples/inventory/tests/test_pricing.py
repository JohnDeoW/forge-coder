import pytest

from inventory.pricing import apply_coupon, order_total


def test_no_discount():
    assert order_total(10.0, 3) == 30.0


def test_bulk_discount_at_threshold():
    assert order_total(10.0, 10) == 90.0


def test_bulk_discount_above_threshold():
    assert order_total(10.0, 20) == 180.0


def test_coupon():
    assert apply_coupon(200.0, 15) == 170.0
    with pytest.raises(ValueError):
        apply_coupon(100.0, 120)
