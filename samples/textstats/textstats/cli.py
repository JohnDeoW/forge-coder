"""命令行入口：python -m textstats.cli [文件路径 ...]"""

import sys
from pathlib import Path

from .analyzer import analyze, compare
from .report import format_report

SAMPLE = (
    "The quick brown fox jumps over the lazy dog. The dog sleeps!\n"
    "Is the fox quick? Yes, the fox is very quick."
)


def main(argv: list[str] | None = None) -> int:
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print(format_report(analyze(SAMPLE), "内置示例"))
        return 0
    texts = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            print(f"文件不存在：{p}", file=sys.stderr)
            return 1
        texts.append(path.read_text(encoding="utf-8"))
        print(format_report(analyze(texts[-1]), path.name))
    if len(texts) == 2:
        result = compare(*texts)
        print(f"相似度：{result['similarity']}，共同词：{', '.join(result['common']) or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
