"""借阅规则：按会员等级区分借期与借阅上限，逾期按天收取罚金。"""

LEVELS = ("standard", "premium")

LOAN_DAYS = {"standard": 14, "premium": 28}  # 借期（天）
BORROW_LIMIT = {"standard": 3, "premium": 6}  # 同时在借的最多册数

DAILY_FEE = 0.5  # 每逾期一天的罚金（元）
FEE_RATE = {"standard": 1.0, "premium": 0.5}  # 各等级的罚金折扣
FEE_CAP = 20.0  # 单次借阅的罚金上限（元）
