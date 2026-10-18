"""files 域 repository 对象（C1 剩余域）。

覆盖 ``api/files.py`` 实际使用的会话文件 / 生成文件 / 会话归属查询。
所有方法委托既有 ``db/repositories/files.py`` 或 ``utils.session`` 函数，
不写 SQL；会话归属校验在方法内 lazy import，保持 db 层导入期不依赖 Web 框架。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from easy_agent.db.repositories import files as files_repo
from easy_agent.db.repositories import users as users_repo

if TYPE_CHECKING:
    from easy_agent.models.db import UserModel
    from easy_agent.db.database import Database


class FileRepository:
    """文件登记、生成文件查询与会话归属校验的薄委托层。"""

    def __init__(self, db: "Database"):
        self._db = db

    # ── 会话上传文件 ──────────────────────────────────────────────────

    def add_session_file(
        self,
        session_id: str,
        filename: str,
        file_path: str,
        file_type: str,
        size: int,
        username: str = "",
    ) -> None:
        files_repo.add_session_file(
            self._db,
            session_id,
            filename,
            file_path,
            file_type,
            size,
            username,
        )

    def get_session_files(self, session_id: str) -> list[dict]:
        return files_repo.get_session_files(self._db, session_id)

    def get_file_record(self, file_id: int, username: str | None = None) -> dict | None:
        return files_repo.get_file_record(self._db, file_id, username)

    def delete_file(self, file_id: int, username: str | None = None) -> bool:
        return files_repo.delete_file(self._db, file_id, username)

    # ── 生成的资产文件 ────────────────────────────────────────────────

    def get_generated_files(self, session_id: str) -> list[dict]:
        return files_repo.get_generated_files(self._db, session_id)

    def get_generated_filenames(self, username: str) -> set[str]:
        return files_repo.get_generated_filenames(self._db, username)

    # ── 归属 / 兜底用户 ───────────────────────────────────────────────

    def get_owned_session_meta(self, session_id: str, username: str) -> dict:
        """返回会话归属元信息；不存在或跨用户统一 404。"""
        from easy_agent.utils.session import get_owned_session_meta

        return get_owned_session_meta(self._db, session_id, username)

    def get_or_create_default_user(self) -> "UserModel":
        return users_repo.get_or_create_default_user(self._db)


__all__ = ["FileRepository"]
