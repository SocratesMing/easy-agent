"""会话级进程内状态注册表（从 ``services/agents/manager.py`` 拆出）。

承载 ``SessionState``、``DropScope``、LRU 顺序字典 ``_sessions`` 与实时字段视图，
供 ``manager.py``（Agent 缓存/装配）与 ``stream_tasks.py``（流式任务生命周期）共用。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from easy_agent.agent.core import EasyAgent

#: 技能作用域的「未指定」哨兵：区别于显式传 ``None``（清除白名单、恢复全量可见）。
_SCOPE_UNSPECIFIED = object()


class DropScope(str, Enum):
    """``drop(session_id, scope)`` 的三种清理范围。"""

    AGENT = "agent"
    SESSION = "session"
    USER = "user"


@dataclass
class SessionState:
    """单个会话在本进程内的全部状态（清理范围见 DropScope）。"""

    agent: EasyAgent | None = None
    expert_key: str = ""
    connectors: tuple[str, ...] | None = None
    edit_target: str = ""
    skill_scope: tuple[str, ...] | None | object = _SCOPE_UNSPECIFIED
    system_prompt_extra_fp: str = ""
    turn_skills: list[str] = field(default_factory=list)
    stream_task: asyncio.Task[None] | None = None
    create_lock: asyncio.Lock | None = None


#: session_id -> SessionState。顺序 = 有缓存 Agent 的会话的 LRU 顺序。
_sessions: OrderedDict[str, SessionState] = OrderedDict()
_sessions_getter: Callable[[], OrderedDict[str, SessionState]] = lambda: _sessions


def bind_sessions_getter(
    getter: Callable[[], OrderedDict[str, SessionState]],
) -> None:
    """绑定会话注册表读取器（兼容测试 monkeypatch ``manager._sessions``）。"""
    global _sessions_getter
    _sessions_getter = getter


def sessions_store() -> OrderedDict[str, SessionState]:
    """返回当前生效的会话状态注册表。"""
    return _sessions_getter()


def _reset_session_state() -> None:
    """清空注册表里的全部会话状态（测试与重新初始化专用，生产路径不调用）。"""
    sessions_store().clear()


def _state_for(session_id: str) -> SessionState:
    """取会话状态；不存在时创建（不改变已有会话的 LRU 顺序）。"""
    store = sessions_store()
    state = store.get(session_id)
    if state is None:
        state = SessionState()
        store[session_id] = state
    return state


def _prune_if_empty(session_id: str, state: SessionState) -> None:
    """所有字段都回到默认值时移除注册表项，避免长期累积空状态。"""
    if (
        state.agent is None
        and not state.expert_key
        and state.connectors is None
        and not state.edit_target
        and state.skill_scope is _SCOPE_UNSPECIFIED
        and not state.system_prompt_extra_fp
        and not state.turn_skills
        and state.stream_task is None
        and state.create_lock is None
    ):
        sessions_store().pop(session_id, None)


def _clear_agent_fields(state: SessionState) -> None:
    """清「随 Agent」的字段（AGENT scope 的核心；落库缓存与流任务 / 锁保留）。"""
    state.agent = None
    state.expert_key = ""
    state.edit_target = ""
    state.skill_scope = _SCOPE_UNSPECIFIED
    state.system_prompt_extra_fp = ""
    state.turn_skills = []


def drop(session_id: str, scope: DropScope) -> None:
    """按 scope 清理会话状态。"""
    store = sessions_store()
    state = store.get(session_id)
    if state is None:
        return
    if scope == DropScope.AGENT:
        _clear_agent_fields(state)
    elif scope == DropScope.SESSION:
        _clear_agent_fields(state)
        state.connectors = None
        state.stream_task = None
        state.create_lock = None
        store.pop(session_id, None)
        return
    elif scope == DropScope.USER:
        state.agent = None
        state.expert_key = ""
        state.system_prompt_extra_fp = ""
    else:
        raise ValueError(f"未知的会话状态清理范围: {scope!r}")
    _prune_if_empty(session_id, state)


def _cached_agent_count() -> int:
    """有缓存 Agent 的会话数。"""
    return sum(1 for state in sessions_store().values() if state.agent is not None)


class _SessionFieldView(MutableMapping):
    """``{session_id: SessionState.某个字段}`` 的实时视图。"""

    __slots__ = ("_factory", "_field")

    def __init__(self, field_name: str, factory: Callable[[], Any]) -> None:
        self._field = field_name
        self._factory = factory

    def __getitem__(self, session_id: str) -> Any:
        state = sessions_store().get(session_id)
        if state is None:
            raise KeyError(session_id)
        return getattr(state, self._field)

    def __setitem__(self, session_id: str, value: Any) -> None:
        setattr(_state_for(session_id), self._field, value)

    def __delitem__(self, session_id: str) -> None:
        state = sessions_store().get(session_id)
        if state is None:
            raise KeyError(session_id)
        setattr(state, self._field, self._factory())
        _prune_if_empty(session_id, state)

    def __iter__(self):
        return iter(list(sessions_store()))

    def __len__(self) -> int:
        return len(sessions_store())


#: 工具侧持有的两张实时表（``save_expert`` 的「待修改专家」与「本轮输入的技能」）。
_EDIT_TARGETS_VIEW = _SessionFieldView("edit_target", str)
_TURN_SKILLS_VIEW = _SessionFieldView("turn_skills", list)


def _create_lock(session_id: str) -> asyncio.Lock:
    """取会话创建锁（不存在则创建），防止同一会话并发创建 Agent。"""
    state = _state_for(session_id)
    if state.create_lock is None:
        state.create_lock = asyncio.Lock()
    return state.create_lock


def _touch_agent_cache(session_id: str) -> None:
    """命中 / 新建即移到注册表末尾（LRU）。"""
    store = sessions_store()
    if session_id in store:
        store.move_to_end(session_id)
