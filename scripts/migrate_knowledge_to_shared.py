#!/usr/bin/env python3
"""把存量按库绑定的 Ragflow 数据集迁入共享数据集（EA_SHARED）。

背景：知识库改造后，创建知识库不再在 Ragflow 单独建数据集（本地相当于
建文件夹），个人库与公共库统一共用一个共享 Ragflow 数据集；个人库文档
打 owner_user_id 标签隔离，检索时按标签过滤。

本脚本处理存量数据：
1. 遍历仍绑定自有数据集（remote_dataset_id 非空）的知识库；
2. 逐文档迁入共享数据集（本地原文优先，缺失时从旧数据集下载），
   个人库文档补打 owner_user_id 标签；
3. 某库全部文档迁移成功后清空其 remote_dataset_id（此后走共享模式）；
4. 汇总旧数据集，交互确认后批量删除（有失败文档的库保留旧数据集）。

用法：
    uv run python scripts/migrate_knowledge_to_shared.py            # 预览（dry-run）
    uv run python scripts/migrate_knowledge_to_shared.py --apply    # 执行迁移

幂等可重跑：文档迁移成功后本地 remote_document_id 已指向共享数据集，
重跑时自动跳过；有失败文档的库修复后重跑即可。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from easy_agent.db.database import Database, init_database  # noqa: E402
from easy_agent.knowledges.config import KnowledgeConfig  # noqa: E402
from easy_agent.knowledges.models import DocumentStatus  # noqa: E402
from easy_agent.knowledges.ragflow import RagflowClient, RagflowError  # noqa: E402
from easy_agent.knowledges.repository import KnowledgeRepository  # noqa: E402
from easy_agent.knowledges.service import (  # noqa: E402
    CHUNK_METHOD,
    PARSER_CONFIG,
    KnowledgeService,
    LocalOriginalStore,
)
from easy_agent.utils.env_loader import load_project_env  # noqa: E402

# 本地文档列表分页大小（repository.list_documents 无上限约束，取宽裕值）
_PAGE_SIZE = 200


def _load_legacy_bases(db: Database) -> list[dict[str, Any]]:
    """读取仍绑定自有数据集的未删除知识库（repository 未提供全量列表，走直查）。"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT * FROM knowledge_bases "
            "WHERE remote_dataset_id IS NOT NULL AND remote_dataset_id!='' "
            "AND status NOT IN ('deleted', 'deleting') "
            "ORDER BY created_at",
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


def _docs_of_shared(data: Any) -> list[Any]:
    if isinstance(data, dict):
        return data.get("docs") or []
    return data if isinstance(data, list) else []


async def _in_shared_dataset(
    ragflow: RagflowClient, shared_id: str, remote_id: str
) -> bool:
    """判断远端文档 ID 是否已在共享数据集中（重跑时跳过已迁移文档）。"""
    try:
        data = await ragflow.list_documents(shared_id, document_id=remote_id)
    except RagflowError:
        return False
    return bool(_docs_of_shared(data))


async def _parse_actually_running(
    ragflow: RagflowClient, shared_id: str, remote_id: str
) -> bool:
    """核查共享数据集内文档解析是否实际已启动（与 service 同名逻辑一致）。"""
    try:
        data = await ragflow.list_documents(shared_id, document_id=remote_id)
    except RagflowError:
        return False
    docs = _docs_of_shared(data)
    if docs and isinstance(docs[0], dict):
        run = str(docs[0].get("run", "")).upper()
        return run in {"RUNNING", "DONE"}
    return False


async def _migrate_document(
    *,
    ragflow: RagflowClient,
    repository: KnowledgeRepository,
    original_store: LocalOriginalStore,
    shared_id: str,
    base: dict[str, Any],
    document: dict[str, Any],
) -> tuple[str, str]:
    """迁移单篇文档到共享数据集。

    Returns:
        (状态, 说明)：状态为 migrated / skipped / failed。
    """
    document_id = str(document["id"])
    old_dataset_id = str(base["remote_dataset_id"])
    remote_id = str(document.get("remote_document_id") or "").strip()

    original = repository.get_original_object(document_id)
    has_local_original = (
        original is not None and str(original.get("status")) == "available"
    )
    if not remote_id and not has_local_original:
        return "skipped", "无远端副本且本地原文缺失，无可迁内容"
    if remote_id and await _in_shared_dataset(ragflow, shared_id, remote_id):
        return "skipped", "已在共享数据集中（此前已迁移）"

    # 上传源：本地原文优先，缺失时从旧数据集下载
    source: io.BytesIO | Any = None
    if has_local_original:
        try:
            source = original_store.open(str(original["storage_key"])).open_binary()
        except Exception:
            source = None
    if source is None:
        if not remote_id:
            return "failed", "本地原文不可用且无远端副本"
        try:
            legacy = await ragflow.download_document(
                dataset_id=old_dataset_id, document_id=remote_id
            )
        except RagflowError as exc:
            return "failed", f"从旧数据集下载失败: {exc}"
        source = io.BytesIO(legacy.content)

    try:
        try:
            remote_documents = await ragflow.upload_documents(
                shared_id,
                [
                    (
                        str(document["name"]),
                        source,
                        str(document.get("content_type") or "application/octet-stream"),
                    )
                ],
            )
            if not remote_documents or not remote_documents[0].get("id"):
                raise KeyError("id")
            new_remote_id = str(remote_documents[0]["id"])
            # 个人库文档补打 owner_user_id 标签（公共库为 None，不打标签）
            await ragflow.update_document(
                shared_id,
                new_remote_id,
                chunk_method=CHUNK_METHOD,
                parser_config=PARSER_CONFIG,
                meta_fields=KnowledgeService._personal_meta_fields(base),
            )
            try:
                await ragflow.parse_documents(shared_id, [new_remote_id])
            except RagflowError:
                if not await _parse_actually_running(ragflow, shared_id, new_remote_id):
                    raise
        except (RagflowError, KeyError) as exc:
            return "failed", f"上传或解析启动失败: {exc}"
        # 解析已在远端排队，本地投影交由后台轮询 worker 跟踪到 READY/FAILED
        repository.update_document(
            document_id,
            remote_document_id=new_remote_id,
            status=DocumentStatus.PROCESSING.value,
            progress=0.0,
            error_code=None,
            error_message=None,
        )
        return "migrated", new_remote_id
    finally:
        close = getattr(source, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass


async def run(args: argparse.Namespace) -> int:
    # 与后端启动一致：standalone 运行时先注入 .env.{AGENT_ENV}，
    # 否则 KnowledgeConfig 拿不到 RAGFLOW_BASE_URL / RAGFLOW_API_KEY
    load_project_env()
    config = KnowledgeConfig.load()
    if not config.enabled:
        print("知识库模块未启用（knowledge.enabled=false），退出")
        return 2

    db = init_database()
    repository = KnowledgeRepository(db)
    ragflow = RagflowClient(config.base_url, config.api_key.get_secret_value())
    store = LocalOriginalStore()
    service = KnowledgeService(repository, ragflow, config, original_store=store)

    bases = _load_legacy_bases(db)
    if not bases:
        print("没有需要迁移的存量知识库（均未绑定自有数据集）")
        return 0

    plans = [
        (base, _load_base_documents(repository, str(base["id"]))) for base in bases
    ]
    total_documents = sum(len(documents) for _, documents in plans)
    print(f"待迁移知识库 {len(plans)} 个，文档 {total_documents} 篇")

    if not args.apply:
        # dry-run 不触碰 Ragflow（连共享数据集也不创建），仅预览本地存量
        for base, documents in plans:
            print(
                f"  - [{base.get('space_type')}] {base['name']}"
                f"（id={base['id']}, dataset={base['remote_dataset_id']},"
                f" 文档 {len(documents)} 篇）"
            )
        print("\ndry-run 预览完成，未做任何修改。加 --apply 执行迁移。")
        return 0

    shared_id = await service._ensure_shared_dataset()
    print(f"共享数据集就绪: {shared_id}")

    deletable_datasets: list[str] = []
    migrated = skipped = failed = 0
    failed_base_names: list[str] = []
    for index, (base, documents) in enumerate(plans, 1):
        base_id = str(base["id"])
        print(f"\n[{index}/{len(plans)}] 知识库: {base['name']}（id={base_id}）")
        base_has_failure = False
        for doc_index, document in enumerate(documents, 1):
            status, detail = await _migrate_document(
                ragflow=ragflow,
                repository=repository,
                original_store=store,
                shared_id=shared_id,
                base=base,
                document=document,
            )
            label = f"  [{doc_index}/{len(documents)}] {document['name']}"
            if status == "migrated":
                migrated += 1
                print(f"  ✓ {label}")
            elif status == "skipped":
                skipped += 1
                print(f"  - {label}（跳过：{detail}）")
            else:
                failed += 1
                base_has_failure = True
                print(f"  ✗ {label}（失败：{detail}）")
        if base_has_failure:
            failed_base_names.append(str(base["name"]))
            print(
                f"  ⚠ 存在失败文档，保留旧数据集 {base['remote_dataset_id']}，"
                "排查后重跑本脚本即可续迁"
            )
        else:
            repository.update_base(base_id, remote_dataset_id=None)
            deletable_datasets.append(str(base["remote_dataset_id"]))
            print("  ✓ 全部文档迁移完成，已切换为共享数据集模式")

    print(
        f"\n迁移结束：成功 {migrated}，跳过 {skipped}，失败 {failed}"
        f"（涉及知识库 {len(failed_base_names)} 个）"
    )

    if deletable_datasets:
        print(f"\n以下 {len(deletable_datasets)} 个旧数据集已无引用：")
        for dataset_id in deletable_datasets:
            print(f"  - {dataset_id}")
        answer = input("确认删除这些旧数据集? [y/N] ")
        if answer.strip().lower() in ("y", "yes"):
            await ragflow.delete_datasets(deletable_datasets)
            print("旧数据集已删除")
        else:
            print("已跳过删除，可稍后在 Ragflow 侧手动清理")
    else:
        print("没有可删除的旧数据集")

    return 1 if failed_base_names else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把存量知识库的自有 Ragflow 数据集迁入共享数据集"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行迁移（默认 dry-run 仅预览）",
    )
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
