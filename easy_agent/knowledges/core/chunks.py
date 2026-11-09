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


__all__ = ["chunk_id", "chunk_text", "ordered_chunks"]
