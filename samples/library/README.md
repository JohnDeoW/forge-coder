# library

ForgeCoder 的演示项目：一个小型图书馆借阅系统（纯 Python，无第三方依赖），由多个相互依赖的模块组成。

- `library/policy.py`：借阅规则（各会员等级的借期、借阅上限、逾期罚金标准）
- `library/dates.py`：日期解析与天数计算
- `library/catalog.py`：馆藏图书与可借副本数
- `library/members.py`：会员信息与借阅上限
- `library/loans.py`：借书、还书流程
- `library/fees.py`：逾期罚金计算
- `library/report.py`：逾期报表
- `library/cli.py`：命令行演示，运行 `python -m library.cli`

运行测试：`pytest`
