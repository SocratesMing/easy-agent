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


# ── 单个技能的现状 / 补装 ─────────────────────────────────────────────


def _inspect_one(username: str, name: str) -> dict[str, Any]:
    """判断单个依赖技能当前的状态（只读，只解析不改动）。

    声明值可以是**目录名**，也可以是 SKILL.md 的 ``name``（常见为中文）——统一经
    ``skill_library.resolve_user_skill_ref`` / ``resolve_public_skill_ref`` 解析出
    canonical 目录名，后续一律用目录名复制，不会产生第二套数据。

    Returns:
        ``{"skill", "declared", "resolved_dir", "matched_by", "state", "detail",
        "path", "candidates"}``；``state`` 取值 ``existing`` / ``disabled`` /
        ``install_ready`` / ``source_missing`` / ``source_unconfigured``。
    """
    declared = name

    def _pack(
        state: str,
        *,
        dir_name: str = "",
        matched_by: str = "",
        detail: str = "",
        path: str = "",
        candidates: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
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

    # 1) 用户技能库（启用 + 停用）：先精确目录名，再按 name 别名
    found = skill_library.find_user_skill(username, declared)
    if found:
        state = "existing" if found["enabled"] else "disabled"
        return _pack(
            state,
            dir_name=found["dir_name"],
            matched_by="dir" if found["dir_name"] == declared else "alias",
            path=str(found["path"]),
        )

    # 2) 公共技能目录：目录名 / 归一化 / name / name 归一化
    if not skill_library.public_skills_dir_candidates():
        return _pack("source_unconfigured", detail="未配置 tools.skills_dir")
    hit = skill_library.resolve_public_skill_ref(declared)
    if hit:
        return _pack(
            "install_ready",
            dir_name=hit["dir_name"],
            matched_by=hit["matched_by"],
            path=str(hit["path"]),
        )

    # 3) 都没命中：给出近似候选，方便运维判断是「声明写错」还是「技能包没放」
    searched = "；".join(str(p) for p in skill_library.public_skills_dir_candidates())
    return _pack(
        "source_missing",
        detail=(
            f"公共技能目录中既没有目录 {declared}/，也没有 name 为 {declared} 的技能"
            f"（已查找: {searched}）"
        ),
        candidates=skill_library.suggest_public_skill_refs(declared),
    )


def provision_expert_skills(username: str, expert_id: str | None) -> dict[str, Any]:
    """补装专家依赖技能，返回**结构化报告**（含失败原因，供提示词与诊断接口使用）。

    Returns:
        ``{
            "expert_id": str, "enabled": bool,
            "required": [声明值（目录名或 name）],
            "resolved": [{"declared", "resolved_dir", "matched_by"}],
            "installed": [本次新装的目录名],
            "skipped": [{"skill", "reason", "detail", "path", "resolved_dir",
                         "matched_by", "candidates"}],
            "missing": [公共目录里解析不到的声明],
            "public_dirs": [尝试过的公共技能目录],
            "user_skills_dir": str,
        }``

    ``reason`` 取值：``existing``（用户已装）/ ``disabled``（用户停用）/
    ``source_missing``（公共技能目录里没有，需运维放包）/ ``source_unconfigured``
    （未配置公共技能目录）/ ``install_failed``（复制失败）。

    ``required`` 里的声明值可以是目录名，也可以是 SKILL.md 的 ``name``（常见为中文）；
    ``resolved`` 记录每条声明实际解析到的目录名与匹配方式，``installed`` 一律是
    canonical 目录名，便于排查「声明写错」与「技能包没放」的区别。
    """
    report: dict[str, Any] = {
        "expert_id": expert_id or "",
        "enabled": _auto_install_enabled(),
        "required": [],
        "resolved": [],
        "installed": [],
        "skipped": [],
        "missing": [],
        "warnings": [],
        "public_dirs": [str(p) for p in skill_library.public_skills_dir_candidates()],
        "user_skills_dir": "",
    }
    if not username or not expert_id:
        # 没有专家 ID 就不补装。这里刻意只打 debug：每轮通用对话都会走到，
        # 用 info 会把日志刷满。真正的排查点是 api/chat.py 那行「expert: xxx」。
        logger.debug(
            f"依赖技能补装跳过 | 无专家 ID | 用户: {username or '(空)'} | expert: {expert_id or '(空)'}"
        )
        return report

    report["user_skills_dir"] = str(skill_library.user_skills_dir(username))
    required = required_skills_of(expert_id)
    report["required"] = list(required)
    if not required or not report["enabled"]:
        # 「专家没声明 required_skills」和「开关关了」是两种完全不同的原因，但现象一样
        # （技能不装）。命中率低，打 info 不刷屏，排查时能直接看到是哪一种。
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
            report["resolved"].append(resolution)
            report["skipped"].append(
                {
                    "skill": name,
                    "reason": "existing",
                    "detail": state["path"],
                    "resolved_dir": resolved_dir,
                    "matched_by": resolution["matched_by"],
                }
            )
            continue

        if state["state"] == "disabled":
            report["resolved"].append(resolution)
            report["skipped"].append(
                {
                    "skill": name,
                    "reason": "disabled",
                    "detail": state["path"],
                    "resolved_dir": resolved_dir,
                    "matched_by": resolution["matched_by"],
                }
            )
            _log_once(
                logging.INFO,
                (username, expert_id, name, "disabled"),
                f"⏸️ 专家依赖技能已被用户停用，不自动装回 | 用户: {username} | "
                f"专家: {expert_id} | 技能: {name} | 目录: {resolved_dir or '(未解析)'}",
            )
            continue

        if state["state"] in {"source_missing", "source_unconfigured"}:
            report["missing"].append(name)
            report["resolved"].append(resolution)
            report["skipped"].append(
                {
                    "skill": name,
                    "reason": state["state"],
                    "detail": state["detail"],
                    "resolved_dir": resolved_dir,
                    "matched_by": resolution["matched_by"],
                    "candidates": state.get("candidates") or [],
                }
            )
            searched = report["public_dirs"] or ["（未配置 tools.skills_dir）"]
            _log_once(
                logging.ERROR,
                (username, expert_id, name, state["state"]),
                f"❌ 专家依赖技能缺失，无法自动安装 | 用户: {username} | 专家: {expert_id} | "
                f"技能: {name} | 请把该技能包放到以下任一目录（目录内需含 SKILL.md）: "
                f"{' 或 '.join(searched)} | 原因: {state['detail']} | "
                f"候选: {state.get('candidates') or '无'}",
            )
            continue

        # 解析到 canonical 目录名后统一复制；声明写的是 name 时也不影响落盘目录名
        target_dir = resolved_dir or name
        try:
            result = skill_library.install_public_skill_to_user(username, target_dir)
        except FileExistsError:  # 并发兜底：另一路刚装好，等价于已装
            report["resolved"].append(resolution)
            report["skipped"].append(
                {
                    "skill": name,
                    "reason": "existing",
                    "detail": state["path"],
                    "resolved_dir": target_dir,
                    "matched_by": resolution["matched_by"],
                }
            )
            continue
        except Exception as exc:  # noqa: BLE001 - 安装失败不阻断对话
            report["resolved"].append(resolution)
            report["skipped"].append(
                {
                    "skill": name,
                    "reason": "install_failed",
                    "detail": str(exc),
                    "resolved_dir": resolved_dir,
                    "matched_by": resolution["matched_by"],
                }
            )
            _log_once(
                logging.WARNING,
                (username, expert_id, name, "install_failed"),
                f"⚠️ 专家依赖技能安装失败（不阻断对话） | 用户: {username} | "
                f"专家: {expert_id} | 技能: {name} | 目录: {target_dir} | {exc}",
            )
            continue

        report["installed"].append(target_dir)
        report["resolved"].append(resolution)
        # 按内容自动归类（失败不影响安装本身，归类可在技能中心手动调整）
        try:
            from . import skill_categories

            skill_categories.auto_assign_skill_dir(username, target_dir, result["path"])
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"依赖技能自动归类失败（可忽略） | 技能: {target_dir} | {exc}")

        # 防「包名不对」：目录叫 factor-creator、SKILL.md 里却写着另一个技能的名字
        # （比如误把 expert-creator 拷过来）。这种包会被正常装上，但内容完全不是
        # 专家要的那个技能，模型照样跑不通，且极难排查——这里显式报警。
        try:
            declared_name = str(
                skill_library.skill_metadata(result["path"]).get("name") or ""
            ).strip()
            if (
                declared_name
                and declared_name != target_dir
                and skill_library.public_skill_source(declared_name)
            ):
                logger.warning(
                    f"⚠️ 依赖技能包疑似放错内容 | 目录: {target_dir} | "
                    f"SKILL.md 声明: {declared_name} | "
                    f"公共目录里已存在同名技能 '{declared_name}'，请确认是否误拷"
                )
                report["warnings"].append(
                    f"技能包 '{target_dir}' 的 SKILL.md 声明为 '{declared_name}'，疑似误拷其他技能"
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"依赖技能包名校验跳过 | 技能: {target_dir} | {exc}")

        logger.info(
            f"🎁 自动安装专家依赖技能 | 用户: {username} | 专家: {expert_id} | "
            f"技能: {name} | 目录: {target_dir} | 匹配方式: {resolution['matched_by']} | "
            f"目标: {result['path']}"
        )

    return report


def ensure_expert_skills(username: str, expert_id: str | None) -> list[str]:
    """兼容入口：返回本次**实际安装**的技能名（详细状态见 ``provision_expert_skills``）。

    在 ``get_or_create_agent_for_session`` 开头（缓存查找之前）调用：技能必须在
    Agent 构造前落盘，否则本轮扫描不到，且缓存命中会一直掩盖这个缺口。
    """
    return list(provision_expert_skills(username, expert_id).get("installed") or [])


#: 会触发「技能不可用」运行时提示的跳过原因
_BLOCKING_REASONS = {"source_missing", "source_unconfigured", "install_failed", "disabled"}

_REASON_TEXT = {
    "source_missing": "公共技能目录中未找到该技能包",
    "source_unconfigured": "服务端未配置公共技能目录（tools.skills_dir）",
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
        resolved = item.get("resolved_dir") or ""
        if resolved and resolved != skill:
            # 声明写的是展示名（常见中文），实际目录名不同——两个都给出，便于用户自查
            lines.append(f"- 依赖技能 `{skill}`（目录 `{resolved}`）当前不可用（{reason}）。")
        else:
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
