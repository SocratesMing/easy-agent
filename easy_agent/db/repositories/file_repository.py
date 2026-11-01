"""files 域 repository 对象（C1 剩余域 / T3b 收敛）。

覆盖会话上传文件（``session_files``）、生成文件（``generated_files``）、文件记录
删除与会话归属校验。T3b 把 ``api/files.py``、``services/session_uploads.py``、
``services/streaming/persistence.py``（生成文件登记）、``tools/session_file_reader.py``
的调用方迁到本对象；方法签名全部写全，不再有 ``(*args, **kwargs)``。

边界：

- 本模块不写 SQL，委托 ``db/repositories/files.py`` / ``sessions.py`` 与
  ``db/session_ownership.py`` 的既有函数；SQL、事务边界、表结构不变；
- 会话归属校验在方法内 lazy import，保持 db 层导入期不依赖 Web 框架；
- :meth:`get_session` 只用于定位会话工作区（``session_uploads`` 的文件目录端口），
  **不做归属校验**；需要鉴权的路由请用 :meth:`get_owned_session_meta`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from easy_agent.db.repositories import files as files_repo
from easy_agent.db.repositories import sessions as sessions_repo

if TYPE_CHECKING:
    from easy_agent.db.database import Database
    from easy_agent.models.db import SessionModel


class FileRepository:
    """文件登记、生成文件查询、删除权限与会话归属校验的薄委托层。"""

    def __init__(self, db: "Database"):
        self._db = db

    # ── 会话上传文件（session_files）──────────────────────────────────

    def add_session_file(
        self,
        session_id: str,
        filename: str,
        file_path: str,
        file_type: str,
        size: int,
        username: str = "",
    ) -> int | None:
        """登记一条会话上传文件，返回 ``session_files.id``（可能为 None）。"""
        return files_repo.add_session_file(
            self._db,
            session_id,
            filename,
            file_path,
            file_type,
            size,
            username,
        )

    def get_session_files(self, session_id: str) -> list[dict]:
        """会话的全部上传文件行（按上传时间倒序）。"""
        return files_repo.get_session_files(self._db, session_id)

    def get_file_record(
        self, file_id: int, username: str | None = None
    ) -> dict | None:
        """按 id 读取单条上传记录（删除前取 ``file_path`` 用；课按用户过滤）。"""
        return files_repo.get_file_record(self._db, file_id, username)

    def delete_file(self, file_id: int, username: str | None = None) -> bool:
        """删除一条上传文件记录（传 ``username`` 时只删该用户的记录）。"""
        return files_repo.delete_file(self._db, file_id, username)

    # ── 生成的资产文件（generated_files）─────────────────────────────

    def add_generated_file(
        self,
        session_id: str,
        message_id: str,
        filename: str,
        file_path: str,
        file_type: str,
        size: int,
    ) -> None:
        """登记一条 Agent 生成的资产文件。"""
        files_repo.add_generated_file(
            self._db,
            session_id,
            message_id,
            filename,
            file_path,
            file_type,
            size,
        )

    def get_generated_files(self, session_id: str) -> list[dict]:
        """会话生成文件行（按创建时间倒序）。"""
        return files_repo.get_generated_files(self._db, session_id)

    def delete_generated_file(self, file_id: int) -> bool:
        """删除单条生成文件记录（例如技能已入库、工作区不再保留该文件）。"""
        return files_repo.delete_generated_file(self._db, file_id)

    def get_generated_filenames(self, username: str) -> set[str]:
        """指定用户所有会话中生成的文件原始文件名集合。"""
        return files_repo.get_generated_filenames(self._db, username)

    # ── 会话（工作区定位 / 归属校验）─────────────────────────────────

    def get_session(self, session_id: str) -> "SessionModel | None":
        """按 id 读取会话（只用于定位工作区目录，不做归属校验）。"""
        return sessions_repo.get_session(self._db, session_id)

    def get_owned_session_meta(self, session_id: str, username: str) -> dict:
        """返回会话归属元信息；不存在或跨用户统一 404（领域异常）。"""
        from easy_agent.db.session_ownership import get_owned_session_meta

        return get_owned_session_meta(self._db, session_id, username)


__all__ = ["FileRepository"]