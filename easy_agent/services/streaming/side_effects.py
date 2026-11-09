"""流式收尾副作用（C2 拆分第二步）：工作区重命名 / 技能入库 / 记忆更新。

这些动作会触碰文件系统、Agent 缓存与后台线程，和「纯投影」「DB 持久化」分开，
便于单测时按需打桩（golden 测试通常只关心事件顺序与落库载荷）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from typing import cast

from easy_agent.db.database import Database
from easy_agent.db.repositories.file_repository import FileRepository
from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.utils.blocking import run_blocking
from easy_agent.services.memory.digest import clip_middle, describe_turn_actions
from easy_agent.services.memory.manager import update_long_term_memory_after_session
from easy_agent.services.memory.triggers import (
    LONG_TERM_BATCHER,
    SESSION_BATCHER,
    has_long_term_signal,
    has_session_signal,
)
from easy_agent.services.streaming.messages import tool_call_records_to_dicts
from easy_agent.services.agents import manager as agent_manager
from easy_agent.services.skills import (
    categories as skill_categories,
    library as skill_library,
)

logger = logging.getLogger("easy_agent.chat_service")


def _file_port(db):
    """文件域数据端口：``FileRepository``、``Database`` 连接或测试替身。

    T3c 起 orchestrator 以 ``db=`` 传入的 ``Database`` 在这里被包成领域
    repository（它仍留在流式引擎边界，见 T3 遗留清单）；测试替身直接实现
    同名方法时按 duck typing 使用，行为不变。
    """
    if isinstance(db, FileRepository):
        return db
    if isinstance(db, Database):
        return FileRepository(db)
    return db


def _session_port(db):
    """会话域数据端口：``SessionRepository``、``Database`` 连接或测试替身。"""
    if isinstance(db, SessionRepository):
        return db
    if isinstance(db, Database):
        return SessionRepository(db)
    return db

# 记忆更新会同步调用 LLM（每次 1~2 次），不能占用默认 executor（默认线程池
# 同时服务文件解析/终端 select 等，慢 LLM 调用会拖住其它 to_thread 任务）。
_MEMORY_UPDATE_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="memory-update"
)

#: 每份记忆最后一个已提交的更新任务（键见 ``submit_memory_updates``）。
_MEMORY_JOB_TAILS: dict[str, Future] = {}
_MEMORY_JOB_TAILS_LOCK = threading.Lock()


def _submit_memory_job(key: str, job: Callable[[], None]) -> Future:
    """提交一次记忆更新；同一份记忆的更新按提交顺序一个接一个执行。

    记忆更新是「读 → 调模型（几秒，慢时几十秒）→ 写」。同一份记忆的两次更新并行时，
    后写的会覆盖先写的：同一会话连续两轮、同一用户的两个会话前后脚结束都会撞上；
    而且后一轮的内容可能被前一轮的旧结论盖回去。这里把同一份记忆的任务串成链：上一个
    结束后才把下一个交给线程池，等待期间不占线程池的线程。跨进程的覆盖由写回前的
    内容核对兜底（``services/memory/manager.py``）。
    """
    done: Future = Future()

    def _run() -> None:
        try:
            job()
        except Exception as exc:  # noqa: BLE001 - 任务自己会记日志，这里只保证链不断
            logger.warning(f"记忆更新任务异常: {exc}")
        finally:
            with _MEMORY_JOB_TAILS_LOCK:
                if _MEMORY_JOB_TAILS.get(key) is done:
                    del _MEMORY_JOB_TAILS[key]
            done.set_result(None)

    def _start(_previous: Future | None = None) -> None:
        try:
            _MEMORY_UPDATE_EXECUTOR.submit(_run)
        except RuntimeError as exc:  # 进程退出时线程池已关闭：放弃本次更新，链照常结束
            logger.warning(f"记忆更新未提交（线程池已关闭）: {exc}")
            with _MEMORY_JOB_TAILS_LOCK:
                if _MEMORY_JOB_TAILS.get(key) is done:
                    del _MEMORY_JOB_TAILS[key]
            done.set_result(None)

    with _MEMORY_JOB_TAILS_LOCK:
        previous = _MEMORY_JOB_TAILS.get(key)
        _MEMORY_JOB_TAILS[key] = done
    if previous is None:
        _start()
    else:
        previous.add_done_callback(_start)
    return done


async def maybe_rename_workspace(agent, db, session_id, tool_call_records, sid):
    """首轮对话完成后（含文件写入时）将工作区从 session_id 重命名为时间戳目录。

    主生成器在遇到 HITL 中断时会提前 return，跳过此步；
    HITL 批准后的 resume 生成器需补做，否则带文件操作的 HITL 会话工作区
    始终停留在 session_id 命名，且不会触发记忆生成所需的目录。

    改目录、重建 Agent 在事件循环上做；两次写库放进同一次线程调用：
    中途被取消（用户点停止）也会两条都写完，库里不会只改了一半。
    """
    try:
        if tool_call_records and agent and not agent._workspace_renamed:
            has_file_writes = any(
                tc[0] in ("write_file", "write_tool") and tc[4]
                for tc in tool_call_records
            )
            if has_file_writes:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                sid_prefix = session_id[:5]
                new_ws_name = f"{ts}_{sid_prefix}"
                old_ws_path = str(agent.workspace_dir.absolute())
                agent.rename_workspace(new_ws_name)
                agent._workspace_renamed = True
                new_ws_path = str(agent.workspace_dir.absolute())
                await run_blocking(
                    _save_workspace_rename,
                    db,
                    session_id,
                    old_ws_path,
                    new_ws_path,
                    new_ws_name,
                )
                logger.info(
                    f"[{sid}] Workspace renamed: {old_ws_path} -> {new_ws_path}"
                )
    except Exception as e:
        logger.warning(f"[{sid}] Workspace rename failed: {e}")


def _save_workspace_rename(db, session_id, old_ws_path, new_ws_path, new_ws_name):
    sessions = _session_port(db)
    sessions.update_generated_file_paths(session_id, old_ws_path, new_ws_path)
    sessions.update_session_workspace_name(session_id, new_ws_name)


def resolve_recorded_path(agent, raw_path: str, skills_root: Path) -> Path:
    """把 generated_files 里记录的路径还原成真实路径（兼容虚拟路径记录）。

    write_file 传绝对虚拟路径（/workspace/... 或 /user-skills/...）时，记录里存的是
    虚拟路径原文，需要按挂载映射翻译回真实路径才能操作文件。``skills_root`` 必须与
    技能中心列目录时用的目录一致（来自 skill_library.user_skills_dir）。
    """
    text = str(raw_path or "")
    if not text:
        return Path("")
    if text.startswith("/user-skills/"):
        return skills_root / text[len("/user-skills/") :]
    virtual_root = f"{agent.workspace_virtual_path}/"
    if text.startswith(virtual_root):
        return agent.workspace_dir / text[len(virtual_root) :]
    return Path(text)


def drop_generated_records(
    agent, db, session_id, target_dir: Path, skills_root: Path
) -> int:
    """删除落在 target_dir 内的生成文件记录（文件已移出工作区，文件面板不该再显示）。"""
    removed = 0
    try:
        files = _file_port(db)
        for row in files.get_generated_files(session_id) or []:
            real = resolve_recorded_path(agent, row.get("file_path", ""), skills_root)
            try:
                inside = target_dir.resolve() in real.resolve().parents
            except (OSError, ValueError):
                inside = False
            if inside:
                # files repository 的 delete_generated_file 内置 None 守卫；cast 只为静态类型收窄
                files.delete_generated_file(cast(int, row.get("id")))
                removed += 1
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 清理生成文件记录失败: {e}")
    return removed


async def register_session_skills(agent, db, session_id, sid) -> list[dict]:
    """会话结束后把本次创建的技能收进技能库，工作区不留副本。

    依据本次会话的 ``generated_files``（每次 write_file 都有记录）判断模型把技能写
    到了哪里，比全量扫描工作区更精确：

    1) 直接写进技能库（``/user-skills/<技能>/SKILL.md``）：文件已在位，补自动归类；
    2) 写在工作区（``<workspace>/.../SKILL.md``）：``register_skill_source`` 移动入库，
       并删除对应的生成文件记录，避免文件面板显示已不存在的文件。

    入库后调用 ``invalidate_user_agents`` 让下一条消息（同一会话）就能加载新技能。
    整个过程失败只记日志，不影响会话收尾。

    读、删生成文件记录放进线程；移动目录、写归类（JSON 文件没加锁，和技能中心路由一样
    只在事件循环上改）、驱逐 Agent 缓存仍在事件循环上。
    """
    if agent is None or db is None:
        return []
    username = getattr(agent, "username", "") or ""
    if not username:
        return []

    registered: list[dict] = []
    try:

        # 以技能中心的目录为准（而非 Agent 实例字段），保证"入库了就能在技能中心看到"
        skills_root = skill_library.user_skills_dir(username)
        seen: set[str] = set()

        rows = await run_blocking(_file_port(db).get_generated_files, session_id)
        for row in rows or []:
            real = resolve_recorded_path(agent, row.get("file_path", ""), skills_root)
            # 只以 SKILL.md 识别技能目录（其余文件随目录一起迁移）
            if real.name != "SKILL.md":
                continue
            skill_dir = real.parent
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue

            parent_is_library = False
            try:
                parent_is_library = skill_dir.parent.resolve() == skills_root.resolve()
            except (OSError, ValueError):
                pass

            if parent_is_library:
                # 已在技能库：只需按内容归类（权限策略已限制为"只能新建"）。
                # 记录一并删除：技能不在工作区，文件面板不该再列它（点开也取不到）。
                if skill_dir.name in seen:
                    continue
                seen.add(skill_dir.name)
                auto_assign_skill_category(username, skill_dir, sid, skill_categories)
                item = {
                    "dir_name": skill_dir.name,
                    "source": "direct",
                    "path": skill_dir,
                    "dropped_records": 0,
                }
                registered.append(item)
                item["dropped_records"] = await run_blocking(
                    drop_generated_records, agent, db, session_id, skill_dir, skills_root
                )
                continue

            # 写在工作区：登记进技能库（移动），并清掉工作区侧的记录
            if skill_dir.name in seen:
                continue
            seen.add(skill_dir.name)
            try:
                result = skill_library.register_skill_source(username, skill_dir)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{sid}] 技能登记失败 | {skill_dir} | {e}")
                continue
            auto_assign_skill_category(
                username, result["path"], sid, skill_categories
            )
            item = {
                "dir_name": result["dir_name"],
                "source": "workspace",
                "path": result["path"],
                "renamed": result["renamed"],
                "warnings": result["warnings"],
                "dropped_records": 0,
            }
            registered.append(item)
            item["dropped_records"] = await run_blocking(
                drop_generated_records, agent, db, session_id, skill_dir, skills_root
            )

        return registered
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{sid}] 技能入库流程异常: {e}")
        return []
    finally:
        # 技能入库后先记进 registered 再去删记录：删记录时被取消（用户点停止）或
        # 中途出错，已入库的技能也要在这里生效
        if registered:
            _reload_user_agents(username, registered, sid)


def _reload_user_agents(username: str, registered: list[dict], sid: str) -> None:
    """技能清单在 Agent 构造时固化：入库后必须驱逐缓存，下一条消息才能加载。"""
    try:

        agent_manager.invalidate_user_agents(username)
        logger.info(
            f"[{sid}] ✅ 技能已入库 | "
            + " | ".join(
                f"{item['dir_name']}（来源: {item['source']}）" for item in registered
            )
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{sid}] 技能入库后驱逐 Agent 缓存失败: {e}")


def auto_assign_skill_category(
    username: str, skill_dir: Path, sid: str, skill_categories
) -> None:
    """按技能名/描述/正文自动归类（失败不影响入库本身）。"""
    try:
        skill_categories.auto_assign_skill_dir(username, skill_dir.name, skill_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{sid}] 技能自动归类失败 | {skill_dir} | {e}")


#: ``memory_model`` 配置对应的模型实例（按键缓存，进程内复用）与已告警过的无效键
_MEMORY_MODELS: dict[str, object] = {}
_MEMORY_MODEL_WARNED: set[str] = set()
_MEMORY_MODELS_LOCK = threading.Lock()


def _memory_llm(agent):
    """记忆整理用的模型：配置了 ``memory_model`` 就用它，否则用会话当前的对话模型。

    记忆整理输出很短、用不上推理，配一个便宜、快的模型能省成本，也不跟主对话抢同一个
    模型的限流额度。键不在 ``models`` 里或建模型失败时告警一次，回落到对话模型。
    """
    fallback = getattr(agent, "model", None)
    if fallback is None:
        from easy_agent.services.agents.manager import _llm_instance  # 延迟导入：测试 monkeypatch 语义：按模块属性替换，函数内导入才能取到替换后的对象

        fallback = _llm_instance
    config = getattr(agent, "config", None)
    key = str(getattr(getattr(config, "agent", None), "memory_model", "") or "").strip()
    if not key:
        return fallback
    with _MEMORY_MODELS_LOCK:
        if key in _MEMORY_MODELS:
            return _MEMORY_MODELS[key]
        if key in _MEMORY_MODEL_WARNED:
            return fallback
        if key not in (getattr(config, "models", None) or {}):
            _MEMORY_MODEL_WARNED.add(key)
            logger.warning(f"memory_model={key} 不在 models 配置里，记忆整理改用对话模型")
            return fallback
        try:
            from easy_agent.agent.model import create_model  # 延迟导入：测试 monkeypatch 语义：按模块属性替换，函数内导入才能取到替换后的对象

            _MEMORY_MODELS[key] = create_model(config, model_name=key)
        except Exception as exc:  # noqa: BLE001 - 建不出来就回落，不影响对话
            _MEMORY_MODEL_WARNED.add(key)
            logger.warning(f"记忆整理模型 {key} 创建失败，改用对话模型: {exc}")
            return fallback
        logger.info(f"🧠 记忆整理使用独立模型 | memory_model={key}")
        return _MEMORY_MODELS[key]


def submit_memory_updates(
    agent, session_id, db, raw_user_msg, accumulated_response, sid, tool_call_records=None
):
    """将「会话级 memory.md」与「用户级 AGENTS.md」的记忆更新提交到后台线程执行。

    主生成器与 HITL resume 生成器均复用此函数，确保无论首轮是否触发 HITL、
    在任务真正完成（含批准后）都会在工作区生成 memory.md，并在下一轮加载。

    注意：
    - update_memory_after_session 内部会同步调用 LLM（阻塞式 HTTP），必须放到线程池，
      否则会阻塞事件循环数秒~十几秒。
    - 写入后不需要重建 Agent：记忆每次运行都由 ``SessionMemoryMiddleware`` 重新加载
      （``agent/memory.py``），下一轮自然读到。改造前这里会调用 ``remove_session_agent``，
      既让下一轮重新加载 MCP 工具、重建 Agent，又顺带清掉会话的「待修改专家」绑定，
      导致「点编辑 → 模型先追问 → 下一轮再保存」时 save_expert 找不到修改目标。
    - 同一份记忆的更新按提交顺序串行（``_submit_memory_job``）：会话记忆按会话排队
      （工作区改名会换路径，按会话 id 才排得住），长期记忆按文件排队（同一用户的所有会话共用）。
    """
    # 1) 会话级记忆：workspace/{username}/{workspace_name}/memory.md
    #    不是每轮都整理：有决策 / 约束 / 纠正信号当轮整理，否则攒够几轮再整理（services/memory/triggers.py）
    if agent and agent.memory_file:
        try:
            # 本轮操作摘要（工具调用 / 改动的文件 / 审批结果），让整理模型看得到决策依据
            turn_actions = describe_turn_actions(
                tool_call_records_to_dicts(tool_call_records or [], result_limit=200)
            )
            session_key = f"session:{session_id}" if session_id else f"file:{Path(agent.memory_file).resolve()}"
            decision = SESSION_BATCHER.add(
                session_key,
                _session_note(raw_user_msg, accumulated_response, turn_actions),
                signal=has_session_signal(raw_user_msg, turn_actions),
            )
            if decision.due:
                _submit_session_update(
                    agent, session_key, decision, raw_user_msg, accumulated_response, turn_actions, sid
                )
            else:
                logger.info(
                    f"[{sid}] 🧠 会话记忆本轮不整理 | 无决策/约束信号，已攒 "
                    f"{decision.pending}/{SESSION_BATCHER.every_n_turns} 轮"
                )
        except Exception as e:
            logger.warning(f"[{sid}] 会话记忆更新提交失败: {e}")

    # 2) 用户级长期记忆：memories/{username}/AGENTS.md
    #    不是每轮都整理：有画像信号当轮整理，否则攒够几轮再整理（services/memory/triggers.py）
    if agent and getattr(agent, "long_term_memory_file", None):
        try:
            long_term_key = f"file:{Path(agent.long_term_memory_file).resolve()}"
            decision = LONG_TERM_BATCHER.add(
                long_term_key, raw_user_msg, signal=has_long_term_signal(raw_user_msg)
            )
            if decision.due:
                _submit_long_term_update(
                    agent, long_term_key, decision, raw_user_msg, accumulated_response, sid
                )
            else:
                logger.info(
                    f"[{sid}] 🧠 长期记忆本轮不整理 | 无画像信号，已攒 "
                    f"{decision.pending}/{LONG_TERM_BATCHER.every_n_turns} 轮"
                )
        except Exception as e:
            logger.warning(f"[{sid}] 用户长期记忆更新提交失败: {e}")


def _session_note(user_message, reply, turn_actions) -> str:
    """攒批时登记的一轮摘要：用户输入、回复节选（开头和结尾）、操作。"""
    parts = [f"用户：{' '.join((user_message or '').split())[:200]}"]
    reply = " ".join((reply or "").split())
    if reply:
        parts.append(f"助手：{clip_middle(reply, 300, tail=100)}")
    if turn_actions:
        parts.append("操作：" + "；".join(line.lstrip("- ") for line in turn_actions.splitlines())[:300])
    return "\n".join(parts)


def _submit_session_update(
    agent, session_key, decision, raw_user_msg, accumulated_response, turn_actions, sid
):
    """提交一次会话记忆整理：本轮 + 之前攒下的轮次一起交给模型。"""
    from easy_agent.services.memory.manager import update_memory_after_session  # 延迟导入：测试 monkeypatch 语义：按模块属性替换，函数内导入才能取到替换后的对象

    session_llm = _memory_llm(agent)
    earlier = decision.earlier

    def _session_memory_job():
        try:
            updated = update_memory_after_session(
                agent.memory_file,
                user_message=raw_user_msg,
                assistant_response=accumulated_response,
                llm=session_llm,
                turn_actions=turn_actions,
                earlier_turns=earlier,
            )
            if updated:
                logger.info(f"[{sid}] 🧠 会话记忆已更新 | 文件: {agent.memory_file}")
        except Exception as e:
            logger.warning(f"[{sid}] 会话记忆更新失败: {e}")

    _submit_memory_job(session_key, _session_memory_job)
    reason = "决策/约束信号" if decision.reason == "signal" else "攒够轮数"
    logger.info(
        f"[{sid}] 🧠 会话记忆更新已提交至后台线程执行 | 原因: {reason} | 合并 {len(earlier) + 1} 轮"
    )


def _submit_long_term_update(agent, long_term_key, decision, raw_user_msg, accumulated_response, sid):
    """提交一次长期记忆整理：本轮 + 之前攒下的轮次一起交给模型。"""
    session_llm = _memory_llm(agent)
    earlier = decision.earlier

    def _long_term_memory_job():
        try:
            updated = update_long_term_memory_after_session(
                agent.long_term_memory_file,
                user_message=raw_user_msg,
                assistant_response=accumulated_response,
                llm=session_llm,
                earlier_user_messages=earlier,
            )
            if updated:
                logger.info(f"[{sid}] 🧠 用户长期记忆已更新 | 文件: {agent.long_term_memory_file}")
        except Exception as e:
            logger.warning(f"[{sid}] 用户长期记忆更新失败: {e}")

    _submit_memory_job(long_term_key, _long_term_memory_job)
    reason = "画像信号" if decision.reason == "signal" else "攒够轮数"
    logger.info(
        f"[{sid}] 🧠 用户长期记忆更新已提交至后台线程执行 | 原因: {reason} | "
        f"合并 {len(earlier) + 1} 轮"
    )


__all__ = [
    "auto_assign_skill_category",
    "drop_generated_records",
    "maybe_rename_workspace",
    "register_session_skills",
    "resolve_recorded_path",
    "submit_memory_updates",
]
