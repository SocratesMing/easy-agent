"""文件夹数据访问：增删改查、同名查找与子树删除。"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from easy_agent.knowledges.core.repository_base import _now


class FolderRepositoryMixin:
    """文件夹相关查询与写入；由 KnowledgeRepository 组合。"""

    # ---------------- 文件夹 ----------------

    def create_folder(
        self, *, base_id: str, name: str, parent_id: str | None = None, sort_order: int = 0
    ) -> dict[str, Any]:
        folder_id = str(uuid.uuid4())
        timestamp = _now()
        self._execute_write(
            """
            INSERT INTO knowledge_folders
                (id, base_id, parent_id, name, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (folder_id, base_id, parent_id, name, sort_order, timestamp, timestamp),
        )
        return self.get_folder(folder_id)

    def get_folder(self, folder_id: str) -> dict[str, Any] | None:
        return self._fetch_one("SELECT * FROM knowledge_folders WHERE id=?", (folder_id,))

    def find_folder_by_name(
        self, base_id: str, name: str, parent_id: str | None = None
    ) -> dict[str, Any] | None:
        """按 (base_id, parent_id, name) 查找文件夹；parent_id 为 None 时匹配根级。

        唯一约束放宽为 (base_id, parent_id, name) 后，parent_id 为 NULL 的
        根级同名不再受数据库唯一性保护（NULL 不参与唯一判断），由本方法
        供服务层显式查重兜底。
        """
        if parent_id is None:
            return self._fetch_one(
                "SELECT * FROM knowledge_folders "
                "WHERE base_id=? AND name=? AND parent_id IS NULL",
                (base_id, name),
            )
        return self._fetch_one(
            "SELECT * FROM knowledge_folders WHERE base_id=? AND name=? AND parent_id=?",
            (base_id, name, parent_id),
        )

    def list_folders(self, base_id: str) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            SELECT f.*, COUNT(DISTINCT d.id) AS document_count,
                   COUNT(DISTINCT child.id) AS child_count
            FROM knowledge_folders f
            LEFT JOIN knowledge_documents d ON d.folder_id=f.id AND d.status!='deleted'
            LEFT JOIN knowledge_folders child ON child.parent_id=f.id
            WHERE f.base_id=?
            GROUP BY f.id
            ORDER BY f.sort_order, f.name
            """,
            (base_id,),
        )

    def update_folder(self, folder_id: str, *, name: str) -> dict[str, Any] | None:
        self._execute_write(
            "UPDATE knowledge_folders SET name=?, updated_at=? WHERE id=?",
            (name, _now(), folder_id),
        )
        return self.get_folder(folder_id)

    def delete_folder_tree(
        self, *, folder_ids: Iterable[str], deleted_at: str, deleted_by: str
    ) -> int:
        """删除文件夹子树，并软删除其中的文档。

        远端副本由服务层在调用前同步删除；本方法在同一事务内软删除子树中
        未删除的文档、清理全部文档的目录引用，再硬删除文件夹行。返回删除的
        文件夹数量。
        """
        ids = list(dict.fromkeys(str(folder_id) for folder_id in folder_ids))
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "UPDATE knowledge_documents SET status='deleted', deleted_at=?, "
                "deleted_by=?, remote_document_id=NULL, progress=NULL, "
                "folder_id=NULL, updated_at=? "
                f"WHERE folder_id IN ({placeholders}) AND status!='deleted'",
                (deleted_at, deleted_by, timestamp, *ids),
            )
            # 子文件夹里可能还有更早软删除的文档，一并清掉目录引用，避免悬空 folder_id
            self.db._execute(
                cursor,
                "UPDATE knowledge_documents SET folder_id=NULL, updated_at=? "
                f"WHERE folder_id IN ({placeholders})",
                (timestamp, *ids),
            )
            self.db._execute(
                cursor,
                f"DELETE FROM knowledge_folders WHERE id IN ({placeholders})",
                tuple(ids),
            )
            return int(cursor.rowcount or 0)
