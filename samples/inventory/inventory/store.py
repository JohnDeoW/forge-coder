from .models import Product


class Inventory:
    """按 SKU 管理商品与库存。"""

    def __init__(self):
        self._products: dict[str, Product] = {}

    def add_product(self, product: Product) -> None:
        if product.sku in self._products:
            raise ValueError(f"SKU 已存在: {product.sku}")
        self._products[product.sku] = product

    def get(self, sku: str) -> Product:
        if sku not in self._products:
            raise KeyError(sku)
        return self._products[sku]

    def restock(self, sku: str, quantity: int) -> int:
        if quantity <= 0:
            raise ValueError("补货数量必须为正数")
        product = self.get(sku)
        product.stock += quantity
        return product.stock

    def remove_stock(self, sku: str, quantity: int) -> int:
        """出库，返回剩余库存；库存不足时应拒绝出库。"""
        if quantity <= 0:
            raise ValueError("出库数量必须为正数")
        product = self.get(sku)
        product.stock -= quantity
        return product.stock

    def low_stock(self, threshold: int = 5) -> list[Product]:
        return [p for p in self._products.values() if p.stock < threshold]

    def products(self) -> list[Product]:
        return list(self._products.values())
