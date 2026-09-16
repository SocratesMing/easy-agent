"""Permission-checked Agent access to complete knowledge-document originals."""

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
from .domain import AllowedAction
from .repository import KnowledgeRepository
from .service import allowed_actions, effective_role
from .storage import OriginalStorageError, create_original_store
from .storage.base import OriginalDocumentStore


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


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
    """Expose originals without exposing or mounting the underlying NAS path."""

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
            # Deliberately hide whether an unauthorized resource exists.
            raise FileNotFoundError("知识资源不存在")
        return base

    def _audit(
        self,
        repository: KnowledgeRepository,
        principal: KnowledgePrincipal | None,
        document_id: str,
        action: str,
        result: str,
    ) -> None:
        if principal is None or not document_id:
            return
        try:
            repository.create_original_access_audit(
                user_id=principal.user_id,
                session_id=self.session_id,
                agent_id=self.agent_id,
                document_id=document_id,
                action=action,
                result=result,
            )
        except Exception:
            # Audit failure must not reveal storage or authorization internals to the model.
            pass

    def _execute(self, action: str, base_id: str = "", document_id: str = "") -> str:
        repository = KnowledgeRepository(get_database())
        principal: KnowledgePrincipal | None = None
        try:
            principal = self._principal()
            if action == "list":
                if not base_id:
                    return "错误：list 操作需要 base_id。"
                self._authorized_base(repository, base_id, principal)
                documents = repository.list_documents(
                    base_id, limit=1000, offset=0
                )
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
                self._audit(repository, principal, document_id, action, "success")
                return (
                    f"文件名：{document['name']}\n"
                    f"类型：{original.get('content_type') or document['content_type']}\n"
                    f"大小：{int(original.get('size_bytes') or 0)} bytes\n"
                    f"SHA-256：{original.get('sha256') or '未知'}"
                )

            max_bytes = (
                self.knowledge_config.original_storage.reads.max_agent_file_size_mb
                * 1024
                * 1024
            )
            size_bytes = int(original.get("size_bytes") or 0)
            if size_bytes > max_bytes:
                raise ValueError(
                    f"原文大小超过 Agent 访问上限（{max_bytes} bytes）"
                )
            store: OriginalDocumentStore = self.original_store
            handle = store.open(str(original["storage_key"]))
            safe_name = _SAFE_FILENAME.sub("_", Path(str(document["name"])).name)
            safe_name = safe_name[:180] or "original.bin"
            workspace_root = Path(self.workspace_dir).resolve()
            materialized_root = workspace_root / "knowledge-originals"
            if materialized_root.is_symlink():
                raise PermissionError("原文工作区路径不安全")
            materialized_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            ttl = self.knowledge_config.original_storage.reads.materialized_file_ttl_seconds
            cutoff = time.time() - ttl
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
            max_total = (
                self.knowledge_config.original_storage.reads.max_agent_total_size_mb
                * 1024
                * 1024
            )
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
            self._audit(repository, principal, document_id, action, "success")
            return (
                f"完整原文已复制到 /workspace/knowledge-originals/"
                f"{document_id}/{safe_name}，可用文件工具读取或分析。"
            )
        except (FileNotFoundError, PermissionError, ValueError, OriginalStorageError) as exc:
            self._audit(repository, principal, document_id, action, "denied_or_failed")
            return f"错误：{exc}"


def create_knowledge_original_tool(
    *,
    username: str,
    session_id: str,
    workspace_dir: str | Path,
    config: KnowledgeConfig,
) -> KnowledgeOriginalTool | None:
    store = create_original_store(config)
    if not config.enabled or store is None:
        return None
    return KnowledgeOriginalTool(
        username=username,
        session_id=session_id,
        workspace_dir=str(workspace_dir),
        knowledge_config=config,
        original_store=store,
    )


__all__ = ["KnowledgeOriginalTool", "create_knowledge_original_tool"]
