# inventory

ForgeCoder 的演示项目：一个小型库存管理模块（纯 Python，无第三方依赖）。

- `inventory/store.py`：商品与库存管理（补货、出库、低库存查询）
- `inventory/pricing.py`：订单金额与优惠券计算
- `inventory/report.py`：库存报表与总价值
- `inventory/cli.py`：命令行演示，运行 `python -m inventory.cli`

运行测试：`pytest`

## 待办

- 按类别汇总库存价值：新增 `category_summary(inventory)`，返回 `{类别: 总价值}`，并补充测试。
