# textstats

ForgeCoder 的演示项目：英文文本统计工具（纯 Python，无第三方依赖）。

- `textstats/analyzer.py`：统计字符数、词数、句子数、平均词长、高频词与阅读时长，对比两段文本的用词
- `textstats/report.py`：把统计结果格式化为文字报告
- `textstats/cli.py`：命令行入口，运行 `python -m textstats.cli 文件路径`（不带参数时统计内置示例）

运行测试：`pytest`

## 待办

- 重构 `analyzer.py`：`analyze` 与 `compare` 一个函数做了所有事情，分词、句子切分等逻辑重复出现。拆分为职责单一的小函数，消除重复；`analyze` 与 `compare` 的函数签名和返回结构保持不变。
- 测试很少：为拆分后的各个函数与 `report.py` 补充测试。
