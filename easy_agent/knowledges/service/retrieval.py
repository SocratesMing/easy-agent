"""检索与证据：元数据过滤、检索召回、相邻 chunk 扩写与多库证据合并。"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from easy_agent.knowledges.core.chunks import (
    chunk_id,
    chunk_text,
    collect_document_chunks,
    ordered_chunks,
)
from easy_agent.knowledges.core.models import (
    AllowedAction,
    DocumentMetadataFilter,
    DocumentStatus,
    Evidence,
    RetrieveResponse,
)
from easy_agent.knowledges.ragflow import RagflowError
from easy_agent.knowledges.service.contracts import _not_found

if TYPE_CHECKING:
    from easy_agent.knowledges.core.models import KnowledgePrincipal

logger = logging.getLogger(__name__)

# 相邻 chunk 重叠：命中块前后各拼接相邻块 10% 的文本（仅扩展检索证据，不改存储）
RETRIEVAL_NEIGHBOR_OVERLAP_RATIO = 0.1
# 计数/名单/汇总类问题：命中块额外带出完整后续 chunk；普通比例分析题不触发
AGGREGATION_QUESTION_RE = re.compile(r"(几个|多少|几家|几户|哪些|数量|合计|总计|总共|前十|排名)")
# 聚合证据在问答上下文中的最大字符数（普通证据仍为 2400）
AGGREGATION_SNIPPET_LIMIT = 6000
# 后续 chunk 遇到新章节标题时停止，避免把下一节内容当作名单补进来
_SECTION_HEADING_RE = re.compile(
    r"(?:^|\n)\s*(?:\d{1,2}(?:\.\d{1,2}){1,3}\s+[\u4e00-\u9fa5]|第[一二三四五六七八九十百]+[章节])"
)


class RetrievalMixin:
    """检索与证据方法；由 KnowledgeService 组合，复用其鉴权与仓储能力。"""

    @staticmethod
    def _metadata_filter_kwargs(
        metadata_filter: DocumentMetadataFilter | None,
    ) -> dict[str, Any]:
        """把过滤器转成 repository._document_filters 的关键字参数。"""
        if metadata_filter is None:
            return {}
        return {
            "publish_date_from": metadata_filter.publish_date_from,
            "publish_date_to": metadata_filter.publish_date_to,
            "report_tags": metadata_filter.report_tags or None,
            "report_second_sources": metadata_filter.report_second_sources or None,
            "report_title_contains": metadata_filter.report_title_contains,
        }

    @staticmethod
    def _matches_metadata_filter(
        document: Mapping[str, Any], metadata_filter: DocumentMetadataFilter | None
    ) -> bool:
        """本地兜底校验：与 SQL 过滤保持同一语义（AND / 多值 OR）。"""
        if metadata_filter is None:
            return True
        publish_date = str(document.get("publish_date") or "")
        if metadata_filter.publish_date_from and (
            not publish_date or publish_date < metadata_filter.publish_date_from
        ):
            return False
        if metadata_filter.publish_date_to and (
            not publish_date or publish_date > metadata_filter.publish_date_to
        ):
            return False
        if metadata_filter.report_tags and str(
            document.get("report_tag") or ""
        ) not in set(metadata_filter.report_tags):
            return False
        if metadata_filter.report_second_sources and str(
            document.get("report_second_source") or ""
        ) not in set(metadata_filter.report_second_sources):
            return False
        if metadata_filter.report_title_contains:
            haystack = str(
                document.get("report_title") or document.get("name") or ""
            ).lower()
            if metadata_filter.report_title_contains.lower() not in haystack:
                return False
        return True

    async def _list_ready_documents(
        self, base_id: str, filter_kwargs: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """分页读取知识库全部已解析文档（本地查询无上限）。"""

        page_size = 200
        documents: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = await self._repo(
                self.repository.list_documents,
                base_id,
                status=DocumentStatus.READY.value,
                limit=page_size,
                offset=offset,
                **filter_kwargs,
            )
            documents.extend(page)
            if len(page) < page_size:
                return documents
            offset += page_size

    async def _retrieve_base_evidence(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        question: str,
        document_ids: list[str] | None,
        top_n: int,
        request_id: str,
        metadata_filter: DocumentMetadataFilter | None = None,
    ) -> RetrieveResponse:
        """执行检索；无可检索文档时返回空证据与提示，不编造。

        共享数据集且已回填 ``base_id`` 元数据的库按元数据过滤，否则用
        ``document_ids`` 圈定范围；元数据无可用证据时回退 ``document_ids``。
        ``metadata_filter`` 为硬过滤：区间外文档不会进入证据。
        """
        base, _ = await self._authorized_base(base_id, principal, AllowedAction.ASK)
        filter_kwargs = self._metadata_filter_kwargs(metadata_filter)
        if document_ids is None:
            # 分页读取全部已解析文档（本地库查询无上限），避免超过 500 篇时
            # 检索范围被静默截断；是否需要 document_ids 范围见下方 meta 分支。
            documents = await self._list_ready_documents(base_id, filter_kwargs)
        else:
            documents = await self._repo(
                self.repository.get_documents, base_id, document_ids
            )
            if len(documents) != len(set(document_ids)):
                raise _not_found()
            documents = [
                item for item in documents if item["status"] == DocumentStatus.READY.value
            ]
        documents = [
            item
            for item in documents
            if item.get("remote_document_id")
            and self._matches_metadata_filter(item, metadata_filter)
        ]
        if not documents:
            return RetrieveResponse(
                evidence=[], warnings=["当前范围没有可检索的已解析文档"], request_id=request_id
            )
        dataset_id = await self._dataset_id_for(base)
        aggregation = bool(AGGREGATION_QUESTION_RE.search(question))
        retrieval = self.config.retrieval
        all_remote_ids = [str(item["remote_document_id"]) for item in documents]
        # 共享数据集且已回填 base_id 元数据的库：按元数据过滤（不再受
        # document_ids 长度限制）；存量库仍走 document_ids 范围以保持原行为。
        use_meta_scope = (
            document_ids is None
            and not str(base.get("remote_dataset_id") or "").strip()
            and int(base.get("meta_backfilled") or 0) == 1
        )
        retrieval_meta: dict[str, Any] | None = None
        if use_meta_scope:
            retrieval_meta = {"base_id": str(base["id"])}
        elif not str(base.get("remote_dataset_id") or "").strip():
            # 个人库走共享数据集时携带个人标签过滤（与 document_ids 双重隔离）
            retrieval_meta = self._personal_meta_fields(base)
        # rerank 前先宽召回；无 rerank 时多出的候选只用于按库配额挑选。
        if aggregation:
            page_size = min(max(retrieval.page_size, 20), 100)
        else:
            page_size = min(max(retrieval.page_size, top_n), 100)
        retrieve_kwargs: dict[str, Any] = {
            "question": question,
            "dataset_ids": [dataset_id],
            "meta_fields": retrieval_meta,
            "page_size": page_size,
            "vector_similarity_weight": retrieval.vector_similarity_weight,
            "top_k": retrieval.top_k,
            "rerank_id": retrieval.rerank_model.strip() or None,
        }
        if not use_meta_scope:
            cap = self.config.limits.max_documents_per_query
            retrieve_kwargs["document_ids"] = all_remote_ids[:cap]
            if len(all_remote_ids) > cap:
                logger.warning(
                    "知识库文档数超过检索范围上限，本次仅检索前 %s 篇（建议执行 "
                    "backfill_base_meta 脚本后改走元数据过滤） | base=%s total=%s",
                    cap,
                    base.get("id"),
                    len(all_remote_ids),
                )
        primary_threshold = (
            retrieval.aggregation_similarity_threshold
            if aggregation
            else retrieval.similarity_threshold
        )
        by_remote = {str(item["remote_document_id"]): item for item in documents}

        async def _retrieve(
            kwargs: dict[str, Any], threshold: float
        ) -> dict[str, Any]:
            try:
                return await self.ragflow.retrieve(
                    **kwargs, similarity_threshold=threshold
                )
            except RagflowError as exc:
                raise self._upstream_error(exc) from exc

        def _collect(chunks: Any) -> list[dict[str, Any]]:
            """把上游 chunk 映射为本地证据候选（范围/分数/状态兜底校验）。"""

            collected: list[dict[str, Any]] = []
            for chunk in chunks or []:
                if not isinstance(chunk, dict):
                    continue
                local = by_remote.get(str(chunk.get("document_id")))
                if local is None or local["status"] != DocumentStatus.READY.value:
                    continue
                # 纵深防御：上游若意外返回范围外文档，这里再挡一次
                if not self._matches_metadata_filter(local, metadata_filter):
                    continue
                snippet = str(
                    chunk.get("content_with_weight") or chunk.get("content") or ""
                ).strip()
                if not snippet:
                    continue
                raw_score = chunk.get("similarity", 0)
                score = (
                    max(0.0, float(raw_score))
                    if isinstance(raw_score, (int, float))
                    else 0.0
                )
                if score < retrieval.minimum_evidence_score:
                    continue
                positions = chunk.get("positions")
                collected.append(
                    {
                        "document": local,
                        "chunk_id": str(
                            chunk.get("chunk_id") or chunk.get("id") or ""
                        ),
                        "snippet": snippet,
                        "score": score,
                        "weak": score < primary_threshold,
                        "position": (
                            positions[0]
                            if isinstance(positions, list) and positions
                            else None
                        ),
                    }
                )
            return collected

        result = await _retrieve(retrieve_kwargs, primary_threshold)
        raw_chunks = result.get("chunks") or []
        candidates = _collect(raw_chunks)
        if use_meta_scope and raw_chunks and not candidates:
            # 上游返回了片段但本地全部被过滤：通常是不支持 meta 过滤（返回了其它
            # 库的 chunk）。回退 document_ids 范围，保证不漏召回。
            retrieve_kwargs = {
                **retrieve_kwargs,
                "document_ids": all_remote_ids[
                    : self.config.limits.max_documents_per_query
                ],
            }
            logger.info(
                "元数据过滤返回的片段均不属于本库，回退 document_ids 重试 | base=%s",
                base.get("id"),
            )
            result = await _retrieve(retrieve_kwargs, primary_threshold)
            candidates = _collect(result.get("chunks"))
        if not candidates and not aggregation:
            # 相关问题最高分可能恰好卡在主阈值以下；用降级阈值再召回一次
            fallback_threshold = retrieval.fallback_similarity_threshold
            fallback = await _retrieve(retrieve_kwargs, fallback_threshold)
            fallback_candidates = _collect(fallback.get("chunks"))
            if fallback_candidates:
                logger.info(
                    "检索主阈值无结果，降阈值重试命中 | base=%s primary=%.2f "
                    "fallback=%.2f chunks=%s",
                    base.get("id"),
                    primary_threshold,
                    fallback_threshold,
                    len(fallback_candidates),
                )
                candidates = fallback_candidates
        await self._expand_neighbor_overlap(
            dataset_id=dataset_id,
            candidates=candidates,
            aggregation=aggregation,
        )
        return self._evidence_response(
            base=base,
            candidates=candidates,
            top_n=top_n,
            aggregation=aggregation,
            request_id=request_id,
        )

    @staticmethod
    def _evidence_response(
        *,
        base: Mapping[str, Any],
        candidates: list[dict[str, Any]],
        top_n: int,
        aggregation: bool,
        request_id: str,
    ) -> RetrieveResponse:
        """同一文档只保留最高分 chunk 后，构造带业务元数据的证据响应。"""

        candidates.sort(key=lambda item: item["score"], reverse=True)
        # 同一文档只保留得分最高的 chunk（相邻块已拼进该 chunk 的证据文本），
        # 避免同一篇文档挤占其它文档的证据名额。
        seen_documents: set[str] = set()
        unique_candidates: list[dict[str, Any]] = []
        for item in candidates:
            document_key = str(item["document"]["id"])
            if document_key in seen_documents:
                continue
            seen_documents.add(document_key)
            unique_candidates.append(item)
        return RetrieveResponse(
            evidence=[
                Evidence(
                    document_id=str(item["document"]["id"]),
                    document_name=str(item["document"]["name"]),
                    snippet=item["snippet"],
                    score=item["score"],
                    position=item["position"],
                    metadata={
                        "base_id": str(base["id"]),
                        "base_name": str(base["name"]),
                        "aggregation": aggregation,
                        "weak": bool(item.get("weak")),
                        "source_record_id": item["document"].get("source_record_id"),
                        "report_title": item["document"].get("report_title"),
                        "report_tag": item["document"].get("report_tag"),
                        "report_second_source": item["document"].get(
                            "report_second_source"
                        ),
                        "publish_date": item["document"].get("publish_date"),
                    },
                )
                for item in unique_candidates[:top_n]
            ],
            request_id=request_id,
        )

    async def _expand_neighbor_overlap(
        self,
        *,
        dataset_id: str,
        candidates: list[dict[str, Any]],
        aggregation: bool = False,
    ) -> None:
        """为命中 chunk 补相邻块内容，模拟 chunk 搭接。

        普通问题：命中块前后各拼接相邻块 10% 的文本；
        计数/汇总类问题：额外拼接完整的下一个 chunk，避免跨页表格或名单被截断。
        只扩展本次检索的证据文本，不修改 RAGFlow 存储；按文档拉取一次 chunk
        顺序，拉取失败时保持原证据不变。
        """

        by_document: dict[str, list[dict[str, Any]]] = {}
        for item in candidates:
            remote_doc_id = str(item["document"]["remote_document_id"])
            by_document.setdefault(remote_doc_id, []).append(item)
        for remote_doc_id, items in by_document.items():
            try:
                # RAGFlow list_chunks 单页上限 100，按页拉全文档后再定位相邻块
                chunks = await collect_document_chunks(
                    self.ragflow, dataset_id, remote_doc_id
                )
            except RagflowError as exc:
                logger.warning(
                    "检索相邻 chunk 拉取失败 doc=%s: %s", remote_doc_id, exc
                )
                continue
            if not chunks:
                continue
            ordered = ordered_chunks(chunks)
            index_by_id = {
                chunk_id(chunk): index
                for index, chunk in enumerate(ordered)
                if chunk_id(chunk)
            }
            # 聚合模式只对得分最高的 2 条做整块扩展，其余仍按 10% 邻接，控制上下文
            items = sorted(items, key=lambda item: item["score"], reverse=True)
            expanded = 0
            for item in items:
                index = index_by_id.get(item["chunk_id"])
                if index is None:
                    continue
                if aggregation and expanded < 2:
                    expanded += 1
                    # 跨页表格/名单可能连续占多个 chunk：最多再带 5 个，遇到下一节即停止
                    for offset in range(1, 6):
                        if index + offset >= len(ordered):
                            break
                        following = chunk_text(ordered[index + offset])
                        if not following:
                            continue
                        if _SECTION_HEADING_RE.search(following[:80]):
                            break
                        if len(item["snippet"]) + len(following) > AGGREGATION_SNIPPET_LIMIT:
                            break
                        item["snippet"] = f'{item["snippet"]}\n{following}'
                    continue
                parts: list[str] = []
                if index > 0:
                    previous = chunk_text(ordered[index - 1])
                    if previous:
                        parts.append(previous[-self._overlap_chars(previous) :])
                parts.append(item["snippet"])
                if index + 1 < len(ordered):
                    following = chunk_text(ordered[index + 1])
                    if following:
                        parts.append(following[: self._overlap_chars(following)])
                item["snippet"] = "\n".join(parts)

    @staticmethod
    def _overlap_chars(text: str) -> int:
        return max(1, int(len(text) * RETRIEVAL_NEIGHBOR_OVERLAP_RATIO))

    @staticmethod
    def _merge_session_evidence(
        results: list[RetrieveResponse],
        *,
        top_n: int,
        per_base_quota: int,
    ) -> list[Evidence]:
        """合并多库检索结果：按库配额保底 + 全局分数补齐 + 同文档去重。"""

        evidence: list[Evidence] = []
        for result in results:
            evidence.extend(result.evidence)
        selected: list[Evidence] = []
        seen_documents: set[str] = set()

        def _take(item: Evidence) -> None:
            if item.document_id in seen_documents:
                return
            seen_documents.add(item.document_id)
            selected.append(item)

        if per_base_quota > 0:
            for result in results:
                for item in result.evidence[:per_base_quota]:
                    _take(item)
        for item in sorted(evidence, key=lambda entry: entry.score, reverse=True):
            if len(selected) >= top_n:
                break
            _take(item)
        selected.sort(key=lambda item: item.score, reverse=True)
        return selected[:top_n]

    async def retrieve_session_evidence(
        self,
        *,
        session_id: str,
        principal: KnowledgePrincipal,
        question: str,
        top_n: int,
        request_id: str,
        metadata_filter: DocumentMetadataFilter | None = None,
    ) -> RetrieveResponse:
        """在会话已选择且仍被授权的知识库范围内检索。"""
        scope = await self.get_session_scope(
            session_id=session_id, principal=principal
        )
        if not scope.base_ids:
            return RetrieveResponse(evidence=[], request_id=request_id)
        results = await asyncio.gather(
            *(
                self._retrieve_base_evidence(
                    base_id=base_id,
                    principal=principal,
                    question=question,
                    document_ids=None,
                    top_n=top_n,
                    request_id=request_id,
                    metadata_filter=metadata_filter,
                )
                for base_id in scope.base_ids
            )
        )
        warnings: list[str] = []
        for result in results:
            warnings.extend(result.warnings)
        return RetrieveResponse(
            evidence=self._merge_session_evidence(
                results,
                top_n=top_n,
                per_base_quota=self.config.retrieval.per_base_quota,
            ),
            warnings=list(dict.fromkeys(warnings)),
            request_id=request_id,
        )

    async def get_session_document_catalog(
        self,
        *,
        session_id: str,
        principal: KnowledgePrincipal,
        include_metadata: bool = False,
    ) -> list[dict[str, Any]]:
        """返回所选知识库的安全文档清单（供聊天模型回答范围类问题）。

        include_metadata=True 时仅附带二级来源元数据，供资料概况问题使用。
        """
        scope = await self.get_session_scope(
            session_id=session_id, principal=principal
        )
        catalog: list[dict[str, Any]] = []
        for base_id in scope.base_ids:
            remaining = self.config.limits.max_documents_per_query - len(catalog)
            if remaining <= 0:
                break
            base, _ = await self._authorized_base(
                base_id, principal, AllowedAction.SELECT_FOR_SESSION
            )
            documents = await self._repo(
                self.repository.list_documents,
                base_id,
                limit=remaining,
                offset=0,
            )
            for document in documents:
                progress = self._normalized_progress(document.get("progress"))
                entry = {
                    "base_id": str(base["id"]),
                    "base_name": str(base["name"]),
                    "document_id": str(document["id"]),
                    "document_name": str(document["name"]),
                    "status": str(document["status"]),
                    "progress": float(progress) if progress is not None else None,
                }
                if include_metadata:
                    entry["report_second_source"] = document.get("report_second_source")
                catalog.append(entry)
        return catalog
