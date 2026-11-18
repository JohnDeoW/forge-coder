"""权限：工作区策略（是否允许 / 需要确认）与审批管理（由谁、如何确认）。"""

from .approvals import WEB_APPROVAL, ApprovalManager
from .policy import Approver, WorkspacePolicy, is_whitelisted, program_of, resolve_in_root

__all__ = ["ApprovalManager", "Approver", "WEB_APPROVAL", "WorkspacePolicy", "is_whitelisted", "program_of", "resolve_in_root"]
