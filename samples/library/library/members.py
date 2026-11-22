"""会员：等级决定借期、借阅上限与罚金折扣。"""

from dataclasses import dataclass

from . import policy


@dataclass
class Member:
    id: str
    name: str
    level: str = "standard"

    @classmethod
    def from_record(cls, record: dict) -> "Member":
        """从导入的会员记录创建（记录中的等级可能带空格或大小写不一）。"""
        level = str(record.get("level") or "standard").strip()
        if level.lower() not in policy.LEVELS:
            raise ValueError(f"未知的会员等级：{level}")
        return cls(id=str(record["id"]), name=str(record["name"]), level=level)


def borrow_limit(member: Member) -> int:
    return policy.BORROW_LIMIT.get(member.level, policy.BORROW_LIMIT["standard"])


def loan_days(member: Member) -> int:
    return policy.LOAN_DAYS.get(member.level, policy.LOAN_DAYS["standard"])


def fee_rate(member: Member) -> float:
    return policy.FEE_RATE.get(member.level, policy.FEE_RATE["standard"])
