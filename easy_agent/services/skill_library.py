"""技能库辅助：用户技能目录（启用/停用）、统计信息、SKILL.md 正文解析。

目录约定（与 Agent 加载目录严格一致）：:

    workspace/{username}/skills/            ← Agent 实际扫描的技能目录（= 启用）
    workspace/{username}/skills-disabled/   ← 停用技能存放处（Agent 不扫，不加载）

「停用」= 把技能目录从 ``skills/`` 原子重命名到 ``skills-disabled/``，因此停用后
deepagents 侧（``_resolve_skills_paths`` 只认 ``skills/``）自然不再加载它；重新启用
即移回。这样不需要给 deepagents 传额外的过滤参数，也不改动技能内容本身。

本模块只做文件系统层面的事，不依赖 Agent 运行期配置。
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import shutil
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Config

logger = logging.getLogger("easy-agent")

#: 技能定义文件（按优先级）
SKILL_FILES = ("SKILL.md", "README.md")

#: 停用技能目录名（位于用户 workspace 下，与 skills/ 同级）
DISABLED_DIR_NAME = "skills-disabled"

#: 统计时的文件数上限，防御性上限（技能目录正常只有几个文件）
MAX_STAT_FILES = 5000


# ── 路径 ──────────────────────────────────────────────────────────────


def user_skills_dir(username: str) -> Path:
    """Agent 实际加载的用户技能目录。"""
    return Config.get_user_workspace_dir(username) / "skills"


def user_disabled_dir(username: str) -> Path:
    """停用技能目录（Agent 不加载）。"""
    return Config.get_user_workspace_dir(username) / DISABLED_DIR_NAME


def is_skill_dir(path: Path) -> bool:
    """目录内存在 SKILL.md 或 README.md 才算技能目录。"""
    return path.is_dir() and any((path / name).exists() for name in SKILL_FILES)


def iter_skill_dirs(root: Path) -> list[Path]:
    """列出目录下所有技能子目录（按名字排序，忽略隐藏目录）。"""
    if not root.exists():
        return []
    return [
        child
        for child in sorted(root.iterdir(), key=lambda p: p.name)
        if not child.name.startswith(".") and is_skill_dir(child)
    ]


# ── frontmatter ───────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> dict[str, str]:
    """解析 SKILL.md 顶部 YAML frontmatter 里的 name/description/icon/version。

    只认单行 `key: value`（现有技能文件都是这种极简形态），解析失败返回空字段。
    """
    meta: dict[str, str] = {}
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return meta
    for line in match.group(1).split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in ("name", "description", "icon", "version") and value:
            meta[key] = value
    return meta


def read_skill_document(skill_dir: Path) -> tuple[str, str]:
    """读取技能定义正文，返回 (文件名, 内容)；都没有时返回 ("", "")。"""
    for name in SKILL_FILES:
        filepath = skill_dir / name
        if filepath.is_file():
            try:
                return name, filepath.read_text(encoding="utf-8", errors="replace")
            except OSError as e:  # 极端情况（权限/损坏）降级为空正文
                logger.warning(f"读取技能文件失败 | {filepath} | {e}")
                return name, ""
    return "", ""


def skill_metadata(skill_dir: Path) -> dict[str, Any]:
    """技能元数据：name/description/icon/version（frontmatter 优先，缺失时降级）。"""
    filename, content = read_skill_document(skill_dir)
    front = parse_frontmatter(content) if content else {}

    description = front.get("description", "")
    if not description and content:
        # 没有 frontmatter 描述时，取第一段有实际内容的正文：
        # 跳过标题/分隔线，并跳过纯符号行（如只有 ">" 的引用符号），避免卡片上出现 ">"
        for line in content.split("\n"):
            stripped = line.strip().lstrip(">").strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("---"):
                continue
            if not re.search(r"[\w\u4e00-\u9fff]", stripped):
                continue
            description = stripped[:200]
            break

    return {
        "name": front.get("name") or skill_dir.name,
        "description": description,
        "icon": front.get("icon", ""),
        "version": front.get("version", ""),
        "doc_file": filename,
    }


# ── 统计 ──────────────────────────────────────────────────────────────


def skill_stats(skill_dir: Path) -> dict[str, Any]:
    """技能目录真实统计：文件数 / 总字节数 / 最近更新时间（ISO8601，UTC）。

    用于卡片底部信息行，替代截图里的「浏览量/下载量」这类本地拿不到的指标。
    """
    file_count = 0
    size_bytes = 0
    latest = 0.0
    truncated = False

    for root, dirs, files in os.walk(skill_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        for filename in files:
            if filename.startswith("."):
                continue
            filepath = Path(root) / filename
            try:
                stat = filepath.stat()
            except OSError:
                continue
            file_count += 1
            size_bytes += stat.st_size
            latest = max(latest, stat.st_mtime)
            if file_count >= MAX_STAT_FILES:
                truncated = True
                break
        if truncated:
            break

    updated_at = (
        datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()
        if latest
        else None
    )
    return {
        "file_count": file_count,
        "size_bytes": size_bytes,
        "updated_at": updated_at,
        "truncated": truncated,
    }


# ── 启用 / 停用 ───────────────────────────────────────────────────────


def list_user_skill_dirs(username: str) -> list[dict[str, Any]]:
    """列出用户技能（含停用），每项 {dir_name, path, enabled}。"""
    items: list[dict[str, Any]] = []
    for enabled, root in (
        (True, user_skills_dir(username)),
        (False, user_disabled_dir(username)),
    ):
        for skill_dir in iter_skill_dirs(root):
            items.append({
                "dir_name": skill_dir.name,
                "path": skill_dir,
                "enabled": enabled,
            })
    return items


def find_user_skill(username: str, dir_name: str) -> dict[str, Any] | None:
    """按目录名找用户技能（启用与停用都找），返回 None 表示不存在。

    找不到时再按 frontmatter ``name`` 做一次别名解析（见 ``resolve_user_skill_ref``）：
    存量技能包常见「目录英文 + name 中文」，专家 ``required_skills`` 或旧前端手打的
    ``/中文名`` 只有别名可用。解析结果仍以**目录名**返回，调用方无需感知别名。
    """
    if not is_safe_skill_ref(dir_name):
        return None
    for root, enabled in (
        (user_skills_dir(username), True),
        (user_disabled_dir(username), False),
    ):
        candidate = root / dir_name
        if is_skill_dir(candidate):
            return {"dir_name": dir_name, "path": candidate, "enabled": enabled}
    found = resolve_user_skill_ref(username, dir_name)
    if found:
        return {
            "dir_name": found["dir_name"],
            "path": found["path"],
            "enabled": found["enabled"],
        }
    return None


def set_skill_enabled(username: str, dir_name: str, enable: bool) -> dict[str, Any]:
    """启用/停用用户技能（原子重命名），返回操作后的 {dir_name, enabled, path}。

    已处于目标状态时直接返回成功（幂等），目录不存在抛 FileNotFoundError，
    目标处已有同名技能抛 FileExistsError。
    """
    if not is_safe_skill_ref(dir_name):
        raise FileNotFoundError(dir_name)
    source_root = user_disabled_dir(username) if enable else user_skills_dir(username)
    target_root = user_skills_dir(username) if enable else user_disabled_dir(username)
    source = source_root / dir_name
    target = target_root / dir_name

    if not is_skill_dir(source):
        if is_skill_dir(target):
            # 已经是目标状态
            return {"dir_name": dir_name, "enabled": enable, "path": target}
        raise FileNotFoundError(dir_name)

    if target.exists():
        raise FileExistsError(f"{target_root.name}/{dir_name}")

    target_root.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    logger.info(
        f"技能{'启用' if enable else '停用'} | 用户: {username} | 技能: {dir_name} | 位置: {target}"
    )
    return {"dir_name": dir_name, "enabled": enable, "path": target}


def remove_user_skill(username: str, dir_name: str) -> Path:
    """彻底删除用户技能（启用或停用目录里的都删），返回被删路径。"""
    found = find_user_skill(username, dir_name)
    if not found:
        raise FileNotFoundError(dir_name)
    shutil.rmtree(str(found["path"]))
    logger.info(f"技能移除 | 用户: {username} | 技能: {dir_name}")
    return found["path"]


# ── 公共技能安装（技能中心「添加」/ 专家依赖自动补装）────────────────────────


def _project_root() -> Path:
    """项目/发行包根目录（``easy_agent`` 包的上一级）。"""
    return Path(__file__).resolve().parents[2]


def public_skills_dir_candidates() -> list[Path]:
    """公共技能目录的**可能位置**（按优先级），全部返回绝对值。

    ``tools.skills_dir`` 默认是相对路径 ``./skills``，而相对路径的基准是**进程
    CWD**——uvicorn / systemd / IDE 启动时 CWD 各不相同，此时 ``Path("./skills")``
    会指到别处，表现为「公共技能明明在项目里，却提示不存在」。因此查找顺序为：

    1. 配置值（绝对路径直接返回）；
    2. ``<进程 CWD>/<配置值>``（保持历史行为，本地开发优先命中）；
    3. ``<项目根>/<配置值>``（换启动目录也能找到，发行包内的技能都在这里）。

    技能中心与「专家依赖技能自动补装」共用本函数，保证两边判断一致。
    """
    from .agent_manager import get_agent_config  # 延迟导入，避免模块级循环依赖

    cfg = get_agent_config()
    raw = cfg["config"].tools.skills_dir if cfg else "./skills"
    raw = str(raw or "").strip()
    if not raw:
        return []

    path = Path(raw).expanduser()
    if path.is_absolute():
        return [path]

    candidates = [Path.cwd() / path, _project_root() / path]
    # 去重且保序
    unique: list[Path] = []
    for item in candidates:
        resolved = item.resolve() if item.exists() else item.absolute()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def public_skills_dir() -> Path | None:
    """公共技能目录：返回第一个**真实存在**的候选位置；都不存在时返回首选路径。

    候选与顺序见 ``public_skills_dir_candidates``。返回不存在的位置而不是 ``None``
    （只要配置了值），是为了让报错信息能指向用户真正写的那个路径。
    """
    candidates = public_skills_dir_candidates()
    if not candidates:
        return None
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


def public_skill_source(dir_name: str) -> Path | None:
    """在候选目录中查找公共技能目录（含 ``SKILL.md``/``README.md``）；找不到返回 ``None``。"""
    if not is_safe_skill_ref(dir_name):
        return None
    for root in public_skills_dir_candidates():
        candidate = root / dir_name
        if is_skill_dir(candidate):
            return candidate
    return None


def install_public_skill_to_user(
    username: str,
    dir_name: str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """把公共技能目录完整复制到用户技能库，返回 ``{dir_name, path}``。

    「安装」到用户侧 = 复制到 ``workspace/{username}/skills/{dir_name}/``，
    Agent 通过 ``/user-skills/`` 路由扫描（见 ``EasyAgent._resolve_skills_paths``）。

    复制走「临时目录 + ``os.replace``」：中途失败不会留下半份技能，并发重复调用
    也只会有一份生效。目标已存在时抛 ``FileExistsError``（``overwrite=True`` 例外），
    停用目录里的同名技能同样视为「用户已有」，不做覆盖。

    Raises:
        FileNotFoundError: 公共技能目录未配置，或所有候选目录里都没有该技能。
            异常信息里带上「找过哪些路径」，便于现场直接把技能包放对位置。
        FileExistsError: 用户技能库（启用或停用）里已有同名技能且未允许覆盖。
    """
    if not is_safe_skill_ref(dir_name):
        raise FileNotFoundError(f"非法技能标识: {dir_name!r}")
    candidates = public_skills_dir_candidates()
    if not candidates:
        raise FileNotFoundError("公共技能目录未配置（tools.skills_dir）")

    source = public_skill_source(dir_name)
    if source is None:
        searched = "；".join(str(item) for item in candidates)
        raise FileNotFoundError(
            f"公共技能 '{dir_name}' 不存在（需含 SKILL.md）。已查找: {searched}"
        )

    target_root = user_skills_dir(username)
    target = target_root / dir_name
    disabled = user_disabled_dir(username) / dir_name

    if not overwrite:
        if target.exists():
            raise FileExistsError(f"skills/{dir_name}")
        if is_skill_dir(disabled):
            raise FileExistsError(f"{DISABLED_DIR_NAME}/{dir_name}")

    target_root.mkdir(parents=True, exist_ok=True)
    temp = target_root / f".tmp_{dir_name}_{uuid.uuid4().hex[:8]}"
    try:
        shutil.copytree(str(source), str(temp))
        if overwrite and target.exists():
            shutil.rmtree(str(target), ignore_errors=True)
        os.replace(str(temp), str(target))
    finally:
        if temp.exists():
            shutil.rmtree(str(temp), ignore_errors=True)

    logger.info(
        f"公共技能安装到用户目录 | 用户: {username} | 技能: {dir_name} | "
        f"源: {source.absolute()} | 目标: {target.absolute()}"
    )
    return {"dir_name": dir_name, "path": target}


# ── 技能身份解析：目录名 ⇄ SKILL.md 的 name ───────────────────────────
#
# 一个技能有两个标识：
#   - **目录名** ``dir_name``：运行时唯一主键（``workspace/{u}/skills/{dir}``、
#     ``/user-skills/{dir}``、斜杠指令、技能卡片 key），**不能改**；
#   - **frontmatter ``name``**：展示名（技能中心卡片、模型看到的技能清单条目），
#     存量包常见中文。
#
# 规范要求两者一致，但现实里有「目录英文 + name 中文」（如
# ``fpp-income-analysis`` / 金市营收情况）与「目录中文 + name 中文」两种偏差。
# 这里提供**只读的别名解析**：任一写法 → canonical 目录名。目录名仍是主键，
# ``name`` 只作为别名兜底，绝不反向写入目录名或路径。

#: 身份索引缓存有效期（秒）。技能包增删会改目录 mtime 立即失效；改 SKILL.md 里的
#: name 不改 mtime，用 TTL 兜底。补装每轮都会跑，不能每次都全量读 frontmatter。
_REF_INDEX_TTL = 30.0

_public_ref_index_cache: dict[str, tuple[float, float, list[dict[str, Any]]]] = {}
_user_ref_index_cache: dict[
    tuple[str, str, str], tuple[float, tuple[float, float], list[dict[str, Any]]]
] = {}


def normalize_skill_ref(text: str) -> str:
    """归一化技能标识用于宽松比对：NFKC、小写、空白/下划线→连字符、折叠连字符。

    ``FPP_Income_Analysis``、``fpp income analysis``、``fpp-income-analysis``
    归一化后相同；中文保持原样（只做全角→半角）。
    """
    value = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")


def is_safe_skill_ref(ref: str) -> bool:
    """声明值能否安全地当作技能标识（挡住 ``../``、绝对路径、空串）。

    ``required_skills`` 与斜杠指令都来自外部输入，而目录名会被直接拼进路径，
    因此先做一次白名单式校验，避免 ``../../etc`` 这类值把查找带出技能根目录。
    """
    raw = str(ref or "").strip()
    if not raw or len(raw) > 128:
        return False
    if raw in {".", ".."} or raw.startswith((".", "/", "\\")):
        return False
    return not any(token in raw for token in ("/", "\\", "\x00"))


def _root_stamp(root: Path) -> float:
    """技能根目录的 mtime（增删技能目录会变），用于缓存失效。"""
    try:
        return root.stat().st_mtime
    except OSError:
        return 0.0


def skill_identity_index(root: Path, *, use_cache: bool = True) -> list[dict[str, Any]]:
    """扫描公共技能目录，返回身份索引（按目录名排序）。

    每项 ``{dir_name, name, path, dir_key, name_key}``：``name`` 取 SKILL.md
    frontmatter（缺失时回退目录名），``*_key`` 是归一化后的比对键。
    """
    key = str(root)
    now = time.monotonic()
    stamp = _root_stamp(root)
    if use_cache:
        cached = _public_ref_index_cache.get(key)
        if cached and cached[1] == stamp and now - cached[0] < _REF_INDEX_TTL:
            return cached[2]

    items: list[dict[str, Any]] = []
    for skill_dir in iter_skill_dirs(root):
        meta = skill_metadata(skill_dir)
        display_name = str(meta.get("name") or skill_dir.name).strip() or skill_dir.name
        items.append({
            "dir_name": skill_dir.name,
            "name": display_name,
            "path": skill_dir,
            "dir_key": normalize_skill_ref(skill_dir.name),
            "name_key": normalize_skill_ref(display_name),
        })
    if use_cache:
        _public_ref_index_cache[key] = (now, stamp, items)
    return items


def _match_identity(
    raw: str, entries: list[dict[str, Any]]
) -> tuple[dict[str, Any], str] | None:
    """按「目录名精确 → 目录名归一化 → name 精确 → name 归一化」匹配。

    **目录名优先**：多技能 name 重名时目录名才是可靠键。返回 ``(命中项, 匹配方式)``，
    未命中返回 ``None``；同一级命中多个时取排序靠前（目录名最小）的那个并打告警。
    """
    ref_key = normalize_skill_ref(raw)
    stages: tuple[tuple[str, Any], ...] = (
        ("dir", lambda item: item["dir_name"] == raw),
        ("dir_normalized", lambda item: bool(ref_key) and item["dir_key"] == ref_key),
        ("name", lambda item: item["name"] == raw),
        ("name_normalized", lambda item: bool(ref_key) and item["name_key"] == ref_key),
    )
    for matched_by, predicate in stages:
        hits = [item for item in entries if predicate(item)]
        if not hits:
            continue
        if len(hits) > 1:
            logger.warning(
                f"⚠️ 技能别名歧义 | 声明: {raw} | 命中: {[h['dir_name'] for h in hits]} | "
                f"按目录名取第一个: {hits[0]['dir_name']}"
            )
        return hits[0], matched_by
    return None


def resolve_public_skill_ref(ref: str, root: Path | None = None) -> dict[str, Any] | None:
    """把「目录名 / frontmatter name / 归一化写法」解析为公共技能目录里的技能。

    不传 ``root`` 时依次扫描 ``public_skills_dir_candidates()``（与
    ``public_skill_source`` 同序），返回第一个命中的候选，保证「换启动目录也能找到」。

    未命中返回 ``None``。返回项在索引项基础上追加 ``matched_by``
    （``dir`` / ``dir_normalized`` / ``name`` / ``name_normalized``）与 ``declared``。
    """
    raw = str(ref or "").strip()
    if not is_safe_skill_ref(raw):
        return None
    roots = [root] if root is not None else public_skills_dir_candidates()
    for candidate_root in roots:
        matched = _match_identity(raw, skill_identity_index(candidate_root))
        if matched:
            item, matched_by = matched
            return {**item, "matched_by": matched_by, "declared": raw}
    return None


def suggest_public_skill_refs(ref: str, limit: int = 5) -> list[dict[str, Any]]:
    """给解析失败的声明挑近似候选，**仅供诊断接口展示，绝不据此自动安装**。

    评分：归一化后相似度（difflib）+ 包含关系加权；中文与英文之间相似度天然低，
    因此主要覆盖拼写错误、连字符/下划线差异、名字片段。
    """
    raw = str(ref or "").strip()
    if not raw:
        return []
    ref_key = normalize_skill_ref(raw)
    scored: dict[str, tuple[float, dict[str, Any]]] = {}
    for root in public_skills_dir_candidates():
        for item in skill_identity_index(root):
            score = max(
                difflib.SequenceMatcher(None, ref_key, item["dir_key"]).ratio(),
                difflib.SequenceMatcher(None, ref_key, item["name_key"]).ratio(),
            )
            for candidate_key in (item["dir_key"], item["name_key"]):
                if ref_key and candidate_key and (
                    ref_key in candidate_key or candidate_key in ref_key
                ):
                    score = max(score, 0.9)
                    break
            if score < 0.6:
                continue
            previous = scored.get(item["dir_name"])
            if previous is None or score > previous[0]:
                scored[item["dir_name"]] = (score, item)
    ordered = sorted(scored.values(), key=lambda pair: (-pair[0], pair[1]["dir_name"]))
    return [
        {"dir_name": item["dir_name"], "name": item["name"], "score": round(score, 2)}
        for score, item in ordered[:limit]
    ]


def _user_identity_index(username: str) -> list[dict[str, Any]]:
    """用户技能库（启用 + 停用）的身份索引；键含目录路径，避免跨测试/跨用户串味。"""
    skills_root = user_skills_dir(username)
    disabled_root = user_disabled_dir(username)
    cache_key = (username, str(skills_root), str(disabled_root))
    stamp = (_root_stamp(skills_root), _root_stamp(disabled_root))
    now = time.monotonic()
    cached = _user_ref_index_cache.get(cache_key)
    if cached and cached[1] == stamp and now - cached[0] < _REF_INDEX_TTL:
        return cached[2]

    entries: list[dict[str, Any]] = []
    for item in list_user_skill_dirs(username):
        try:
            meta = skill_metadata(Path(item["path"]))
        except OSError:
            continue
        display_name = str(meta.get("name") or item["dir_name"]).strip() or item["dir_name"]
        entries.append({
            **item,
            "name": display_name,
            "dir_key": normalize_skill_ref(item["dir_name"]),
            "name_key": normalize_skill_ref(display_name),
        })
    _user_ref_index_cache[cache_key] = (now, stamp, entries)
    return entries


def resolve_user_skill_ref(username: str, ref: str) -> dict[str, Any] | None:
    """在**用户技能库**里按目录名或 name 解析，返回标准技能项 ``{dir_name, path, enabled}``。

    用于 ``find_user_skill`` 的别名兜底与对话内技能选择：用户可能手打 ``/中文名``，
    或旧数据里存的是展示名。命中后一律以 canonical 目录名返回。
    """
    raw = str(ref or "").strip()
    if not is_safe_skill_ref(raw):
        return None
    matched = _match_identity(raw, _user_identity_index(username))
    if not matched:
        return None
    item, matched_by = matched
    return {
        "dir_name": item["dir_name"],
        "name": item["name"],
        "path": item["path"],
        "enabled": item.get("enabled", True),
        "matched_by": matched_by,
        "declared": raw,
    }


# ── 登记（对话创建 / 工作区收编）───────────────────────────────────────────

#: 合法技能目录名：小写字母或数字开头，仅含小写字母、数字、连字符、下划线
SKILL_DIR_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def normalize_skill_dir_name(name: str) -> str:
    """把候选名字规范成合法技能目录名（小写、连字符分隔）；无法规范化时返回空串。"""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    cleaned = cleaned[:64].strip("-")
    return cleaned if SKILL_DIR_NAME_RE.match(cleaned) else ""


def unique_skill_dir_name(username: str, preferred: str) -> str:
    """在技能库（启用 + 停用）里挑一个不冲突的目录名：重名时追加 -2、-3…"""
    base = preferred or "skill"
    taken = {item["dir_name"] for item in list_user_skill_dirs(username)}
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"


def register_skill_source(
    username: str,
    source_dir: Path | str,
    dir_name: str | None = None,
) -> dict[str, Any]:
    """把一个目录登记为用户技能（移动进技能库），返回登记结果。

    用于「对话创建技能」：模型可能把技能写在工作区，也可能直接写进技能库，
    收尾时统一走这里入库。

    - 校验：必须是目录、含非空 ``SKILL.md``，否则 ``ValueError``
    - 命名：优先用 ``dir_name``（缺省取源目录名），规范化为安全目录名
    - 重名：与既有技能（启用 + 停用）冲突时追加 ``-2``、``-3``…，绝不覆盖
    - 移动：``shutil.move`` 到 ``skills/``，源目录不再保留（工作区不留副本）

    Returns:
        ``{"dir_name", "path", "renamed", "moved_from", "warnings"}``
    """
    source = Path(source_dir)
    if not source.is_dir():
        raise ValueError(f"源目录不存在: {source}")
    skill_file = source / "SKILL.md"
    if not skill_file.is_file():
        raise ValueError(f"目录内缺少 SKILL.md: {source}")
    content = skill_file.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        raise ValueError(f"SKILL.md 内容为空: {skill_file}")

    preferred = normalize_skill_dir_name(dir_name or source.name)
    if not preferred:
        raise ValueError(f"技能目录名不合法: {dir_name or source.name}")

    target_root = user_skills_dir(username)
    target_root.mkdir(parents=True, exist_ok=True)
    final_name = unique_skill_dir_name(username, preferred)
    target = target_root / final_name
    if target.exists():  # 并发兜底：极端情况下同名技能刚被别人创建
        raise FileExistsError(str(target))

    shutil.move(str(source), str(target))

    # 目标名与源目录名不同（规范化或重名加后缀）时算重命名
    renamed = final_name != source.name
    warnings: list[str] = []
    front = parse_frontmatter(content)
    if not front.get("name"):
        warnings.append("SKILL.md frontmatter 缺少 name（将按目录名展示）")
    if not front.get("description"):
        warnings.append("SKILL.md frontmatter 缺少 description（触发依赖描述，建议补上）")

    logger.info(
        f"技能登记 | 用户: {username} | 技能: {final_name}"
        f"{'（源目录名 ' + source.name + ' 已重命名）' if renamed else ''}"
        f" | 来源: {source} | 目标: {target}"
        f"{' | 提示: ' + '; '.join(warnings) if warnings else ''}"
    )
    return {
        "dir_name": final_name,
        "path": target,
        "renamed": renamed,
        "moved_from": source,
        "warnings": warnings,
    }


# ── 「/技能目录名」指令解析与拼装 ──────────────────────────────────────────

#: 技能指令形如 ``/技能目录名``：必须以消息开头（或紧跟在上一条令牌之后），
#: 目录名限字母/数字/中文开头、字母数字连字符下划线中文组成，且后面必须跟空白或
#: 消息结束。这两个约束共同避免把 ``/usr/bin``、``/workspace/out`` 这类路径当成技能。
#: 允许中文是为了兼容「目录名本身是中文」的技能包与用户手打 ``/中文名``。
SKILL_DIRECTIVE_RE = re.compile(r"^/([A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9_\-\u4e00-\u9fff]*)(?=\s|$)")

#: 单轮最多解析的技能数：避免超长消息构造出大量目录查询
MAX_SKILL_DIRECTIVES = 32


def parse_skill_directives(message: str) -> list[str]:
    """从消息文本里解析开头的 ``/技能目录名`` 指令。

    旧前端（或用户手打）会把已选技能前置成指令，多条以空格分隔，例如::

        /pptx-generator /docx 帮我排个版

    只认「消息开头连续出现」的令牌，与前端 ``utils/skillTokens.js`` 的解析规则一致：
    正文中间出现的 ``/技能名`` 是用户自己打的字，不当作发送时拼接的指令。

    Returns:
        按出现顺序去重后的目录名；无指令返回空列表。
    """
    text = (message or "").lstrip()
    dirs: list[str] = []
    seen: set[str] = set()
    while text and len(dirs) < MAX_SKILL_DIRECTIVES:
        matched = SKILL_DIRECTIVE_RE.match(text)
        if not matched:
            break
        name = matched.group(1)
        if name not in seen:
            seen.add(name)
            dirs.append(name)
        text = text[matched.end():].lstrip()
    return dirs


def resolve_skill_dirs(
    names: list[str],
    username: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """把目录名映射为该用户实际拥有的技能（启用与停用都算）。

    请求体的 ``skills`` 字段与消息文本里的 ``/目录名`` 指令都会经过这里校验：只有
    真实存在于该用户技能库（``skills/`` 或 ``skills-disabled/``）的目录名才被放行，
    其余归入未识别——调用方只记日志，既不阻断对话，也不给模型拼出无效指令。

    手打 ``/中文名``（SKILL.md 的 name）时按别名解析到 canonical 目录名，放行规则
    与目录名一致；返回项里的 ``dir_name`` 恒为目录名，``name`` 才是展示名。

    Args:
        names: ``parse_skill_directives`` 的结果，或请求体 ``skills`` 字段
        username: 当前用户

    Returns:
        ``(命中的技能列表, 未识别的目录名列表)``，均保持输入顺序且去重。技能项形如
        ``{"dir_name", "path", "enabled", "name"}``，其中 ``name`` 是 SKILL.md 的展示名。
    """
    if not names:
        return [], []

    by_dir_name = {item["dir_name"]: item for item in list_user_skill_dirs(username)}
    resolved: list[dict[str, Any]] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        item = by_dir_name.get(name)
        if not item:
            # 手打 ``/中文名`` 或旧数据里存的是展示名 → 别名解析到目录名后再查
            alias = resolve_user_skill_ref(username, name)
            item = by_dir_name.get(alias["dir_name"]) if alias else None
        if not item:
            unknown.append(name)
            continue
        resolved.append({
            "dir_name": item["dir_name"],
            "path": item["path"],
            "enabled": item["enabled"],
            "name": skill_metadata(item["path"]).get("name") or item["dir_name"],
        })
    return resolved, unknown


def resolve_turn_skills(
    field_names: list[str] | None,
    message: str,
    username: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """本轮技能解析（chat 与专家链路共用）。

    字段优先：请求体的 ``skills`` 字段是输入框的选中项（新前端），消息文本开头的
    ``/目录名`` 是旧前端 / 用户手打的兼容写法，两者合并去重后交给
    ``resolve_skill_dirs`` 校验归属。字段为空或缺省时行为与改造前逐字一致。
    """
    names = list(field_names or []) + parse_skill_directives(message)
    return resolve_skill_dirs(names, username)


def pending_skill_directives(dir_names: list[str], message: str) -> list[str]:
    """挑出「需要新拼进模型输入」的技能目录名（消息文本开头尚未出现的那些）。

    新前端的消息文本是用户原话 → 返回全部目录名；旧前端 / 手打 ``/目录名`` 时，文本里
    已有的那份保持原样、原位置，这里不再重复拼，避免模型输入里出现两份同样的指令。
    """
    in_text = set(parse_skill_directives(message))
    pending: list[str] = []
    for name in dir_names:
        if not name or name in in_text or name in pending:
            continue
        pending.append(name)
    return pending


def inject_skill_directives(dir_names: list[str], text: str) -> str:
    """把技能指令前缀拼到文本开头，得到「给模型看的那一份」输入。

    技能指令只进模型输入（``parsed_content``）；落库正文、气泡与会话标题用的都是用户
    原话。没有需要新拼的目录名时原样返回 ``text``（不 Trim、不改写），保证旧前端的
    行为与改造前逐字一致。
    """
    prefix = " ".join(f"/{name}" for name in dir_names if name)
    if not prefix:
        return text
    body = (text or "").strip()
    return f"{prefix} {body}".strip()
