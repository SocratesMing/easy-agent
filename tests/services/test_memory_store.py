"""结构化记忆存储测试（JSONL sidecar）。

覆盖：
- 条目校验 / 脱敏 / 截断 / 重复 key supersede；
- JSONL 原子写、读回、损坏行容错；
- Markdown 渲染 / 旧 Markdown 解析导入；
- memory_manager 集成（结构化写入、失败回退、环境开关、日志不打印全文）。

全部使用 tmp_path 与 fake LLM，不依赖网络、数据库与真实 agent。
"""

import hashlib
import json
import logging
import multiprocessing
import time
from pathlib import Path

import pytest

from easy_agent.services import memory_locks, memory_manager
from easy_agent.services.memory_store import (
    ENV_FLAG,
    MemoryEntry,
    MemoryStore,
    MemoryValidationError,
    content_digest,
    extract_title,
    make_entry,
    memory_lock,
    memory_lock_path,
    normalize_key,
    parse_markdown,
    render_markdown,
    store_path_for,
    structured_memory_enabled,
    sync_from_markdown,
    validate_entry,
    write_structured_memory,
)


class FakeLLM:
    """按顺序返回预设内容的假模型（不联网）。"""

    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages[0].content)
        content = self._responses.pop(0) if self._responses else ""
        return type("Response", (), {"content": content})()


SESSION_MD = (
    "# 会话记忆\n\n"
    "## 决策与理由\n"
    "- 数据库统一用 MySQL —— 开发与生产一致\n\n"
    "## 偏好与约定\n"
    "- 注释一律用中文\n"
)

LONG_TERM_MD = (
    "# 用户长期记忆\n\n"
    "## 工作背景\n"
    "- 后端工程师 —— 负责 easy-agent 项目\n\n"
    "## 当前关注\n"
    "- 结构化记忆落地\n"
)


# ---------------------------------------------------------------------------
# key / 校验 / 脱敏
# ---------------------------------------------------------------------------


def test_normalize_key_is_stable_across_punctuation():
    assert normalize_key("数据库统一用 MySQL —— 开发与生产一致") == normalize_key(
        "数据库统一用MySQL，开发与生产一致"
    )
    assert normalize_key("数据库统一用 MySQL") == "数据库统一用mysql"
    assert normalize_key("   ") == ""
    assert normalize_key("！？").startswith("k")


def test_validate_entry_redacts_secrets_and_aadhaar():
    raw = make_entry(
        "生产库 password: hunter2，token=abcd1234efgh，aadhaar 1234 5678 9012",
        category="约定与路径",
        source="test",
    )
    valid = validate_entry(raw)

    assert "hunter2" not in valid.value
    assert "abcd1234efgh" not in valid.value
    assert "5678 9012" not in valid.value
    assert "[REDACTED:credential]" in valid.value
    assert "[REDACTED:aadhaar]" in valid.value


def test_validate_entry_truncates_and_collapses_whitespace():
    raw = MemoryEntry(id="", key="", value="a" * 500 + "\n\tb", category="", source="")
    valid = validate_entry(raw, max_value_chars=50)

    assert len(valid.value) <= 50
    assert "\n" not in valid.value
    assert valid.category == "未分类"
    assert valid.source == "unknown"
    assert valid.key
    assert valid.id == "mem_" + hashlib.sha1(valid.key.encode("utf-8")).hexdigest()[:16]


def test_validate_entry_rejects_empty_content():
    with pytest.raises(MemoryValidationError):
        validate_entry(MemoryEntry(id="x", key="k", value="   \n\t", category="分组"))


def test_sensitive_scan_is_not_over_eager_for_plain_words():
    valid = validate_entry(make_entry("token 预算每轮控制在 2000 以内"))
    assert "token 预算" in valid.value


# ---------------------------------------------------------------------------
# 存储：去重 / supersede / 原子写 / 读回
# ---------------------------------------------------------------------------


def test_upsert_supersedes_same_key_and_keeps_version(tmp_path):
    store = MemoryStore(tmp_path / "memory.jsonl", title="会话记忆")
    first = store.upsert(
        make_entry(
            "接口统一带 /agent 前缀 —— 与网关一致",
            category="偏好与约定",
            source="llm:session",
            updated_at="2026-10-18T00:00:00+00:00",
        )
    )
    assert first.version == 1
    assert len(store) == 1

    second = store.upsert(
        make_entry(
            "接口统一带/agent前缀，与网关及前端路由一致",
            category="偏好与约定",
            source="llm:session",
            updated_at="2026-10-18T00:05:00+00:00",
        )
    )

    assert len(store) == 1
    assert second.id == first.id
    assert second.version == 2
    assert second.updated_at == "2026-10-18T00:05:00+00:00"
    assert "前端路由" in second.value

    # 相同内容再次写入是幂等的（不 bump 版本 / 更新时间）
    third = store.upsert(
        make_entry(
            "接口统一带/agent前缀，与网关及前端路由一致",
            category="偏好与约定",
            source="llm:session",
            updated_at="2026-10-18T00:09:00+00:00",
        )
    )
    assert third.version == 2
    assert third.updated_at == second.updated_at


def test_jsonl_atomic_write_and_read_back(tmp_path):
    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path, title="会话记忆")
    store.upsert(make_entry("注释一律用中文", category="偏好与约定", source="llm:session"))
    store.upsert(make_entry("接口路径统一带 /agent 前缀", category="偏好与约定", source="llm:session"))
    revision = store.save()

    assert revision == 1
    assert path.exists()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["type"] == "meta"
    assert records[0]["revision"] == 1
    assert records[0]["count"] == 2
    assert len([r for r in records if r["type"] == "entry"]) == 2
    # 临时文件必须清理干净（原子替换，不残留 .tmp）
    assert not list(tmp_path.glob(".*.tmp"))

    loaded = MemoryStore(path)
    loaded.load()
    assert loaded.revision == 1
    assert loaded.title == "会话记忆"
    assert [e.value for e in loaded.entries] == [
        "注释一律用中文",
        "接口路径统一带 /agent 前缀",
    ]
    assert {e.source for e in loaded.entries} == {"llm:session"}

    loaded.save()
    assert MemoryStore(path).load().revision == 2


def test_load_tolerates_corrupt_lines_and_missing_file(tmp_path):
    empty = MemoryStore(tmp_path / "missing.jsonl")
    assert empty.load().entries == []
    assert empty.revision == 0

    path = tmp_path / "memory.jsonl"
    good = make_entry("真实条目", category="决策与理由", source="llm:session")
    path.write_text(
        '{"type": "meta", "schema": 1, "revision": 7, "title": "会话记忆"}\n'
        "this is not json\n"
        "[]\n"
        + json.dumps(good.to_dict(), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    store = MemoryStore(path).load()
    assert [e.value for e in store.entries] == ["真实条目"]
    assert store.revision == 7
    assert store.title == "会话记忆"


def test_apply_entries_supersedes_duplicates_in_same_batch(tmp_path):
    store = MemoryStore(tmp_path / "memory.jsonl", title="会话记忆")
    entries = [
        make_entry("接口统一带 /agent 前缀 —— 与网关一致", category="偏好与约定", source="llm:session"),
        make_entry("接口统一带/agent前缀，与网关及前端一致", category="偏好与约定", source="llm:session"),
    ]

    store.apply_entries(entries)

    assert len(store) == 1
    assert store.entries[0].version == 2
    assert "前端" in store.entries[0].value


def test_store_trims_to_max_entries(tmp_path):
    store = MemoryStore(tmp_path / "memory.jsonl", max_entries=2)
    for index in range(4):
        store.upsert(make_entry(f"条目{index}", category="分组", source="test"))
    assert [e.value for e in store.entries] == ["条目2", "条目3"]


# ---------------------------------------------------------------------------
# Markdown 渲染 / 解析 / 预算
# ---------------------------------------------------------------------------


def test_render_markdown_groups_preserving_order():
    entries = [
        make_entry("决策A", category="决策与理由"),
        make_entry("决策C", category="决策与理由"),
        make_entry("偏好B", category="偏好与约定"),
    ]
    markdown = render_markdown(entries, title="会话记忆")

    assert markdown == (
        "# 会话记忆\n\n"
        "## 决策与理由\n- 决策A\n- 决策C\n\n"
        "## 偏好与约定\n- 偏好B\n"
    )
    assert render_markdown([], title="会话记忆") == "# 会话记忆\n"
    assert render_markdown([], title="") == ""


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


def test_parse_markdown_imports_legacy_sections_and_continuations():
    entries = parse_markdown(LEGACY_MD)

    assert extract_title(LEGACY_MD) == "会话记忆"
    assert [e.category for e in entries] == [
        "决策与理由",
        "决策与理由",
        "偏好与约定",
        "下一步",
    ]
    assert entries[2].value == "注释一律用中文 接口路径统一带 /agent 前缀"
    assert all(e.source == "import:markdown" for e in entries)


def test_parse_markdown_skips_code_fences_and_hr():
    markdown = (
        "# 会话记忆\n\n```\n- 不是记忆\n```\n\n---\n\n"
        "## 决策与理由\n- 真实决策\n"
    )
    entries = parse_markdown(markdown)
    assert [e.value for e in entries] == ["真实决策"]


def test_write_structured_memory_dedupes_and_fits_budget(tmp_path):
    memory_file = tmp_path / "memory.md"
    markdown = (
        "# 会话记忆\n\n"
        "## 偏好与约定\n"
        "- 接口统一带 /agent 前缀 —— 与网关一致\n"
        "- 接口统一带/agent前缀，与网关及前端路由一致\n"
        + "\n".join(f"- 决策条目{index:02d} " + "内容" * 20 for index in range(10))
        + "\n"
    )

    result = write_structured_memory(
        memory_file,
        markdown,
        title="会话记忆",
        source="llm:session",
        max_chars=220,
    )

    assert result is not None
    assert result.entries >= 1
    assert result.revision == 1
    assert result.title == "会话记忆"
    assert len(result.markdown) <= 220
    # 同 key 的重复条目只保留最新的一个版本
    assert result.markdown.count("接口统一带") == 1
    assert "前端路由" in result.markdown

    loaded = MemoryStore(store_path_for(memory_file)).load()
    # 同一轮输出里的重复 key 也走 supersede：保留最新值且 version = 2
    duplicate = [entry for entry in loaded.entries if entry.key.startswith("接口统一带")]
    assert len(duplicate) == 1 and duplicate[0].version == 2
    assert len(loaded) == result.entries
    assert [e.key for e in loaded.entries] == [
        e.key for e in parse_markdown(result.markdown)
    ]


def test_write_structured_memory_returns_none_when_no_entries(tmp_path):
    memory_file = tmp_path / "memory.md"
    assert (
        write_structured_memory(
            memory_file,
            "```\n只有代码块\n```\n",
            title="会话记忆",
            source="llm:session",
            max_chars=600,
        )
        is None
    )
    assert not store_path_for(memory_file).exists()


def test_sync_from_markdown_imports_once_and_is_idempotent(tmp_path):
    memory_file = tmp_path / "memory.md"
    memory_file.write_text(LEGACY_MD, encoding="utf-8")
    sidecar = store_path_for(memory_file)
    assert not sidecar.exists()

    assert sync_from_markdown(memory_file) is True
    assert sidecar.exists()
    first = MemoryStore(sidecar).load()
    assert len(first) == 4
    assert first.title == "会话记忆"
    assert first.markdown_sha256 == hashlib.sha256(LEGACY_MD.encode("utf-8")).hexdigest()

    revision = first.revision
    assert sync_from_markdown(memory_file) is False
    assert MemoryStore(sidecar).load().revision == revision


def test_structured_memory_env_switch():
    assert structured_memory_enabled({}) is True
    assert structured_memory_enabled({ENV_FLAG: "1"}) is True
    assert structured_memory_enabled({ENV_FLAG: "true"}) is True
    assert structured_memory_enabled({ENV_FLAG: "0"}) is False
    assert structured_memory_enabled({ENV_FLAG: "off"}) is False
    assert ENV_FLAG == "EASY_STRUCTURED_MEMORY"


# ---------------------------------------------------------------------------
# memory_manager 集成
# ---------------------------------------------------------------------------


def test_update_memory_writes_jsonl_and_markdown_consistently(tmp_path):
    memory_file = tmp_path / "memory.md"
    llm = FakeLLM(SESSION_MD)

    assert (
        memory_manager.update_memory_after_session(
            memory_file,
            user_message="我们统一用 MySQL",
            assistant_response="已记录约定。",
            llm=llm,
        )
        is True
    )

    markdown = memory_file.read_text(encoding="utf-8")
    assert "## 决策与理由" in markdown
    assert "- 注释一律用中文" in markdown

    sidecar = store_path_for(memory_file)
    assert sidecar.exists()
    store = MemoryStore(sidecar).load()
    assert store.title == "会话记忆"
    assert {e.value for e in store.entries} == {
        "数据库统一用 MySQL —— 开发与生产一致",
        "注释一律用中文",
    }
    assert {e.source for e in store.entries} == {"llm:session"}
    # Markdown 与 sidecar 内容一致（渲染视图可逆）
    parsed = parse_markdown(markdown)
    assert {e.key for e in parsed} == {e.key for e in store.entries}
    assert {e.value for e in parsed} == {e.value for e in store.entries}


def test_update_memory_preserves_version_across_legacy_migration(tmp_path):
    memory_file = tmp_path / "memory.md"
    memory_file.write_text(
        "# 会话记忆\n\n## 决策与理由\n- 数据库统一用 MySQL —— 与生产一致\n",
        encoding="utf-8",
    )
    llm = FakeLLM(
        "# 会话记忆\n\n## 决策与理由\n"
        "- 数据库统一用 MySQL —— 与生产一致，且已否决 SQLite\n"
    )

    assert (
        memory_manager.update_memory_after_session(
            memory_file, user_message="补充否决 SQLite 的原因", assistant_response="已更新。", llm=llm
        )
        is True
    )

    store = MemoryStore(store_path_for(memory_file)).load()
    assert len(store) == 1
    entry = store.entries[0]
    assert entry.version == 2
    assert "已否决 SQLite" in entry.value
    assert "已否决 SQLite" in memory_file.read_text(encoding="utf-8")


def test_update_memory_falls_back_to_plain_markdown_when_parse_fails(tmp_path):
    memory_file = tmp_path / "memory.md"
    raw = "```\n只有代码块，没有记忆条目\n```"
    llm = FakeLLM(raw)

    assert (
        memory_manager.update_memory_after_session(
            memory_file, user_message="随便聊聊", assistant_response="好的", llm=llm
        )
        is True
    )

    assert memory_file.read_text(encoding="utf-8") == raw
    assert not store_path_for(memory_file).exists()


def test_update_memory_respects_env_switch(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "0")
    memory_file = tmp_path / "memory.md"
    llm = FakeLLM(SESSION_MD)

    assert (
        memory_manager.update_memory_after_session(
            memory_file, user_message="记录约定", assistant_response="好的", llm=llm
        )
        is True
    )

    assert memory_file.read_text(encoding="utf-8") == SESSION_MD.strip()
    assert not store_path_for(memory_file).exists()


def test_update_memory_redacts_secrets_before_persisting(tmp_path):
    memory_file = tmp_path / "memory.md"
    llm = FakeLLM(
        "# 会话记忆\n\n## 偏好与约定\n- 生产库 password: hunter2 不写进记忆\n"
    )

    memory_manager.update_memory_after_session(
        memory_file, user_message="记住生产库口令", assistant_response="已脱敏记录", llm=llm
    )

    assert "hunter2" not in memory_file.read_text(encoding="utf-8")
    assert "hunter2" not in store_path_for(memory_file).read_text(encoding="utf-8")


def test_update_long_term_memory_writes_structured_sidecar(tmp_path):
    memory_file = tmp_path / "AGENTS.md"
    llm = FakeLLM(LONG_TERM_MD)

    assert (
        memory_manager.update_long_term_memory_after_session(
            memory_file,
            user_message="我是一名后端工程师",
            assistant_response="已记录长期背景。",
            llm=llm,
        )
        is True
    )

    markdown = memory_file.read_text(encoding="utf-8")
    assert "- 后端工程师 —— 负责 easy-agent 项目" in markdown
    store = MemoryStore(store_path_for(memory_file)).load()
    assert store.title == "用户长期记忆"
    assert {e.category for e in store.entries} == {"工作背景", "当前关注"}
    assert {e.source for e in store.entries} == {"llm:long_term"}


def test_write_memory_syncs_sidecar_for_direct_writes(tmp_path):
    memory_file = tmp_path / "memory.md"
    memory_manager.write_memory(memory_file, LEGACY_MD)

    store = MemoryStore(store_path_for(memory_file)).load()
    assert len(store) == 4
    assert memory_file.read_text(encoding="utf-8") == LEGACY_MD
    # 直接写 Markdown 不回写渲染视图，但 sidecar 必须反映其内容
    assert {e.category for e in store.entries} == {"决策与理由", "偏好与约定", "下一步"}


def test_second_update_with_unchanged_entries_is_idempotent(tmp_path):
    memory_file = tmp_path / "memory.md"
    llm = FakeLLM(SESSION_MD, SESSION_MD)

    for _ in range(2):
        assert (
            memory_manager.update_memory_after_session(
                memory_file, user_message="记录约定", assistant_response="好的", llm=llm
            )
            is True
        )

    store = MemoryStore(store_path_for(memory_file)).load()
    assert {entry.version for entry in store.entries} == {1}
    # 每次更新只保存一次 sidecar；第二次不会因 Markdown hash 语义重复导入
    assert store.revision == 2


def test_update_memory_logs_metadata_not_full_content(tmp_path, caplog):
    memory_file = tmp_path / "memory.md"
    marker = "唯一标记ZQ7XYZ"
    llm = FakeLLM(f"# 会话记忆\n\n## 偏好与约定\n- {marker} 不应出现在日志\n")

    with caplog.at_level(logging.INFO, logger="easy_agent.memory"):
        memory_manager.update_memory_after_session(
            memory_file, user_message="记录偏好", assistant_response="好的", llm=llm
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert marker not in messages
    assert "sha256" in messages
    assert "条目: 1" in messages
    assert content_digest(memory_file.read_text(encoding="utf-8")) in messages


# ---------------------------------------------------------------------------
# 跨进程文件锁 / 双写原子性
# ---------------------------------------------------------------------------
#
# 说明：以下是本文件新增的专项测试。用 fork 拉起子进程做「读-改-写」，
# 只有跨进程 flock 生效时 revision 才会严格等于累计写入次数；Windows 上
# 没有 fork 时跳过（对应实现会降级为进程内锁并 warning）。

FORK_AVAILABLE = "fork" in multiprocessing.get_all_start_methods()


def _lock_writer_process(args):
    """子进程：锁内 load -> upsert -> save，模拟并发记忆更新。"""
    path_str, worker_id, iterations = args
    path = Path(path_str)
    for index in range(iterations):
        with memory_lock(path):
            store = MemoryStore(path).load()
            store.upsert(
                make_entry(
                    f"并发条目{worker_id}-{index} 内容",
                    category="并发",
                    source="test",
                )
            )
            store.save()


def _dual_write_process(args):
    """子进程：并发写 Markdown + sidecar，验证两者始终对应同一内容。"""
    memory_file_str, worker_id, iterations = args
    memory_file = Path(memory_file_str)
    for index in range(iterations):
        memory_manager.write_memory(
            memory_file,
            f"# 会话记忆\n\n## 并发\n- 并发写入{worker_id}-{index} 内容\n",
        )


def test_lock_path_is_shared_by_markdown_and_sidecar(tmp_path):
    markdown = tmp_path / "memory.md"
    sidecar = tmp_path / "memory.jsonl"

    assert memory_lock_path(markdown) == memory_lock_path(sidecar)
    assert memory_lock_path(markdown).name == ".memory.jsonl.lock"


def test_memory_lock_is_reentrant_in_same_thread(tmp_path):
    target = tmp_path / "memory.md"
    with memory_lock(target):
        with memory_lock(target):
            with memory_lock(target, exclusive=False):
                assert memory_lock_path(target).exists()


def test_lock_degrades_with_single_warning_when_fcntl_missing(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(memory_locks, "_fcntl", None)
    monkeypatch.setattr(memory_locks, "_LOCK_WARNING_EMITTED", False)
    monkeypatch.setattr(memory_locks, "_FLOCK_BROKEN", False)

    with caplog.at_level(logging.WARNING, logger="easy_agent.memory.store"):
        with memory_lock(tmp_path / "memory.md"):
            pass
        with memory_lock(tmp_path / "memory.md"):
            pass

    degraded = [r.getMessage() for r in caplog.records if "降级" in r.getMessage()]
    assert len(degraded) == 1
    assert "进程内串行" in degraded[0]
    assert not memory_locks.cross_process_lock_supported()

    # 降级后记忆读写仍可用（不因平台无 flock 而失败）。
    store = MemoryStore(tmp_path / "memory.jsonl", title="会话记忆")
    store.upsert(make_entry("降级平台仍可写入", category="并发", source="test"))
    store.save()
    assert [entry.value for entry in MemoryStore(tmp_path / "memory.jsonl").load().entries] == [
        "降级平台仍可写入"
    ]


@pytest.mark.skipif(not FORK_AVAILABLE, reason="需要 fork 做多进程并发测试")
def test_cross_process_lock_serializes_read_modify_write(tmp_path):
    path = tmp_path / "memory.jsonl"
    MemoryStore(path, title="会话记忆").save()

    workers, iterations = 4, 5
    context = multiprocessing.get_context("fork")
    processes = [
        context.Process(
            target=_lock_writer_process, args=((str(path), worker_id, iterations),)
        )
        for worker_id in range(workers)
    ]
    for process in processes:
        process.start()

    # 写的同时持续读取：原子替换保证每行都能被 json 解析（不会读到半截文件）。
    while any(process.is_alive() for process in processes):
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    assert isinstance(json.loads(line), dict)
        time.sleep(0.005)

    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0

    store = MemoryStore(path).load()
    # 每次 save 都在锁内基于最新 revision +1；丢锁时会出现重复 revision。
    assert store.revision == workers * iterations + 1
    assert len(store) == workers * iterations
    assert len({entry.value for entry in store.entries}) == workers * iterations


@pytest.mark.skipif(not FORK_AVAILABLE, reason="需要 fork 做多进程并发测试")
def test_cross_process_dual_write_keeps_markdown_and_sidecar_consistent(tmp_path):
    memory_file = tmp_path / "memory.md"
    memory_manager.write_memory(memory_file, "# 会话记忆\n\n## 并发\n- 初始内容\n")

    workers, iterations = 4, 4
    context = multiprocessing.get_context("fork")
    processes = [
        context.Process(
            target=_dual_write_process, args=((str(memory_file), worker_id, iterations),)
        )
        for worker_id in range(workers)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
        assert process.exitcode == 0

    markdown = memory_file.read_text(encoding="utf-8")
    store = MemoryStore(store_path_for(memory_file)).load()
    # 双写在同一把锁内完成：Markdown 与 sidecar 必须对应同一次写入。
    assert store.markdown_sha256 == hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    assert {entry.key for entry in parse_markdown(markdown)} == {
        entry.key for entry in store.entries
    }
    assert {entry.value for entry in parse_markdown(markdown)} == {
        entry.value for entry in store.entries
    }


def test_structured_memory_switch_still_rolls_back_whole_feature(tmp_path, monkeypatch):
    """回退开关：关闭结构化记忆时只写 Markdown，不产生 sidecar（保持旧行为）。"""
    monkeypatch.setenv(ENV_FLAG, "0")
    memory_file = tmp_path / "memory.md"
    memory_manager.write_memory(memory_file, LEGACY_MD)

    assert memory_file.read_text(encoding="utf-8") == LEGACY_MD
    assert not store_path_for(memory_file).exists()
