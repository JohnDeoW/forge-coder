# 项目说明（供 ForgeCoder 读取）

- 纯 Python 项目，无第三方依赖；业务代码在 `inventory/`，测试在 `tests/`。
- 运行全部测试：`pytest -q`；运行演示：`python -m inventory.cli`。
- 代码约定：函数带类型注解；参数不合法时抛出 `ValueError`，错误信息使用中文；金额结果用 `round(x, 2)` 保留两位小数。
- 新增功能需在 `tests/` 中补充对应测试；修复缺陷时不要修改已有测试的期望值。
