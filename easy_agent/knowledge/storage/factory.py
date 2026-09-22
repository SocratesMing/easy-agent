from __future__ import annotations

from ..config import KnowledgeConfig
from .base import OriginalDocumentStore
from .filesystem import FileSystemOriginalStore


def create_original_store(config: KnowledgeConfig) -> OriginalDocumentStore | None:
    if not config.original_storage.enabled:
        return None
    return FileSystemOriginalStore(config.original_storage)
