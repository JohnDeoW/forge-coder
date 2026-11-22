# 项目说明（供 ForgeCoder 读取）

- 纯 Python 项目，无第三方依赖；业务代码在 `library/`，测试在 `tests/`。
- 运行全部测试：`pytest -q`；运行演示：`python -m library.cli`。
- 模块依赖：`loans` 调用 `catalog`、`members`、`fees`、`dates`；`fees` 与 `members` 读取 `policy` 中的规则。修改前先弄清调用链，在问题的根源处修复，不要在调用方打补丁绕过。
- 代码约定：函数带类型注解；参数不合法时抛出 `ValueError`，错误信息使用中文；金额结果用 `round(x, 2)` 保留两位小数。
- 修复缺陷时不要修改已有测试的期望值；新增功能需在 `tests/` 中补充对应测试。
