"""技能数据通道预检：技能声明的取数工具 vs 本会话实际可用工具（2026-11-19）。

背景：``fpp-income-analysis`` 的 SKILL.md 第三节点名 12 个 ``*_tool``，而该用户
工作区的 ``.mcp.json`` 只配了外汇/贵金属数据服务，交集为 0。模型读完 274 行文档
才发现"技能要的工具一个都没有"，随后反复重读同一份文档（思考逐字相同 ×3）——
这不是文档写错，而是**技能装上了、数据通道没接上**。

这里在编译期把差集算出来，交给技能段提示词（``agent/skills_middleware.py``）：
模型第一眼就知道"这个技能现在取不到数"，不必读完再撞墙，也就没有"读文档—
发现没工具—重启计划"的循环路径。

**不新增配置项**：工具清单来自技能自身（frontmatter 的 ``metadata.include_tools``
+ 正文里反引号包裹的 ``*_tool``），可用工具来自本会话的工具集。

识别口径与 ``agent/skills.py::declared_include_tools`` 保持一致：只扫
``<dir>/<skill>/SKILL.md`` 一层，不递归、不读 references/ 与 scripts/。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from easy_agent.agent.skills import parse_skill_frontmatter, read_skill_md_cached
from easy_agent.utils.mcp_tool_names import resolve_tool_names

logger = logging.getLogger("easy_agent.agent")

#: 正文里以反引号包裹的取数工具名（``get_income_matrix_tool`` / ``list_dimensions_tool``）。
#: 只认 ``_tool`` 结尾的全小写蛇形名：SKILL.md 里还会反引号引用 ``get_income_matrix(...)``
#: 这种带参数的调用式写法，它们不是工具全名，不能算进声明清单。
_TOOL_REF_RE = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)*_tool)`")

#: 单个技能最多展示多少个缺失工具名（提示词里只列前几个 + 总数，避免技能段膨胀）。
_MAX_SHOWN = 4

#: 框架内置工具：技能可以用 ``include_tools`` 声明它们，但这类工具**永远在**
#: （不来自 MCP，也不进 ``raw_tools``）。不并入"可用集"会把完全可用的技能
#: 误标成"数据通道未接入"，让模型平白拒绝一个能用的技能。
FRAMEWORK_BUILTIN_TOOLS = frozenset(
    {
        "read_file",
        "read_uploaded_file",
        "write_file",
        "edit_file",
        "ls",
        "glob",
        "grep",
        "execute",
        "write_todos",
        "task",
    }
)


@dataclass(frozen=True)
class SkillGap:
    """一个技能的取数工具就位情况。"""

    declared: tuple[str, ...]
    """技能声明的工具名（去重、保序）。"""

    missing: tuple[str, ...]
    """本会话不可用的工具名。"""

    @property
    def total(self) -> int:
        return len(self.declared)

    @property
    def available(self) -> int:
        return self.total - len(self.missing)

    @property
    def fully_missing(self) -> bool:
        """声明的工具一个也不可用——技能在这个会话里等于没有数据通道。"""
        return bool(self.declared) and self.available == 0

    def summary(self) -> str:
        """日志用的一行摘要。"""
        return f"{self.available}/{self.total} 可用，缺失 {len(self.missing)} 个"


def declared_tools(text: str) -> tuple[str, ...]:
    """从一份 SKILL.md 正文里提取它声明的取数工具名（去重、保序）。

    同时接受两种写法：

    - frontmatter ``metadata.include_tools``（官方约定，空格分隔）；
    - 正文里的反引号引用（``get_income_matrix_tool``）——大量存量技能只在
      正文表格里点名工具，不写 include_tools，只看 frontmatter 会漏。

    解析失败（无 frontmatter、YAML 坏）时**不抛错**：正文反引号仍然照提，
    这层预检是"尽力而为"的提示，不能因为一个坏文件阻断装配。
    """
    names: list[str] = []

    meta, _error = parse_skill_frontmatter(text or "")
    extra = meta.get("metadata") if isinstance(meta, dict) else None
    raw = extra.get("include_tools") if isinstance(extra, dict) else None
    if isinstance(raw, str):
        names.extend(part for part in raw.split() if part)
    elif isinstance(raw, list):
        names.extend(str(part).strip() for part in raw if str(part).strip())

    names.extend(match.group(1) for match in _TOOL_REF_RE.finditer(text or ""))

    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return tuple(ordered)


def skill_tool_gaps(
    skill_dirs: Iterable[str | Path | None] | None,
    available: Iterable[str] | None,
) -> dict[str, SkillGap]:
    """按技能目录名汇总"声明了工具但会话里没有"的差集。

    Args:
        skill_dirs: 技能源目录（真实路径），通常是公共技能目录 + 用户技能目录。
        available: 本会话实际注入的工具名（含被技能门控摘出去的那部分）。

    Returns:
        ``{技能目录名: SkillGap}``。**没有任何声明**的技能不出现在结果里——
        不写 include_tools、正文也不点工具的技能照旧工作，不产生噪音。
    """
    actual = [str(name) for name in (available or ()) if name]
    gaps: dict[str, SkillGap] = {}

    try:
        for root in skill_dirs or ():
            if not root:
                continue
            base = Path(root)
            try:
                if not base.is_dir():
                    continue
                entries = sorted(base.iterdir())
            except OSError as exc:  # 目录级异常（权限 / 网络盘）不能阻断装配
                logger.warning("技能预检跳过目录 %s（%s）", base, exc)
                continue
            for entry in entries:
                skill_md = entry / "SKILL.md"
                if not entry.is_dir() or not skill_md.is_file():
                    continue
                text = read_skill_md_cached(skill_md)
                if text is None:
                    continue
                declared = declared_tools(text)
                if not declared:
                    continue
                # 复用 MCP 前缀兼容层：技能里写原名、实际工具带 ``server__`` 前缀时
                # 后缀唯一匹配仍算「就位」，否则开了前缀的环境会全员误报缺失。
                _resolved, unresolved = resolve_tool_names(declared, actual)
                if not unresolved:
                    continue
                missing_set = set(unresolved)
                gaps[entry.name] = SkillGap(
                    declared=declared,
                    missing=tuple(name for name in declared if name in missing_set),
                )
    except Exception as exc:  # noqa: BLE001 - 预检失败降级为"不提示"，绝不阻断建图
        logger.warning("技能数据通道预检失败，本次跳过（%s: %s）", type(exc).__name__, exc)
    return gaps


def gap_fingerprint(gaps: dict[str, SkillGap]) -> list[str]:
    """把差集压成稳定的指纹行，参与编译图缓存键。

    技能正文里的工具清单变了（重装/升级技能）必须重建编译图，否则会一直挂着
    上一次的"工具未就位"提示——那是会误导模型的错误信息，比不提示更糟。

    **必须同时包含 "缺失数/声明数"**：提示文案由 ``fully_missing`` 决定
    （全缺是 ⛔「数据通道未接入」，部分缺是 ⚠️「部分工具未就位」）。只压
    ``missing`` 时，"声明 A（缺）" 与 "声明 A + B（B 就位）" 会算出同一个指纹，
    技能升级后旧图会永久渲染 ⛔，让模型拒绝一个已经部分可用的技能。
    """
    return [
        f"gap:{name}={len(gap.missing)}/{gap.total}:{'|'.join(gap.missing)}"
        for name, gap in sorted(gaps.items())
        if gap.missing
    ]


def format_gap_hint(gap: SkillGap) -> str:
    """技能清单里给单个技能追加的提示行（一两行，不展开全部工具名）。"""
    shown = "、".join(gap.missing[:_MAX_SHOWN])
    more = f" 等共 {len(gap.missing)} 个" if len(gap.missing) > _MAX_SHOWN else ""
    if gap.fully_missing:
        return (
            f"  -> ⛔ 数据通道未接入：本技能需要的工具（{shown}{more}）"
            "在本会话全部不可用。**严禁再读取 SKILL.md 核对或换参数试探**："
            "立即停止尝试并直接告知用户「数据未就位」，明确列出上述缺失工具/服务。"
        )
    return (
        f"  -> ⚠️ 部分取数工具未就位（{shown}{more}）："
        "涉及这些缺失工具的步骤直接告知用户「数据未就位」并跳过，"
        "**严禁重复读取 SKILL.md 核对或换参数重试**。"
    )


__all__ = [
    "FRAMEWORK_BUILTIN_TOOLS",
    "SkillGap",
    "declared_tools",
    "format_gap_hint",
    "gap_fingerprint",
    "skill_tool_gaps",
]
