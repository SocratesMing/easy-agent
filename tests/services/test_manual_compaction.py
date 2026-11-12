"""真实 DeepAgents 图 + SQLite checkpoint 的手动压缩验收。"""
import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import Field

from easy_agent.agent.manual_compaction import prepare_compaction
from easy_agent.agent.summarization import summarization_settings
from easy_agent.agent.turn_context import TurnAwareSummarizationMiddleware
from easy_agent.db.database import Database
from easy_agent.db.repositories.session_runtime import SessionRuntimeRepository
from easy_agent.services.sessions import compaction
from easy_agent.services.sessions.thread import plan_turn


class Model(BaseChatModel):
    inputs: list = Field(default_factory=list)
    fail: bool = False

    @property
    def _llm_type(self):
        return "manual-compaction-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.inputs.append(messages)
        if self.fail:
            raise RuntimeError("model failed")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="SUMMARY: older facts"))])


def config():
    llm = SimpleNamespace(context_length=1000, provider="openai", retry=None, api_key="test", api_base=None, model="test", protocol="openai", headers={})
    return SimpleNamespace(agent=SimpleNamespace(session_thread=True), llm=llm, models={"small": llm},
                           summarization=SimpleNamespace(enabled=False, compression_threshold=.8, compression_target=.1))


def agent(saver, tmp_path, model=None):
    model = model or Model()
    cfg = config()
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    graph = create_deep_agent(model=model, backend=backend, checkpointer=saver,
                             middleware=[TurnAwareSummarizationMiddleware(model, backend=backend, **summarization_settings(cfg, "small"))])
    return SimpleNamespace(agent=graph, model=model, model_name="small", config=cfg, system_prompt="base",
                           _build_backend=lambda paths: backend)


def history():
    return [row for i in range(8) for row in (
        {"role": "user", "content": f"question {i} " + "old facts " * 150},
        {"role": "assistant", "content": "answer " * 50})]


def test_manual_context_preserves_and_counts_history_reasoning(tmp_path):
    from easy_agent.agent.manual_compaction import _context_view, estimate_context
    from easy_agent.agent.token_counting import count_context_tokens

    a = agent(None, tmp_path)
    answer = AIMessage("answer", additional_kwargs={"reasoning_content": "想" * 4000})
    messages = [HumanMessage("question"), answer]
    _, middleware, effective = _context_view(a, messages, {})

    assert effective[-1] is answer
    assert middleware.token_counter(effective) == count_context_tokens(messages)
    plain = [messages[0], AIMessage("answer")]
    assert estimate_context(a, messages, {})["before_tokens"] - estimate_context(a, plain, {})["before_tokens"] == 4000
    assert messages[-1].additional_kwargs["reasoning_content"] == "想" * 4000


def runtime(tmp_path):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "business.db")}})
    db.init_tables()
    rt = SessionRuntimeRepository(db)
    now = datetime.now(UTC)
    rt.try_acquire_session_stream("s1", "operation", (now + timedelta(seconds=60)).isoformat(), now.isoformat())
    return rt


def repo(rows):
    return SimpleNamespace(get_owned_session=lambda sid, username: SimpleNamespace(
        session_id=sid, username=username, workspace_name="test", messages=rows))


@pytest.mark.parametrize("failure", ["version", "missing_package", "missing_method"])
async def test_incompatible_adapter_rejects_before_backend(tmp_path, monkeypatch, failure):
    from importlib.metadata import PackageNotFoundError
    from easy_agent.agent import manual_compaction

    a = agent(None, tmp_path)
    a._build_backend = lambda paths: pytest.fail("incompatible adapter accessed backend")
    if failure == "missing_method":
        monkeypatch.setattr(manual_compaction.SummarizationMiddleware, "_acreate_summary", None)
    else:
        def installed(name):
            if failure == "missing_package":
                raise PackageNotFoundError(name)
            return "unsupported"
        monkeypatch.setattr(manual_compaction, "version", installed)
    with pytest.raises(RuntimeError, match="不支持手动压缩"):
        await prepare_compaction(a, [HumanMessage("old")], {})
    with pytest.raises(RuntimeError, match="不支持手动压缩"):
        manual_compaction.estimate_context(a, [], {})
    assert not a.model.inputs


def test_install_contract_matches_verified_adapter():
    from importlib.metadata import requires
    import tomllib
    from pathlib import Path
    from packaging.requirements import Requirement
    from easy_agent.agent.manual_compaction import VERIFIED_VERSIONS, compatibility_reason

    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    requirements = {r.name: r for r in map(Requirement, project["project"]["dependencies"])}
    installed_metadata = {r.name: r for r in map(Requirement, requires("easy-agent"))}
    locked = {p["name"]: p["version"] for p in tomllib.loads((root / "uv.lock").read_text())["package"]}
    for name, expected in VERIFIED_VERSIONS.items():
        assert str(requirements[name].specifier) == f"=={expected}"
        assert str(installed_metadata[name].specifier) == f"=={expected}"
        assert locked[name] == expected
    assert compatibility_reason() is None


async def test_real_graph_restart_next_model_and_idempotency(tmp_path):
    rows = history()
    rt = runtime(tmp_path)
    path = str(tmp_path / "checkpoint.db")
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        a = agent(saver, tmp_path)
        session = repo(rows).get_owned_session("s1", "tester")
        result = await compaction._prepare_and_commit(repo(rows), rt, a, session, "operation", "request-1")
        assert result["status"] == "compacted"
        assert len(a.model.inputs) == 1  # 仅摘要，不调用主模型回答
        state = await a.agent.aget_state({"configurable": {"thread_id": "s1"}})
        assert len(state.values["messages"]) == len(rows)
        assert not state.next and not state.tasks
        assert not state.values.get("turn_context")
        again = await compaction._prepare_and_commit(repo(rows), rt, a, session, "operation", "request-1")
        assert again == result and len(a.model.inputs) == 1
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        a = agent(saver, tmp_path)
        plan = await plan_turn(a, session_id="s1", turn_id="next", history=rows, raw="new question")
        assert plan.mode == "continue" and len(plan.messages) == 1
        await a.agent.ainvoke({"messages": plan.messages}, {"configurable": {"thread_id": "s1"}})
        sent = "\n".join(str(m.content) for m in a.model.inputs[-1])
        assert "SUMMARY: older facts" in sent and "new question" in sent
        assert "question 0" not in sent
        assert rows == history()  # UI 原始记录完全保留


async def test_failure_does_not_replace_mismatched_checkpoint(tmp_path):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "c.db")) as saver:
        a = agent(saver, tmp_path)
        await a.agent.ainvoke({"messages": [HumanMessage(content="original", id="unmatched")]}, {"configurable": {"thread_id": "s1"}})
        before = await a.agent.aget_state({"configurable": {"thread_id": "s1"}})
        a.model.fail = True
        rows = history()
        with pytest.raises(RuntimeError):
            await compaction._prepare_and_commit(repo(rows), runtime(tmp_path), a, repo(rows).get_owned_session("s1", "tester"), "operation", "request-2")
        after = await a.agent.aget_state({"configurable": {"thread_id": "s1"}})
        assert after.values == before.values


@pytest.mark.parametrize('cancelled', [False, True])
async def test_compact_after_failed_generation_uses_summary_on_next_turn(tmp_path, monkeypatch, cancelled):
    from easy_agent.services.streaming.messages import stable_history

    rows = stable_history(history())
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'c.db')) as saver:
        a = agent(saver, tmp_path)
        cfg = {'configurable': {'thread_id': 's1'}}
        a.model.fail = not cancelled
        plan = await plan_turn(a, session_id='s1', turn_id='failed-turn', history=rows, raw='cancelled question')
        if cancelled:
            started = asyncio.Event()
            async def blocked(*args, **kwargs):
                started.set()
                await asyncio.Future()
            with monkeypatch.context() as patch:
                patch.setattr(Model, 'ainvoke', blocked)
                generation = asyncio.create_task(a.agent.ainvoke({'messages': plan.messages}, cfg))
                await asyncio.wait_for(started.wait(), 5)
                generation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await generation
        else:
            with pytest.raises(RuntimeError, match='model failed'):
                await a.agent.ainvoke({'messages': plan.messages}, cfg)
        before = await a.agent.aget_state(cfg)
        assert before.next == ('model',)
        if not cancelled:
            assert before.tasks[0].error
        a.model.fail = False
        rows.append({'role': 'user', 'turn_id': 'failed-turn', 'content': 'cancelled question'})
        result = await compaction._prepare_and_commit(repo(rows), runtime(tmp_path), a,
                                                     repo(rows).get_owned_session('s1', 'tester'), 'operation', 'after-failure')
        assert result['status'] == 'compacted'
        saved = await a.agent.aget_state(cfg)
        assert not saved.next and not saved.tasks
        assert len(saved.values['messages']) == len(before.values['messages'])
        plan = await plan_turn(a, session_id='s1', turn_id='next', history=rows, raw='next question')
        await a.agent.ainvoke({'messages': plan.messages}, cfg)
        sent = '\n'.join(str(m.content) for m in a.model.inputs[-1])
        assert 'SUMMARY: older facts' in sent and 'next question' in sent
        assert 'question 0' not in sent


async def test_pending_graph_interrupt_is_not_abandoned_by_compaction(tmp_path):
    from fastapi import HTTPException
    from langgraph.types import interrupt

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'c.db')) as saver:
        a = agent(saver, tmp_path)
        # 真实图中断；DB 即使尚未写入 pending_approval 也必须保护。
        def approval(messages, *args, **kwargs):
            interrupt({'action_requests': [{'name': 'execute', 'args': {'command': 'rm file'}}]})
        a.model._generate = approval
        await a.agent.ainvoke({'messages': [HumanMessage(content='approval', id='approval')]},
                             {'configurable': {'thread_id': 's1'}})
        before = await a.agent.aget_state({'configurable': {'thread_id': 's1'}})
        with pytest.raises(HTTPException) as error:
            await compaction._prepare_and_commit(repo(history()), runtime(tmp_path), a,
                                                repo(history()).get_owned_session('s1', 'tester'), 'operation', 'blocked')
        assert error.value.status_code == 409
        after = await a.agent.aget_state({'configurable': {'thread_id': 's1'}})
        assert after == before


async def test_long_tool_chain_can_be_summarized_with_bounded_input(tmp_path):
    from easy_agent.agent.token_counting import count_context_tokens

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'c.db')) as saver:
        a = agent(saver, tmp_path)
        messages = [HumanMessage(content='ORIGINAL GOAL: build the requested document')]
        for i in range(5):
            messages.extend([
                AIMessage(content='', tool_calls=[{'name': 'read_file', 'args': {'path': str(i)}, 'id': f't{i}'}]),
                ToolMessage(content='工具返回的大段内容 ' * 900 + f' latest result {i}', tool_call_id=f't{i}'),
            ])
        messages.append(HumanMessage(content='recent question'))
        result = await prepare_compaction(a, messages, {})
        assert result and result['after_tokens'] < result['before_tokens']
        assert len(a.model.inputs) == 1
        sent = a.model.inputs[0]
        assert 'ORIGINAL GOAL' in sent[0].content and 'latest result 4' in sent[0].content
        # 摘要提示本身有额外开销；对话预算仍是配置的 4000 tokens。
        assert count_context_tokens(sent) < 6000


async def test_skip_and_lease_fence(tmp_path):
    rt = runtime(tmp_path)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "c.db")) as saver:
        a = agent(saver, tmp_path)
        rows = [{"role": "user", "content": "hello"}]
        session = repo(rows).get_owned_session("s1", "tester")
        result = await compaction._prepare_and_commit(repo(rows), rt, a, session, "operation", "request-3")
        assert result["status"] == "skipped" and not a.model.inputs
        assert result['compaction']['after_tokens'] > 0
        assert result['compaction']['before_tokens'] == result['compaction']['after_tokens']
        assert result['compaction']['context_length'] == 1000
        with pytest.raises(RuntimeError, match="租约"):
            rt.guarded_commit("s1", "wrong-owner", lambda: pytest.fail("must not commit"))
        assert not rt.release_session_stream("s1", "wrong-owner", datetime.now(UTC).isoformat())
        assert rt.get_session_runtime("s1")["stream_owner"] == "operation"


async def test_skip_after_local_reply_reports_compacted_context_not_old_provider_usage(tmp_path):
    rows = history()
    rt = runtime(tmp_path)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'c.db')) as saver:
        a = agent(saver, tmp_path)
        # 留出近期回复预算，使本地短回复后没有新增的可移出历史。
        a.config.summarization.compression_target = .5
        session = repo(rows).get_owned_session('s1', 'tester')
        first = await compaction._prepare_and_commit(repo(rows), rt, a, session, 'operation', 'first')
        before = await a.agent.aget_state({'configurable': {'thread_id': 's1'}})
        rows.extend([
            {'role': 'user', 'content': '你好', 'turn_id': 'local-turn'},
            {'role': 'assistant', 'content': '请提出相关问题', 'usage': {'input_tokens': 0, 'output_tokens': 0}},
        ])
        result = await compaction._prepare_and_commit(repo(rows), rt, a, session, 'operation', 'local-skip')
        assert result['status'] == 'skipped'
        record = result['compaction']
        assert record['anchor_message_count'] == len(rows)
        assert 0 < record['after_tokens'] < first['compaction']['before_tokens'] / 5
        assert len(a.model.inputs) == 1
        after = await a.agent.aget_state({'configurable': {'thread_id': 's1'}})
        assert after.values['_summarization_event'] == before.values['_summarization_event']


async def test_archive_failure_and_manual_when_auto_disabled(tmp_path, monkeypatch):
    from deepagents.middleware.summarization import SummarizationMiddleware
    cfg = config()
    assert summarization_settings(cfg, "small")["keep"][1] > 1000
    assert summarization_settings(cfg, "small", manual=True)["keep"] == ("tokens", 100)
    async def failed(*args):
        return None
    monkeypatch.setattr(SummarizationMiddleware, "_aoffload_to_backend", failed)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "c.db")) as saver:
        a = agent(saver, tmp_path)
        plan = await plan_turn(a, session_id="s1", turn_id="x", history=history(), raw="")
        with pytest.raises(RuntimeError, match="归档失败"):
            await prepare_compaction(a, plan.messages[:-1], {})
        assert not (await a.agent.aget_state({"configurable": {"thread_id": "s1"}})).values


async def test_catch_up_partial_reply_and_repeat_compaction(tmp_path):
    from easy_agent.agent.manual_compaction import terminal_update_node
    from easy_agent.services.streaming.messages import stable_history
    from easy_agent.services.sessions.thread import INTERRUPTED_SUFFIX

    rows = stable_history(history())
    rt = runtime(tmp_path)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'c.db')) as saver:
        a = agent(saver, tmp_path)
        cfg = {'configurable': {'thread_id': 's1'}}
        # checkpoint 只同步了第一轮和半截回复，DB 已有其余七轮。
        await a.agent.aupdate_state(cfg, {'messages': [
            HumanMessage(content=rows[0]['content'], id=rows[0]['turn_id']),
            AIMessage(content='answer '),
        ]}, as_node=terminal_update_node(a.agent))
        session = repo(rows).get_owned_session('s1', 'tester')
        await compaction._prepare_and_commit(repo(rows), rt, a, session, 'operation', 'catch-up')
        state = await a.agent.aget_state(cfg)
        assert any(INTERRUPTED_SUFFIX in str(m.content) for m in state.values['messages'])
        assert len([m for m in state.values['messages'] if isinstance(m, HumanMessage)]) == 8
        first_cutoff = state.values['_summarization_event']['cutoff_index']
        rows.extend([{'role': 'user', 'content': 'new facts ' * 700, 'turn_id': 'new-turn'},
                     {'role': 'assistant', 'content': 'new answer ' * 100}])
        result = await compaction._prepare_and_commit(repo(rows), rt, a, session, 'operation', 'repeat-id')
        assert result['status'] == 'compacted'
        state = await a.agent.aget_state(cfg)
        assert state.values['_summarization_event']['cutoff_index'] > first_cutoff
        assert len(state.values['context_compactions']) == 2
        assert (await plan_turn(a, session_id='s1', turn_id='next', history=rows, raw='next')).mode == 'continue'


async def test_successful_mismatch_replaces_only_at_commit(tmp_path):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'c.db')) as saver:
        a = agent(saver, tmp_path)
        cfg = {'configurable': {'thread_id': 's1'}}
        await a.agent.ainvoke({'messages': [HumanMessage(content='unrelated', id='wrong')]}, cfg)
        rows = history()
        await compaction._prepare_and_commit(repo(rows), runtime(tmp_path), a,
                                            repo(rows).get_owned_session('s1', 'tester'), 'operation', 'replace-id')
        state = await a.agent.aget_state(cfg)
        assert len(state.values['messages']) == len(rows)
        assert not any(m.id == 'wrong' for m in state.values['messages'])
        assert (await plan_turn(a, session_id='s1', turn_id='next', history=rows, raw='next')).mode == 'continue'


async def test_same_database_rejected_and_expired_cancelled_leases_fenced(tmp_path):
    from fastapi import HTTPException
    rt = runtime(tmp_path)
    async with AsyncSqliteSaver.from_conn_string(rt._db.db_path) as saver:
        a = agent(saver, tmp_path)
        with pytest.raises(HTTPException) as error:
            await compaction._prepare_and_commit(repo(history()), rt, a,
                                                repo(history()).get_owned_session('s1', 'tester'), 'operation', 'same-db')
        assert error.value.status_code == 409
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    rt.upsert_session_runtime('s1', stream_until=expired)
    assert not rt.renew_stream_lease('s1', 'operation')
    with pytest.raises(RuntimeError, match='租约'):
        rt.guarded_commit('s1', 'operation', lambda: pytest.fail('expired commit'))
    now = datetime.now(UTC)
    assert rt.try_acquire_session_stream('s1', 'new-operation', (now + timedelta(minutes=1)).isoformat(), now.isoformat())[0]
    assert not rt.release_session_stream('s1', 'operation', now.isoformat())
    rt.upsert_session_runtime('s1', cancel_requested=True)
    with pytest.raises(RuntimeError, match='取消'):
        rt.guarded_commit('s1', 'new-operation', lambda: pytest.fail('cancelled commit'))


async def test_duplicate_requests_share_work_and_survive_disconnected_waiter(tmp_path, monkeypatch):
    import asyncio
    rows = history()
    calls = []
    started, finish, second_lookup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def execute(*args):
        calls.append(args)
        started.set()
        await finish.wait()
        return {'status': 'compacted'}
    async def no_records(sid):
        if calls:
            second_lookup.set()
        return []
    monkeypatch.setattr(compaction, '_execute', execute)
    monkeypatch.setattr(compaction, 'records', no_records)
    first = asyncio.create_task(compaction.compact(repo(rows), 's1', 'tester', 'small', 'same-request'))
    await started.wait()
    second = asyncio.create_task(compaction.compact(repo(rows), 's1', 'tester', 'small', 'same-request'))
    await second_lookup.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    finish.set()
    assert (await second)['status'] == 'compacted'
    assert len(calls) == 1


async def test_duplicate_requests_share_identity_lookup_before_model_work(monkeypatch):
    from easy_agent.utils.usage_context import UsageIdentity

    identity_started, release_identity = asyncio.Event(), asyncio.Event()
    second_lookup, execution_started, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    identity_calls, executions, lookups = [], [], []

    async def identity(username, session_id):
        identity_calls.append((username, session_id))
        identity_started.set()
        await release_identity.wait()
        return UsageIdentity("real-id", session_id)

    async def no_records(session_id):
        lookups.append(session_id)
        if len(lookups) == 2:
            second_lookup.set()
        return []

    async def execute(*args):
        executions.append(args)
        execution_started.set()
        await finish.wait()
        return {"status": "compacted"}

    monkeypatch.setattr(compaction, "resolve_usage_identity", identity)
    monkeypatch.setattr(compaction, "records", no_records)
    monkeypatch.setattr(compaction, "_execute", execute)
    first = asyncio.create_task(compaction.compact(repo(history()), "s1", "tester", "small", "identity-wait"))
    second = None
    try:
        await asyncio.wait_for(identity_started.wait(), 5)
        second = asyncio.create_task(compaction.compact(repo(history()), "s1", "tester", "small", "identity-wait"))
        await asyncio.wait_for(second_lookup.wait(), 5)
        assert identity_calls == [("tester", "s1")]
        release_identity.set()
        await asyncio.wait_for(execution_started.wait(), 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        finish.set()
        assert (await asyncio.wait_for(second, 5))["status"] == "compacted"
        assert len(executions) == 1
        assert executions[0][-1] == UsageIdentity("real-id", "s1")
    finally:
        release_identity.set()
        finish.set()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)


@pytest.mark.parametrize('failure', ['media', 'sentinel', 'write'])
async def test_prepare_or_write_failures_leave_checkpoint_unchanged(tmp_path, monkeypatch, failure):
    from deepagents.middleware.summarization import SummarizationMiddleware
    from easy_agent.agent.manual_compaction import terminal_update_node

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'c.db')) as saver:
        a = agent(saver, tmp_path)
        cfg = {'configurable': {'thread_id': 's1'}}
        await a.agent.aupdate_state(cfg, {'messages': [HumanMessage(content='old', id='old')]},
                                    as_node=terminal_update_node(a.agent))
        before = await a.agent.aget_state(cfg)
        async def failed(*args, **kwargs):
            if failure == 'media':
                return [], True
            if failure == 'sentinel':
                return 'Previous conversation was too long to summarize.'
            raise OSError('checkpoint write failed')
        if failure == 'write':
            monkeypatch.setattr(a.agent, 'aupdate_state', failed)
        else:
            monkeypatch.setattr(SummarizationMiddleware,
                                '_aoffload_inline_media' if failure == 'media' else '_acreate_summary', failed)
        rows = history()
        with pytest.raises((RuntimeError, OSError)):
            await compaction._prepare_and_commit(repo(rows), runtime(tmp_path), a,
                                                repo(rows).get_owned_session('s1', 'tester'), 'operation', failure)
        assert (await a.agent.aget_state(cfg)).values == before.values


async def test_committed_result_recovers_after_read_failure(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'c.db')) as saver:
        a = agent(saver, tmp_path)
        original = a.agent.aget_state
        calls = 0
        async def fail_post_write(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:  # initial read, plan_turn read, then commit validation
                raise OSError('read interrupted after save')
            return await original(*args, **kwargs)
        monkeypatch.setattr(a.agent, 'aget_state', fail_post_write)
        monkeypatch.setattr(compaction, 'sqlite_checkpointer_active', lambda: True)
        monkeypatch.setattr(compaction, 'detect_worker_count', lambda: 1)
        monkeypatch.setattr(compaction, 'get_session_checkpointer', lambda sid: saver)
        rows = history()
        rt = runtime(tmp_path)
        session = repo(rows).get_owned_session('s1', 'tester')
        with pytest.raises(OSError, match='read interrupted'):
            await compaction._prepare_and_commit(repo(rows), rt, a, session, 'operation', 'unknown-outcome')
        saved = await compaction.records('s1')
        assert saved[0]['id'] == 'unknown-outcome' and saved[0]['status'] == 'compacted'
        recovered = await compaction._prepare_and_commit(repo(rows), rt, a, session, 'operation', 'unknown-outcome')
        assert recovered['compaction'] == saved[0] and len(a.model.inputs) == 1
