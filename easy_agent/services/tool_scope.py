"""专家技能作用域：把「专家绑定的技能包 + 本轮另选的技能」归一成技能白名单。

背景（本次改造）：改造前技能是**全量挂载**——``agent/workspace.py:_resolve_skills_paths``
固定返回 ``/skills/builtin/`` + ``/user-skills/``，``SkillsMiddleware`` 把用户库里所有
技能都列进技能清单，所以「这位专家只用我给它选的几个技能包」在模型眼里并不存在：
它看到的是整个技能库，谁描述更贴合就调谁。

本模块只负责**把选择归一成作用域**，不负责挂载与过滤（挂载在
``agent/backend.py`` + ``backends/scoped_skills.py``）：

    skill_whitelist = None   → 不做限制（= 改造前行为）
    skill_whitelist = (... ) → 只有这些目录进技能清单、且其内容可读

来源与合并规则：

1. 专家**没绑定**技能包 → ``None``（全量）。这是用户明确要求的语义：
   「创建专家时一个技能包都不选 = 默认都可以使用」，同时保证存量专家行为不变；
2. 专家绑定了技能包 → 白名单 = 专家技能包 ∪ 本轮输入框另选的技能（去重保序）。
   公共（预置）专家的 ``required_skills`` 与本轮另选同样是并集（需求点 4）；
3. 声明了技能包、但**一个都解析不到**时回退 ``None`` 并记 WARNING：
   技能包被删 / 被停用 / 服务端没放包时，宁可退化成"技能库全都可见"，
   也不要把一次技能缺失放大成"这个专家彻底没有技能"。

失败策略与 ``skill_provision`` 一致：任何异常都不阻断对话，最多退化成全量。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from . import skill_library

logger = logging.getLogger("easy-agent")


@dataclass(frozen=True)
class ToolScope:
    """一次请求的技能作用域（不可变，便于参与缓存签名比较）。

    Attributes:
        skill_whitelist: 允许使用的技能目录名；``None`` = 不限制（全量），
            ``()`` = 显式无技能。**目录名**口径（``/user-skills/`` 与
            ``/skills/builtin/`` 下的一级子目录名），不是 SKILL.md 里的展示名。
        source: 作用域来源，只用于日志排查：
            ``disabled`` / ``none``（全量）/ ``expert`` / ``expert+turn`` / ``turn``。
    """

    skill_whitelist: Optional[tuple[str, ...]] = None
    source: str = "none"

    def signature(self) -> tuple:
        """缓存签名：参与 Agent 缓存驱逐比较（作用域变了就必须重建）。"""
        return (self.skill_whitelist,)

    def describe(self) -> str:
        """日志用的一句话描述（必须能区分「全量」与「显式无技能」）。"""
        if self.skill_whitelist is None:
            return f"全量可见（来源={self.source}）"
        if not self.skill_whitelist:
            return f"显式无技能（来源={self.source}）"
        return (
            f"{len(self.skill_whitelist)} 个: {', '.join(self.skill_whitelist)}"
            f"（来源={self.source}）"
        )


def tool_scope_enabled(config) -> bool:
    """作用域总开关（``expert.tool_scope_enabled``，默认开）。

    关闭后一律返回"不限制"，等价于改造前的全量挂载，用于线上出问题时的快速回退
    （不需要回滚代码）。``config`` 是调用方手里的应用配置，取不到时按开启处理；
    本模块不反向 import ``agent_manager``（那样会和 save_expert 工具成环）。
    """
    return bool(getattr(config, "expert_tool_scope_enabled", True))


def resolve_tool_scope(
    username: str,
    *,
    expert_skills: Iterable[str] | None = None,
    turn_skills: Iterable[str] | None = None,
    expert_id: str = "",
    sid: str = "",
    enabled: bool = True,
) -> ToolScope:
    """把「专家绑定的技能包」与「本轮另选的技能」归一成 :class:`ToolScope`。

    Args:
        username: 当前用户（技能归属校验口径与 ``skill_library.resolve_skill_dirs`` 一致）。
        expert_skills: 专家绑定的技能目录名/展示名（自建专家取
            ``export_required_skills``，公共专家取 ``required_skills``）。
        turn_skills: 本轮输入框选中的技能目录名（``chat.py`` 的 ``turn.dirs``）。
        expert_id: 仅用于日志。
        sid: 会话日志前缀。
        enabled: 作用域总开关，调用方用 :func:`tool_scope_enabled` 从应用配置取。

    Returns:
        :class:`ToolScope`；开关关闭或专家未绑定技能包时 ``skill_whitelist=None``。
    """
    expert_list = _clean(expert_skills)
    turn_list = _clean(turn_skills)

    if not enabled:
        logger.info(
            f"[{sid}] 🔓 技能作用域已关闭（expert.tool_scope_enabled=false），全量挂载 | "
            f"expert={expert_id or '(无)'} | 专家技能={expert_list or '无'} | "
            f"本轮另选={turn_list or '无'}"
        )
        return ToolScope(None, "disabled")

    if not expert_list:
        # 专家没有绑定技能包：全量可见（含存量专家与"创建时一个都没选"的专家）。
        # 本轮另选只影响 pin（既有行为），不收窄作用域。
        if turn_list:
            logger.info(
                f"[{sid}] 🔓 技能作用域=全量（专家未绑定技能包）| expert={expert_id or '(无)'} | "
                f"本轮另选={turn_list}（仅 pin，不限制可见范围）"
            )
        else:
            logger.debug(
                f"[{sid}] 🔓 技能作用域=全量（无专家技能、无本轮另选）| expert={expert_id or '(无)'}"
            )
        return ToolScope(None, "none")

    expert_allowed, expert_dropped = _resolve(username, expert_list, sid, "专家技能包")
    turn_allowed, turn_dropped = _resolve(username, turn_list, sid, "本轮另选技能")

    allowed: list[str] = []
    for name in [*expert_allowed, *turn_allowed]:
        if name not in allowed:
            allowed.append(name)

    if not allowed:
        logger.warning(
            f"[{sid}] ⚠️ 技能作用域：专家绑定的技能包一个都不可用，回退为全量可见 | "
            f"expert={expert_id or '(无)'} | 声明={expert_list} | 剔除={expert_dropped}"
        )
        return ToolScope(None, "expert")

    source = "expert+turn" if (expert_allowed and turn_allowed) else (
        "expert" if expert_allowed else "turn"
    )
    logger.info(
        f"[{sid}] 🔒 技能作用域已收窄 | expert={expert_id or '(无)'} | 来源={source} | "
        f"专家技能={expert_allowed or '无'} | 本轮另选={turn_allowed or '无'} | "
        f"剔除（不存在/已停用）={[*expert_dropped, *turn_dropped] or '无'} | "
        f"最终白名单={allowed}"
    )
    return ToolScope(tuple(allowed), source)


# ── 内部 ──────────────────────────────────────────────────────────────


def normalize_expert_skills(
    username: str, names: Iterable[str] | None, sid: str = ""
) -> tuple[list[str], list[str]]:
    """把用户选定的技能包归一成 canonical 目录名（保存专家时用）。

    与 :func:`_resolve` 的差别：**停用的技能保留**——保存专家表达的是"以后就用
    这几个包"，用户可能只是暂时停用；真正对话时由 :func:`resolve_tool_scope` 剔除。
    找不到的技能（已彻底删除）剔除并记 WARNING，因为它确实不存在了。

    解析顺序：用户技能库（含已添加的公共技能，目录名 / 中文展示名别名）→
    公共技能 canonical 目录（公共包还没"添加"到用户清单时，仍按目录名记下来，
    对话前由 ``skill_provision`` 自动补装）。

    Returns:
        ``(保留的目录名, 剔除的原始值)``。
    """
    kept: list[str] = []
    dropped: list[str] = []
    for raw in _clean(names):
        resolved = _resolve_one(username, raw)
        if not resolved:
            dropped.append(raw)
            continue
        if resolved not in kept:
            kept.append(resolved)
    if dropped:
        logger.warning(
            f"[{sid}] ⚠️ 保存专家：{len(dropped)} 个技能包已不存在，未写入专家绑定 | {dropped}"
        )
    return kept, dropped


def _resolve_one(username: str, name: str) -> str:
    """单个技能名 → canonical 目录名；两条通道都解析不到时返回空串。"""
    try:
        found = skill_library.find_user_skill(username, name)
        if found:
            return str(found["dir_name"])
    except Exception as exc:  # noqa: BLE001 - 解析失败按"未找到"处理
        logger.warning(f"技能包解析失败（用户库）| 用户={username} | {name} | {exc}")
    try:
        hit = skill_library.resolve_skill_ref(name)
        if hit:
            return str(hit["dir_name"])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"技能包解析失败（公共目录）| {name} | {exc}")
    return ""


def _clean(values: Iterable[str] | None) -> list[str]:
    """去掉空白项并保序去重。"""
    result: list[str] = []
    for value in values or ():
        name = str(value or "").strip()
        if name and name not in result:
            result.append(name)
    return result


def _resolve(
    username: str, names: list[str], sid: str, label: str
) -> tuple[list[str], list[str]]:
    """技能名 → 可用目录名；返回 ``(可用, 剔除)``。

    复用 ``skill_library.resolve_skill_dirs``：目录名优先、展示名（中文）兜底，
    并给出 ``enabled``。**停用的技能不进白名单**——它不在挂载源里，列进去只会让
    模型看到一条读不到的技能。
    """
    if not names:
        return [], []
    try:
        resolved, unknown = skill_library.resolve_skill_dirs(names, username)
    except Exception as exc:  # noqa: BLE001 - 解析失败不能让对话挂掉
        logger.warning(
            f"[{sid}] ⚠️ {label}解析失败，按全部剔除处理 | 用户={username} | {names} | {exc}"
        )
        return [], list(names)

    allowed = [item["dir_name"] for item in resolved if item.get("enabled", True)]
    disabled = [item["dir_name"] for item in resolved if not item.get("enabled", True)]
    dropped = [*unknown, *disabled]
    if unknown:
        logger.warning(
            f"[{sid}] ⚠️ {label}里有 {len(unknown)} 个技能不存在，已从作用域剔除 | {unknown}"
        )
    if disabled:
        logger.warning(
            f"[{sid}] ⚠️ {label}里有 {len(disabled)} 个技能已停用，已从作用域剔除 | {disabled}"
        )
    return allowed, dropped
