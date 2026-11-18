from .store import Inventory


def total_value(inventory: Inventory) -> float:
    return round(sum(p.value for p in inventory.products()), 2)


def stock_report(inventory: Inventory) -> list[str]:
    """按库存从少到多输出报表行。"""
    rows = sorted(inventory.products(), key=lambda p: p.stock)
    return [f"{p.sku}\t{p.name}\t{p.stock}" for p in rows]
