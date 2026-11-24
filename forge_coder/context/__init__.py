"""上下文：每次模型调用前注入的参考资料（providers），以及上下文窗口用量统计（window）。"""

from .providers import SeenFilesProvider, create_context_builder, read_project_notes, repo_map_provider
from .references import ReferencedFilesProvider, attachment, find_references, strip_attachments
from .window import context_usage, format_context_usage

__all__ = ["ReferencedFilesProvider", "SeenFilesProvider", "attachment", "context_usage",
           "create_context_builder", "find_references", "format_context_usage", "read_project_notes", "repo_map_provider",
           "strip_attachments"]
