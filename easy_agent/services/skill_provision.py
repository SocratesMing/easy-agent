"""预置专家依赖技能的自动补装（DA-13 S2：只登记，不复制）。

公共（预置）专家可以在 ``config.yaml`` 里用 ``required_skills`` 声明依赖技能，
例如因子研究专家依赖 ``factor-creator``。用户第一次用该专家对话时，这里把这些
技能**「添加」到用户的公共技能清单**（``workspace/{u}/builtin-skills.json``），
使只读路由 ``/skills/builtin/`` 对该用户可见。

为什么不是复制：公共技能包本体只有一份，位于 ``config.tools.skills_dir``
（仓库 ``skills/``），升级后所有用户立刻生效；复制进用户目录会变成 N 份副本、
升级要逐个迁移（S2 之前的历史包袱已由 ``builtin_skills.migrate_user_copies`` 清理）。
专家包只声明依赖，不打包技能。

判定顺序（每一轮建 Agent 前都会跑一遍，纯文件读取，毫秒级）：

1. 技能库已有且启用（用户技能 fork 或已添加的公共技能）→ ``existing``，跳过；
2. 已有但被用户停用 → ``disabled``，**不自动装回**（停用是主动意图）；
3. 公共技能目录里没有这个目录 → ``source_missing`` / ``source_unconfigured``，
   记 ERROR 并在运行时提示里告知模型（需要运维放包）；
4. 否则 ``builtin_skills.add()`` 登记并启用 → ``installed``。

失败策略：**绝不让专家不可用**。补装失败只影响「技能能不能用」，不影响这一轮对话；
报告里带上失败原因，由 ``build_skill_runtime_notice`` 转成一段提示词注入，让模型如实
告诉用户「技能未就绪」，而不是假装执行了技能脚本。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from easy_agent.agent import builtin_skills

from . import public_expert_service, skill_library

logger = logging.getLogger("easy-agent")

#: 同一 (用户, 专家, 技能, 原因) 的告警日志节流窗口（秒）——每轮对话都会检查，
#: 不节流会把「技能包没放」刷满整个日志。
_REPORT_INTERVAL = 600.0
_report_at: dict[tuple[str, str, str, str], float] = {}


# ── 开关与声明 ────────────────────────────────────────────────────────


def _auto_install_enabled() -> bool:
    """配置开关 ``expert.auto_install_required_skills``（默认开）。"""
    from .agent_manager import get_agent_config  # 延迟导入，避免模块级循环依赖

    cfg = get_agent_config()
    if not cfg:
        return True
    return bool(getattr(cfg.get("config"), "expert_auto_install_required_skills", True))


def required_skills_of(expert_id: str | None, username: str = "") -> list[str]:
    """读取专家绑定的技能包目录名。

    两条来源（顺序固定）：

    1. **自建专家**（落库）：``expert_agents.export_required_skills``——用户在创建 /
       修改专家时于输入框选定的技能包（本次改造新增，见 ``api/chat.py`` 与
       ``tools/expert_writer.py``）。给了 ``username`` 才查，避免每轮通用对话都读库；
    2. **公共（预置）专家**（目录 + ``config.yaml``）：``required_skills``。

    只读库字段与 YAML，不读提示词正文：本函数在每轮创建 Agent 前都会跑，
    不能带来可感知的延迟。
    """
    if not expert_id:
        return []

    if username:
        try:
            from easy_agent.db import get_database
            from easy_agent.db.expert_store import ExpertStore
            from easy_agent.tools.expert_writer import resolve_owned_expert

            record = resolve_owned_expert(get_database(), username, expert_id)
            if record:
                skills = [
                    str(item).strip()
                    for item in (record.get("export_required_skills") or [])
                    if str(item).strip()
                ]
                logger.debug(
                    f"专家绑定技能读取（自建）| 用户: {username} | 专家: {expert_id} | "
                    f"技能包: {skills or '(未绑定)'}"
                )
                return skills
        except Exception as exc:  # noqa: BLE001 - 读库失败退化为公共专家口径
            logger.warning(
                f"⚠️ 读取自建专家绑定技能失败，按公共专家口径处理 | 用户: {username} | "
                f"专家: {expert_id} | {exc}"
            )

    try:
        return public_expert_service.required_skills_of_public_expert(expert_id)
    except Exception as exc:  # noqa: BLE001 - 扫描失败不能影响对话
        logger.warning(f"⚠️ 读取公共专家依赖技能失败 | expert={expert_id} | {exc}")
        return []


def _log_once(level: int, key: tuple[str, str, str, str], message: str) -> None:
    """按 (用户, 专家, 技能, 原因) 节流地打日志。"""
    now = time.monotonic()
    last = _report_at.get(key)
    if last is not None and now - last < _REPORT_INTERVAL:
        return
    _report_at[key] = now
    logger.log(level, message)


# ── 单个技能的现状 / 补装 ─────────────────────────────────────────────


def _inspect_one(username: str, name: str) -> dict[str, Any]:
    """判断单个依赖技能当前的状态（只读，不写清单）。

    声明值可以是**目录名**，也可以是 SKILL.md 的 ``name``（常见为中文）——统一经
    ``skill_library.resolve_*_ref`` 解析出 canonical 目录名，后续一律用目录名操作，
    不会产生第二套数据。

    Returns:
        ``{"skill", "declared", "resolved_dir", "matched_by", "state", "detail",
        "path", "candidates"}``；``state`` 取值 ``existing`` / ``disabled`` /
        ``installed_ready`` / ``source_missing`` / ``source_unconfigured``。
    """
    declared = name

    def _pack(state: str, *, dir_name: str = "", matched_by: str = "", detail: str = "",
              path: str = "", candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return {
            "skill": declared,
            "declared": declared,
            "resolved_dir": dir_name,
            "matched_by": matched_by,
            "state": state,
            "detail": detail,
            "path": path,
            "candidates": candidates or [],
        }

    # 1) 用户技能库（含已添加的公共技能）：先精确目录名，再按 name 别名
    found = skill_library.find_user_skill(username, declared)
    if found:
        state = "existing" if found.get("enabled") else "disabled"
        return _pack(
            state,
            dir_name=found["dir_name"],
            matched_by=found.get("matched_by", "dir"),
            path=str(found["path"]),
        )

    root = builtin_skills.builtin_root()
    if root is None:
        return _pack("source_unconfigured", detail="未配置 tools.skills_dir 或该目录不存在")

    # 2) 公共技能目录：目录名 / 归一化 / name / name 归一化
    hit = skill_library.resolve_skill_ref(declared, root)
    if hit:
        return _pack(
            "installed_ready",
            dir_name=hit["dir_name"],
            matched_by=hit["matched_by"],
            path=str(hit["path"]),
        )

    # 3) 都没命中：给出近似候选，方便运维判断是「声明写错」还是「技能包没放」
    return _pack(
        "source_missing",
        detail=f"公共技能目录中既没有目录 {declared}/，也没有 name 为 {declared} 的技能",
        path=str(root / declared),
        candidates=skill_library.suggest_skill_refs(declared, root),
    )


def provision_expert_skills(username: str, expert_id: str | None) -> dict[str, Any]:
    """补装专家依赖技能，返回**结构化报告**（含失败原因，供提示词与诊断接口使用）。

    Returns:
        ``{
            "expert_id": str, "enabled": bool,
            "required": [声明值（目录名或 name）],
            "resolved": [{"declared", "resolved_dir", "matched_by"}],
            "installed": [本次新添加的目录名],
            "existing": [已可用目录名], "skipped": [{"skill", "reason", "detail", "path",
            "resolved_dir", "matched_by", "candidates"}],
            "missing": [公共目录里解析不到的声明],
            "skills_root": str, "cache_invalidated": int,
        }``

    ``reason`` 取值：``existing``（已可用）/ ``disabled``（用户停用）/
    ``source_missing``（公共技能目录里没有，需运维放包）/ ``source_unconfigured``
    （未配置公共技能目录）/ ``install_failed``（写清单失败）。

    ``required`` 里的声明值可以是目录名，也可以是 SKILL.md 的 ``name``（常见为中文）；
    ``resolved`` 记录每条声明实际解析到的目录名与匹配方式，``installed``/``existing``
    一律是 canonical 目录名（清单键），便于排查「声明写错」与「技能没放」的区别。
    """
    report: dict[str, Any] = {
        "expert_id": expert_id or "",
        "enabled": _auto_install_enabled(),
        "required": [],
        "resolved": [],
        "installed": [],
        "existing": [],
        "skipped": [],
        "missing": [],
        "skills_root": "",
        "cache_invalidated": 0,
    }
    if not username or not expert_id:
        # 没有专家 ID 就不补装。这里刻意只打 debug：每轮通用对话都会走到，
        # 用 info 会把日志刷满。真正的排查点是 chat 入口那行「expert: xxx」。
        logger.debug(
            f"依赖技能补装跳过 | 无专家 ID | 用户: {username or '(空)'} | expert: {expert_id or '(空)'}"
        )
        return report

    root = builtin_skills.builtin_root()
    report["skills_root"] = str(root) if root else ""

    required = required_skills_of(expert_id, username)
    report["required"] = list(required)
    if not required or not report["enabled"]:
        # 「专家没声明 required_skills」和「开关关了」是两种完全不同的原因，但现象一样
        # （技能没装）。命中率低，打 info 不刷屏，排查时能直接看到是哪一种。
        logger.info(
            f"依赖技能补装无动作 | expert={expert_id} | "
            f"enabled={report['enabled']} | required={required or '(未声明)'}"
        )
        return report

    for name in required:
        state = _inspect_one(username, name)
        resolved_dir = state.get("resolved_dir") or ""
        resolution = {
            "declared": state.get("declared", name),
            "resolved_dir": resolved_dir,
            "matched_by": state.get("matched_by", ""),
        }
        if state["state"] == "existing":
            report["existing"].append(resolved_dir or name)
            report["resolved"].append(resolution)
            continue
        if state["state"] == "disabled":
            report["skipped"].append(
                {"skill": name, "reason": "disabled", "detail": state["detail"], "path": state["path"],
                 "resolved_dir": resolved_dir, "matched_by": state.get("matched_by", "")}
            )
            report["resolved"].append(resolution)
            _log_once(
                logging.INFO,
                (username, expert_id, name, "disabled"),
                f"⏸️ 专家依赖技能已被用户停用，不自动装回 | 用户: {username} | "
                f"专家: {expert_id} | 技能: {name} | 目录: {resolved_dir or '(未解析)'}",
            )
            continue
        if state["state"] in {"source_missing", "source_unconfigured"}:
            report["missing"].append(name)
            report["skipped"].append(
                {
                    "skill": name,
                    "reason": state["state"],
                    "detail": state["detail"],
                    "path": state["path"],
                    "resolved_dir": resolved_dir,
                    "matched_by": state.get("matched_by", ""),
                    "candidates": state.get("candidates") or [],
                }
            )
            report["resolved"].append(resolution)
            where = report["skills_root"] or "（未配置 tools.skills_dir）"
            _log_once(
                logging.ERROR,
                (username, expert_id, name, state["state"]),
                f"❌ 专家依赖技能缺失，无法自动添加 | 用户: {username} | 专家: {expert_id} | "
                f"技能: {name} | 请把该技能包放到公共技能目录（目录内需含 SKILL.md）: {where} | "
                f"原因: {state['detail']} | 候选: {state.get('candidates') or '无'}",
            )
            continue

        # 解析到 canonical 目录名后统一登记；声明写的是 name 时也不影响清单键
        target_dir = resolved_dir or name
        try:
            builtin_skills.add(username, target_dir, root)
        except FileExistsError:  # 并发兜底：另一路刚添加成功，等价于已可用
            report["existing"].append(target_dir)
            report["resolved"].append(resolution)
            continue
        except Exception as exc:  # noqa: BLE001 - 添加失败不阻断对话
            report["skipped"].append(
                {"skill": name, "reason": "install_failed", "detail": str(exc), "path": state["path"],
                 "resolved_dir": resolved_dir, "matched_by": state.get("matched_by", "")}
            )
            report["resolved"].append(resolution)
            _log_once(
                logging.WARNING,
                (username, expert_id, name, "install_failed"),
                f"⚠️ 专家依赖技能添加失败（不阻断对话） | 用户: {username} | "
                f"专家: {expert_id} | 技能: {name} | 目录: {target_dir} | {exc}",
            )
            continue

        report["installed"].append(target_dir)
        report["resolved"].append(resolution)
        logger.info(
            f"🎁 自动添加专家依赖技能 | 用户: {username} | 专家: {expert_id} | "
            f"技能: {name} | 目录: {target_dir} | 匹配方式: {resolution['matched_by']} | 公共目录: {root}"
        )

    if report["installed"]:
        # 清单变了：已缓存的 Agent 看不到新技能（技能清单在构造时注入），必须驱逐。
        try:
            from .agent_manager import invalidate_user_agents

            report["cache_invalidated"] = int(invalidate_user_agents(username) or 0)
        except Exception as exc:  # noqa: BLE001 - 驱逐失败最多是下一轮才生效
            logger.warning(f"⚠️ 依赖技能添加后驱逐缓存 Agent 失败 | 用户: {username} | {exc}")

    return report


def ensure_expert_skills(username: str, expert_id: str | None) -> list[str]:
    """兼容入口：返回本次**实际添加**的技能名（详细状态见 ``provision_expert_skills``）。

    在 ``get_or_create_agent_for_session`` 之前调用：技能必须在 Agent 构造前登记，
    否则本轮扫描不到，且缓存命中会一直掩盖这个缺口。
    """
    return list(provision_expert_skills(username, expert_id).get("installed") or [])


def required_skills_status(username: str, expert_id: str) -> dict[str, Any]:
    """只读地给出依赖技能的安装现状（排查接口用，不写清单、不驱逐缓存）。"""
    root = builtin_skills.builtin_root()
    required = required_skills_of(expert_id, username)
    items = [_inspect_one(username, name) for name in required]
    return {
        "expert_id": expert_id,
        "auto_install": _auto_install_enabled(),
        "required": required,
        "skills_root": str(root) if root else "",
        "items": items,
    }


# ── 运行时提示 ────────────────────────────────────────────────────────

#: 会触发「技能不可用」运行时提示的跳过原因
_BLOCKING_REASONS = {"source_missing", "source_unconfigured", "install_failed", "disabled"}

_REASON_TEXT = {
    "source_missing": "服务端公共技能目录中没有这个技能包",
    "source_unconfigured": "服务端未配置公共技能目录（tools.skills_dir）",
    "install_failed": "写入用户技能清单失败",
    "disabled": "用户已在技能中心停用该技能",
}


def build_skill_runtime_notice(report: dict[str, Any]) -> str:
    """把报告转成一段注入系统提示词末尾的「运行时提示」；无需提示时返回空串。

    为什么要在提示词里说：技能是**渐进式披露**——没登记进清单，模型既看不到技能，
    也不知道"为什么没有"。只在后端记日志的话，用户只会得到一句「扫描不到 xxx」，
    无法定位。这段提示让模型直接给出原因与修复方式。
    """
    blocked = [
        item for item in (report.get("skipped") or [])
        if item.get("reason") in _BLOCKING_REASONS
    ]
    if not blocked:
        return ""

    lines = [
        "## 运行时环境提示（系统注入，不要复述本段标题）",
        "以下是本会话依赖技能的实际状态，优先级高于你的经验推断：",
    ]
    for item in blocked:
        skill = item.get("skill", "")
        reason = _REASON_TEXT.get(item.get("reason", ""), "不可用")
        resolved = item.get("resolved_dir", "")
        # 声明值与目录名不同时（如声明写的是中文 name），把目录名一并给出，
        # 否则运维在技能中心按名字找不到该包
        if resolved and resolved != skill:
            lines.append(f"- 依赖技能 `{skill}`（目录 `{resolved}`）当前不可用（{reason}）。")
        else:
            lines.append(f"- 依赖技能 `{skill}` 当前不可用（{reason}）。")
    lines.append(
        "- 若本轮任务依赖上述技能：先如实告知用户「技能未就绪」并给出上面的原因与修复方式"
        "（把技能包放入服务端公共技能目录，或在技能中心启用），"
        "**不要虚构技能产物，也不要声称已执行技能脚本**。"
    )
    lines.append("- 若本轮任务不需要该技能（如纯代码审查、方案设计），照常完成，不必提及本段。")
    return "\n".join(lines)
