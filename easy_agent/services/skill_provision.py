"""专家依赖技能的自动补装。

公共（预置）专家可以在 ``config.yaml`` 里用 ``required_skills`` 声明依赖技能，
例如因子研究专家依赖 ``factor-creator``。用户第一次用该专家对话时，这里把缺失的
技能从公共技能目录复制进用户技能库，使 ``/user-skills/`` 路由能扫到它。

**为什么不把技能打进专家包**：技能是用户可见、可停用、可升级的一等对象，走
「公共技能 + 用户安装」这套既有机制，用户在技能中心就能看到并管理它；专家包只
声明依赖，不复制一份自己的副本（否则同一技能多份、升级要改多处）。

判定顺序（每一轮建 Agent 前都会跑一遍，成本约 4ms）：

1. 用户技能库 ``workspace/{u}/skills/{name}`` 已有 → 跳过（已装）；
2. 用户把它拖到了 ``skills-disabled/`` → 跳过并**不装回**（停用是主动意图）；
3. 两个目录都没有 → 从公共技能目录（候选见 ``skill_library.public_skills_dir_candidates``）
   原子复制过来，然后驱逐该用户的缓存 Agent。

失败策略：**绝不让专家不可用**。装不上就明确记一条 ERROR（含「技能包该放哪」），
并把状态交给调用方在系统提示词里注入一句运行时提示，让模型对用户说清楚
「技能未就绪」，而不是假装执行。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from . import public_expert_service, skill_library

logger = logging.getLogger("easy-agent")

#: 同一 (用户, 专家, 技能, 原因) 的告警日志节流窗口（秒）——每轮对话都会检查，
#: 不节流会把"技能包没放"刷满整个日志。
_REPORT_INTERVAL = 600.0
_report_at: dict[tuple[str, str, str, str], float] = {}


def _auto_install_enabled() -> bool:
    """配置开关 ``expert.auto_install_required_skills``（默认开）。"""
    from .agent_manager import get_agent_config  # 延迟导入，避免模块级循环依赖

    cfg = get_agent_config()
    if not cfg:
        return True
    return bool(getattr(cfg.get("config"), "expert_auto_install_required_skills", True))


def required_skills_of(expert_id: str | None) -> list[str]:
    """读取公共专家声明的依赖技能目录名；非公共专家或无声明时返回空列表。

    只读 ``config.yaml``（不读提示词正文）：本函数在每轮创建 Agent 前都会跑，
    不能带来可感知的延迟。
    """
    if not expert_id:
        return []
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


def provision_expert_skills(username: str, expert_id: str | None) -> dict[str, Any]:
    """补装专家依赖技能，返回**结构化报告**（含失败原因，供提示词与诊断接口使用）。

    Returns:
        ``{
            "expert_id": str, "enabled": bool,
            "required": [技能名], "installed": [本次新装],
            "skipped": [{"skill", "reason", "detail"}],
            "missing": [公共目录里找不到的技能],
            "public_dirs": [尝试过的公共技能目录],
            "user_skills_dir": str,
        }``

    ``reason`` 取值：``existing``（用户已装）/ ``disabled``（用户停用）/
    ``source_missing``（公共技能目录里没有，需运维放包）/ ``install_failed``（复制失败）。
    """
    report: dict[str, Any] = {
        "expert_id": expert_id or "",
        "enabled": _auto_install_enabled(),
        "required": [],
        "installed": [],
        "skipped": [],
        "missing": [],
        "warnings": [],
        "public_dirs": [str(p) for p in skill_library.public_skills_dir_candidates()],
        "user_skills_dir": "",
    }
    if not username or not expert_id:
        return report

    report["user_skills_dir"] = str(skill_library.user_skills_dir(username))
    required = required_skills_of(expert_id)
    report["required"] = list(required)
    if not required or not report["enabled"]:
        return report

    for name in required:
        existing = skill_library.find_user_skill(username, name)
        if existing:
            reason = "existing" if existing["enabled"] else "disabled"
            report["skipped"].append({"skill": name, "reason": reason, "detail": str(existing["path"])})
            if reason == "disabled":
                _log_once(
                    logging.INFO,
                    (username, expert_id, name, reason),
                    f"⏸️ 专家依赖技能已被用户停用，不自动装回 | 用户: {username} | "
                    f"专家: {expert_id} | 技能: {name}",
                )
            continue

        try:
            result = skill_library.install_public_skill_to_user(username, name)
        except FileNotFoundError as exc:
            report["missing"].append(name)
            report["skipped"].append(
                {"skill": name, "reason": "source_missing", "detail": str(exc)}
            )
            searched = report["public_dirs"] or ["（未配置 tools.skills_dir）"]
            _log_once(
                logging.ERROR,
                (username, expert_id, name, "source_missing"),
                f"❌ 专家依赖技能缺失，无法自动安装 | 用户: {username} | 专家: {expert_id} | "
                f"技能: {name} | 请把该技能包放到以下任一目录（目录内需含 SKILL.md）: "
                f"{' 或 '.join(searched)} | 原因: {exc}",
            )
            continue
        except Exception as exc:  # noqa: BLE001 - 安装失败不阻断对话
            report["skipped"].append(
                {"skill": name, "reason": "install_failed", "detail": str(exc)}
            )
            _log_once(
                logging.WARNING,
                (username, expert_id, name, "install_failed"),
                f"⚠️ 专家依赖技能安装失败（不阻断对话） | 用户: {username} | "
                f"专家: {expert_id} | 技能: {name} | {exc}",
            )
            continue

        report["installed"].append(name)
        # 按内容自动归类（失败不影响安装本身，归类可在技能中心手动调整）
        try:
            from . import skill_categories

            skill_categories.auto_assign_skill_dir(username, name, result["path"])
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"依赖技能自动归类失败（可忽略） | 技能: {name} | {exc}")

        # 防「包名不对」：目录叫 factor-creator、SKILL.md 里却写着另一个技能的名字
        # （比如误把 expert-creator 拷过来）。这种包会被正常装上，但内容完全不是
        # 专家要的那个技能，模型照样跑不通，且极难排查——这里显式报警。
        try:
            declared = str(skill_library.skill_metadata(result["path"]).get("name") or "").strip()
            if declared and declared != name and skill_library.public_skill_source(declared):
                logger.warning(
                    f"⚠️ 依赖技能包疑似放错内容 | 目录: {name} | SKILL.md 声明: {declared} | "
                    f"公共目录里已存在同名技能 '{declared}'，请确认是否误拷"
                )
                report["warnings"] = report.get("warnings") or []
                report["warnings"].append(
                    f"技能包 '{name}' 的 SKILL.md 声明为 '{declared}'，疑似误拷其他技能"
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"依赖技能包名校验跳过 | 技能: {name} | {exc}")

        logger.info(
            f"🎁 自动安装专家依赖技能 | 用户: {username} | 专家: {expert_id} | "
            f"技能: {name} | 目标: {result['path']}"
        )

    return report


def ensure_expert_skills(username: str, expert_id: str | None) -> list[str]:
    """兼容入口：返回本次**实际安装**的技能名（详细状态见 ``provision_expert_skills``）。

    在 ``get_or_create_agent_for_session`` 开头（缓存查找之前）调用：技能必须在
    Agent 构造前落盘，否则本轮扫描不到，且缓存命中会一直掩盖这个缺口。
    """
    return list(provision_expert_skills(username, expert_id).get("installed") or [])


#: 会触发「技能不可用」运行时提示的跳过原因
_BLOCKING_REASONS = {"source_missing", "install_failed", "disabled"}

_REASON_TEXT = {
    "source_missing": "公共技能目录中未找到该技能包",
    "install_failed": "安装到用户技能库失败",
    "disabled": "用户已停用该技能",
}


def build_skill_runtime_notice(report: dict[str, Any]) -> str:
    """把报告转成一段注入系统提示词末尾的「运行时提示」；无需提示时返回空串。

    为什么要在提示词里说：技能是**渐进式披露**——没装进 ``/user-skills/``，模型既
    看不到技能清单，也不知道"为什么没有"。只在后端记日志的话，用户只会得到一句
    「扫描不到 factor-creator」，无法定位。这段提示让模型直接给出原因与修复方式。
    """
    blocked = [
        item for item in (report.get("skipped") or [])
        if item.get("reason") in _BLOCKING_REASONS
    ]
    warnings = list(report.get("warnings") or [])
    if not blocked and not warnings:
        return ""

    lines = [
        "## 运行时环境提示（系统注入，不要复述本段标题）",
        "以下是本会话的实际技能安装状态，优先级高于你的经验推断：",
    ]
    for item in blocked:
        skill = item.get("skill", "")
        reason = _REASON_TEXT.get(item.get("reason", ""), "不可用")
        lines.append(f"- 依赖技能 `{skill}` 当前不可用（{reason}）。")
    for warning in warnings:
        lines.append(f"- ⚠️ {warning}，请先与用户确认技能内容是否正确。")
    lines.append(
        "- 若本轮任务依赖上述技能：先如实告知用户「技能未就绪或不可信」并给出上面的原因与"
        "修复方式（把正确的技能包放入服务端公共技能目录，或由用户在技能中心安装/启用），"
        "**不要虚构技能产物，也不要声称已执行技能脚本**。"
    )
    lines.append("- 若本轮任务不需要该技能（如纯代码审查、方案设计），照常完成，不必提及本段。")
    return "\n".join(lines)
