"""命令行演示：python -m inventory.cli"""

from .models import Product
from .report import stock_report, total_value
from .store import Inventory


def demo_inventory() -> Inventory:
    inv = Inventory()
    inv.add_product(Product("A001", "机械键盘", "外设", 399.0, 12))
    inv.add_product(Product("A002", "无线鼠标", "外设", 129.0, 3))
    inv.add_product(Product("B001", "显示器支架", "配件", 199.0, 7))
    return inv


def main() -> None:
    inv = demo_inventory()
    for line in stock_report(inv):
        print(line)
    print(f"库存总价值: {total_value(inv)}")


if __name__ == "__main__":
    main()
