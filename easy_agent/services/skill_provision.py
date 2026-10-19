"""专家依赖技能的自动补装。

公共（预置）专家可以在 ``config.yaml`` 里用 ``required_skills`` 声明依赖技能，
例如因子研究专家依赖 ``factor-creator``。用户第一次用该专家对话时，这里把缺失的
技能从公共技能目录复制进用户技能库，使 ``/user-skills/`` 路由能扫到它。

**为什么不把技能打进专家包**：技能是用户可见、可停用、可升级的一等对象，走
「公共技能 + 用户安装」这套既有机制，用户在技能中心就能看到并管理它；专家包只
声明依赖，不复制一份自己的副本（否则同一技能多份、升级要改多处）。

四条设计约束（前两条是线上必须遵守的）：

1. **失败绝不阻断**：公共技能缺失、复制失败、磁盘满 —— 只记 warning，Agent 照常
   创建，专家提示词里有「未检测到技能」的降级分支。专家可用性不能被技能绑架。
2. **尊重用户停用**：技能被移到 ``skills-disabled/`` 是用户的主动意图，不自动装回；
   只有「启用目录与停用目录都没有（真删了）」才视为需要补装。
3. **只对公共预置专家生效**：自建专家的 ``skills_names`` 是展示标签，语义不同，
   混用会误导（用户以为写了名字就自动有技能）。
4. **幂等 + 只驱逐该用户**：重复调用只会有一份技能；装完驱逐该用户的缓存 Agent，
   否则旧 Agent 持有的技能清单里没有新技能。
"""

from __future__ import annotations

import logging

from . import public_expert_service, skill_library

logger = logging.getLogger("easy-agent")


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


def ensure_expert_skills(username: str, expert_id: str | None) -> list[str]:
    """确保专家声明的依赖技能已在用户技能库中；返回本次**实际安装**的技能名。

    在 ``get_or_create_agent_for_session`` 开头（缓存查找之前）调用：技能必须在
    Agent 构造前落盘，否则本轮扫描不到，且缓存命中会一直掩盖这个缺口。
    """
    if not username or not expert_id:
        return []
    if not _auto_install_enabled():
        return []

    required = required_skills_of(expert_id)
    if not required:
        return []

    installed: list[str] = []
    for name in required:
        existing = skill_library.find_user_skill(username, name)
        if existing:
            if not existing["enabled"]:
                logger.info(
                    f"⏸️ 专家依赖技能已被用户停用，不自动装回 | 用户: {username} | "
                    f"专家: {expert_id} | 技能: {name}"
                )
            continue
        try:
            result = skill_library.install_public_skill_to_user(username, name)
        except FileNotFoundError:
            logger.warning(
                f"⚠️ 专家依赖技能不在公共技能目录，已跳过（专家降级运行） | "
                f"专家: {expert_id} | 技能: {name} | "
                f"公共目录: {skill_library.public_skills_dir()}"
            )
            continue
        except Exception as exc:  # noqa: BLE001 - 安装失败不阻断对话
            logger.warning(
                f"⚠️ 专家依赖技能安装失败（不阻断对话） | 用户: {username} | "
                f"专家: {expert_id} | 技能: {name} | {exc}"
            )
            continue

        installed.append(name)
        # 按内容自动归类（失败不影响安装本身，归类可在技能中心手动调整）
        try:
            from . import skill_categories

            skill_categories.auto_assign_skill_dir(
                username, name, result["path"]
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"依赖技能自动归类失败（可忽略） | 技能: {name} | {exc}")

        logger.info(
            f"🎁 自动安装专家依赖技能 | 用户: {username} | 专家: {expert_id} | "
            f"技能: {name} | 目标: {result['path']}"
        )

    return installed
