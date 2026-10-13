"""专家 Agent 的生成与版本目录管理。

职责（见 ``docs/expert-agent-solution.md`` §4、§6）：

1. **LLM 生成**专家配置：输入「引导词模板（含分类标签候选）+ 用户描述」，输出严格
   JSON（``scenario`` / ``capabilities`` / ``workflow`` / ``boundaries`` /
   ``system_prompt`` / ``features_prompt`` / ``description`` / ``skills_names`` /
   ``tag_id``），带剥围栏 + 重试 + 字段校验，``tag_id`` 会按配置的候选列表做白名单
   校验（编造的标签会被清空）；
2. **版本目录**构建：``workspace/{username}/experts/{export_agent_id}/v{n}/``
   下写 ``{英文名}.md``（结构化专家提示词，文件名取英文标识名、不含随机
   数字）/ ``README.md`` / ``config.yaml`` / ``manifest.json``，外层 ``active``
   文本文件记录当前生效版本号。修改时新建 ``v{n+1}`` 而**不覆盖** ``v{n}``，
   保证运行中的旧会话不会被半成品配置影响；
3. **运行时读取**：``load_active_prompt()`` 只读 ``active`` 指向的那一份
   提示词文件（兼容旧版 ``EXPERT.md`` 与新版 ``{英文名}.md``），读取失败返回
   空串，由调用方降级为通用 Agent；
4. **旧版本清理**：``cleanup_old_versions()`` 按「保留最近 N 个版本」+「保留
   N 天」双策略清目录，**永不删除 ``active`` 指向的版本**。

本模块只做「文件 + LLM」，不碰数据库（落库见 ``db/expert_store.py``）。
"""

import json
import logging
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

from ..config import Config
from .prompt_loader import get_prompts_dir

logger = logging.getLogger(__name__)

# 引导词模板在提示词目录下的相对路径（走 PROMPTS_DIR，天然可配置）
GUIDE_RELATIVE_PATH = "expert/create_agent_guide.md"

# 引导词内置兜底：prompts 目录里没有模板文件时使用（与 prompt_loader
# 「文件为权威、代码兜底」的约定一致）
DEFAULT_GUIDE = """你是一个「专家 Agent 配置生成器」。请根据用户的描述，为这个专家生成完整的配置。

要求：
1. 只输出一个 JSON 对象，不要输出任何解释文字、不要使用 Markdown 代码围栏。
2. JSON 字段如下（前 6 个必填，其余可选但强烈建议提供）：
   - "scenario": 字符串，专家应用场景描述（80~200 字）
   - "capabilities": 字符串数组，核心能力（3~6 条，每条一句话）
   - "workflow": 字符串数组，工作流程步骤（3~6 步，按执行顺序）
   - "boundaries": 字符串数组，操作边界 / 禁止事项（2~5 条）
   - "system_prompt": 字符串，完整的专家系统提示词正文（300~800 字，用第二人称
     "你是……" 撰写，包含角色定位、专业方法、输出规范、与用户交互的方式）
   - "features_prompt": 字符串数组，能力示例提示词（3~5 条，可直接点击使用的短句）
   - "description": 字符串，专家的主要功能描述（**不超过 300 字**），用于卡片与详情页
     展示。写成一段连贯的话，聚焦"这个专家能帮用户做什么"，不要写营销式口号
   - "skills_names": 字符串数组，**恰好 3 个**技能标签（每个 2~6 个汉字），根据用户
     提供的经验信息提炼，体现该专家最核心的专业专长；若经验与金融相关，必须使用
     金融类专业术语（如 "宏观投研"、"债券投研"、"因子分析"、"风险控制"、"资负测算"）
   - "tag_id": 字符串，从下面的「候选分类标签」中选出**唯一一个**最匹配的 tag_id；
     确实都不匹配时输出 "other"
   - "name_en": 字符串，专家英文标识名（小写字母/数字/连字符，3~40 字符，
     体现专长领域，如 "stock-invest-advisor"、"quant-factor-researcher"）
   - "profession_zh": 字符串，专家头衔中文（如 "资深量化策略研究员"）
   - "profession_en": 字符串，专家头衔英文（如 "Senior Quantitative Researcher"）
   - "output_spec": 字符串数组，输出规范（3~6 条：结论呈现结构、数据标注要求、
     语言风格与长度偏好等）

候选分类标签（tag_id: tag_name）：
{tag_options}

用户输入的专家名称：{name_cn}
用户对专家的描述：{user_prompt}
"""

_MAX_NAME_LEN = 50
_MAX_PROMPT_LEN = 4000
# 专家功能描述上限（前端卡片与详情页展示用；按需求不超过 300 字）
MAX_DESCRIPTION_LEN = 300
# 技能标签数量上限（引导词要求 3 个，这里放宽到 5 个做兜底截断）
MAX_SKILLS_NAMES = 5


# ── 标识生成 ──────────────────────────────────────────────────────────

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def slugify(name: str, max_len: int = 24) -> str:
    """把中英文名称转成稳定的 URL/文件名友好的 slug。

    中文无法音译，故保留 ``[a-z0-9-]`` 后若为空，回退为 ``expert``（唯一性由
    追加的 ``md5(id)[:8]`` 保证）。
    """
    text = (name or "").strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9\-_]", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text[:max_len] or "expert"


def build_expert_ids(name_cn: str) -> tuple[str, str]:
    """生成 ``(export_agent_id, export_name_en)``。

    - ``export_agent_id``：``expert_{slug}_{uuid8}``，全局唯一（同时也是目录名）；
    - ``export_name_en``：``{slug}``，**不含随机数字**（需求：配置包内的提示词
      文件按英文名命名，如 ``stock-invest-advisor.md``）。纯中文名的 slug 兜底为
      ``expert``，LLM 生成配置后会用更贴切的 ``name_en`` 回写覆盖（见
      ``api/expert.py`` 的 ``_generate_and_store``）。同名专家靠 ``export_agent_id``
      区分，目录互不冲突，故 ``export_name_en`` 不加 UNIQUE 约束。
    """
    import uuid

    slug = slugify(name_cn)
    agent_id = f"expert_{slug}_{uuid.uuid4().hex[:8]}"
    return agent_id, slug


# ── 引导词模板 ────────────────────────────────────────────────────────


def load_create_guide() -> str:
    """读取创建专家的引导词模板，缺失时返回内置兜底文案。"""
    path = get_prompts_dir() / GUIDE_RELATIVE_PATH
    try:
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            if content.strip():
                return content
    except OSError as exc:  # noqa: BLE001
        logger.warning("读取专家引导词模板失败，使用内置兜底: %s", exc)
    return DEFAULT_GUIDE


def render_tag_options(tags: Optional[list[dict]] = None) -> str:
    """把配置里的 ``expert_tags`` 渲染成引导词里的候选清单文本。

    同时支持 ``[{tag_id, tag_name}]`` 与 ``{tag_id: tag_name}`` 两种结构；
    列表为空时返回一段兜底说明（此时引导词会要求模型输出 ``other``）。
    """
    lines: list[str] = []
    if isinstance(tags, dict):
        tags = [{"tag_id": k, "tag_name": v} for k, v in tags.items()]
    for item in tags or []:
        if not isinstance(item, dict):
            continue
        tag_id = str(item.get("tag_id") or "").strip()
        tag_name = str(item.get("tag_name") or "").strip()
        if tag_id and tag_name:
            lines.append(f"- {tag_id}: {tag_name}")
    if not lines:
        return "（未配置候选标签，tag_id 一律输出 \"other\"）"
    return "\n".join(lines)


def render_guide(
    name_cn: str, user_prompt: str, tag_options: Optional[list[dict]] = None
) -> str:
    """把模板里的单花括号占位替换为实际值。

    用 ``str.replace`` 而非 ``str.format``：模板里可能出现 JSON 花括号，
    ``format`` 会把它们当占位符而报错。
    """
    guide = load_create_guide()
    return (
        guide.replace("{tag_options}", render_tag_options(tag_options))
        .replace("{name_cn}", name_cn or "")
        .replace("{user_prompt}", user_prompt or "")
        .replace("{prompt}", user_prompt or "")
    )


# ── LLM 产物解析 ──────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json_object(text: str) -> dict:
    """从 LLM 输出中尽力提取一个 JSON 对象。

    依次尝试：直接 ``json.loads`` -> 剥掉 ``` 代码围栏 -> 截取首个 ``{`` 到
    最后一个 ``}``。全部失败抛 ``ValueError``。
    """
    if not text or not text.strip():
        raise ValueError("LLM 输出为空")

    candidates: list[str] = [text.strip()]
    stripped = _FENCE_RE.sub("", text.strip())
    if stripped != candidates[0]:
        candidates.append(stripped)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("LLM 输出不是合法 JSON 对象")


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in value.splitlines() if p.strip()]
        return parts or [value.strip()]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if str(text).strip():
                    out.append(str(text).strip())
        return out
    return [str(value)]


def normalize_config(data: dict) -> dict:
    """把 LLM 产物规整成固定结构，并做最小必要校验。

    Raises:
        ValueError: 缺少必需的 ``system_prompt``（没有它专家就退化成通用 Agent）。
    """
    system_prompt = data.get("system_prompt") or data.get("systemPrompt") or ""
    if isinstance(system_prompt, list):
        system_prompt = "\n".join(str(x) for x in system_prompt)
    system_prompt = str(system_prompt).strip()
    if not system_prompt:
        raise ValueError("LLM 产物缺少 system_prompt")

    description = str(
        data.get("description") or data.get("export_description") or ""
    ).strip()
    if len(description) > MAX_DESCRIPTION_LEN:
        # 需求：功能描述不超过 300 字（卡片/详情页展示用）
        description = description[:MAX_DESCRIPTION_LEN].rstrip() + "…"

    skills_names = _as_str_list(
        data.get("skills_names") or data.get("export_skills_names")
    )[:MAX_SKILLS_NAMES]

    return {
        "scenario": str(data.get("scenario") or "").strip(),
        "capabilities": _as_str_list(data.get("capabilities")),
        "workflow": _as_str_list(data.get("workflow")),
        "boundaries": _as_str_list(data.get("boundaries")),
        "system_prompt": system_prompt,
        "features_prompt": _as_str_list(data.get("features_prompt")),
        # 卡片/详情页展示字段：功能描述 + 技能标签
        "description": description,
        "skills_names": skills_names,
        # 分类标签（原始值；是否在候选列表内由 generate_expert_config 校验）
        "tag_id": str(data.get("tag_id") or data.get("tagId") or "").strip(),
        # 可选扩展字段：英文标识名 / 头衔 / 输出规范（LLM 未给时留空，由
        # write_expert_version 用中文名 slug 等兜底）
        "name_en": str(data.get("name_en") or data.get("nameEn") or "").strip(),
        "profession_zh": str(data.get("profession_zh") or data.get("professionZh") or "").strip(),
        "profession_en": str(data.get("profession_en") or data.get("professionEn") or "").strip(),
        "output_spec": _as_str_list(data.get("output_spec") or data.get("outputSpec")),
    }


def pick_tag_id(raw_tag_id: str, tags: Optional[list[dict]] = None) -> str:
    """校验 LLM 给出的 ``tag_id`` 是否在配置的候选列表内。

    不在候选内（或没配候选）时返回空串，**不落脏标签**——宁可前端显示为
    「未分类」，也不要让大模型编造的标签污染筛选 chips。
    """
    value = (raw_tag_id or "").strip()
    if not value:
        return ""
    allowed = set()
    if isinstance(tags, dict):
        tags = [{"tag_id": k} for k in tags]
    for item in tags or []:
        if isinstance(item, dict):
            tag_id = str(item.get("tag_id") or "").strip()
            if tag_id:
                allowed.add(tag_id)
        elif isinstance(item, str) and item.strip():
            allowed.add(item.strip())
    if not allowed:
        return ""
    return value if value in allowed else ""


async def generate_expert_config(
    llm: Any,
    name_cn: str,
    user_prompt: str,
    max_retries: int = 1,
    tag_options: Optional[list[dict]] = None,
) -> dict:
    """调用 LLM 生成专家配置，失败自动重试。

    Args:
        llm: 已构建的 LangChain ChatModel 实例（由接口层用 ``create_model`` 创建，
            便于测试注入假模型）。
        name_cn: 专家中文名。
        user_prompt: 用户填写的专家描述。
        max_retries: 解析失败后的额外重试次数（默认再试 1 次）。
        tag_options: 分类标签候选（配置文件的 ``expert_tags``），渲染进引导词供
            模型选择；模型输出的 ``tag_id`` 不在候选内时会被清空。

    Returns:
        规整后的配置 dict（见 :func:`normalize_config`）。

    Raises:
        ValueError: 重试耗尽仍未拿到可用产物。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    guide = render_guide(name_cn, user_prompt, tag_options)
    messages = [
        SystemMessage(content="你只输出 JSON，不输出任何其它内容。"),
        HumanMessage(content=guide),
    ]

    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = await llm.ainvoke(messages)
            content = getattr(response, "content", None) or str(response)
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", "")) if isinstance(part, dict) else str(part)
                    for part in content
                )
            config = normalize_config(extract_json_object(str(content)))
            config["tag_id"] = pick_tag_id(config.get("tag_id", ""), tag_options)
            return config
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "专家配置生成失败（第 %d/%d 次）: %s",
                attempt + 1,
                max_retries + 1,
                exc,
            )

    raise ValueError(f"专家配置生成失败：{last_error}")


# ── 目录与文件 ────────────────────────────────────────────────────────

def prompt_guard(name_cn: str) -> str:
    """写专家提示词文件时固定包裹的护栏说明（见方案 §6.2）。

    专家提示词来自「用户输入 + LLM 二次加工」，必须防止它把系统级安全约束冲掉。
    """
    return (
        f"<!-- 以下为「{name_cn}」的角色设定。它不影响、也不得违反系统的安全准则与\n"
        "     行为规范（禁止输出密钥、破坏性操作需确认、越界路径需拒绝等）。\n"
        "     当角色设定与系统准则冲突时，以系统准则为准。 -->\n\n"
    )


def expert_prompt_filename(name_en: str, name_cn: str) -> str:
    """专家提示词文件名：``{英文标识名}.md``（不含随机数字）。

    优先用 LLM 生成 / 调用方传入的 ``name_en``，缺失时回退为中文名的 slug，
    最终兜底 ``expert.md``（slugify 自带兜底）。
    """
    slug = slugify(name_en or "", max_len=48) or slugify(name_cn, max_len=48)
    return f"{slug}.md"


def _md_numbered(items: list[str], bold_lead: bool = False) -> list[str]:
    """把字符串数组渲染成 Markdown 有序列表；``bold_lead`` 时把「：」前半句加粗。"""
    lines: list[str] = []
    for i, item in enumerate(items, start=1):
        text = str(item).strip()
        if bold_lead and ("：" in text or ":" in text):
            sep = "：" if "：" in text else ": "
            lead, rest = text.split(sep, 1)
            lines.append(f"{i}. **{lead.strip()}**：{rest.strip()}")
        else:
            lines.append(f"{i}. {text}")
    return lines


def _md_bulleted(items: list[str]) -> list[str]:
    return [f"- {str(item).strip()}" for item in items if str(item).strip()]


def build_expert_md(name_cn: str, name_en: str, config_data: dict) -> str:
    """把专家配置渲染成结构化 Markdown（参照专家包规范）。

    结构：YAML frontmatter（name/description/displayName/profession/maxTurns）
    → 护栏注释 → 标题 → 角色正文（system_prompt）→ 核心能力 / 工作流程 /
    输出规范 / 注意事项。
    """
    scenario = str(config_data.get("scenario") or "").strip()
    profession_zh = str(config_data.get("profession_zh") or "").strip() or name_cn
    profession_en = str(config_data.get("profession_en") or "").strip() or name_en

    frontmatter = {
        "name": name_en,
        "description": scenario,
        "displayName": {"en": name_en, "zh": name_cn},
        "profession": {"en": profession_en, "zh": profession_zh},
        "maxTurns": 50,
    }
    fm_text = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).rstrip("\n")

    parts: list[str] = [f"---\n{fm_text}\n---\n", prompt_guard(name_cn), f"# {name_cn}\n"]

    body = str(config_data.get("system_prompt") or "").strip()
    if body:
        parts.append(body + "\n")

    capabilities = config_data.get("capabilities") or []
    if capabilities:
        parts.append("## 核心能力\n")
        parts.extend(_md_numbered([str(c) for c in capabilities], bold_lead=True))
        parts.append("")

    workflow = config_data.get("workflow") or []
    if workflow:
        parts.append("## 工作流程\n")
        parts.extend(_md_numbered([str(w) for w in workflow]))
        parts.append("")

    output_spec = config_data.get("output_spec") or []
    if output_spec:
        parts.append("## 输出规范\n")
        parts.extend(_md_bulleted([str(o) for o in output_spec]))
        parts.append("")

    boundaries = config_data.get("boundaries") or []
    if boundaries:
        parts.append("## 注意事项\n")
        parts.extend(_md_bulleted([str(b) for b in boundaries]))
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def build_expert_readme(
    name_cn: str,
    name_en: str,
    version: int,
    config_data: dict,
    *,
    model_name: str = "",
    generated_at: str = "",
) -> str:
    """生成版本目录里的 ``README.md``：告诉用户这个专家是什么、怎么用。"""
    scenario = str(config_data.get("scenario") or "").strip()
    capabilities = [str(c).strip() for c in (config_data.get("capabilities") or []) if str(c).strip()]
    features = [str(f).strip() for f in (config_data.get("features_prompt") or []) if str(f).strip()]
    boundaries = [str(b).strip() for b in (config_data.get("boundaries") or []) if str(b).strip()]
    prompt_file = expert_prompt_filename(name_en, name_cn)

    lines: list[str] = [f"# {name_cn}\n"]
    if scenario:
        lines.append(f"{scenario}\n")

    lines.extend(
        [
            "## 类型\n",
            "Agent 型（单个 AI 专家）\n",
        ]
    )

    if capabilities:
        lines.append("## 功能\n")
        lines.extend(_md_bulleted(capabilities))
        lines.append("")

    if features:
        lines.append("## 使用示例\n")
        lines.extend(_md_bulleted(features))
        lines.append("")

    lines.extend(
        [
            "## 使用方式\n",
            "1. 在「专家 · 技能 · 连接器」面板的**专家** Tab 中找到该专家，点击「使用」；",
            "2. 输入框工具栏出现专家胶囊后，后续对话即由该专家以其专属人设与工作流回答；",
            "3. 点击胶囊上的 × 可退出专家模式，回到通用智能体。\n",
            "## 版本信息\n",
            f"- 版本：v{int(version)}",
            f"- 生成时间：{generated_at or '未知'}",
            f"- 生成模型：{model_name or '未记录'}\n",
            "## 目录结构\n",
            f"- `{prompt_file}`：专家提示词（运行时注入的系统人设，本包的核心文件）",
            "- `README.md`：本说明文件",
            "- `config.yaml`：结构化配置（场景 / 能力 / 流程 / 边界 / 示例提示词）",
            "- `manifest.json`：生成元数据（模型、生成时间、原始 LLM 输出）\n",
        ]
    )

    if boundaries:
        lines.append("## 注意事项\n")
        lines.extend(_md_bulleted(boundaries))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def get_expert_root(username: str) -> Path:
    """专家根目录：``{workspace_dir}/{username}/experts/``。"""
    return Config.get_user_workspace_dir(username) / "experts"


def get_expert_dir(username: str, expert_agent_id: str) -> Path:
    return get_expert_root(username) / expert_agent_id


def get_version_dir(username: str, expert_agent_id: str, version: int) -> Path:
    return get_expert_dir(username, expert_agent_id) / f"v{int(version)}"


def _atomic_write(path: Path, content: str) -> None:
    """先写临时文件再替换，避免读到半成品文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def write_expert_version(
    username: str,
    expert_agent_id: str,
    version: int,
    name_cn: str,
    config_data: dict,
    *,
    name_en: str = "",
    model_name: str = "",
    raw_output: Optional[dict] = None,
    activate: bool = True,
) -> str:
    """写一个版本目录并（可选）把 ``active`` 指向它。

    目录内容：

    - ``{英文名}.md``：结构化专家提示词（frontmatter + 角色 + 能力/流程/规范），
      文件名取 ``name_en``（无随机数字），缺失时回退中文名 slug；
    - ``README.md``：面向用户的使用说明；
    - ``config.yaml`` / ``manifest.json``：配置与生成元数据。

    Returns:
        该版本目录的绝对路径字符串。
    """
    version_dir = get_version_dir(username, expert_agent_id, version)
    version_dir.mkdir(parents=True, exist_ok=True)

    final_name_en = slugify(name_en or "", max_len=48) or slugify(name_cn)
    generated_at = datetime.now().isoformat(timespec="seconds")

    prompt_file = expert_prompt_filename(final_name_en, name_cn)
    _atomic_write(
        version_dir / prompt_file,
        build_expert_md(name_cn, final_name_en, config_data),
    )
    _atomic_write(
        version_dir / "README.md",
        build_expert_readme(
            name_cn,
            final_name_en,
            version,
            config_data,
            model_name=model_name,
            generated_at=generated_at,
        ),
    )

    config_yaml = {
        "expert_agent_id": expert_agent_id,
        "name_cn": name_cn,
        "name_en": final_name_en,
        "version": int(version),
        "tag_id": config_data.get("tag_id", ""),
        "description": config_data.get("description", ""),
        "skills_names": config_data.get("skills_names", []),
        "scenario": config_data.get("scenario", ""),
        "capabilities": config_data.get("capabilities", []),
        "workflow": config_data.get("workflow", []),
        "boundaries": config_data.get("boundaries", []),
        "output_spec": config_data.get("output_spec", []),
        "features_prompt": config_data.get("features_prompt", []),
        "prompt_file": prompt_file,
        "update_timestamp": generated_at,
    }
    _atomic_write(
        version_dir / "config.yaml",
        yaml.safe_dump(config_yaml, allow_unicode=True, sort_keys=False),
    )

    manifest = {
        "expert_agent_id": expert_agent_id,
        "name_cn": name_cn,
        "name_en": final_name_en,
        "version": int(version),
        "prompt_file": prompt_file,
        "generated_at": generated_at,
        "model": model_name,
        "raw_llm_output": raw_output,
    }
    _atomic_write(
        version_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2),
    )

    if activate:
        set_active_version(username, expert_agent_id, version)

    logger.info(
        "专家版本目录已写入 | user=%s | expert=%s | v%s | dir=%s",
        username,
        expert_agent_id,
        version,
        version_dir,
    )
    return str(version_dir)


def set_active_version(username: str, expert_agent_id: str, version: int) -> None:
    """把 ``active`` 指针文件写成指定版本号。"""
    _atomic_write(
        get_expert_dir(username, expert_agent_id) / "active", str(int(version))
    )


def list_version_dirs(username: str, expert_agent_id: str) -> list[int]:
    """列出该专家已存在的版本号（升序）。"""
    root = get_expert_dir(username, expert_agent_id)
    if not root.is_dir():
        return []
    versions: list[int] = []
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith("v") and child.name[1:].isdigit():
            versions.append(int(child.name[1:]))
    return sorted(versions)


def read_active_version(username: str, expert_agent_id: str) -> int:
    """读取 ``active`` 指向的版本号；缺失或非法时回退到最大版本号（无则 0）。"""
    active_file = get_expert_dir(username, expert_agent_id) / "active"
    try:
        if active_file.is_file():
            raw = active_file.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                return int(raw)
    except OSError as exc:  # noqa: BLE001
        logger.warning("读取 active 指针失败: %s", exc)

    versions = list_version_dirs(username, expert_agent_id)
    if versions:
        logger.warning(
            "专家 %s 的 active 指针缺失/非法，回退到最大版本 v%s",
            expert_agent_id,
            versions[-1],
        )
        return versions[-1]
    return 0


def _find_prompt_file(version_dir: Path) -> Optional[Path]:
    """定位版本目录里的专家提示词文件。

    优先兼容旧版的 ``EXPERT.md``；新版本按英文名命名（如
    ``stock-invest-advisor.md``），取除 ``README.md`` 外的第一个 ``*.md``。
    """
    legacy = version_dir / "EXPERT.md"
    if legacy.is_file():
        return legacy
    candidates = sorted(
        p for p in version_dir.glob("*.md") if p.name.lower() != "readme.md"
    )
    return candidates[0] if candidates else None


def load_active_prompt(
    username: str, expert_agent_id: str
) -> tuple[str, int, str]:
    """读取当前生效版本的专家提示词。

    Returns:
        ``(prompt_text, version, version_dir)``。任一步失败返回 ``("", 0, "")``，
        由调用方（接口层）降级为通用 Agent 并告警，不阻断用户对话。
    """
    version = read_active_version(username, expert_agent_id)
    if version <= 0:
        return "", 0, ""
    version_dir = get_version_dir(username, expert_agent_id, version)
    prompt_path = _find_prompt_file(version_dir)
    try:
        if prompt_path and prompt_path.is_file():
            return prompt_path.read_text(encoding="utf-8"), version, str(version_dir)
    except OSError as exc:  # noqa: BLE001
        logger.warning("读取专家提示词文件失败: %s", exc)
    return "", version, str(version_dir)


# ── 清理 ──────────────────────────────────────────────────────────────

def cleanup_old_versions(
    username: str,
    expert_agent_id: str,
    keep_versions: int = 3,
    retain_days: int = 30,
) -> list[int]:
    """清理旧版本目录。**永不删除 ``active`` 指向的版本**。

    保留规则（满足任一即保留）：
    - 版本号属于最新的 ``keep_versions`` 个；
    - 目录修改时间在 ``retain_days`` 天以内。

    Returns:
        被删除的版本号列表。
    """
    version_dir = get_expert_dir(username, expert_agent_id)
    if not version_dir.is_dir():
        return []

    active = read_active_version(username, expert_agent_id)
    versions = list_version_dirs(username, expert_agent_id)
    keep_newest = set(versions[-max(int(keep_versions), 1) :])
    cutoff = datetime.now() - timedelta(days=max(int(retain_days), 0))

    removed: list[int] = []
    for ver in versions:
        if ver == active or ver in keep_newest:
            continue
        path = get_version_dir(username, expert_agent_id, ver)
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            if mtime >= cutoff:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed.append(ver)
        except OSError as exc:  # noqa: BLE001
            logger.warning("清理旧版本 v%s 失败: %s", ver, exc)

    if removed:
        logger.info(
            "专家 %s 旧版本已清理 | 保留 active=v%s | 删除 %s",
            expert_agent_id,
            active,
            removed,
        )
    return removed


def remove_expert_dir(username: str, expert_agent_id: str) -> None:
    """物理删除整个专家目录（删除专家时调用）。"""
    path = get_expert_dir(username, expert_agent_id)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        logger.info("专家目录已删除 | user=%s | expert=%s", username, expert_agent_id)


# ── 参数校验 ──────────────────────────────────────────────────────────

def validate_create_params(name_cn: str, prompt: str) -> None:
    """校验创建/修改专家的必填项与长度上限。

    Raises:
        ValueError: 校验不通过（由接口层转 400）。
    """
    if not name_cn or not name_cn.strip():
        raise ValueError("专家名称不能为空")
    if len(name_cn.strip()) > _MAX_NAME_LEN:
        raise ValueError(f"专家名称不能超过 {_MAX_NAME_LEN} 个字符")
    if not prompt or not prompt.strip():
        raise ValueError("专家描述不能为空")
    if len(prompt) > _MAX_PROMPT_LEN:
        raise ValueError(f"专家描述不能超过 {_MAX_PROMPT_LEN} 个字符")
