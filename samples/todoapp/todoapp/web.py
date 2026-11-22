"""网页接口（标准库 http.server）：

    GET  /todos?status=open|done   列出待办事项
    POST /todos                    新增，请求体 {"title": "..."}
    POST /todos/<id>/done          标记完成

python -m todoapp.web --port 8000      启动服务
python -m todoapp.web --selftest       在临时端口启动服务，依次调用各接口并打印结果后退出
"""

import argparse
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request
from urllib.parse import parse_qs, urlparse

from .store import TodoStore

STATUS = {"open": False, "done": True}


def make_handler(store: TodoStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # 不在终端打印访问日志
            pass

        def _send(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/todos":
                query = parse_qs(urlparse(self.path).query)
                status = (query.get("status") or [None])[0]
                if status is not None and status not in STATUS:
                    return self._send(400, {"error": "status 只能是 open 或 done"})
                return self._send(200, store.list(None if status is None else STATUS[status]))
            self._send(404, {"error": "接口不存在"})

        def do_POST(self) -> None:
            parts = urlparse(self.path).path.strip("/").split("/")
            try:
                if parts == ["todos"]:
                    length = int(self.headers.get("Content-Length") or 0)
                    data = json.loads(self.rfile.read(length - 1) or b"{}")
                    return self._send(201, store.add(str(data.get("title") or "")))
                if len(parts) == 3 and parts[0] == "todos" and parts[2] == "done":
                    return self._send(200, store.complete(int(parts[1])))
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            self._send(404, {"error": "接口不存在"})

    return Handler


def make_server(store: TodoStore, port: int = 0) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))


def _call(base: str, method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def selftest() -> int:
    """在临时端口启动服务，调用各接口并检查结果；全部通过返回 0。"""
    with tempfile.TemporaryDirectory() as tmp:
        server = make_server(TodoStore(Path(tmp) / "todos.json"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        checks = [
            ("新增待办", ("POST", "/todos", {"title": "买牛奶"}), 201),
            ("再新增一条", ("POST", "/todos", {"title": "写周报"}), 201),
            ("标记第 1 条完成", ("POST", "/todos/1/done", None), 200),
            ("列出全部", ("GET", "/todos", None), 200),
            ("只列出未完成", ("GET", "/todos?status=open", None), 200),
            ("只列出已完成", ("GET", "/todos?status=done", None), 200),
        ]
        failed = 0
        try:
            for name, (method, path, payload), expected in checks:
                status, body = _call(base, method, path, payload)
                ok = status == expected
                failed += not ok
                print(f"{'✔' if ok else '✘'} {name}: {method} {path} -> {status} {json.dumps(body, ensure_ascii=False)}")
        finally:
            server.shutdown()
        print("自检通过" if not failed else f"自检失败：{failed} 项")
        return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="todoapp.web")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--file", default="todos.json")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    server = make_server(TodoStore(args.file), args.port)
    print(f"服务已启动：http://127.0.0.1:{server.server_address[1]}/todos（Ctrl+C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
