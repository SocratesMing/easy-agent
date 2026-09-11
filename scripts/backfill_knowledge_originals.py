#!/usr/bin/env python3
"""Backfill legacy RAGFlow-only documents into configured original storage.

Dry-run is the default. Pass ``--execute`` only after reviewing the candidates.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from easy_agent.config import Config  # noqa: E402
from easy_agent.db import KnowledgeRepository, init_database  # noqa: E402
from easy_agent.knowledge.config import KnowledgeConfig  # noqa: E402
from easy_agent.knowledge.ragflow import RagflowClient, RagflowError  # noqa: E402
from easy_agent.knowledge.storage import OriginalStorageError, create_original_store  # noqa: E402


async def run(*, execute: bool, limit: int, base_id: str | None) -> int:
    agent_config = Config.from_yaml(Config.resolve_config_path())
    knowledge_config = KnowledgeConfig.load()
    store = create_original_store(knowledge_config)
    if not knowledge_config.enabled or store is None:
        print("原文存储未启用，请检查当前 EasyAgent 配置的 knowledge 段。")
        return 2

    database = init_database(agent_config.database.model_dump())
    repository = KnowledgeRepository(database)
    candidates = repository.list_documents_missing_originals(base_id, limit=limit)
    print(f"待回填文档：{len(candidates)} 份；模式：{'execute' if execute else 'dry-run'}")
    if not execute:
        for row in candidates:
            print(f"- {row['id']} | {row['name']} | {row['size_bytes']} bytes")
        return 0

    ragflow = RagflowClient(knowledge_config)
    succeeded = 0
    failed = 0
    try:
        for row in candidates:
            document_id = str(row["id"])
            base = repository.get_base(str(row["base_id"]))
            if base is None or not base.get("remote_dataset_id"):
                print(f"FAILED {document_id}: missing dataset mapping")
                failed += 1
                continue
            try:
                legacy = await ragflow.download_document(
                    dataset_id=str(base["remote_dataset_id"]),
                    document_id=str(row["remote_document_id"]),
                    request_id=f"backfill-{document_id}",
                )
                storage_key = store.build_key(
                    str(row["base_id"]), document_id, str(row["name"])
                )
                repository.create_original_object(
                    document_id=document_id,
                    provider=store.provider,
                    storage_key=storage_key,
                    original_name=str(row["name"]),
                    content_type=str(row.get("content_type") or legacy.content_type),
                    size_bytes=len(legacy.content),
                    created_by=str(row["created_by"]),
                )
                stored = store.put_atomic(
                    storage_key, io.BytesIO(legacy.content), len(legacy.content)
                )
                repository.update_original_object(
                    document_id,
                    status="available",
                    size_bytes=stored.size_bytes,
                    sha256=stored.sha256,
                    stored_at=datetime.now(UTC).isoformat(),
                    error_code=None,
                    error_message=None,
                )
                print(f"OK {document_id}")
                succeeded += 1
            except (RagflowError, OriginalStorageError, KeyError) as exc:
                repository.update_original_object(
                    document_id,
                    status="failed",
                    error_code=getattr(exc, "code", "ORIGINAL_BACKFILL_FAILED"),
                    error_message="历史原文回填失败",
                )
                print(f"FAILED {document_id}: {getattr(exc, 'code', type(exc).__name__)}")
                failed += 1
    finally:
        await ragflow.close()
    print(f"回填完成：成功 {succeeded}，失败 {failed}。")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史知识库文档原文")
    parser.add_argument("--execute", action="store_true", help="执行回填（默认仅预览）")
    parser.add_argument("--limit", type=int, default=100, help="本次最多处理数")
    parser.add_argument("--base-id", default=None, help="仅处理指定知识库")
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    return asyncio.run(run(execute=args.execute, limit=args.limit, base_id=args.base_id))


if __name__ == "__main__":
    raise SystemExit(main())
