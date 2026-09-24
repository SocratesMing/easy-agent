"""带权限校验的 Agent 原文访问工具。

不暴露、不挂载底层存储路径；materialize 把原文安全复制进当前会话
工作区。读取上限（旧版 original_storage.reads 配置段的固定值）：
单文件 100MB、会话总量 200MB、落盘文件 TTL 3600 秒。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from ..db import get_database
from .auth import KnowledgePrincipal
from .config import KnowledgeConfig
from .models import AllowedAction
from .operations_repository import KnowledgeOperationsRepository
from .repository import KnowledgeRepository
from .service import (
    LocalOriginalStore,
    OriginalStorageError,
    allowed_actions,
    effective_role,
)


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")

# Agent 原文读取上限（旧版配置的固定值，新版写死）
MAX_AGENT_FILE_SIZE_MB = 100
MAX_AGENT_TOTAL_SIZE_MB = 200
MATERIALIZED_FILE_TTL_SECONDS = 3600


class KnowledgeOriginalArgs(BaseModel):
    action: Literal["list", "metadata", "materialize"] = Field(
        ...,
        description=(
            "list 列出某知识库的原文；metadata 查看一份原文信息；"
            "materialize 将完整原文安全复制到当前会话 /workspace/knowledge-originals 下"
        ),
    )
    base_id: str = Field(default="", description="list 操作必填的知识库 ID")
    document_id: str = Field(
        default="", description="metadata/materialize 操作必填的文档 ID"
    )


class KnowledgeOriginalTool(BaseTool):
    """在不对模型暴露存储路径的前提下提供原文访问。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "access_knowledge_original"
    description: str = (
        "访问知识库中的完整原文档，而不是 RAG 切片。"
        "当用户要求逐页、全表、全文或原始文件分析时使用。"
        "先用 list 找到文档 ID，再用 materialize 将文件放入当前会话工作区。"
    )
    args_schema: type = KnowledgeOriginalArgs

    username: str
    session_id: str = ""
    workspace_dir: str
    knowledge_config: KnowledgeConfig
    original_store: Any
    agent_id: str = "easy-agent"

    def _run(self, **kwargs) -> str:
        return self._execute(**kwargs)

    async def _arun(self, **kwargs) -> str:
        return self._execute(**kwargs)

    def _principal(self) -> KnowledgePrincipal:
        user = get_database().get_user_by_username(self.username)
        if user is None:
            raise PermissionError("当前用户不存在或已失效")
        return KnowledgePrincipal(
            user_id=user.user_id,
            username=user.username,
            department_id=user.organization_id.strip() or None,
        )

    @staticmethod
    def _authorized_base(
        repository: KnowledgeRepository,
        base_id: str,
        principal: KnowledgePrincipal,
    ) -> dict[str, Any]:
        base = repository.get_base(base_id)
        if base is None:
            raise FileNotFoundError("知识资源不存在")
        role = effective_role(base, principal, repository.list_permissions(base_id))
        if role is None or AllowedAction.DOWNLOAD not in allowed_actions(role):
            # 刻意不区分「无权」与「不存在」，避免资源存在性泄露
            raise FileNotFoundError("知识资源不存在")
        return base

    def _audit(
        self,
        db,
        principal: KnowledgePrincipal | None,
        document_id: str,
        action: str,
        result: str,
    ) -> None:
        if principal is None or not document_id:
            return
        if not self.knowledge_config.audit.enabled:
            return
        try:
            KnowledgeOperationsRepository(db).record_audit(
                request_id=f"agent-original-{uuid.uuid4()}",
                actor_user_id=principal.user_id,
                actor_username=principal.username,
                action=f"knowledge_original.{action}",
                object_type="knowledge_document",
                object_id=document_id,
                outcome=result,
                details={
                    "session_id": self.session_id,
                    "agent_id": self.agent_id,
                },
                max_details_bytes=self.knowledge_config.audit.max_details_bytes,
            )
        except Exception:
            # 审计失败不得向模型泄露存储或授权内部细节
            pass

    def _execute(self, action: str, base_id: str = "", document_id: str = "") -> str:
        db = get_database()
        repository = KnowledgeRepository(db)
        principal: KnowledgePrincipal | None = None
        try:
            principal = self._principal()
            if action == "list":
                if not base_id:
                    return "错误：list 操作需要 base_id。"
                self._authorized_base(repository, base_id, principal)
                documents = repository.list_documents(base_id, limit=1000, offset=0)
                available = [
                    row
                    for row in documents
                    if str(row.get("original_status")) == "available"
                ]
                if not available:
                    return "该知识库暂无可用的完整原文。"
                lines = [f"可用原文 {len(available)} 份："]
                lines.extend(
                    f"- {row['name']} | document_id={row['id']} | "
                    f"size={int(row.get('size_bytes') or 0)} bytes"
                    for row in available
                )
                return "\n".join(lines)

            if action not in {"metadata", "materialize"} or not document_id:
                return "错误：metadata/materialize 操作需要 document_id。"
            document = repository.get_document(document_id)
            if document is None:
                raise FileNotFoundError("知识资源不存在")
            self._authorized_base(repository, str(document["base_id"]), principal)
            original = repository.get_original_object(document_id)
            if original is None or str(original.get("status")) != "available":
                raise FileNotFoundError("完整原文暂不可用")
            if action == "metadata":
                self._audit(db, principal, document_id, action, "success")
                return (
                    f"文件名：{document['name']}\n"
                    f"类型：{original.get('content_type') or document['content_type']}\n"
                    f"大小：{int(original.get('size_bytes') or 0)} bytes\n"
                    f"SHA-256：{original.get('sha256') or '未知'}"
                )

            max_bytes = MAX_AGENT_FILE_SIZE_MB * 1024 * 1024
            size_bytes = int(original.get("size_bytes") or 0)
            if size_bytes > max_bytes:
                raise ValueError(
                    f"原文大小超过 Agent 访问上限（{max_bytes} bytes）"
                )
            store: LocalOriginalStore = self.original_store
            handle = store.open(str(original["storage_key"]))
            safe_name = _SAFE_FILENAME.sub("_", Path(str(document["name"])).name)
            safe_name = safe_name[:180] or "original.bin"
            workspace_root = Path(self.workspace_dir).resolve()
            materialized_root = workspace_root / "knowledge-originals"
            if materialized_root.is_symlink():
                raise PermissionError("原文工作区路径不安全")
            materialized_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            cutoff = time.time() - MATERIALIZED_FILE_TTL_SECONDS
            for candidate in materialized_root.rglob("*"):
                if candidate.is_file() and not candidate.is_symlink():
                    try:
                        if candidate.stat().st_mtime < cutoff:
                            candidate.unlink()
                    except OSError:
                        pass
            current_total = sum(
                candidate.stat().st_size
                for candidate in materialized_root.rglob("*")
                if candidate.is_file() and not candidate.is_symlink()
            )
            destination_dir = materialized_root / document_id
            if destination_dir.is_symlink():
                raise PermissionError("原文文档路径不安全")
            destination = destination_dir / safe_name
            replaced_size = (
                destination.stat().st_size
                if destination.is_file() and not destination.is_symlink()
                else 0
            )
            max_total = MAX_AGENT_TOTAL_SIZE_MB * 1024 * 1024
            if current_total - replaced_size + size_bytes > max_total:
                raise ValueError(
                    f"当前会话原文总量超过上限（{max_total} bytes）"
                )
            destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
            digest = hashlib.sha256()
            written = 0
            try:
                with temporary.open("xb") as output:
                    os.chmod(temporary, 0o600)
                    for chunk in handle.iter_bytes():
                        output.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                expected_sha = str(original.get("sha256") or "")
                if written != handle.size_bytes or (
                    expected_sha and digest.hexdigest() != expected_sha
                ):
                    raise OriginalStorageError(
                        "ORIGINAL_STORAGE_INTEGRITY_FAILED",
                        "原文完整性校验失败",
                        retryable=False,
                    )
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            self._audit(db, principal, document_id, action, "success")
            return (
                f"完整原文已复制到 /workspace/knowledge-originals/"
                f"{document_id}/{safe_name}，可用文件工具读取或分析。"
            )
        except (FileNotFoundError, PermissionError, ValueError, OriginalStorageError) as exc:
            self._audit(db, principal, document_id, action, "denied_or_failed")
            return f"错误：{exc}"


def create_knowledge_original_tool(
    *,
    username: str,
    session_id: str,
    workspace_dir: str | Path,
    config: KnowledgeConfig,
) -> KnowledgeOriginalTool | None:
    # 先判禁用再建存储：LocalOriginalStore 构造会 mkdir，禁用态不应产生目录
    if not config.enabled:
        return None
    return KnowledgeOriginalTool(
        username=username,
        session_id=session_id,
        workspace_dir=str(workspace_dir),
        knowledge_config=config,
        original_store=LocalOriginalStore(),
    )


__all__ = ["KnowledgeOriginalTool", "create_knowledge_original_tool"]
