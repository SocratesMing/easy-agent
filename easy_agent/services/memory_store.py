"""结构化用户记忆存储（JSONL sidecar）。

设计目标（详见 ``docs/design/2026-10-18-structured-memory.md``）：

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
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("easy_agent.memory.store")

SCHEMA_VERSION = 1
STORE_SUFFIX = ".jsonl"
ENV_FLAG = "EASY_STRUCTURED_MEMORY"

MAX_KEY_CHARS = 80
MAX_VALUE_CHARS = 600
MAX_ENTRIES = 50
MIN_TRIMMED_VALUE_CHARS = 16
DEFAULT_CATEGORY = "未分类"
DEFAULT_SOURCE = "unknown"

SOURCE_LLM_SESSION = "llm:session"
SOURCE_LLM_LONG_TERM = "llm:long_term"
SOURCE_IMPORT = "import:markdown"
SOURCE_DIRECT_WRITE = "markdown:direct"

REDACTION_MARKER = "[REDACTED:{kind}]"

Clock = Callable[[], str]


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """当前 UTC 时间（秒级 ISO 8601），作为条目更新时间。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def full_digest(text: str) -> str:
    """完整 sha256（用于校验 Markdown 与 sidecar 是否对应）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def content_digest(text: str) -> str:
    """日志用短 hash（不泄露记忆内容）。"""
    return full_digest(text)[:12]


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _collapse_whitespace(text: str) -> str:
    """把换行 / 制表符折叠为单空格，避免条目污染 Markdown 行结构。"""
    return re.sub(r"\s+", " ", text or "").strip()


def truncate_text(text: str, max_chars: int) -> str:
    """按字符截断并追加省略号，返回值长度不超过 ``max_chars``。"""
    text = text or ""
    max_chars = int(max_chars)
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars == 1:
        return "…"
    return text[: max_chars - 1].rstrip() + "…"


def structured_memory_enabled(env: Mapping[str, str] | None = None) -> bool:
    """结构化记忆开关，默认开启；``EASY_STRUCTURED_MEMORY=0`` 回退旧行为。"""
    source = os.environ if env is None else env
    raw = source.get(ENV_FLAG)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "off", "no", "disabled"}


def store_path_for(memory_file: str | Path) -> Path:
    """由 Markdown 记忆文件推导 sidecar 路径：``memory.md -> memory.jsonl``。"""
    path = Path(memory_file)
    if path.suffix == STORE_SUFFIX:
        return path
    return path.with_suffix(STORE_SUFFIX)


# ---------------------------------------------------------------------------
# 敏感信息扫描（简单模式，宁可少量误伤也不落盘明文）
# ---------------------------------------------------------------------------

_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|authorization)\b(\s*[:=：]\s*)(?!\[REDACTED:)([^\s,;，；、。]{4,})"
)
_CN_SECRET_ASSIGN_RE = re.compile(
    r"(密码|密钥|口令|令牌)(\s*[:=：]\s*)(?!\[REDACTED:)([^\s,;，；、。]{3,})"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_AADHAAR_RE = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")
_CN_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")


def detect_sensitive(text: str) -> tuple[str, ...]:
    """返回命中的敏感类型（仅类型，不返回明文），用于日志与测试。"""
    if not text:
        return ()
    kinds: list[str] = []
    if _PRIVATE_KEY_RE.search(text):
        kinds.append("private_key")
    if _BEARER_RE.search(text):
        kinds.append("credential")
    if _SECRET_ASSIGN_RE.search(text) or _CN_SECRET_ASSIGN_RE.search(text):
        kinds.append("credential")
    if _AADHAAR_RE.search(text):
        kinds.append("aadhaar")
    if _CN_ID_RE.search(text):
        kinds.append("cn_id")
    return tuple(dict.fromkeys(kinds))


def redact_sensitive(text: str) -> tuple[str, int]:
    """把密钥 / PII 替换为 ``[REDACTED:kind]``，返回 (清洗后文本, 命中次数)。"""
    if not text:
        return text, 0
    hits = 0
    result = text

    def _mark(kind: str) -> str:
        nonlocal hits
        hits += 1
        return REDACTION_MARKER.format(kind=kind)

    result = _PRIVATE_KEY_RE.sub(lambda _m: _mark("private_key"), result)
    result = _BEARER_RE.sub(lambda _m: _mark("credential"), result)

    def _replace_assignment(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}{_mark('credential')}"

    result = _SECRET_ASSIGN_RE.sub(_replace_assignment, result)
    result = _CN_SECRET_ASSIGN_RE.sub(_replace_assignment, result)
    result = _AADHAAR_RE.sub(lambda _m: _mark("aadhaar"), result)
    result = _CN_ID_RE.sub(lambda _m: _mark("cn_id"), result)
    return result, hits


# ---------------------------------------------------------------------------
# 数据模型与校验
# ---------------------------------------------------------------------------


class MemoryValidationError(ValueError):
    """记忆条目未通过校验（空内容、无法生成 key 等）。"""


@dataclass(frozen=True)
class MemoryEntry:
    """一条结构化记忆。

    字段与设计文档一致：``id/key/value/category/source/confidence/updated_at``，
    另加 ``version``（同一 key 每次内容变更 +1，用于 supersede 审计）。
    """

    id: str
    key: str
    value: str
    category: str = DEFAULT_CATEGORY
    source: str = DEFAULT_SOURCE
    confidence: float | None = None
    updated_at: str = ""
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "entry",
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "source": self.source,
            "confidence": self.confidence,
            "updated_at": self.updated_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MemoryEntry:
        """容错构造：缺字段 / 类型不对时按默认值兜底，不抛异常。"""
        value = str(data.get("value") or "")
        key = str(data.get("key") or "")
        confidence = data.get("confidence")
        try:
            confidence = None if confidence is None else float(confidence)
        except (TypeError, ValueError):
            confidence = None
        version = _safe_int(data.get("version"), 1)
        return cls(
            id=str(data.get("id") or "") or entry_id_for_key(key or value),
            key=key,
            value=value,
            category=str(data.get("category") or DEFAULT_CATEGORY),
            source=str(data.get("source") or DEFAULT_SOURCE),
            confidence=confidence,
            updated_at=str(data.get("updated_at") or ""),
            version=max(1, version),
        )


_KEY_SPLIT_RE = re.compile(r"——|—|--+|\s-\s|[：:，,；;。|｜]")
_KEY_CLEAN_RE = re.compile(r"[\W_]+")


def entry_id_for_key(key: str) -> str:
    """由规范化 key 派生稳定 id（同一 key 跨进程 / 跨导入保持一致）。"""
    return "mem_" + hashlib.sha1((key or "").encode("utf-8")).hexdigest()[:16]


def normalize_key(text: str) -> str:
    """把条目文本压成稳定 key（去分隔符、标点、空白与大小写差异）。

    取第一处分隔符（``——`` / ``：`` / ``，`` 等）之前的部分作为 key，
    这样 "数据库统一用 MySQL —— 开发与生产一致" 与
    "数据库统一用MySQL，开发与生产一致" 归并为同一条记忆。
    """
    raw = _collapse_whitespace(text)
    if not raw:
        return ""
    head = _KEY_SPLIT_RE.split(raw, maxsplit=1)[0]
    head = _KEY_CLEAN_RE.sub("", head).casefold()
    if head:
        return head[:MAX_KEY_CHARS]
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"k{digest}"


def make_entry(
    value: str,
    *,
    category: str = DEFAULT_CATEGORY,
    source: str = DEFAULT_SOURCE,
    key: str | None = None,
    confidence: float | None = None,
    updated_at: str | None = None,
    version: int = 1,
    clock: Clock | None = None,
) -> MemoryEntry:
    """构造条目（不做脱敏 / 截断，写入前交给 :func:`validate_entry`）。"""
    normalized_key = normalize_key(key or value) or normalize_key(value)
    if not normalized_key:
        raise MemoryValidationError("记忆条目缺少可用 key")
    return MemoryEntry(
        id=entry_id_for_key(normalized_key),
        key=normalized_key,
        value=value or "",
        category=_collapse_whitespace(category) or DEFAULT_CATEGORY,
        source=(source or "").strip() or DEFAULT_SOURCE,
        confidence=confidence,
        updated_at=updated_at or (clock or _utc_now)(),
        version=max(1, int(version)),
    )


def validate_entry(
    entry: MemoryEntry,
    *,
    max_value_chars: int = MAX_VALUE_CHARS,
    now: str | None = None,
) -> MemoryEntry:
    """校验并规范化条目。

    - 折叠换行 / 多余空白，空内容直接拒绝；
    - 密钥 / PII 脱敏（脱敏计入内容，不记录明文）；
    - 超长 value 截断到 ``max_value_chars``；
    - 补全 id / category / source / updated_at。
    """
    value = _collapse_whitespace(entry.value)
    if not value:
        raise MemoryValidationError("记忆内容为空")

    kinds = detect_sensitive(value)
    if kinds:
        logger.warning(
            "记忆条目含敏感信息，已脱敏 | 类型: %s | 内容 hash: %s",
            ",".join(kinds),
            content_digest(value),
        )
    value, _hits = redact_sensitive(value)
    value = truncate_text(value, max_value_chars)
    if not value:
        raise MemoryValidationError("脱敏/截断后记忆内容为空")

    key = normalize_key(entry.key) or normalize_key(value)
    if not key:
        raise MemoryValidationError("记忆条目缺少可用 key")

    category = _collapse_whitespace(entry.category.lstrip("#"))
    source = (entry.source or "").strip() or DEFAULT_SOURCE
    confidence = entry.confidence
    if confidence is not None:
        try:
            confidence = min(1.0, max(0.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = None

    version = entry.version if entry.version >= 1 else 1
    return replace(
        entry,
        id=entry_id_for_key(key),
        key=key,
        value=value,
        category=category or DEFAULT_CATEGORY,
        source=source,
        confidence=confidence,
        updated_at=entry.updated_at or now or _utc_now(),
        version=version,
    )


def merge_entries(existing: MemoryEntry | None, incoming: MemoryEntry) -> MemoryEntry:
    """同 key 合并：内容相同保持幂等；内容变化则 supersede（version + 1）。"""
    if existing is None:
        return incoming
    if existing.value == incoming.value and existing.category == incoming.category:
        if incoming.confidence is not None and incoming.confidence != existing.confidence:
            return replace(existing, confidence=incoming.confidence)
        return existing
    return replace(
        incoming,
        id=existing.id,
        version=max(1, existing.version) + 1,
    )


# ---------------------------------------------------------------------------
# Markdown 解析 / 渲染
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)、])\s+(.+)$")
_FENCE_RE = re.compile(r"^\s*```")
_HR_RE = re.compile(r"^[-*_]{3,}$")
_HTML_COMMENT_RE = re.compile(r"^\s*<!--")
_HEADING_MARKUP_RE = re.compile(r"[*_`~]+")


def _clean_heading(text: str) -> str:
    return _HEADING_MARKUP_RE.sub("", text or "").strip()


def extract_title(markdown: str, default_title: str = "") -> str:
    """取 Markdown 一级标题（没有则返回默认标题）。"""
    for line in (markdown or "").splitlines():
        match = _HEADING_RE.match(line.strip())
        if match and len(match.group(1)) == 1:
            return _clean_heading(match.group(2)) or default_title
    return default_title


def parse_markdown(
    markdown: str,
    *,
    source: str = SOURCE_IMPORT,
    now: str | None = None,
) -> list[MemoryEntry]:
    """把旧 / LLM 生成的 Markdown 解析为条目（best effort）。

    - 一级标题视为文档标题（不生成条目）；
    - 二级及以下标题作为 ``category``；
    - ``-`` / ``*`` / ``+`` / 有序列表项各生成一条；缩进的续行并入上一条；
    - 代码块、水平线、HTML 注释跳过；无标题的普通段落也按条目处理。
    """
    entries: list[MemoryEntry] = []
    if not markdown:
        return entries

    category = DEFAULT_CATEGORY
    buffer: list[str] = []
    timestamp = now or _utc_now()
    in_fence = False

    def flush() -> None:
        if not buffer:
            return
        value = " ".join(part for part in (p.strip() for p in buffer) if part)
        buffer.clear()
        if not value:
            return
        try:
            entries.append(
                make_entry(value, category=category, source=source, updated_at=timestamp)
            )
        except MemoryValidationError:
            return

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if _FENCE_RE.match(line):
            in_fence = not in_fence
            flush()
            continue
        if in_fence:
            continue
        if not stripped:
            flush()
            continue
        if _HTML_COMMENT_RE.match(line):
            continue
        if _HR_RE.match(stripped):
            flush()
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            flush()
            text = _clean_heading(heading.group(2))
            if len(heading.group(1)) >= 2 and text:
                category = text
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            flush()
            buffer.append(bullet.group(1).strip())
            continue

        buffer.append(stripped)

    flush()
    return entries


def render_markdown(entries: Iterable[MemoryEntry], *, title: str = "") -> str:
    """把条目渲染为 Markdown（保持 ``# 标题`` + ``## 分组`` + ``- 条目`` 结构）。"""
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
        lines.append("")

    current_category: str | None = None
    for entry in entries:
        category = entry.category or DEFAULT_CATEGORY
        if category != current_category:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"## {category}")
            current_category = category
        lines.append(f"- {entry.value}")

    while lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def fit_entries_to_budget(
    entries: Iterable[MemoryEntry],
    *,
    title: str = "",
    max_chars: int,
) -> tuple[list[MemoryEntry], str]:
    """裁剪条目使渲染结果不超过 ``max_chars``。

    策略：从文档尾部（提示词约定中优先级最低）开始，先截短最后一条，仍超限则整条丢弃。
    返回 (保留的条目, 渲染后的 Markdown)，Markdown 长度保证 <= ``max_chars``。
    """
    work = list(entries)
    max_chars = int(max_chars)
    if max_chars <= 0:
        return [], ""

    empty_markdown = render_markdown([], title=title)
    if len(empty_markdown) > max_chars:
        return [], empty_markdown[:max_chars]

    while True:
        markdown = render_markdown(work, title=title)
        if len(markdown) <= max_chars:
            return work, markdown
        if not work:
            return [], markdown[:max_chars]
        excess = len(markdown) - max_chars
        last = work[-1]
        trimmed_len = len(last.value) - excess
        if MIN_TRIMMED_VALUE_CHARS <= trimmed_len < len(last.value):
            trimmed_value = last.value[:trimmed_len].rstrip()
            trimmed_key = normalize_key(trimmed_value)
            # 截断后 key 需按新内容重算，否则 sidecar 与渲染出的 Markdown 会推导出不同 key
            work[-1] = replace(
                last,
                value=trimmed_value,
                key=trimmed_key or last.key,
                id=entry_id_for_key(trimmed_key) if trimmed_key else last.id,
            )
        else:
            work.pop()


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_text(path: Path, text: str) -> None:
    """临时文件 + ``os.replace`` 原子写；同目录保证 replace 不跨文件系统。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _fsync_directory(path.parent)


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
        """读取 JSONL；损坏行跳过并计数，缺失文件视为空存储。"""
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
        """原子写 JSONL，revision + 1，返回新 revision。"""
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

    def upsert_many(self, entries: Iterable[MemoryEntry]) -> list[MemoryEntry]:
        stored: list[MemoryEntry] = []
        for entry in entries:
            try:
                stored.append(self.upsert(entry))
            except (MemoryValidationError, TypeError, ValueError) as exc:
                logger.warning(
                    "跳过无效记忆条目 | 内容 hash: %s | %s",
                    content_digest(str(getattr(entry, "value", ""))),
                    exc,
                )
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

    幂等：Markdown 内容 hash 与 meta 一致时直接返回 ``False``，不 bump revision。
    """
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
    "content_digest",
    "detect_sensitive",
    "extract_title",
    "fit_entries_to_budget",
    "full_digest",
    "make_entry",
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
