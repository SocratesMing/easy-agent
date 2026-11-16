"""会话文件下载与聊天引用共用的工作区目标校验。"""

from pathlib import Path
from typing import Literal

from easy_agent.models.exceptions import BadRequestError, NotFoundError


def resolve_workspace_path(
    workspace_dir: Path, path: str, kind: Literal["file", "directory"] = "file"
) -> Path:
    target = (workspace_dir / path).resolve()
    base = workspace_dir.resolve()
    if target != base and base not in target.parents:
        raise BadRequestError("非法的文件路径")
    matches = target.is_file() if kind == "file" else target.is_dir()
    if not matches:
        raise NotFoundError("文件不存在" if kind == "file" else "目录不存在")
    return target
