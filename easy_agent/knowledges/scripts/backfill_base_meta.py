#!/usr/bin/env python3
"""为存量知识库文档回填 ``base_id`` 元数据（RAGFlow meta_fields）。

背景：检索原先依赖 ``document_ids`` 圈定知识库范围，单个知识库超过 500 篇
文档时会被静默截断。新上传文档已经写入 ``base_id`` 元数据，检索可改走
元数据过滤；本脚本负责给历史文档补齐同一元数据，并在整库成功后把
``knowledge_bases.meta_backfilled`` 置 1，让检索切换到 meta 范围。

用法：
    uv run python easy_agent/knowledges/scripts/backfill_base_meta.py            # 预览（dry-run）
    uv run python easy_agent/knowledges/scripts/backfill_base_meta.py --apply    # 执行回填
    uv run python easy_agent/knowledges/scripts/backfill_base_meta.py --apply --base-id <id>

幂等可重跑：已回填的库（meta_backfilled=1）自动跳过；单篇失败只影响该库，
修复后重跑即可。回填只更新 RAGFlow 的元数据副本，不改本地库与原文。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from easy_agent.bootstrap.initialization import initialize_runtime  # noqa: E402
from easy_agent.db.database import Database, init_database  # noqa: E402
from easy_agent.knowledges.core.config import KnowledgeConfig  # noqa: E402
from easy_agent.knowledges.core.repository import KnowledgeRepository  # noqa: E402
from easy_agent.knowledges.ragflow import RagflowClient, RagflowError  # noqa: E402
from easy_agent.knowledges.service import KnowledgeService  # noqa: E402

# 单库文档列表分页大小与并发度（只更新元数据，受上游限流影响，不宜过高）
_PAGE_SIZE = 200
_CONCURRENCY = 5


def _load_pending_bases(db: Database, base_ids: list[str]) -> list[dict[str, Any]]:
    """读取待回填的知识库（未删除且 meta_backfilled=0）。"""

    conditions = ["status NOT IN ('deleted', 'deleting')", "COALESCE(meta_backfilled, 0)=0"]
    params: list[Any] = []
    if base_ids:
        placeholders = ",".join("?" for _ in base_ids)
        conditions.append(f"id IN ({placeholders})")
        params.extend(base_ids)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT * FROM knowledge_bases WHERE "
            + " AND ".join(conditions)
            + " ORDER BY created_at",
            tuple(params),
        )
        rows = cursor.fetchall()
    return [dict(row) for row in rows]


def _load_base_documents(
    repository: KnowledgeRepository, base_id: str
) -> list[dict[str, Any]]:
    """分页读取知识库全部未删除文档。"""

    documents: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = repository.list_documents(base_id, limit=_PAGE_SIZE, offset=offset)
        documents.extend(page)
        if len(page) < _PAGE_SIZE:
            return documents
        offset += _PAGE_SIZE


async def _backfill_document(
    *,
    ragflow: RagflowClient,
    dataset_id: str,
    base: dict[str, Any],
    document: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> tuple[str, str]:
    """给单篇文档回填元数据，返回 (状态, 说明)。"""

    remote_id = str(document.get("remote_document_id") or "").strip()
    if not remote_id:
        return "skipped", "无远端副本"
    meta_fields = KnowledgeService._document_meta_fields(base, document) or {}
    async with semaphore:
        try:
            await ragflow.update_document(
                dataset_id, remote_id, meta_fields=meta_fields
            )
        except RagflowError as exc:
            return "failed", str(exc)
    return "updated", remote_id


async def run(args: argparse.Namespace) -> int:
    app_config = initialize_runtime().config
    if app_config is None:
        print("ERROR: 配置未加载，无法回填", file=sys.stderr)
        return 2
    config = KnowledgeConfig.load()
    if not config.enabled:
        print("知识库模块未启用（knowledge.enabled=false），退出")
        return 2

    # 知识库建表函数与默认管理员策略在 import 时注册；personnel 必须显式导入
    import easy_agent.knowledges.personnel  # noqa: F401 - 延迟导入：schema 注册

    db = await asyncio.to_thread(init_database, app_config.database.model_dump())
    repository = KnowledgeRepository(db)
    ragflow = RagflowClient(config.base_url, config.api_key.get_secret_value())
    service = KnowledgeService(repository, ragflow, config)

    bases = await asyncio.to_thread(_load_pending_bases, db, args.base_id)
    if not bases:
        print("没有需要回填的知识库（均已标记 meta_backfilled=1）")
        await ragflow.close()
        return 0

    plans: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for base in bases:
        documents = await asyncio.to_thread(
            _load_base_documents, repository, str(base["id"])
        )
        plans.append((base, documents))
    downloadable = sum(
        1
        for _, documents in plans
        for document in documents
        if str(document.get("remote_document_id") or "").strip()
    )
    print(f"待回填知识库 {len(plans)} 个，远端文档 {downloadable} 篇")

    if not args.apply:
        for base, documents in plans:
            print(
                f"  - [{base.get('space_type')}] {base['name']}"
                f"（id={base['id']}, 文档 {len(documents)} 篇）"
            )
        print("\ndry-run 预览完成，未做任何修改。加 --apply 执行回填。")
        await ragflow.close()
        return 0

    semaphore = asyncio.Semaphore(_CONCURRENCY)
    updated = skipped = failed = 0
    failed_bases: list[str] = []
    try:
        for index, (base, documents) in enumerate(plans, 1):
            base_id = str(base["id"])
            print(f"\n[{index}/{len(plans)}] 知识库: {base['name']}（id={base_id}）")
            if str(base.get("remote_dataset_id") or "").strip():
                dataset_id = str(base["remote_dataset_id"])
            else:
                # 共享数据集：所有个人/公共库共用 EA_SHARED
                dataset_id = await service._ensure_shared_dataset()
            results = await asyncio.gather(
                *(
                    _backfill_document(
                        ragflow=ragflow,
                        dataset_id=dataset_id,
                        base=base,
                        document=document,
                        semaphore=semaphore,
                    )
                    for document in documents
                )
            )
            base_failed = 0
            for document, (status, detail) in zip(documents, results):
                if status == "updated":
                    updated += 1
                elif status == "skipped":
                    skipped += 1
                    print(f"  - {document['name']}（跳过：{detail}）")
                else:
                    failed += 1
                    base_failed += 1
                    print(f"  ✗ {document['name']}（失败：{detail}）")
            if base_failed:
                failed_bases.append(str(base["name"]))
                print("  ⚠ 存在失败文档，本库保持 document_ids 检索，修复后重跑即可")
            else:
                await asyncio.to_thread(
                    repository.update_base, base_id, meta_backfilled=1
                )
                print("  ✓ 回填完成，检索已切换为 base_id 元数据过滤")
    finally:
        await ragflow.close()

    print(
        f"\n回填结束：成功 {updated}，跳过 {skipped}，失败 {failed}"
        f"（涉及知识库 {len(failed_bases)} 个）"
    )
    return 1 if failed_bases else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="为存量知识库文档回填 base_id 元数据（RAGFlow meta_fields）"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行回填（默认 dry-run 仅预览）",
    )
    parser.add_argument(
        "--base-id",
        action="append",
        default=[],
        help="只回填指定知识库 ID，可重复传入",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
