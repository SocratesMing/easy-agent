"""结构化用户记忆存储（JSONL sidecar）。

设计目标（详见 ``docs/archive/design/2026-10-18-structured-memory.md``）：

- **Markdown 仍是权威读取文件**：``memory.md`` / ``AGENTS.md`` 的读取方（agent
  提示词、旧代码、用户直接编辑）完全不变；
- **结构化 sidecar**：同目录 ``memory.jsonl`` / ``AGENTS.jsonl`` 保存条目化记忆，
  带稳定 key、来源、更新时间与版本号，用于去重、supersede 与审计；
- **原子写**：JSONL 先写临时文件再 ``os.replace``，读方不会看到半截文件；
- **校验与脱敏**：长度上限、密钥 / PII 简单扫描、重复 key 合并 / 新版本覆盖；
- **纯函数 + 小文件操作**：不依赖数据库、网络与 agent 运行时，便于单测。

JSONL 布局（首行 meta，其后每条记忆一行）::

    {"type": "meta", "schema": 1, "revision": 3, "title": "会话记忆", ...}
    {"type": "entry", "id": "mem_...", "key": "...", "value": "...", ...}

读取对损坏行、缺字段、缺失文件全部容错（best effort）；外部直接编辑 Markdown
后，内容 hash 不一致会在下一次同步时以 Markdown 为准回灌 sidecar。

实现拆分（批次 2A）：文件锁 / 原子写在 :mod:`easy_agent.services.memory_locks`，
条目模型与 Markdown 编解码在 :mod:`easy_agent.services.memory_entries`；本模块保留
:class:`MemoryStore`、写入/同步入口，并 re-export 拆分前的全部公开名字（对象身份
不变），既有 ``from easy_agent.services.memory_store import ...`` 导入路径仍可用。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

# 以下导入同时是兼容 re-export：对象身份与子模块一致（纯移动，不改行为）。
from easy_agent.services.memory_entries import (  # noqa: F401 - 兼容重导出（测试锁定）
    DEFAULT_CATEGORY,
    DEFAULT_SOURCE,
    ENV_FLAG,
    MAX_KEY_CHARS,
    MAX_VALUE_CHARS,
    MIN_TRIMMED_VALUE_CHARS,
    REDACTION_MARKER,
    SOURCE_DIRECT_WRITE,
    SOURCE_IMPORT,
    SOURCE_LLM_LONG_TERM,
    SOURCE_LLM_SESSION,
    Clock,
    MemoryEntry,
    MemoryValidationError,
    _safe_int,
    _utc_now,
    content_digest,
    detect_sensitive,
    entry_id_for_key,
    extract_title,
    fit_entries_to_budget,
    full_digest,
    make_entry,
    merge_entries,
    normalize_key,
    parse_markdown,
    redact_sensitive,
    render_markdown,
    structured_memory_enabled,
    truncate_text,
    validate_entry,
)
from easy_agent.services.memory_locks import (
    LOCK_SUFFIX,
    STORE_SUFFIX,
    _atomic_write_text,
    atomic_write_text,
    cross_process_lock_supported,
    memory_lock,
    memory_lock_path,
    store_path_for,
)

# 沿用原模块 logger 名：既有日志过滤与测试断言依赖该名字（纯移动，不改行为）。
logger = logging.getLogger("easy_agent.memory.store")

SCHEMA_VERSION = 1
MAX_ENTRIES = 50


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.warning("读取记忆文件失败 | 文件: %s | %s", path, exc)
        return ""


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------


class MemoryStore:
    """单个 Markdown 记忆文件对应的结构化 sidecar。

    典型用法::

        store = MemoryStore(store_path_for(memory_file), title="会话记忆")
        store.load()
        store.apply_entries(parse_markdown(markdown))
        store.markdown_sha256 = full_digest(markdown)
        store.save()

    本类不做文件锁；并发写依赖 ``os.replace`` 的原子性，最后写入者胜出。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        title: str = "",
        max_entries: int = MAX_ENTRIES,
        max_value_chars: int = MAX_VALUE_CHARS,
        clock: Clock | None = None,
    ) -> None:
        self.path = Path(path)
        self.title = title
        self.max_entries = max(1, int(max_entries))
        self.max_value_chars = max(1, int(max_value_chars))
        self._clock = clock or _utc_now
        self._entries: dict[str, MemoryEntry] = {}
        self.revision = 0
        self.updated_at = ""
        self.markdown_sha256 = ""

    # -- 读取 ---------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._entries

    @property
    def entries(self) -> list[MemoryEntry]:
        """按插入（文档）顺序返回条目。"""
        return list(self._entries.values())

    def get(self, key: str) -> MemoryEntry | None:
        normalized = normalize_key(key)
        return self._entries.get(normalized)

    def load(self) -> MemoryStore:
        """读取 JSONL；共享锁下读取，避免读到 Markdown/JSONL 双写的中间态。"""
        with memory_lock(self.path, exclusive=False):
            return self._load_locked()

    def _load_locked(self) -> MemoryStore:
        """``load`` 的实际实现（调用方需已持有锁）。"""
        self._entries.clear()
        self.revision = 0
        self.updated_at = ""
        self.markdown_sha256 = ""

        text = _read_text(self.path)
        if not text:
            return self

        skipped = 0
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except (json.JSONDecodeError, TypeError, ValueError):
                skipped += 1
                continue
            if not isinstance(record, dict):
                skipped += 1
                continue

            kind = record.get("type", "entry")
            if kind == "meta":
                self.revision = _safe_int(record.get("revision"), self.revision)
                self.title = str(record.get("title") or self.title)
                self.updated_at = str(record.get("updated_at") or self.updated_at)
                self.markdown_sha256 = str(record.get("markdown_sha256") or "")
                continue
            if kind != "entry":
                skipped += 1
                continue

            try:
                entry = validate_entry(
                    MemoryEntry.from_dict(record),
                    max_value_chars=self.max_value_chars,
                    now=self._clock(),
                )
            except (MemoryValidationError, TypeError, ValueError):
                skipped += 1
                continue

            existing = self._entries.get(entry.key)
            if existing is None:
                self._entries[entry.key] = entry
            else:
                merged = merge_entries(existing, entry)
                self._entries[entry.key] = replace(
                    merged, version=max(existing.version, entry.version, merged.version)
                )

        if skipped:
            logger.warning(
                "结构化记忆存在损坏行，已跳过 | 文件: %s | 行数: %d", self.path, skipped
            )
        return self

    # -- 写入 ---------------------------------------------------------

    def _meta_record(self) -> dict[str, Any]:
        return {
            "type": "meta",
            "schema": SCHEMA_VERSION,
            "revision": self.revision,
            "title": self.title,
            "updated_at": self.updated_at,
            "count": len(self._entries),
            "markdown_sha256": self.markdown_sha256,
        }

    def save(self) -> int:
        """原子写 JSONL，revision + 1，返回新 revision。

        写入在跨进程排他锁内完成；``write_structured_memory`` 等复合操作
        在外层已持锁时通过可重入锁复用。
        """
        with memory_lock(self.path):
            return self._save_locked()

    def _save_locked(self) -> int:
        self.revision += 1
        self.updated_at = self._clock()
        records = [self._meta_record()]
        records.extend(entry.to_dict() for entry in self._entries.values())
        payload = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
        )
        _atomic_write_text(self.path, payload)
        logger.info(
            "结构化记忆已保存 | 文件: %s | 条目: %d | revision: %d | sha256: %s",
            self.path,
            len(self._entries),
            self.revision,
            self.markdown_sha256[:12] or "-",
        )
        return self.revision

    def upsert(self, entry: MemoryEntry) -> MemoryEntry:
        """写入单条：校验 -> 同 key 合并 / supersede -> 条目数裁剪。"""
        valid = validate_entry(
            entry, max_value_chars=self.max_value_chars, now=self._clock()
        )
        existing = self._entries.get(valid.key)
        stored = merge_entries(existing, valid) if existing is not None else valid
        self._entries[stored.key] = stored
        self._trim_overflow()
        return stored

    def replace_entries(
        self, entries: Iterable[MemoryEntry], *, title: str | None = None
    ) -> list[MemoryEntry]:
        """整体替换（导入 / 外部编辑回灌用），条目版本重置。"""
        if title is not None:
            self.title = title
        self._entries = {}
        for entry in entries:
            try:
                valid = validate_entry(
                    entry, max_value_chars=self.max_value_chars, now=self._clock()
                )
            except (MemoryValidationError, TypeError, ValueError):
                continue
            existing = self._entries.get(valid.key)
            self._entries[valid.key] = (
                merge_entries(existing, valid) if existing is not None else valid
            )
        self._trim_overflow()
        return self.entries

    def apply_entries(self, entries: Iterable[MemoryEntry]) -> list[MemoryEntry]:
        """增量应用（LLM 更新用）：保留未变条目的版本号，变化条目 version + 1。"""
        previous = dict(self._entries)
        ordered: dict[str, MemoryEntry] = {}
        for entry in entries:
            try:
                valid = validate_entry(
                    entry, max_value_chars=self.max_value_chars, now=self._clock()
                )
            except (MemoryValidationError, TypeError, ValueError) as exc:
                logger.warning(
                    "跳过无效记忆条目 | 内容 hash: %s | %s",
                    content_digest(str(getattr(entry, "value", ""))),
                    exc,
                )
                continue
            old = ordered.get(valid.key) or previous.get(valid.key)
            ordered[valid.key] = merge_entries(old, valid) if old is not None else valid
        self._entries = ordered
        self._trim_overflow()
        return self.entries

    def _trim_overflow(self) -> None:
        while len(self._entries) > self.max_entries:
            # 丢弃最早写入的条目：新写入优先保留，避免新记忆被静默丢弃
            self._entries.pop(next(iter(self._entries)))

    def render_markdown(self, *, title: str | None = None) -> str:
        return render_markdown(
            self.entries, title=self.title if title is None else title
        )


# ---------------------------------------------------------------------------
# Markdown <-> sidecar 同步入口
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuredWriteResult:
    """结构化写入结果：调用方据此写 Markdown 并记录日志。"""

    markdown: str
    entries: int
    revision: int
    title: str


def write_structured_memory(
    memory_file: str | Path,
    markdown: str,
    *,
    title: str,
    source: str,
    max_chars: int,
    previous_markdown: str | None = None,
    max_entries: int = MAX_ENTRIES,
    max_value_chars: int | None = None,
    clock: Clock | None = None,
) -> StructuredWriteResult | None:
    """把 LLM 生成的 Markdown 落成结构化条目并渲染回 Markdown。

    整个「读取 sidecar → 合并条目 → 渲染 → 写回」过程持有跨进程排他锁，
    Markdown 与 JSONL 共用同一锁文件；返回 ``None`` 表示调用方应回退旧
    Markdown 写入路径，保证不丢数据。
    """
    with memory_lock(memory_file):
        return _write_structured_memory_locked(
            memory_file,
            markdown,
            title=title,
            source=source,
            max_chars=max_chars,
            previous_markdown=previous_markdown,
            max_entries=max_entries,
            max_value_chars=max_value_chars,
            clock=clock,
        )


def _write_structured_memory_locked(
    memory_file: str | Path,
    markdown: str,
    *,
    title: str,
    source: str,
    max_chars: int,
    previous_markdown: str | None = None,
    max_entries: int = MAX_ENTRIES,
    max_value_chars: int | None = None,
    clock: Clock | None = None,
) -> StructuredWriteResult | None:
    """``write_structured_memory`` 的实际实现（调用方需已持有锁）。

    返回 ``None`` 表示解析不出任何条目（调用方应回退旧 Markdown 写入路径，
    保证不丢数据），或渲染后无内容可写。异常不在此吞掉，由调用方统一兜底。
    """
    if not markdown or not markdown.strip():
        return None

    now = (clock or _utc_now)()
    entries = parse_markdown(markdown, source=source, now=now)
    if not entries:
        logger.info(
            "结构化记忆解析为空，回退旧 Markdown 路径 | 文件: %s | 长度: %d",
            memory_file,
            len(markdown),
        )
        return None

    # 单条 value 上限：至少给整篇预算留出标题/分组开销，避免长条目被过早截断
    value_cap = max_value_chars
    if value_cap is None:
        value_cap = max(64, int(max_chars) - 16)

    store = MemoryStore(
        store_path_for(memory_file),
        title=title,
        max_entries=max_entries,
        max_value_chars=value_cap,
        clock=clock,
    )
    store.load()

    # 首次升级 / sidecar 缺失 / Markdown 被外部改动：以 Markdown 为准重建
    if previous_markdown and (
        not store.entries or store.markdown_sha256 != full_digest(previous_markdown)
    ):
        previous_entries = parse_markdown(previous_markdown, source=SOURCE_IMPORT, now=now)
        if previous_entries:
            store.replace_entries(previous_entries)

    store.title = extract_title(markdown, title or store.title)
    store.apply_entries(entries)

    fitted, rendered = fit_entries_to_budget(
        store.entries, title=store.title, max_chars=int(max_chars)
    )
    if not fitted:
        logger.info(
            "结构化记忆无条目可写入，回退旧 Markdown 路径 | 文件: %s | 长度: %d",
            memory_file,
            len(markdown),
        )
        return None

    # 预算裁剪可能截短最后一条（不一定减少条数），因此始终回灌裁剪结果
    store.apply_entries(fitted)
    rendered = store.render_markdown()
    if len(rendered) > int(max_chars):
        # 合并/键重算导致渲染变长时再裁剪一轮（正常路径不会触发）
        fitted, rendered = fit_entries_to_budget(
            store.entries, title=store.title, max_chars=int(max_chars)
        )
        if not fitted:
            return None
        store.apply_entries(fitted)
        rendered = store.render_markdown()

    store.markdown_sha256 = full_digest(rendered)
    revision = store.save()
    return StructuredWriteResult(
        markdown=rendered,
        entries=len(store.entries),
        revision=revision,
        title=store.title,
    )


def sync_from_markdown(
    memory_file: str | Path,
    *,
    markdown: str | None = None,
    source: str = SOURCE_IMPORT,
    title: str | None = None,
    max_entries: int = MAX_ENTRIES,
    clock: Clock | None = None,
) -> bool:
    """旧 Markdown 首次导入 / 外部修改回灌 sidecar。

    整段「读 Markdown → 比较 hash → 重建条目 → 写 sidecar」在排他锁内完成，
    幂等：Markdown 内容 hash 与 meta 一致时直接返回 ``False``，不 bump revision。
    """
    with memory_lock(memory_file):
        return _sync_from_markdown_locked(
            memory_file,
            markdown=markdown,
            source=source,
            title=title,
            max_entries=max_entries,
            clock=clock,
        )


def _sync_from_markdown_locked(
    memory_file: str | Path,
    *,
    markdown: str | None = None,
    source: str = SOURCE_IMPORT,
    title: str | None = None,
    max_entries: int = MAX_ENTRIES,
    clock: Clock | None = None,
) -> bool:
    """``sync_from_markdown`` 的实际实现（调用方需已持有锁）。"""
    text = _read_text(Path(memory_file)) if markdown is None else markdown
    if not text or not text.strip():
        return False

    store = MemoryStore(
        store_path_for(memory_file),
        title=title or "",
        max_entries=max_entries,
        clock=clock,
    )
    store.load()
    digest = full_digest(text)
    if store.entries and store.markdown_sha256 == digest:
        return False

    entries = parse_markdown(text, source=source)
    if not entries:
        return False

    store.title = extract_title(text, title or store.title)
    store.replace_entries(entries)
    store.markdown_sha256 = digest
    store.save()
    logger.info(
        "已从 Markdown 导入结构化记忆 | 文件: %s | 条目: %d | revision: %d",
        memory_file,
        len(store),
        store.revision,
    )
    return True


__all__ = [
    "DEFAULT_CATEGORY",
    "ENV_FLAG",
    "LOCK_SUFFIX",
    "MAX_ENTRIES",
    "MAX_KEY_CHARS",
    "MAX_VALUE_CHARS",
    "SCHEMA_VERSION",
    "SOURCE_DIRECT_WRITE",
    "SOURCE_IMPORT",
    "SOURCE_LLM_LONG_TERM",
    "SOURCE_LLM_SESSION",
    "STORE_SUFFIX",
    "MemoryEntry",
    "MemoryStore",
    "MemoryValidationError",
    "StructuredWriteResult",
    "atomic_write_text",
    "content_digest",
    "cross_process_lock_supported",
    "detect_sensitive",
    "extract_title",
    "fit_entries_to_budget",
    "full_digest",
    "make_entry",
    "memory_lock",
    "memory_lock_path",
    "merge_entries",
    "normalize_key",
    "parse_markdown",
    "redact_sensitive",
    "render_markdown",
    "store_path_for",
    "structured_memory_enabled",
    "sync_from_markdown",
    "truncate_text",
    "validate_entry",
    "write_structured_memory",
]
