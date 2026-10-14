"""公共（预置）专家 Agent 的目录发现与读取。

与「我的专家」的关系（见 ``docs/expert-agent-solution.md``）：

- **我的专家**：用户在界面里创建，落库（``expert_agents`` 表）+ 版本目录
  （``workspace/{username}/experts/{id}/v{n}/``），可改可删；
- **公共专家**：随代码/发行包预置，**只落目录、不落库**，只读（不可改、不可删），
  全部放在一个公共目录下（默认项目根的 ``exports/``）。

目录约定（两种布局都支持，逐个专家目录自适应）：

1. **版本目录（预置专家的标准布局，与「我的专家」完全一致）**：
   ``exports/{expert_id}/active``（内容为版本号）+ ``exports/{expert_id}/v{n}/``
   下放 ``config.yaml`` / ``manifest.json`` / 提示词 ``md`` / ``README.md``；
2. **平铺（兼容旧目录）**：
   ``exports/{expert_id}/config.yaml`` + ``exports/{expert_id}/{name_en}.md``。
   缺 ``active`` 时取最大版本号。

``config.yaml`` 里识别的键与 ``expert_service.write_expert_version`` 写出的完全一致
（``name_cn`` / ``name_en`` / ``description`` / ``skills_names`` / ``tag_id`` /
``features_prompt`` …），因此「用我的专家目录里的配置文件直接拷成公共专家」也能被识别。

本模块只读文件系统，不碰数据库；返回的字段名与 ``api/expert.py`` 的 ``_to_public``
保持一致，前端卡片/详情页/召唤链路可原样复用。

**存在标志**：一个目录只有在能找到提示词 ``*.md`` 时才算「一个公共专家」。缺失提示词
的目录会在列表里被跳过、召唤时返回 404 —— 这与「我的专家」在磁盘缺失时降级为通用
Agent 的策略不同：公共专家的内容随发行包提供，缺文件属打包错误，需要显式暴露，
而不是静默地用通用 Agent 回答。
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

#: 公共专家目录的环境变量覆盖项（优先级：配置 > 环境变量 > 默认 ./exports）
ENV_EXPERTS_DIR = "EASY_EXPERTS_DIR"
DEFAULT_EXPERTS_DIR = "./exports"

#: 需要跳过的目录名（隐藏目录、模板目录）
_SKIP_PREFIXES = (".", "_")

#: 公共专家对外统一状态：永远「可用」（预置内容随发行包一起验证过）
STATUS_ACTIVE = "active"


# ── 根目录解析 ────────────────────────────────────────────────────────


def public_experts_root() -> Path:
    """公共专家根目录。

    解析优先级：``config.yaml`` 的 ``tools.experts_dir`` > 环境变量
    ``EASY_EXPERTS_DIR`` > 默认 ``./exports``（相对进程工作目录，与
    ``tools.skills_dir`` 的 ``./skills`` 同口径）。

    ``tools.experts_dir`` 采用 ``getattr`` 读取：老配置里没有该键时自动降级，
    不需要改动 ``config.py`` 也不会报错。
    """
    configured = ""
    try:
        # 延迟导入：避免 services 包初始化期的循环依赖
        from .agent_manager import get_agent_config

        cfg = get_agent_config()
        if cfg and cfg.get("config"):
            configured = getattr(cfg["config"].tools, "experts_dir", "") or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取 tools.experts_dir 失败，改用环境变量/默认值: %s", exc)

    raw = configured or os.environ.get(ENV_EXPERTS_DIR, "") or DEFAULT_EXPERTS_DIR
    return Path(raw)


# ── 单个专家目录的解析 ────────────────────────────────────────────────


def _read_yaml(path: Path) -> dict:
    """读 YAML；缺失/损坏时返回空 dict（公共专家不该因一个坏文件整列失败）。"""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("公共专家配置解析失败 | %s | %s", path, exc)
        return {}


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("公共专家 manifest 解析失败 | %s | %s", path, exc)
        return {}


def _find_prompt_file(folder: Path) -> Optional[Path]:
    """定位目录里的专家提示词文件。

    兼容旧版 ``EXPERT.md``；否则取除 ``README.md`` 外按名称排序的第一个 ``*.md``。
    """
    legacy = folder / "EXPERT.md"
    if legacy.is_file():
        return legacy
    candidates = sorted(
        p for p in folder.glob("*.md") if p.name.lower() != "readme.md"
    )
    return candidates[0] if candidates else None


def _parse_version_dirs(expert_dir: Path) -> list[int]:
    versions: list[int] = []
    for child in expert_dir.iterdir():
        if child.is_dir() and child.name.startswith("v") and child.name[1:].isdigit():
            versions.append(int(child.name[1:]))
    return sorted(versions)


def _resolve_content_dir(expert_dir: Path) -> tuple[Path, int]:
    """定位「专家内容目录」与其版本号。

    Returns:
        ``(内容目录, 版本号)``；无法识别返回 ``(expert_dir, 1)``（当作平铺处理）。
    """
    active_file = expert_dir / "active"
    versions = _parse_version_dirs(expert_dir)

    if active_file.is_file():
        raw = ""
        try:
            raw = active_file.read_text(encoding="utf-8").strip()
        except OSError as exc:  # noqa: BLE001
            logger.warning("读取公共专家 active 指针失败 | %s | %s", active_file, exc)
        if raw.isdigit() and (expert_dir / f"v{raw}").is_dir():
            return expert_dir / f"v{raw}", int(raw)
        if versions:
            # active 非法：回退最大版本，与「我的专家」的容错口径一致
            logger.warning(
                "公共专家 %s 的 active 指针非法(%r)，回退 v%s",
                expert_dir.name,
                raw,
                versions[-1],
            )
            return expert_dir / f"v{versions[-1]}", versions[-1]

    if versions:
        return expert_dir / f"v{versions[-1]}", versions[-1]
    return expert_dir, 1


def _as_str_list(value: Any) -> list[str]:
    """规整字符串数组字段（兼容 JSON 字符串、英文逗号分隔的写法）。"""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.startswith("["):
            try:
                import json

                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            except Exception:  # noqa: BLE001
                pass
        return [part.strip() for part in text.replace("，", ",").split(",") if part.strip()]
    return []


def _first_plain_line(md_text: str) -> str:
    """取 md 正文里第一行非标题/非空内容，作为缺 description 时的兜底描述。"""
    for raw in md_text.splitlines():
        line = raw.strip().lstrip("#").strip()
        if not line or line.startswith("---") or ":" in line[:20]:
            continue
        return line[:120]
    return ""


def read_public_expert(expert_dir: Path) -> Optional[dict]:
    """把一个公共专家目录解析成卡片数据；无法识别时返回 ``None``。

    返回字段与 ``api/expert.py`` 的 ``_to_public`` 对齐（前端可直接复用卡片
    与召唤链路），额外带 ``scope="public"`` 供前端做只读渲染。
    """
    if not expert_dir.is_dir() or expert_dir.name.startswith(_SKIP_PREFIXES):
        return None

    content_dir, version = _resolve_content_dir(expert_dir)
    prompt_path = _find_prompt_file(content_dir)
    if prompt_path is None:
        logger.warning(
            "跳过公共专家目录（未找到提示词 md）| %s", expert_dir.absolute()
        )
        return None

    cfg = _read_yaml(content_dir / "config.yaml") or _read_yaml(expert_dir / "config.yaml")
    manifest = _read_json(content_dir / "manifest.json")

    expert_id = str(cfg.get("expert_agent_id") or expert_dir.name).strip()
    name_cn = str(cfg.get("name_cn") or "").strip()
    name_en = str(cfg.get("name_en") or "").strip()

    # 提示词正文（仅用于兜底 name / description，不放进列表响应体）
    prompt_text = ""
    try:
        prompt_text = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        logger.warning("读取公共专家提示词失败 | %s | %s", prompt_path, exc)

    if not name_cn:
        # 无 config.yaml 时的兜底：目录名 / 文件名
        name_cn = expert_dir.name.replace("_", " ").replace("-", " ").strip() or name_en
    if not name_en:
        name_en = prompt_path.stem

    description = str(cfg.get("description") or "").strip() or _first_plain_line(prompt_text)

    updated = (
        str(cfg.get("update_timestamp") or "").strip()
        or str(manifest.get("generated_at") or "").strip()
    )

    return {
        "export_agent_id": expert_id,
        "session_id": "",
        "tag_id": str(cfg.get("tag_id") or "").strip(),
        "export_name_en": name_en,
        "export_name_cn": name_cn,
        # 公共专家列表不下发提示词全文（列表接口保持轻量），
        # 召唤时由 load_public_prompt() 按需读取。
        "export_prompt": "",
        "export_description": description,
        "export_skills_names": _as_str_list(cfg.get("skills_names")),
        "export_features_prompt": _as_str_list(cfg.get("features_prompt")),
        "export_file_path": str(prompt_path),
        "agent_status": STATUS_ACTIVE,
        "export_version": int(cfg.get("version") or version or 1),
        "create_timestamp": updated,
        "update_timestamp": updated,
        # 只读标记：前端据此隐藏「编辑 / 删除」，并展示「预置」标签
        "scope": "public",
    }


# ── 列表 / 查找 / 读提示词 ────────────────────────────────────────────


def discover_public_experts() -> list[dict]:
    """扫描公共专家目录，返回全部可识别的公共专家（按中文名排序）。"""
    root = public_experts_root()
    if not root.is_dir():
        logger.info("公共专家目录不存在（跳过）| %s", root.absolute())
        return []

    items: list[dict] = []
    seen: set[str] = set()
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith(_SKIP_PREFIXES):
            continue
        item = read_public_expert(child)
        if not item:
            continue
        # 极端情况（两个目录声明了同一个 expert_agent_id）：先到先得，避免前端 key 冲突
        if item["export_agent_id"] in seen:
            logger.warning(
                "公共专家 ID 重复，已跳过 | id=%s | dir=%s",
                item["export_agent_id"],
                child.name,
            )
            continue
        seen.add(item["export_agent_id"])
        items.append(item)

    items.sort(key=lambda row: (row.get("export_name_cn") or "", row["export_agent_id"]))
    logger.info("📂 公共专家目录 | 目录: %s | 数量: %s", root.absolute(), len(items))
    return items


def find_public_expert(expert_id: str) -> Optional[dict]:
    """按 ``export_agent_id`` 查找公共专家；不存在返回 ``None``。

    直接按目录名命中（O(1)），不依赖遍历顺序；目录名与 ID 不一致时（config.yaml
    里写了别的 ID）退化为一次全量扫描，保证「列表里能看到的，召唤时一定找得到」。
    """
    if not expert_id:
        return None

    root = public_experts_root()
    direct = root / expert_id
    if direct.is_dir():
        item = read_public_expert(direct)
        if item:
            return item

    for item in discover_public_experts():
        if item["export_agent_id"] == expert_id:
            return item
    return None


def load_public_prompt(expert_id: str) -> tuple[str, int, str]:
    """读取公共专家的提示词。

    Returns:
        ``(prompt_text, version, prompt_file_path)``。专家不存在或提示词不可读时
        返回 ``("", 0, "")``，由接口层降级为通用 Agent 并告警（与
        ``expert_service.load_active_prompt`` 的口径一致）。
    """
    item = find_public_expert(expert_id)
    if not item:
        return "", 0, ""
    prompt_path = Path(item.get("export_file_path") or "")
    try:
        if prompt_path.is_file():
            return (
                prompt_path.read_text(encoding="utf-8"),
                int(item.get("export_version") or 1),
                str(prompt_path),
            )
    except OSError as exc:  # noqa: BLE001
        logger.warning("读取公共专家提示词失败 | %s | %s", prompt_path, exc)
    return "", int(item.get("export_version") or 0), str(prompt_path)
