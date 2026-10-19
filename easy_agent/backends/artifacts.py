"""会话 CompositeBackend 的 default：框架产物的隐藏目录 + 转发 execute（DA-04）。

``CompositeBackend`` 里没命中任何路由的路径都交给 ``default``。改造前 default 的文件根就是
会话工作区（与 ``/workspace/`` 路由同一个目录），于是：

- ``/secret/a.txt`` 与 ``/workspace/secret/a.txt`` 是同一个文件，写在 ``/workspace/...`` 上的
  deny 规则能经根路径别名绕过；
- 框架写的 ``/large_tool_results/``、``/conversation_history/`` 落进用户工作区（文件面板可见、
  会被同步进 LangSmith 沙箱）。

现在 default 的文件根是每个会话自己的隐藏目录（``workspace/{user}/.agent/{session_id}/``，
在会话工作区之外），且只允许写框架产物前缀 ``artifacts_root``（``/.agent/``）——写到别处的
文件用户看不到，直接报错提示改写 ``/workspace/``。``execute`` 原样转给会话 shell：工作目录
仍是会话工作区，路径翻译、bwrap / Landlock 沙箱、LangSmith 同步都不变。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import (
    PERMISSION_DENIED,
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileUploadResponse,
    SandboxBackendProtocol,
    WriteResult,
)

#: 框架产物（大工具结果、对话历史、二进制 blob）的虚拟根，见 ``CompositeBackend.artifacts_root``。
AGENT_ARTIFACTS_ROOT = "/.agent"


class AgentArtifactsBackend(FilesystemBackend, SandboxBackendProtocol):
    """文件操作落在会话隐藏目录（只写 ``artifacts_root``），命令执行交给会话 shell。"""

    def __init__(
        self,
        root_dir: str | Path,
        shell: Any,
        *,
        artifacts_root: str = AGENT_ARTIFACTS_ROOT,
        workspace_virtual_path: str = "/workspace",
    ) -> None:
        super().__init__(root_dir=root_dir, virtual_mode=True)
        self._shell = shell
        self._writable_prefix = artifacts_root.rstrip("/") + "/"
        self._workspace = workspace_virtual_path.rstrip("/") or "/workspace"

    @property
    def shell(self) -> Any:
        """真正执行命令的会话 shell（``PathTranslatingShell`` / LangSmith hybrid shell）。"""
        return self._shell

    def _outside_error(self, path: str) -> str | None:
        if path.startswith(self._writable_prefix):
            return None
        return (
            f"Error: {path} 不在任何挂载目录下，写在这里用户看不到。"
            f"请写到 {self._workspace}/ 下（例如 {self._workspace}{path}）。"
        )

    # ── 命令执行 ──────────────────────────────────────────────
    @property
    def id(self) -> str:
        return str(getattr(self._shell, "id", "") or f"agent-artifacts-{id(self):x}")

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self._shell.execute(command, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return await self._shell.aexecute(command, timeout=timeout)

    # ── 写入只允许框架产物前缀（异步版本默认经 to_thread 调到这里） ──
    def write(self, file_path: str, content: str) -> WriteResult:
        if error := self._outside_error(file_path):
            return WriteResult(error=error)
        return super().write(file_path, content)

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        if error := self._outside_error(file_path):
            return EditResult(error=error)
        return super().edit(file_path, old_string, new_string, replace_all=replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        if error := self._outside_error(file_path):
            return DeleteResult(error=error)
        return super().delete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        allowed = [(path, data) for path, data in files if self._outside_error(path) is None]
        uploaded = iter(super().upload_files(allowed) if allowed else [])
        return [
            next(uploaded) if self._outside_error(path) is None
            else FileUploadResponse(path=path, error=PERMISSION_DENIED)
            for path, _ in files
        ]
