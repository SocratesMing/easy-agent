"""记忆存储模块拆分回归（批次 2A）：memory_locks / memory_entries / re-export。

覆盖：

- ``memory_store`` 的 re-export 与子模块是同一对象（既有导入路径兼容）；
- 依赖方向：``memory_locks`` / ``memory_entries`` 不 import ``memory_store``；
- 锁语义：Markdown / JSONL 共用锁文件、同线程可重入、锁文件保留；
- 原子写：UTF-8 / LF、替换原地生效、不残留临时文件；
- 条目校验 / 脱敏 / parse / render / 预算裁剪行为不变；
- 跨进程 flock 拆分后仍生效（fork，无网络、无数据库）。

全部使用 tmp_path 与 fork 子进程，不依赖网络、数据库与真实 agent。
"""

from __future__ import annotations

import ast
import json
import multiprocessing
import time
from pathlib import Path

import pytest

import easy_agent.services.memory_entries as memory_entries
import easy_agent.services.memory_locks as memory_locks
import easy_agent.services.memory_store as memory_store

# 拆分前 ``memory_store`` 的公开契约（__all__ 原样冻结，防止 re-export 漂移）。
EXPECTED_ALL = {
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
}

ENTRY_EXPORTS = [
    "DEFAULT_CATEGORY",
    "DEFAULT_SOURCE",
    "ENV_FLAG",
    "MAX_KEY_CHARS",
    "MAX_VALUE_CHARS",
    "MIN_TRIMMED_VALUE_CHARS",
    "REDACTION_MARKER",
    "SOURCE_DIRECT_WRITE",
    "SOURCE_IMPORT",
    "SOURCE_LLM_LONG_TERM",
    "SOURCE_LLM_SESSION",
    "MemoryEntry",
    "MemoryValidationError",
    "content_digest",
    "detect_sensitive",
    "entry_id_for_key",
    "extract_title",
    "fit_entries_to_budget",
    "full_digest",
    "make_entry",
    "merge_entries",
    "normalize_key",
    "parse_markdown",
    "redact_sensitive",
    "render_markdown",
    "structured_memory_enabled",
    "truncate_text",
    "validate_entry",
]

LOCK_EXPORTS = [
    "LOCK_SUFFIX",
    "STORE_SUFFIX",
    "atomic_write_text",
    "cross_process_lock_supported",
    "memory_lock",
    "memory_lock_path",
    "store_path_for",
]

STORE_DEFINED_EXPORTS = [
    "MAX_ENTRIES",
    "SCHEMA_VERSION",
    "MemoryStore",
    "StructuredWriteResult",
    "sync_from_markdown",
    "write_structured_memory",
]


# ---------------------------------------------------------------------------
# re-export 身份 / 依赖方向
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ENTRY_EXPORTS)
def test_memory_store_reexports_entries_with_same_identity(name):
    assert getattr(memory_store, name) is getattr(memory_entries, name)


@pytest.mark.parametrize("name", LOCK_EXPORTS)
def test_memory_store_reexports_locks_with_same_identity(name):
    assert getattr(memory_store, name) is getattr(memory_locks, name)


@pytest.mark.parametrize("name", STORE_DEFINED_EXPORTS)
def test_memory_store_keeps_store_owned_symbols(name):
    value = getattr(memory_store, name)
    assert value is not None
    if hasattr(value, "__module__"):
        assert value.__module__ == memory_store.__name__


def test_memory_store_all_contract_is_frozen():
    assert set(memory_store.__all__) == EXPECTED_ALL
    assert len(memory_store.__all__) == len(EXPECTED_ALL)
    for name in memory_store.__all__:
        assert hasattr(memory_store, name), name


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            names.add(prefix + (node.module or ""))
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize(
    "module", [memory_locks, memory_entries], ids=["memory_locks", "memory_entries"]
)
def test_extracted_modules_do_not_import_memory_store(module):
    imported = _imported_module_names(Path(module.__file__))
    assert not any("memory_store" in name for name in imported), imported


# ---------------------------------------------------------------------------
# 锁 / 原子写
# ---------------------------------------------------------------------------


def test_lock_path_is_shared_by_markdown_and_sidecar(tmp_path):
    markdown = tmp_path / "memory.md"
    sidecar = tmp_path / "memory.jsonl"

    assert memory_locks.store_path_for(markdown) == sidecar
    assert memory_locks.store_path_for(sidecar) == sidecar
    assert memory_locks.memory_lock_path(markdown) == memory_locks.memory_lock_path(sidecar)
    assert memory_locks.memory_lock_path(markdown).name == ".memory.jsonl.lock"


def test_memory_lock_is_reentrant_and_keeps_lock_file(tmp_path):
    target = tmp_path / "memory.md"
    with memory_locks.memory_lock(target):
        with memory_locks.memory_lock(target):
            with memory_locks.memory_lock(target, exclusive=False):
                assert memory_locks.memory_lock_path(target).exists()
    # 释放后保留空锁文件（删除会让第三个进程锁到新 inode，破坏互斥）。
    assert memory_locks.memory_lock_path(target).exists()


def test_atomic_write_text_is_lf_utf8_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "memory.md"
    memory_locks.atomic_write_text(target, "第一行\n第二行\n")

    assert target.read_bytes() == "第一行\n第二行\n".encode("utf-8")
    assert not list(tmp_path.glob(".*.tmp"))

    memory_locks.atomic_write_text(target, "替换\n")
    assert target.read_text(encoding="utf-8") == "替换\n"
    assert not list(tmp_path.glob(".*.tmp"))


# ---------------------------------------------------------------------------
# 条目校验 / parse / render
# ---------------------------------------------------------------------------

LEGACY_MD = (
    "# 会话记忆\n\n"
    "## 决策与理由\n"
    "- 数据库统一用 MySQL —— 开发与生产一致\n"
    "- 已否决 SQLite，避免方言差异\n\n"
    "## 偏好与约定\n"
    "- 注释一律用中文\n"
    "  接口路径统一带 /agent 前缀\n\n"
    "## 下一步\n"
    "- 待补结构化记忆测试\n"
)


def test_entry_validation_redacts_and_rejects_empty():
    raw = memory_entries.make_entry(
        "生产库 password: hunter2，aadhaar 1234 5678 9012",
        category="约定与路径",
        source="test",
    )
    valid = memory_entries.validate_entry(raw)

    assert "hunter2" not in valid.value
    assert "5678 9012" not in valid.value
    assert "[REDACTED:credential]" in valid.value
    assert "[REDACTED:aadhaar]" in valid.value
    assert valid.id == memory_entries.entry_id_for_key(valid.key)

    with pytest.raises(memory_entries.MemoryValidationError):
        memory_entries.validate_entry(
            memory_entries.MemoryEntry(id="x", key="k", value="   \n\t")
        )


def test_normalize_key_and_merge_supersede_behavior_unchanged():
    assert memory_entries.normalize_key(
        "数据库统一用 MySQL —— 开发与生产一致"
    ) == memory_entries.normalize_key("数据库统一用MySQL，开发与生产一致")

    first = memory_entries.validate_entry(
        memory_entries.make_entry("接口统一带 /agent 前缀 —— 与网关一致", category="约定")
    )
    second = memory_entries.validate_entry(
        memory_entries.make_entry(
            "接口统一带/agent前缀，与网关及前端路由一致", category="约定"
        )
    )

    merged = memory_entries.merge_entries(first, second)
    assert merged.id == first.id
    assert merged.version == 2
    assert "前端路由" in merged.value
    # 相同内容再次合并是幂等的（不 bump 版本）
    assert memory_entries.merge_entries(first, first).version == first.version


def test_parse_render_roundtrip_and_budget_unchanged():
    entries = memory_entries.parse_markdown(LEGACY_MD)

    assert [entry.category for entry in entries] == [
        "决策与理由",
        "决策与理由",
        "偏好与约定",
        "下一步",
    ]
    assert entries[2].value == "注释一律用中文 接口路径统一带 /agent 前缀"
    assert all(entry.source == "import:markdown" for entry in entries)
    assert memory_entries.extract_title(LEGACY_MD) == "会话记忆"

    rendered = memory_entries.render_markdown(entries, title="会话记忆")
    assert rendered.startswith("# 会话记忆\n\n## 决策与理由\n- 数据库统一用 MySQL —— 开发与生产一致\n")
    reparsed = memory_entries.parse_markdown(rendered)
    assert [(entry.key, entry.value) for entry in reparsed] == [
        (entry.key, entry.value) for entry in entries
    ]

    fitted, markdown = memory_entries.fit_entries_to_budget(
        entries, title="会话记忆", max_chars=80
    )
    assert fitted
    assert len(markdown) <= 80
    assert len(memory_entries.render_markdown(fitted, title="会话记忆")) == len(markdown)


# ---------------------------------------------------------------------------
# 跨进程锁仍可用
# ---------------------------------------------------------------------------

FORK_AVAILABLE = "fork" in multiprocessing.get_all_start_methods()


def _lock_writer_process(args):
    """子进程：锁内 load -> upsert -> save（与既有测试 helper 同构）。"""
    path_str, worker_id, iterations = args
    path = Path(path_str)
    for index in range(iterations):
        with memory_locks.memory_lock(path):
            store = memory_store.MemoryStore(path).load()
            store.upsert(
                memory_entries.make_entry(
                    f"拆分并发条目{worker_id}-{index} 内容", category="并发", source="test"
                )
            )
            store.save()


@pytest.mark.skipif(not FORK_AVAILABLE, reason="需要 fork 做多进程并发测试")
def test_cross_process_lock_still_serializes_read_modify_write(tmp_path):
    path = tmp_path / "memory.jsonl"
    memory_store.MemoryStore(path, title="会话记忆").save()

    workers, iterations = 3, 3
    context = multiprocessing.get_context("fork")
    processes = [
        context.Process(
            target=_lock_writer_process, args=((str(path), worker_id, iterations),)
        )
        for worker_id in range(workers)
    ]
    for process in processes:
        process.start()

    # 写的同时读取：原子替换保证每行都能被 json 解析（不会读到半截文件）。
    while any(process.is_alive() for process in processes):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    assert isinstance(json.loads(line), dict)
        time.sleep(0.005)

    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0

    store = memory_store.MemoryStore(path).load()
    # 丢锁会出现重复 revision；严格等于累计写入次数说明跨进程锁仍生效。
    assert store.revision == workers * iterations + 1
    assert len(store) == workers * iterations
    assert len({entry.value for entry in store.entries}) == workers * iterations
