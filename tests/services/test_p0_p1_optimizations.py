"""回归测试：P0 / P1 架构与热路径优化。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from easy_agent.api import chat as chat_api
from easy_agent.api import sessions as sessions_api
from easy_agent.db.repositories.stream_events import (
    StreamEventRepository,
    append_stream_events_batch,
    list_stream_events,
)
from easy_agent.services import scheduler as scheduler_service
from easy_agent.services import usage as usage_service
from easy_agent.services.agents import chat_turn
from easy_agent.services.agents import manager as agent_manager
from easy_agent.utils.prompt_probe import prompt_fingerprint
from easy_agent.services.mcp import tools as mcp_tools
from easy_agent.services.streaming.events import StreamEventWriter
from easy_agent.services.streaming.sse import format_sse, parse_sse_frame


def test_invalidate_mcp_cache_for_user_preserves_global(monkeypatch, tmp_path):
    """单用户刷新 MCP 缓存不得清空全局缓存、全局客户端或关闭全局持久会话。"""
    user_path = tmp_path / "workspace" / "alice" / "mcp.json"
    global_path = tmp_path / "global_mcp.json"
    closed_scopes: list[list[str]] = []

    monkeypatch.setattr(mcp_tools.Config, "get_user_mcp_path", lambda u, c=None: user_path)
    monkeypatch.setattr(mcp_tools, "_find_mcp_config", lambda: global_path)
    monkeypatch.setattr(
        mcp_tools.mcp_sessions,
        "close_mcp_scopes_sync",
        lambda scopes: closed_scopes.append(list(scopes)),
    )
    monkeypatch.setattr(mcp_tools, "_mcp_tools_cache", {
        str(user_path): (1.0, {"u_srv": []}),
        str(global_path): (1.0, {"g_srv": []}),
    })
    monkeypatch.setattr(mcp_tools, "_mcp_clients", {
        "user-client-key": object(),
        "global-client-key": object(),
    })
    monkeypatch.setattr(mcp_tools, "_mcp_client_scopes", {
        str(user_path): {"user-client-key"},
        str(global_path): {"global-client-key"},
    })

    mcp_tools.invalidate_mcp_cache("alice")

    assert str(user_path) not in mcp_tools._mcp_tools_cache
    assert str(global_path) in mcp_tools._mcp_tools_cache
    assert "user-client-key" not in mcp_tools._mcp_clients
    assert "global-client-key" in mcp_tools._mcp_clients
    assert closed_scopes == [[str(user_path)]]


@pytest.mark.asyncio
async def test_stream_events_batch_insert_and_writer(db):
    """StreamEventWriter 使用批量事务写入并保持事件顺序。"""
    repo = StreamEventRepository(db)

    frames = [format_sse({"type": "content", "content": f"chunk-{i}"}) for i in range(5)]
    count = append_stream_events_batch(db, "s-batch", frames)
    assert count == 5
    rows = list_stream_events(db, "s-batch")
    assert [r["payload"] for r in rows] == frames

    writer = StreamEventWriter("s-writer", repository=repo)
    for f in frames:
        writer.append(f)
    await writer.aclose()
    rows2 = repo.list_stream_events("s-writer")
    assert [r["payload"] for r in rows2] == frames


@pytest.mark.asyncio
async def test_with_session_token_totals_skips_parsing_text_deltas(monkeypatch):
    """高频 text_delta 帧不触发 parse_sse_frame JSON 解析，token_usage/done 仍正常改写。"""
    parsed_frames: list[str] = []
    real_parse = usage_service.parse_sse_frame

    def spy_parse(frame: str):
        parsed_frames.append(frame)
        return real_parse(frame)

    monkeypatch.setattr(usage_service, "parse_sse_frame", spy_parse)

    async def fake_totals(identity):
        return {"input_tokens": 120, "output_tokens": 45}

    monkeypatch.setattr(usage_service, "_session_token_totals", fake_totals)

    async def raw_stream():
        yield format_sse({"type": "text_delta", "content": "hello"})
        yield format_sse({"type": "thinking_delta", "content": "hmm"})
        yield format_sse({"type": "token_usage", "input_tokens": 10, "output_tokens": 5})
        yield format_sse({"type": "done", "usage": {"input_tokens": 10, "output_tokens": 5}})

    out = [
        frame
        async for frame in usage_service.with_session_token_totals(
            raw_stream(), identity=SimpleNamespace(user_id="u1", session_id="s1")
        )
    ]

    assert len(parsed_frames) == 2
    assert parse_sse_frame(out[2])["input_tokens"] == 120
    assert parse_sse_frame(out[3])["usage"]["output_tokens"] == 45


def test_delete_session_uploads_handles_store_uri_and_local_file(monkeypatch, tmp_path):
    """删会话上传文件同时支持 store:// 对象存储 URI 与本地文件。"""
    local_file = tmp_path / "local.txt"
    local_file.write_text("hi", encoding="utf-8")
    deleted_store_uris: list[tuple[str, str]] = []

    monkeypatch.setattr(
        sessions_api,
        "delete_stored_upload",
        lambda uri, username="": deleted_store_uris.append((uri, username)) or True,
    )

    class _FakeRepo:
        def get_session_files(self, session_id: str):
            return [
                {"file_path": "store://uploads/alice/f1_doc.pdf"},
                {"file_path": str(local_file)},
            ]

    sessions_api._delete_session_uploads(_FakeRepo(), "s1", "alice")

    assert deleted_store_uris == [("store://uploads/alice/f1_doc.pdf", "alice")]
    assert not local_file.exists()


def test_recover_resume_expert_id_from_agent_runs():
    """跨 Pod 恢复时能从 agent_runs.request_json 恢复 expert_agent_id。"""
    class _FakeRuns:
        def get_run(self, run_id: str):
            assert run_id == "sess-1:msg-1"
            return {"request_json": json.dumps({"expert_agent_id": "exp-42"})}

    repo = SimpleNamespace(runs=_FakeRuns())
    assert chat_turn._recover_resume_expert_id(repo, "sess-1", "msg-1") == "exp-42"


def test_try_reuse_cached_agent_evicts_on_system_prompt_extra_change():
    """普通轮次 system_prompt_extra 变化时驱逐旧缓存；HITL 恢复（approval_mode=None）保留缓存。"""
    cached = SimpleNamespace(
        model_name="m1",
        enable_web_search=False,
        approval_mode="manual",
        skill_whitelist=None,
        enable_user_interaction=True,
        system_prompt="base + old",
    )
    state = agent_manager.SessionState(
        agent=cached,
        expert_key="exp-1",
        system_prompt_extra_fp=prompt_fingerprint("old prompt"),
    )

    # HITL 恢复：approval_mode=None，不因 system_prompt_extra="" 驱逐
    reused = agent_manager._try_reuse_cached_agent(
        state,
        "s1",
        cached=cached,
        effective_model="m1",
        enable_user_interaction=True,
        web_search_enabled=False,
        enable_web_search=None,
        expert_key="exp-1",
        current_scope=None,
        only_connectors=None,
        system_prompt_extra="",
        approval_mode=None,
    )
    assert reused is cached

    # 普通轮次：专家提示词更新，必须驱逐旧缓存
    reused2 = agent_manager._try_reuse_cached_agent(
        state,
        "s1",
        cached=cached,
        effective_model="m1",
        enable_user_interaction=True,
        web_search_enabled=False,
        enable_web_search=False,
        expert_key="exp-1",
        current_scope=None,
        only_connectors=None,
        system_prompt_extra="new prompt",
        approval_mode="manual",
    )
    assert reused2 is None
    assert state.agent is None


@pytest.mark.asyncio
async def test_stream_heartbeat_detects_cross_pod_cancel_without_lease_lost(monkeypatch):
    """跨副本 cancel_requested=1 在短轮询内取消 owner_task 且不触发 lease_lost。"""
    monkeypatch.setattr(chat_api, "_STREAM_HEARTBEAT_SECONDS", 10.0)
    monkeypatch.setattr(chat_api, "_CANCEL_POLL_SECONDS", 0.02)

    lease_lost = asyncio.Event()
    checks = {"n": 0}

    def fake_check_cancel(session_id: str) -> bool:
        checks["n"] += 1
        return checks["n"] >= 2

    async def worker():
        await asyncio.sleep(10)

    owner = asyncio.create_task(worker())
    hb = asyncio.create_task(
        chat_api._stream_heartbeat(
            "s-cancel", "ancel", None, None, None, owner, lease_lost,
            check_cancel=fake_check_cancel,
        )
    )
    with pytest.raises(asyncio.CancelledError):
        await owner
    await hb
    assert not lease_lost.is_set()
    assert checks["n"] >= 2


def test_scheduler_extracts_multimodal_content_and_execution_trace():
    """定时任务兼容结构化多模态内容块并提取思考与工具调用轨迹。"""
    msgs = [
        SimpleNamespace(
            type="ai",
            content="",
            additional_kwargs={"reasoning_content": "先查数据"},
            tool_calls=[{"id": "tc-1", "name": "web_search", "args": {"q": "news"}}],
        ),
        SimpleNamespace(
            type="tool",
            tool_call_id="tc-1",
            content=[{"type": "text", "text": "结果 A"}],
            status="success",
        ),
        SimpleNamespace(
            type="ai",
            content=[{"type": "text", "text": "最终汇总"}],
            additional_kwargs={"reasoning_content": "整理结论"},
            tool_calls=[],
        ),
    ]

    assert scheduler_service._extract_final_content(msgs) == "最终汇总"
    thinking, tool_calls = scheduler_service._extract_execution_trace(msgs)
    assert thinking == "先查数据\n\n整理结论"
    assert len(tool_calls) == 1
    assert tool_calls[0]["name"] == "web_search"
    assert tool_calls[0]["result"] == "结果 A"
    assert tool_calls[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_prune_orphans_mysql_falls_back_when_separate_checkpoint_db(monkeypatch):
    """独立 checkpoint 分库无 sessions 表时，_prune_orphans_mysql 自动回退到应用层对账。"""
    from easy_agent.persistence import checkpoints as cp_mod

    deleted: list[str] = []

    class _FakeMysqlSaver:
        async def list_orphan_threads(self, *, limit: int = 500):
            raise RuntimeError("Table 'cp_db.sessions' doesn't exist")

        async def list_distinct_session_threads(self, *, limit: int = 500):
            return [("alive-1", "alive-1"), ("gone-1", "gone-1"), ("gone-1", "gone-1-m1")]

        async def adelete_thread(self, thread_id: str):
            deleted.append(thread_id)

    monkeypatch.setattr(cp_mod, "_mysql_saver", _FakeMysqlSaver())
    monkeypatch.setattr(
        cp_mod.sessions_repo,
        "existing_session_ids",
        lambda db, sids: {"alive-1"},
    )

    count = await cp_mod._prune_orphans_mysql(db=object())
    assert count == 2
    assert deleted == ["gone-1", "gone-1-m1"]


def test_resolve_username_from_request_prefers_bearer_then_query(monkeypatch):
    """resolve_username_from_request 优先校验 Bearer Header，缺失或无效时回退 query token。"""
    from easy_agent.middleware import auth as auth_mod

    monkeypatch.setattr(
        auth_mod,
        "verify_token_sso",
        lambda tok, db, req=None: {"good-hdr": "alice", "good-q": "bob"}.get(tok),
    )

    req_hdr = SimpleNamespace(headers={"Authorization": "Bearer good-hdr"})
    assert auth_mod.resolve_username_from_request(req_hdr, object(), token="good-q") == "alice"

    req_q = SimpleNamespace(headers={})
    assert auth_mod.resolve_username_from_request(req_q, object(), token="good-q") == "bob"

    req_none = SimpleNamespace(headers={"Authorization": "Bearer bad"})
    assert auth_mod.resolve_username_from_request(req_none, object(), token="bad") is None

