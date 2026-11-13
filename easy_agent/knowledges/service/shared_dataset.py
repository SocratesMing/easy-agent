"""共享数据集：解析/创建 EA_SHARED、缓存 embedding 模型并做数据集路由。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from easy_agent.knowledges.ragflow import RagflowError
from easy_agent.knowledges.service.contracts import KnowledgeServiceError
from easy_agent.knowledges.service.uploads import (
    CHUNK_METHOD,
    DATASET_PERMISSION,
    PARSER_CONFIG,
)

logger = logging.getLogger(__name__)


# 共享数据集：所有走共享模式的个人/公共知识库共用同一个 Ragflow 数据集。
# 个人库文档通过 meta_fields={"owner_user_id": ...} 打个人标签，检索时
# 携带同名过滤条件实现个人隔离（与 document_ids 范围约束形成双重隔离）。
SHARED_DATASET_NAME = "EA_SHARED"
# 进程级缓存（KnowledgeService 每请求实例化，共享数据集 ID 必须跨实例复用）
_shared_dataset_id: str | None = None
_shared_dataset_lock: asyncio.Lock | None = None
# 进程级缓存：共享数据集绑定的实际 embedding 模型。由 RAGFlow 数据集直接
# 解析得到，不再从配置显式指定。空字符串极端情况表示上游数据集尚未绑定模型。
_effective_embedding_model: str | None = None


def _get_shared_dataset_lock() -> asyncio.Lock:
    """惰性创建模块级锁（避免 import 时绑定事件循环的兼容问题）。"""
    global _shared_dataset_lock
    if _shared_dataset_lock is None:
        _shared_dataset_lock = asyncio.Lock()
    return _shared_dataset_lock


def reset_shared_dataset_cache() -> None:
    """清空共享数据集 ID / embedding 模型进程级缓存（测试用）。"""
    global _shared_dataset_id, _effective_embedding_model
    _shared_dataset_id = None
    _effective_embedding_model = None


class SharedDatasetMixin:
    """共享数据集方法；由 KnowledgeService 组合，复用其上游客户端与配置。"""

    async def _ensure_shared_dataset(self) -> str:
        """返回共享数据集 ID，并顺带解析出实际生效的 embedding 模型。

        规则（模型一律从上游 RAGFlow 数据集解析，不再通过配置显式指定）：

        1. 配了 ``RAGFLOW_SHARED_DATASET_ID`` → 校验存在，直接用其
           ``embedding_model``；
        2. 未配置 → 查找/创建 ``EA_SHARED`` 共享数据集，从拿到的
           数据集读取 ``embedding_model``；创建时不指定模型，让 RAGFlow
           用租户默认（数据集创建好后自然会带上模型）。

        模型缓存于模块级 ``_effective_embedding_model``，同一进程内
        后续调用不再重复查数据集。
        """
        global _shared_dataset_id, _effective_embedding_model

        # 1) 显式指定数据集 ID：直接用
        configured = self.config.shared_dataset_id.strip()
        if configured:
            if _shared_dataset_id == configured and _effective_embedding_model is not None:
                return configured
            dataset = await self._get_dataset(configured)
            if dataset is None:
                raise KnowledgeServiceError(
                    "KNOWLEDGE_SHARED_DATASET_NOT_FOUND",
                    f"共享数据集 {configured} 不存在或当前 API Key 无权访问；"
                    "请确认 RAGFLOW_BASE_URL / RAGFLOW_API_KEY / "
                    "RAGFLOW_SHARED_DATASET_ID 指向同一套 RAGFlow",
                    status_code=409,
                )
            _shared_dataset_id = configured
            _effective_embedding_model = str(
                dataset.get("embedding_model") or ""
            ).strip()
            logger.info(
                "共享数据集就绪（显式指定） | id=%s model=%s",
                configured,
                _effective_embedding_model,
            )
            return configured

        # 2) 未指定：按名称查找 / 创建 EA_SHARED（进程级缓存）
        if _shared_dataset_id is not None and _effective_embedding_model is not None:
            return _shared_dataset_id
        async with _get_shared_dataset_lock():
            if _shared_dataset_id is not None and _effective_embedding_model is not None:
                return _shared_dataset_id

            found = await self._find_shared_dataset()
            if found is None:
                _shared_dataset_id = await self._create_shared_dataset()
            else:
                _shared_dataset_id = found

            # 从最终确定的数据集读模型
            dataset = await self._get_dataset(_shared_dataset_id)
            _effective_embedding_model = str(
                (dataset or {}).get("embedding_model") or ""
            ).strip()
            logger.info(
                "共享数据集就绪 | id=%s model=%s",
                _shared_dataset_id,
                _effective_embedding_model,
            )
            return _shared_dataset_id

    async def _create_shared_dataset(self) -> str:
        """创建共享数据集：不指定 embedding_model，让 RAGFlow 用租户默认。"""
        try:
            remote = await self.ragflow.create_dataset(
                name=SHARED_DATASET_NAME,
                description="EasyAgent 共享知识库（个人/公共库共用）",
                # 不指定模型，RAGFlow 会用租户默认
                permission=DATASET_PERMISSION,
                chunk_method=CHUNK_METHOD,
                parser_config=PARSER_CONFIG,
            )
            return str(remote["id"])
        except RagflowError:
            # 并发竞态：其他进程/实例刚创建了同名数据集 → 重查复用
            found = await self._find_shared_dataset(name=SHARED_DATASET_NAME)
            if found is None:
                raise
            return found

    async def _get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        """按 ID 查询上游数据集详情；不存在返回 None。"""

        try:
            result = await self.ragflow.list_datasets(
                dataset_id=dataset_id, page=1, page_size=1
            )
        except RagflowError as exc:
            raise self._upstream_error(exc) from exc
        datasets = result.get("datasets") if isinstance(result, dict) else None
        for item in datasets or []:
            if isinstance(item, dict) and str(item.get("id")) == dataset_id:
                return item
        return None

    async def _find_shared_dataset(
        self, *, name: str = SHARED_DATASET_NAME
    ) -> str | None:
        """分页拉取数据集列表，客户端按名称精确匹配查找共享数据集。

        注意：不能用 ``name`` 查询参数过滤——定制版 Ragflow 对不存在的
        名称也返回 code=102 权限错误（实测），只能全量列表后本地匹配。
        未找到返回 None；Ragflow 异常同样返回 None（由调用方兜底）。
        """
        try:
            page = 1
            while page <= 20:  # 安全上限：2000 个数据集
                result = await self.ragflow.list_datasets(page=page, page_size=100)
                datasets = result.get("datasets") if isinstance(result, dict) else None
                items = datasets or []
                for item in items:
                    if (
                        isinstance(item, dict)
                        and str(item.get("name")) == name
                    ):
                        return str(item["id"])
                # 客户端列表分支返回的 total 为本页长度：不满一页即到末尾
                if len(items) < 100:
                    return None
                page += 1
        except RagflowError:
            return None
        return None

    async def _dataset_id_for(self, base: Mapping[str, Any]) -> str:
        """数据集路由：存量库沿用自有数据集，共享库（remote_dataset_id 为空）
        统一落到共享数据集。"""
        own = str(base.get("remote_dataset_id") or "").strip()
        if own:
            return own
        return await self._ensure_shared_dataset()
