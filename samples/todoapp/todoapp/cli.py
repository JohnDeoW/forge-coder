"""命令行入口：python -m todoapp.cli [--file 路径] add|list|done ..."""

import argparse
import sys

from .store import TodoStore

STATUS = {"open": False, "done": True}


def format_todo(todo: dict) -> str:
    return f"{todo['id']:>3}. [{'x' if todo['done'] else ' '}] {todo['title']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="todoapp")
    parser.add_argument("--file", default="todos.json", help="数据文件路径")
    sub = parser.add_subparsers(dest="command", required=True)
    add_p = sub.add_parser("add", help="新增待办事项")
    add_p.add_argument("title")
    list_p = sub.add_parser("list", help="列出待办事项")
    list_p.add_argument("--status", choices=list(STATUS), help="按状态筛选")
    done_p = sub.add_parser("done", help="标记完成")
    done_p.add_argument("id", type=int)
    args = parser.parse_args(argv)

    store = TodoStore(args.file)
    try:
        if args.command == "add":
            print("已添加：" + format_todo(store.add(args.title)))
        elif args.command == "list":
            todos = store.list(None if args.status is None else not STATUS[args.status])
            print("\n".join(format_todo(t) for t in todos) or "（没有待办事项）")
        elif args.command == "done":
            print("已完成：" + format_todo(store.complete(args.id)))
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
