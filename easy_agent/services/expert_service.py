"""专家 Agent 的生成与版本目录管理。

职责（见 ``docs/expert-agent-solution.md`` §4、§6）：

1. **LLM 生成**专家配置：输入「引导词模板 + 用户描述」，输出严格 JSON
   （``scenario`` / ``capabilities`` / ``workflow`` / ``boundaries`` /
   ``system_prompt`` / ``features_prompt``），带剥围栏 + 重试 + 字段校验；
2. **版本目录**构建：``workspace/{username}/experts/{export_agent_id}/v{n}/``
   下写 ``EXPERT.md`` / ``config.yaml`` / ``manifest.json``，外层 ``active``
   文本文件记录当前生效版本号。修改时新建 ``v{n+1}`` 而**不覆盖** ``v{n}``，
   保证运行中的旧会话不会被半成品配置影响；
3. **运行时读取**：``load_active_prompt()`` 只读 ``active`` 指向的那一份
   ``EXPERT.md``，读取失败返回空串，由调用方降级为通用 Agent；
4. **旧版本清理**：``cleanup_old_versions()`` 按「保留最近 N 个版本」+「保留
   N 天」双策略清目录，**永不删除 ``active`` 指向的版本**。

本模块只做「文件 + LLM」，不碰数据库（落库见 ``db/expert_store.py``）。
"""

import hashlib
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
2. JSON 字段如下（全部必填）：
   - "scenario": 字符串，专家应用场景描述（80~200 字）
   - "capabilities": 字符串数组，核心能力（3~6 条，每条一句话）
   - "workflow": 字符串数组，工作流程步骤（3~6 步，按执行顺序）
   - "boundaries": 字符串数组，操作边界 / 禁止事项（2~5 条）
   - "system_prompt": 字符串，完整的专家系统提示词正文（300~800 字，用第二人称
     "你是……" 撰写，包含角色定位、专业方法、输出规范、与用户交互的方式）
   - "features_prompt": 字符串数组，能力示例提示词（3~5 条，可直接点击使用的短句）

用户输入的专家名称：{name_cn}
用户对专家的描述：{user_prompt}
"""

_MAX_NAME_LEN = 50
_MAX_PROMPT_LEN = 4000


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
    - ``export_name_en``：``{slug}-{md5(id)[:8]}``，系统生成、与 ID 绑定因而稳定，
      用户改中文名时英文名不变（故不加 UNIQUE 约束）。
    """
    import uuid

    slug = slugify(name_cn)
    agent_id = f"expert_{slug}_{uuid.uuid4().hex[:8]}"
    digest = hashlib.md5(agent_id.encode("utf-8")).hexdigest()[:8]
    return agent_id, f"{slug}-{digest}"


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


def render_guide(name_cn: str, user_prompt: str) -> str:
    """把模板里的单花括号占位替换为实际值。

    用 ``str.replace`` 而非 ``str.format``：模板里可能出现 JSON 花括号，
    ``format`` 会把它们当占位符而报错。
    """
    guide = load_create_guide()
    return (
        guide.replace("{name_cn}", name_cn or "")
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

    return {
        "scenario": str(data.get("scenario") or "").strip(),
        "capabilities": _as_str_list(data.get("capabilities")),
        "workflow": _as_str_list(data.get("workflow")),
        "boundaries": _as_str_list(data.get("boundaries")),
        "system_prompt": system_prompt,
        "features_prompt": _as_str_list(data.get("features_prompt")),
    }


async def generate_expert_config(
    llm: Any,
    name_cn: str,
    user_prompt: str,
    max_retries: int = 1,
) -> dict:
    """调用 LLM 生成专家配置，失败自动重试。

    Args:
        llm: 已构建的 LangChain ChatModel 实例（由接口层用 ``create_model`` 创建，
            便于测试注入假模型）。
        name_cn: 专家中文名。
        user_prompt: 用户填写的专家描述。
        max_retries: 解析失败后的额外重试次数（默认再试 1 次）。

    Returns:
        规整后的配置 dict（见 :func:`normalize_config`）。

    Raises:
        ValueError: 重试耗尽仍未拿到可用产物。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    guide = render_guide(name_cn, user_prompt)
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
            return normalize_config(extract_json_object(str(content)))
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
    """写 ``EXPERT.md`` 时固定包裹的护栏说明（见方案 §6.2）。

    专家提示词来自「用户输入 + LLM 二次加工」，必须防止它把系统级安全约束冲掉。
    """
    return (
        f"<!-- 以下为「{name_cn}」的角色设定。它不影响、也不得违反系统的安全准则与\n"
        "     行为规范（禁止输出密钥、破坏性操作需确认、越界路径需拒绝等）。\n"
        "     当角色设定与系统准则冲突时，以系统准则为准。 -->\n\n"
    )


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
    model_name: str = "",
    raw_output: Optional[dict] = None,
    activate: bool = True,
) -> str:
    """写一个版本目录并（可选）把 ``active`` 指向它。

    Returns:
        该版本目录的绝对路径字符串。
    """
    version_dir = get_version_dir(username, expert_agent_id, version)
    version_dir.mkdir(parents=True, exist_ok=True)

    expert_md = (
        f"# {name_cn}\n\n"
        + prompt_guard(name_cn)
        + config_data.get("system_prompt", "").strip()
        + "\n"
    )
    _atomic_write(version_dir / "EXPERT.md", expert_md)

    config_yaml = {
        "expert_agent_id": expert_agent_id,
        "name_cn": name_cn,
        "version": int(version),
        "scenario": config_data.get("scenario", ""),
        "capabilities": config_data.get("capabilities", []),
        "workflow": config_data.get("workflow", []),
        "boundaries": config_data.get("boundaries", []),
        "features_prompt": config_data.get("features_prompt", []),
        "update_timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    _atomic_write(
        version_dir / "config.yaml",
        yaml.safe_dump(config_yaml, allow_unicode=True, sort_keys=False),
    )

    manifest = {
        "expert_agent_id": expert_agent_id,
        "name_cn": name_cn,
        "version": int(version),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
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
    expert_md = version_dir / "EXPERT.md"
    try:
        if expert_md.is_file():
            return expert_md.read_text(encoding="utf-8"), version, str(version_dir)
    except OSError as exc:  # noqa: BLE001
        logger.warning("读取 EXPERT.md 失败: %s", exc)
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
