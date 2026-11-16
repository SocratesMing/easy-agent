"""RAGFlow chunk 数据的文本提取与顺序辅助（service / 原文工具共用）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def chunk_id(chunk: Mapping[str, Any]) -> str:
    """返回 chunk 的稳定 ID（兼容 chunk_id / id 两种上游字段）。"""

    return str(chunk.get("chunk_id") or chunk.get("id") or "")


def chunk_text(chunk: Mapping[str, Any]) -> str:
    """返回 chunk 正文（兼容 content_with_weight / content 两种上游字段）。"""

    return str(chunk.get("content_with_weight") or chunk.get("content") or "").strip()


def ordered_chunks(
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """优先按 chunk_order_int/order_int 排序，字段缺失时保留接口顺序。"""

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


async def collect_document_chunks(
    client: Any,
    dataset_id: str,
    document_id: str,
    *,
    max_pages: int = 10,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """分页拉取一篇文档的全部 chunk（RAGFlow 单页上限 100）。

    结束条件：累计条数达到上游 total、某页不满、或已达 max_pages。
    上游异常由调用方处理；本函数只负责分页与过滤非 dict 条目。
    """

    chunks: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        payload = await client.list_chunks(
            dataset_id, document_id, page=page, page_size=page_size
        )
        batch = payload.get("chunks") if isinstance(payload, dict) else None
        if not isinstance(batch, list) or not batch:
            break
        chunks.extend(item for item in batch if isinstance(item, dict))
        total = payload.get("total")
        if isinstance(total, int) and len(chunks) >= total:
            break
        if len(batch) < page_size:
            break
    return chunks


__all__ = ["chunk_id", "chunk_text", "collect_document_chunks", "ordered_chunks"]
