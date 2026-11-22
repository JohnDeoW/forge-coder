# 项目说明（供 ForgeCoder 读取）

- 纯 Python 标准库项目；业务代码在 `todoapp/`，测试在 `tests/`。
- 运行测试：`pytest -q`。测试只覆盖 `store.py`，命令行与网页接口需要实际运行验证：
  - 命令行：`python -m todoapp.cli --file <临时文件> add ...` / `list` / `done`，验证时用 `--file` 指定临时文件，不要提交数据文件；
  - 网页接口：`python -m todoapp.web --selftest`（自动启动、调用并退出）；不要直接运行 `python -m todoapp.web` 启动常驻服务，命令会一直等待直到超时。
- 修复入口的问题后，为对应入口补充测试（可调用 `cli.main([...])`，网页接口可参考 `web.selftest` 启动临时服务）。
- 代码约定：函数带类型注解；错误信息使用中文。
