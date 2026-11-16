"""知识库 CRUD 数据访问层：按职责组合各仓储 Mixin。"""

from __future__ import annotations

from easy_agent.knowledges.repository.base import RepositoryBaseMixin
from easy_agent.knowledges.repository.bases import BaseRepositoryMixin
from easy_agent.knowledges.repository.documents import DocumentRepositoryMixin
from easy_agent.knowledges.repository.folders import FolderRepositoryMixin
from easy_agent.knowledges.repository.ingestion import IngestionRepositoryMixin
from easy_agent.knowledges.repository.runtime import RuntimeRepositoryMixin


class KnowledgeRepository(
    BaseRepositoryMixin,
    DocumentRepositoryMixin,
    FolderRepositoryMixin,
    IngestionRepositoryMixin,
    RepositoryBaseMixin,
    RuntimeRepositoryMixin,
):
    """知识库模块唯一数据访问入口（事务由 Database.get_connection 管理）。"""


__all__ = ["KnowledgeRepository"]
