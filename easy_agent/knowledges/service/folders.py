"""文件夹：层级深度、同级重名冲突与目录树删除规则。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from easy_agent.knowledges.core.models import (
    AllowedAction,
    FolderEnsureResponse,
    FolderListResponse,
    FolderSummary,
)
from easy_agent.knowledges.service.contracts import (
    KnowledgeServiceError,
    _folder_depth_error,
    _folder_missing_error,
    _not_found,
)

if TYPE_CHECKING:
    from easy_agent.knowledges.core.models import KnowledgePrincipal


def _split_folder_segments(path: str) -> list[str]:
    """按 / 或 \\ 拆分相对路径段，过滤空白段。"""

    return [part.strip() for part in re.split(r"[\\/]+", path) if part.strip()]


class FolderMixin:
    """文件夹方法；由 KnowledgeService 组合，复用其仓储、鉴权与远端删除能力。"""

    async def list_folders(
        self, base_id: str, principal: KnowledgePrincipal
    ) -> FolderListResponse:
        """列出知识库内全部文件夹（含文档与子文件夹计数）。"""

        _, role = await self._authorized_base(base_id, principal)
        rows = await self._repo(self.repository.list_folders, base_id)
        return FolderListResponse(
            items=[self._folder_summary(row, role) for row in rows]
        )

    async def _require_folder(
        self,
        base_id: str,
        folder_id: str,
        *,
        missing_message: str = "文件夹不存在",
    ) -> dict[str, Any]:
        """读取文件夹并校验归属；不存在或跨库时按 404 处理。"""

        folder = await self._repo(self.repository.get_folder, folder_id)
        if folder is None or str(folder["base_id"]) != base_id:
            raise _folder_missing_error(missing_message)
        return folder

    @staticmethod
    def _is_folder_conflict(exc: Exception) -> bool:
        """数据库唯一约束冲突（并发创建/重命名同名文件夹）。"""

        text = str(exc).upper()
        return "UNIQUE" in text or "DUPLICATE" in text

    @staticmethod
    def _folder_conflict_error() -> KnowledgeServiceError:
        """同级同名文件夹冲突（预检查命中或唯一约束冲突）统一 409。"""

        return KnowledgeServiceError(
            "KNOWLEDGE_FOLDER_EXISTS", "同名文件夹已存在", status_code=409
        )

    @staticmethod
    @contextmanager
    def _folder_conflict_as_409() -> Iterator[None]:
        """把同级同名文件夹的唯一约束冲突统一翻译为 409。"""

        try:
            yield
        except KnowledgeServiceError:
            raise
        except Exception as exc:
            if FolderMixin._is_folder_conflict(exc):
                raise FolderMixin._folder_conflict_error() from exc
            raise

    async def create_folder(
        self,
        *,
        base_id: str,
        name: str,
        parent_id: str | None,
        principal: KnowledgePrincipal,
    ) -> FolderSummary:
        """创建文件夹：校验层级深度（最多 5 层）与同级重名。"""

        _, role = await self._authorized_base(
            base_id, principal, AllowedAction.CREATE_FOLDER
        )
        if parent_id:
            await self._require_folder(
                base_id, parent_id, missing_message="上级文件夹不存在"
            )
            if await self._folder_chain_depth(base_id, parent_id) >= 5:
                raise _folder_depth_error()
        with self._folder_conflict_as_409():
            existing = await self._repo(
                self.repository.find_folder_by_name, base_id, name, parent_id
            )
            if existing is not None:
                raise self._folder_conflict_error()
            row = await self._repo(
                self.repository.create_folder,
                base_id=base_id,
                name=name,
                parent_id=parent_id,
            )
        return self._folder_summary(row, role)

    async def _folder_chain_depth(
        self, base_id: str, folder_id: str | None
    ) -> int:
        """文件夹层级深度：自身记 1，逐级向上到库根；库根（无文件夹）为 0。"""
        depth = 0
        current = folder_id
        seen: set[str] = set()
        while current:
            if current in seen:  # 脏数据环保护
                break
            seen.add(current)
            folder = await self._repo(self.repository.get_folder, current)
            if folder is None or str(folder["base_id"]) != base_id:
                raise _folder_missing_error()
            depth += 1
            current = str(folder.get("parent_id") or "") or None
        return depth

    async def _ensure_folder_dirs(
        self, base_id: str, folder_id: str | None, dirs: list[str]
    ) -> str | None:
        """逐级 find-or-create 目录段，返回叶子文件夹 ID；空段列表原样返回。

        同级同名文件夹已存在则直接复用，重复上传不产生重复目录。段名拒绝
        「.」「..」与超长名，深度沿用 create_folder 的 5 层上限。
        """
        for segment in dirs:
            if segment in {".", ".."} or len(segment) > 127:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_INVALID_UPLOAD_PATH",
                    "上传路径包含非法目录名",
                    status_code=422,
                )
        current = folder_id
        depth = await self._folder_chain_depth(base_id, current)
        for segment in dirs:
            existing = await self._repo(
                self.repository.find_folder_by_name, base_id, segment, current
            )
            if existing is None and depth >= 5:
                raise _folder_depth_error()
            if existing is None:
                try:
                    row = await self._repo(
                        self.repository.create_folder,
                        base_id=base_id,
                        name=segment,
                        parent_id=current,
                    )
                    current = str(row["id"])
                except Exception as exc:
                    # 并发下撞唯一约束：回读已创建的同名文件夹复用
                    if not self._is_folder_conflict(exc):
                        raise
                    existing = await self._repo(
                        self.repository.find_folder_by_name,
                        base_id,
                        segment,
                        current,
                    )
                    if existing is None:
                        raise
                    current = str(existing["id"])
            else:
                current = str(existing["id"])
            depth += 1
        return current

    async def ensure_folders(
        self,
        *,
        base_id: str,
        folder_id: str | None,
        paths: list[str],
        principal: KnowledgePrincipal,
    ) -> FolderEnsureResponse:
        """批量确保相对目录树存在（文件夹上传的空目录补齐），幂等。

        paths 为纯目录相对路径（如「资料/报告」），自 folder_id（或库根）起
        逐级 find-or-create；返回实际处理的路径数。权限门槛与上传一致
        （UPLOAD），因为它是文件夹上传流程的一部分。
        """
        await self._authorized_base(base_id, principal, AllowedAction.UPLOAD)
        if folder_id:
            await self._require_folder(base_id, folder_id)
        ensured = 0
        for raw_path in paths:
            segments = _split_folder_segments(str(raw_path or ""))
            if not segments:
                continue
            await self._ensure_folder_dirs(base_id, folder_id, segments)
            ensured += 1
        return FolderEnsureResponse(ensured=ensured)

    async def update_folder(
        self, *, folder_id: str, name: str, principal: KnowledgePrincipal
    ) -> FolderSummary:
        """重命名文件夹，同级重名时冲突报错。"""

        folder = await self._repo(self.repository.get_folder, folder_id)
        if folder is None:
            raise _not_found()
        _, role = await self._authorized_base(
            str(folder["base_id"]), principal, AllowedAction.EDIT_FOLDER
        )
        with self._folder_conflict_as_409():
            if str(folder["name"]) != name:
                existing = await self._repo(
                    self.repository.find_folder_by_name,
                    str(folder["base_id"]),
                    name,
                    folder.get("parent_id"),
                )
                if existing is not None:
                    raise self._folder_conflict_error()
            row = await self._repo(
                self.repository.update_folder, folder_id, name=name
            )
        assert row is not None
        return self._folder_summary(row, role)

    @staticmethod
    def _folder_subtree_ids(
        folders: Iterable[Mapping[str, Any]], root_id: str
    ) -> list[str]:
        """返回 root 文件夹及其全部后代文件夹 ID。"""
        children: dict[str, list[str]] = {}
        for row in folders:
            parent_id = row.get("parent_id")
            if parent_id:
                children.setdefault(str(parent_id), []).append(str(row["id"]))
        ids: list[str] = []
        stack = [root_id]
        while stack:
            current = stack.pop()
            ids.append(current)
            stack.extend(children.get(current, []))
        return ids

    async def delete_folder(
        self, *, folder_id: str, principal: KnowledgePrincipal
    ) -> None:
        """删除文件夹及其全部内容（含子文件夹与文档）。

        先同步删除子树内文档的远端（Ragflow）副本，再在同一事务内软删除本地
        文档并硬删除文件夹；远端删除失败时保持本地原状，用户可重试。
        """

        folder = await self._repo(self.repository.get_folder, folder_id)
        if folder is None:
            raise _not_found()
        base_id = str(folder["base_id"])
        base, _role = await self._authorized_base(
            base_id, principal, AllowedAction.DELETE_FOLDER
        )
        folders = await self._repo(self.repository.list_folders, base_id)
        folder_ids = self._folder_subtree_ids(folders, folder_id)
        documents = await self._repo(
            self.repository.list_documents_in_folders, base_id, folder_ids
        )
        remote_ids = [
            remote_id
            for document in documents
            if (remote_id := str(document.get("remote_document_id") or "").strip())
        ]
        if remote_ids:
            dataset_id = await self._dataset_id_for(base)
            await self._delete_remote_in_batches(dataset_id, remote_ids)
        await self._repo(
            self.repository.delete_folder_tree,
            folder_ids=folder_ids,
            deleted_at=datetime.now(UTC).isoformat(),
            deleted_by=principal.user_id,
        )
