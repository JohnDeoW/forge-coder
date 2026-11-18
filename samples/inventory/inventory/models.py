from dataclasses import dataclass


@dataclass
class Product:
    sku: str
    name: str
    category: str
    price: float
    stock: int = 0

    @property
    def value(self) -> float:
        return self.price * self.stock
