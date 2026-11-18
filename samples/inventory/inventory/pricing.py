BULK_QUANTITY = 10
BULK_DISCOUNT = 0.10


def order_total(unit_price: float, quantity: int) -> float:
    """计算订单金额：购买数量达到 BULK_QUANTITY（含）时整单打九折。"""
    if quantity <= 0:
        raise ValueError("购买数量必须为正数")
    total = unit_price * quantity
    if quantity > BULK_QUANTITY:
        total *= 1 - BULK_DISCOUNT
    return round(total, 2)


def apply_coupon(total: float, percent: float) -> float:
    """使用百分比优惠券，percent 取值 0~100。"""
    if not 0 <= percent <= 100:
        raise ValueError("优惠比例必须在 0~100 之间")
    return round(total * (1 - percent / 100), 2)
