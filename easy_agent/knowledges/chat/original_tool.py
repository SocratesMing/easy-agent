"""带权限校验的 Agent 原文访问工具。

不暴露、不挂载底层存储路径；materialize 把原文安全复制进当前会话
工作区。读取上限：单文件 100MB、会话总量 200MB、落盘文件 TTL 3600 秒。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from easy_agent.db import get_database
from easy_agent.knowledges.api import KnowledgePrincipal
from easy_agent.knowledges.core.config import KnowledgeConfig
from easy_agent.knowledges.core.models import AllowedAction
from easy_agent.knowledges.core.operations_repository import KnowledgeOperationsRepository
from easy_agent.knowledges.core.original_store import LocalOriginalStore, OriginalStorageError
from easy_agent.knowledges.core.permissions import allowed_actions, effective_role
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.ragflow import RagflowClient, RagflowError


_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")

# Agent 原文读取上限
MAX_AGENT_FILE_SIZE_MB = 100
MAX_AGENT_TOTAL_SIZE_MB = 200
MATERIALIZED_FILE_TTL_SECONDS = 3600
# extract 返回给模型的文本上限，避免把整本 PDF 一次性塞进上下文/checkpoint
MAX_AGENT_EXTRACT_CHARS = 120_000
_SHARED_DATASET_NAME = "EA_SHARED"


class KnowledgeOriginalArgs(BaseModel):
    """工具入参 schema：action 四选一，按动作携带 base_id / document_id。"""

    action: Literal["list", "metadata", "extract", "materialize"] = Field(
        ...,
        description=(
            "list 列出知识库的原文；metadata 查看一份原文信息；"
            "extract 返回一份文档的已解析文本（PDF/Office 优先用这个）；"
            "materialize 将完整原文安全复制到当前会话 /workspace/knowledge-originals 下"
        ),
    )
    base_id: str = Field(
        default="",
        description=(
            "可选。list 操作推荐留空：默认列出当前会话已选择的知识库；"
            "也可传真实知识库 ID 或名称，但不要猜测或臆造"
        ),
    )
    document_id: str = Field(
        default="",
        description=(
            "metadata/extract/materialize 操作必填的文档 ID；"
            "必须先调用 list 获取真实 document_id，不要猜测"
        ),
    )


class KnowledgeOriginalTool(BaseTool):
    """在不对模型暴露存储路径的前提下提供原文访问。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "access_knowledge_original"
    description: str = (
        "访问知识库中的完整原文档，而不是 RAG 切片。"
        "当用户要求逐页、全表、全文或原始文件分析时使用。"
        "先用 list 找到文档 ID；list 的 base_id 可省略，省略时自动使用当前会话"
        "已选择的知识库，不要用知识库名称冒充 base_id。"
        "PDF/Office 原文分析必须用 extract 获取已解析文本；"
        "不要用 read_file 读取 materialize 后的二进制文件（会返回 base64 并撑爆上下文）。"
        "materialize 仅用于需要原文件路径的场景；不要臆造 document_id。"
    )
    args_schema: type = KnowledgeOriginalArgs

    username: str
    session_id: str = ""
    workspace_dir: str
    knowledge_config: KnowledgeConfig
    original_store: Any
    # 可注入的 RAGFlow 客户端（测试/宿主复用）；为空时 extract 按配置临时创建
    ragflow_client: Any = None
    agent_id: str = "easy-agent"

    def _run(self, **kwargs) -> str:
        if kwargs.get("action") == "extract":
            try:
                return asyncio.run(self._extract_text(**kwargs))
            except RuntimeError as exc:
                return f"错误：extract 需要在异步工具调用中执行（{exc}）"
        return self._execute(**kwargs)

    async def _arun(self, **kwargs) -> str:
        if kwargs.get("action") == "extract":
            return await self._extract_text(**kwargs)
        return self._execute(**kwargs)

    def _principal(self) -> KnowledgePrincipal:
        """按用户名实时重建 principal，用户已删除或失效时立即拒绝。"""

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
        """校验知识库可访问且角色含 DOWNLOAD 权限，返回库记录。"""

        base = repository.get_base(base_id)
        if base is None:
            raise FileNotFoundError("知识资源不存在")
        role = effective_role(base, principal, repository.list_permissions(base_id))
        if role is None or AllowedAction.DOWNLOAD not in allowed_actions(role):
            # 刻意不区分「无权」与「不存在」，避免资源存在性泄露
            raise FileNotFoundError("知识资源不存在")
        return base

    def _scoped_bases(
        self,
        repository: KnowledgeRepository,
        principal: KnowledgePrincipal,
    ) -> list[dict[str, Any]]:
        """当前会话已绑定、且当前用户仍可下载的知识库。"""

        if not self.session_id:
            return []
        base_ids = repository.get_session_scope(
            session_id=self.session_id, user_id=principal.user_id
        )
        bases: list[dict[str, Any]] = []
        for base_id in base_ids:
            try:
                bases.append(self._authorized_base(repository, base_id, principal))
            except FileNotFoundError:
                # 范围里已失效/被回收的知识库跳过，不能因此阻断其它库
                continue
        return bases

    @staticmethod
    def _base_options(bases: list[dict[str, Any]]) -> str:
        """把可选知识库格式化成模型可复制的候选清单。"""

        return "；".join(
            f"{base.get('name') or '未命名知识库'} (base_id={base['id']})"
            for base in bases
        )

    def _resolve_list_bases(
        self,
        repository: KnowledgeRepository,
        principal: KnowledgePrincipal,
        requested: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """解析 list 的目标知识库。

        优先精确 base_id；其次在当前会话范围内按名称精确匹配。两者都没有时，
        返回可执行提示而不是笼统的「知识资源不存在」，避免模型反复臆造 ID。
        """

        scoped = self._scoped_bases(repository, principal)
        selector = requested.strip()
        if not selector:
            if scoped:
                return scoped, None
            return [], (
                "当前会话未绑定知识库。请先在聊天输入框的“知识库”选择器中选择"
                "知识库，再调用 list；不要猜测 base_id。"
            )
        try:
            return [self._authorized_base(repository, selector, principal)], None
        except FileNotFoundError:
            pass
        matches = [
            base
            for base in scoped
            if str(base.get("name") or "").strip() == selector
        ]
        if len(matches) == 1:
            return matches, None
        if len(matches) > 1:
            return [], (
                "当前会话有多个同名知识库，请改用真实 base_id："
                f"{self._base_options(matches)}"
            )
        if scoped:
            return [], (
                f"未找到知识库 {selector!r}。当前会话可选："
                f"{self._base_options(scoped)}。可省略 base_id 直接列出全部，"
                "不要继续猜测。"
            )
        return [], (
            f"未找到知识库 {selector!r}，且当前会话未绑定知识库。"
            "请先在聊天输入框的“知识库”选择器中选择知识库，或提供真实 base_id。"
        )

    def _list_available_originals(
        self,
        repository: KnowledgeRepository,
        bases: list[dict[str, Any]],
    ) -> str:
        """列出目标知识库中可用的完整原文，输出真实 base_id/document_id。"""

        max_docs = max(
            1, int(self.knowledge_config.limits.max_documents_per_query)
        )
        lines: list[str] = []
        total = 0
        for base in bases:
            remaining = max_docs - total
            if remaining <= 0:
                break
            documents = repository.list_documents(
                str(base["id"]), limit=remaining, offset=0
            )
            available = [
                row
                for row in documents
                if str(row.get("original_status")) == "available"
            ]
            if not available:
                continue
            lines.append(
                f"知识库：{base.get('name') or '未命名知识库'} | "
                f"base_id={base['id']}"
            )
            lines.extend(
                f"- {row['name']} | document_id={row['id']} | "
                f"size={int(row.get('size_bytes') or 0)} bytes"
                for row in available
            )
            total += len(available)
        if not total:
            return "当前授权知识范围内暂无可用的完整原文。"
        return "\n".join([
            f"可用原文 {total} 份（list 可省略 base_id；"
            "metadata/extract/materialize 直接使用 document_id；"
            "PDF/Office 正文用 extract）：",
            *lines,
        ])

    @staticmethod
    def _chunk_text(chunk: Mapping[str, Any]) -> str:
        return str(
            chunk.get("content_with_weight") or chunk.get("content") or ""
        ).strip()

    @classmethod
    def _ordered_chunks(cls, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        key = next(
            (
                name
                for name in ("chunk_order_int", "order_int")
                if chunks
                and all(isinstance(item.get(name), (int, float)) for item in chunks)
            ),
            None,
        )
        return sorted(chunks, key=lambda item: item[key]) if key else chunks

    async def _extract_with_client(
        self,
        client: Any,
        *,
        base: Mapping[str, Any],
        document: Mapping[str, Any],
    ) -> str:
        """通过 RAGFlow chunk 列表返回已解析文本，避免模型读取 PDF 二进制。"""

        remote_id = str(document.get("remote_document_id") or "").strip()
        if not remote_id:
            return "错误：文档尚未同步到解析服务，暂无已解析文本。"
        dataset_id = str(base.get("remote_dataset_id") or "").strip()
        if not dataset_id:
            dataset_id = str(self.knowledge_config.shared_dataset_id or "").strip()
        if not dataset_id:
            data = await client.list_datasets(
                name=_SHARED_DATASET_NAME, page=1, page_size=10
            )
            datasets = data.get("datasets") if isinstance(data, dict) else None
            for item in datasets or []:
                if isinstance(item, dict) and item.get("name") == _SHARED_DATASET_NAME:
                    dataset_id = str(item.get("id") or "")
                    break
        if not dataset_id:
            return "错误：无法定位文档所在的数据集。"

        chunks: list[dict[str, Any]] = []
        for page in range(1, 101):
            data = await client.list_chunks(
                dataset_id, remote_id, page=page, page_size=100
            )
            batch = data.get("chunks") if isinstance(data, dict) else None
            if not isinstance(batch, list) or not batch:
                break
            chunks.extend(item for item in batch if isinstance(item, dict))
            total = data.get("total")
            if len(batch) < 100 or (
                isinstance(total, (int, float)) and len(chunks) >= int(total)
            ):
                break
        text = "\n".join(
            value
            for value in (
                self._chunk_text(item) for item in self._ordered_chunks(chunks)
            )
            if value
        )
        if not text:
            return "错误：文档暂无已解析文本，请确认解析状态为已就绪。"
        if len(text) > MAX_AGENT_EXTRACT_CHARS:
            text = (
                text[:MAX_AGENT_EXTRACT_CHARS]
                + "\n\n[文本过长，已截断；如需后续内容请用检索或分段工具。]"
            )
        return f"文档：{document['name']}\n\n{text}"

    async def _extract_text(
        self, *, action: str, base_id: str = "", document_id: str = ""
    ) -> str:
        del action, base_id
        db = get_database()
        repository = KnowledgeRepository(db)
        principal: KnowledgePrincipal | None = None
        try:
            principal = self._principal()
            if not document_id:
                return "错误：extract 操作需要 document_id。请先调用 list 获取真实文档 ID。"
            document = repository.get_document(document_id)
            if document is None:
                raise FileNotFoundError(
                    "文档不存在，请先调用 list 获取真实 document_id"
                )
            base = self._authorized_base(
                repository, str(document["base_id"]), principal
            )
            if self.ragflow_client is not None:
                text = await self._extract_with_client(
                    self.ragflow_client, base=base, document=document
                )
            else:
                async with RagflowClient(
                    self.knowledge_config.base_url,
                    self.knowledge_config.api_key.get_secret_value(),
                ) as client:
                    text = await self._extract_with_client(
                        client, base=base, document=document
                    )
            if not text.startswith("错误："):
                self._audit(db, principal, document_id, "extract", "success")
            return text
        except (FileNotFoundError, PermissionError, ValueError, RagflowError) as exc:
            self._audit(db, principal, document_id, "extract", "denied_or_failed")
            return f"错误：{exc}"

    def _audit(
        self,
        db,
        principal: KnowledgePrincipal | None,
        document_id: str,
        action: str,
        result: str,
    ) -> None:
        """把工具动作写入审计日志（受 audit 开关控制，失败静默）。"""

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
        """执行 list / metadata / materialize 动作，错误转成模型可读文本。

        materialize 先清理 TTL 过期副本并核验会话总量配额，再经临时文件
        原子落盘（大小 + SHA-256 双重完整性校验），成功后返回工作区相对路径。
        """

        db = get_database()
        repository = KnowledgeRepository(db)
        principal: KnowledgePrincipal | None = None
        try:
            principal = self._principal()
            if action == "list":
                bases, message = self._resolve_list_bases(
                    repository, principal, base_id
                )
                if message is not None:
                    return message
                return self._list_available_originals(repository, bases)

            if action == "extract":
                return "错误：extract 需要通过工具的异步调用执行。"
            if action not in {"metadata", "materialize"} or not document_id:
                return (
                    "错误：metadata/materialize 操作需要 document_id。"
                    "请先调用 list 获取真实 document_id，不要猜测。"
                )
            document = repository.get_document(document_id)
            if document is None:
                raise FileNotFoundError(
                    "文档不存在，请先调用 list 获取真实 document_id"
                )
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
                f"{document_id}/{safe_name}。PDF/Office 等二进制文件不要用 "
                "read_file 读取；需要正文请改用 extract action。"
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
    """构造带权限校验的原文工具；知识模块禁用时返回 None。"""
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
