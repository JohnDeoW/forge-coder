# todoapp

ForgeCoder 的演示项目：待办事项应用（纯 Python 标准库），同一份数据有命令行与网页接口两个入口。

- `todoapp/store.py`：待办事项的存取（JSON 文件）
- `todoapp/cli.py`：命令行入口
  - `python -m todoapp.cli add "买牛奶"`：新增
  - `python -m todoapp.cli list [--status open|done]`：列出（可按状态筛选）
  - `python -m todoapp.cli done 1`：标记完成
  - 数据默认保存在当前目录的 `todos.json`，可用 `--file` 指定
- `todoapp/web.py`：网页接口（标准库 http.server）
  - `GET /todos?status=open|done`、`POST /todos`（JSON：`{"title": ...}`）、`POST /todos/<id>/done`
  - `python -m todoapp.web --port 8000` 启动服务
  - `python -m todoapp.web --selftest` 在临时端口启动服务、依次调用各接口并打印结果，结束后自动退出

运行测试：`pytest`（目前只覆盖 store）

## 待办

- 命令行与网页接口还没有测试，上线前请实际运行一遍两个入口，确认功能正常。
