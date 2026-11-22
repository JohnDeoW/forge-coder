# 项目说明（供 ForgeCoder 读取）

- 纯 Python 项目，无第三方依赖；业务代码在 `textstats/`，测试在 `tests/`。
- 运行全部测试：`pytest -q`；运行命令行：`python -m textstats.cli`（统计内置示例）。
- 重构的要求：对外接口（`analyze`、`compare`、`format_report` 的签名与返回结构）保持不变；先补充能锁定当前行为的测试，再分步重构，每一步后运行测试。
- 重构中发现的不一致行为先向用户说明，确认后再修改，不要悄悄改变结果。
- 代码约定：函数带类型注解；参数不合法时抛出 `ValueError`，错误信息使用中文；小数结果用 `round(x, 2)`。
