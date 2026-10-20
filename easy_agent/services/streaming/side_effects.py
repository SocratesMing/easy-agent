"""流式收尾副作用（C2 拆分第二步）：工作区重命名 / 技能入库 / 记忆更新。

这些动作会触碰文件系统、Agent 缓存与后台线程，和「纯投影」「DB 持久化」分开，
便于单测时按需打桩（golden 测试通常只关心事件顺序与落库载荷）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("easy_agent.chat_service")

# 记忆更新会同步调用 LLM（每次 1~2 次），不能占用默认 executor（默认线程池
# 同时服务文件解析/终端 select 等，慢 LLM 调用会拖住其它 to_thread 任务）。
_MEMORY_UPDATE_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="memory-update"
)


def maybe_rename_workspace(agent, db, session_id, tool_call_records, sid):
    """首轮对话完成后（含文件写入时）将工作区从 session_id 重命名为时间戳目录。

    主生成器在遇到 HITL 中断时会提前 return，跳过此步；
    HITL 批准后的 resume 生成器需补做，否则带文件操作的 HITL 会话工作区
    始终停留在 session_id 命名，且不会触发记忆生成所需的目录。
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
                db.update_generated_file_paths(session_id, old_ws_path, new_ws_path)
                db.update_session_workspace_name(session_id, new_ws_name)
                logger.info(
                    f"[{sid}] Workspace renamed: {old_ws_path} -> {new_ws_path}"
                )
    except Exception as e:
        logger.warning(f"[{sid}] Workspace rename failed: {e}")


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
        for row in db.get_generated_files(session_id) or []:
            real = resolve_recorded_path(agent, row.get("file_path", ""), skills_root)
            try:
                inside = target_dir.resolve() in real.resolve().parents
            except (OSError, ValueError):
                inside = False
            if inside:
                db.delete_generated_file(row.get("id"))
                removed += 1
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{session_id[-5:]}] 清理生成文件记录失败: {e}")
    return removed


def register_session_skills(agent, db, session_id, sid) -> list[dict]:
    """会话结束后把本次创建的技能收进技能库，工作区不留副本。

    依据本次会话的 ``generated_files``（每次 write_file 都有记录）判断模型把技能写
    到了哪里，比全量扫描工作区更精确：

    1) 直接写进技能库（``/user-skills/<技能>/SKILL.md``）：文件已在位，补自动归类；
    2) 写在工作区（``<workspace>/.../SKILL.md``）：``register_skill_source`` 移动入库，
       并删除对应的生成文件记录，避免文件面板显示已不存在的文件。

    入库后调用 ``invalidate_user_agents`` 让下一条消息（同一会话）就能加载新技能。
    整个过程失败只记日志，不影响会话收尾。
    """
    if agent is None or db is None:
        return []
    username = getattr(agent, "username", "") or ""
    if not username:
        return []

    try:
        from easy_agent.services import agent_manager, skill_categories, skill_library

        # 以技能中心的目录为准（而非 Agent 实例字段），保证"入库了就能在技能中心看到"
        skills_root = skill_library.user_skills_dir(username)
        registered: list[dict] = []
        seen: set[str] = set()

        for row in db.get_generated_files(session_id) or []:
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
                dropped = drop_generated_records(
                    agent, db, session_id, skill_dir, skills_root
                )
                registered.append(
                    {
                        "dir_name": skill_dir.name,
                        "source": "direct",
                        "path": skill_dir,
                        "dropped_records": dropped,
                    }
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
            dropped = drop_generated_records(
                agent, db, session_id, skill_dir, skills_root
            )
            registered.append(
                {
                    "dir_name": result["dir_name"],
                    "source": "workspace",
                    "path": result["path"],
                    "renamed": result["renamed"],
                    "warnings": result["warnings"],
                    "dropped_records": dropped,
                }
            )

        if registered:
            # 技能清单在 Agent 构造时固化：入库后必须驱逐缓存，下一条消息才能加载
            agent_manager.invalidate_user_agents(username)
            logger.info(
                f"[{sid}] ✅ 技能已入库 | "
                + " | ".join(
                    f"{item['dir_name']}（来源: {item['source']}）" for item in registered
                )
            )
        return registered
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{sid}] 技能入库流程异常: {e}")
        return []


def auto_assign_skill_category(
    username: str, skill_dir: Path, sid: str, skill_categories
) -> None:
    """按技能名/描述/正文自动归类（失败不影响入库本身）。"""
    try:
        skill_categories.auto_assign_skill_dir(username, skill_dir.name, skill_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{sid}] 技能自动归类失败 | {skill_dir} | {e}")


def submit_memory_updates(
    agent, session_id, db, raw_user_msg, accumulated_response, sid
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
    """
    # 1) 会话级记忆：workspace/{username}/{workspace_name}/memory.md
    if agent and agent.memory_file:
        try:
            from easy_agent.services.memory_manager import update_memory_after_session

            session_llm = getattr(agent, "model", None)
            if session_llm is None:
                from easy_agent.services.agent_manager import _llm_instance

                session_llm = _llm_instance

            def _session_memory_job():
                try:
                    updated = update_memory_after_session(
                        agent.memory_file,
                        user_message=raw_user_msg,
                        assistant_response=accumulated_response,
                        llm=session_llm,
                    )
                    if updated:
                        logger.info(
                            f"[{sid}] 🧠 会话记忆已更新 | 文件: {agent.memory_file} | "
                            f"上限 2000 字符"
                        )
                except Exception as e:
                    logger.warning(f"[{sid}] 会话记忆更新失败: {e}")

            loop = asyncio.get_running_loop()
            loop.run_in_executor(_MEMORY_UPDATE_EXECUTOR, _session_memory_job)
            logger.info(f"[{sid}] 🧠 会话记忆更新已提交至后台线程执行")
        except Exception as e:
            logger.warning(f"[{sid}] 会话记忆更新提交失败: {e}")

    # 2) 用户级长期记忆：memories/{username}/AGENTS.md
    if agent and getattr(agent, "long_term_memory_file", None):
        try:
            from easy_agent.services.memory_manager import update_long_term_memory_after_session

            session_llm = getattr(agent, "model", None)
            if session_llm is None:
                from easy_agent.services.agent_manager import _llm_instance

                session_llm = _llm_instance

            def _long_term_memory_job():
                try:
                    update_long_term_memory_after_session(
                        agent.long_term_memory_file,
                        user_message=raw_user_msg,
                        assistant_response=accumulated_response,
                        llm=session_llm,
                    )
                    logger.info(
                        f"[{sid}] 🧠 用户长期记忆已更新 | 文件: {agent.long_term_memory_file}"
                    )
                except Exception as e:
                    logger.warning(f"[{sid}] 用户长期记忆更新失败: {e}")

            loop = asyncio.get_running_loop()
            loop.run_in_executor(_MEMORY_UPDATE_EXECUTOR, _long_term_memory_job)
            logger.info(f"[{sid}] 🧠 用户长期记忆更新已提交至后台线程执行")
        except Exception as e:
            logger.warning(f"[{sid}] 用户长期记忆更新提交失败: {e}")


__all__ = [
    "auto_assign_skill_category",
    "drop_generated_records",
    "maybe_rename_workspace",
    "register_session_skills",
    "resolve_recorded_path",
    "submit_memory_updates",
]
