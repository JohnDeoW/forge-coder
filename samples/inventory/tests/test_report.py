from inventory.cli import demo_inventory
from inventory.report import stock_report, total_value


def test_total_value():
    assert total_value(demo_inventory()) == 399.0 * 12 + 129.0 * 3 + 199.0 * 7


def test_stock_report_order():
    rows = stock_report(demo_inventory())
    assert [r.split("\t")[0] for r in rows] == ["A002", "B001", "A001"]
