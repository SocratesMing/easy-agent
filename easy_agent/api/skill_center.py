"""技能中心 API：公共技能列表、用户技能列表、添加/移除技能"""

import io
import logging
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import Config
from ..db import Database, get_database
from ..middleware import get_current_username
from ..skills import discover_skills
from ..services import (
    get_agent_config,
    invalidate_user_agents,
    skill_categories,
    skill_library,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/skill-center", tags=["Skill Center"])


def _parse_skill_metadata(skill_dir: Path) -> dict:
    """从 SKILL.md / README.md 的 frontmatter 解析技能元数据（实现见 services/skill_library）。"""
    return skill_library.skill_metadata(skill_dir)


def _resolve_row_category(
    store: dict,
    dir_name: str,
    metadata: dict,
    skill_dir: Path,
) -> str:
    """单条技能的归属 id：显式归属优先，缺省时才读正文做关键词推断。"""
    if store["assignments"].get(dir_name):
        return skill_categories.resolve_from_store(
            store, dir_name, metadata.get("name", ""), metadata.get("description", "")
        )

    _, body = skill_library.read_skill_document(skill_dir)
    return skill_categories.resolve_from_store(
        store,
        dir_name,
        metadata.get("name", ""),
        metadata.get("description", ""),
        body,
    )


def _category_name_map(store: dict) -> dict:
    """id -> 类别名（含虚拟兜底类别）。"""
    names = {item["id"]: item["name"] for item in store["categories"]}
    names[skill_categories.DEFAULT_CATEGORY_ID] = skill_categories.DEFAULT_CATEGORY_NAME
    return names


def _backfill_missing_assignments(username: str, store: dict, items: list[dict]) -> bool:
    """给没有归属记录的历史技能按内容补录一次分类，返回是否有写入。

    分类只在技能创建时定稿、之后不再变动；早于该规则的技能在此补录一次，
    补录结果与当时的推断一致（``auto_assign_skill`` 幂等，已有归属不会覆盖）。
    """
    changed = False
    for item in items:
        skill_dir = item["path"]
        if store["assignments"].get(skill_dir.name):
            continue
        metadata = _parse_skill_metadata(skill_dir)
        _, body = skill_library.read_skill_document(skill_dir)
        skill_categories.auto_assign_skill(
            username,
            skill_dir.name,
            name=metadata.get("name", ""),
            description=metadata.get("description", ""),
            body=body,
        )
        changed = True
    return changed


def _get_user_skills_dir(username: str) -> Path:
    """用户技能目录（Agent 实际加载的目录，见 services/skill_library）。"""
    return skill_library.user_skills_dir(username)


def _public_skills_root() -> Path | None:
    """公共技能目录（运行期配置优先）。

    统一走 ``skill_library.public_skills_dir()``：技能中心与「专家依赖技能自动补装」
    必须对「公共技能在哪」有完全一致的判断，否则会出现一边装得上、一边找不到。
    """
    return skill_library.public_skills_dir()


# ── 公共技能 ──────────────────────────────────────────────────────────


@router.get("/public-skills", summary="获取公共技能列表")
async def list_public_skills(
    username: Annotated[str, Depends(get_current_username)],
):
    """从系统 skills 目录加载所有公共技能，并标记用户是否已添加"""
    _cfg = get_agent_config()
    # 运行期配置结构为 {"config": Config, ...}；公共技能目录在 _cfg["config"].tools.skills_dir
    skills_root = _cfg["config"].tools.skills_dir if _cfg else "./skills"
    abs_skills_root = str(Path(skills_root).absolute())
    skills = discover_skills(skills_root or None)
    logger.info(
        f"📂 公共技能目录 | 目录: {abs_skills_root} | 技能数量: {len(skills)}"
    )

    # dir_name -> enabled（含已停用：added=True 但 enabled=False）
    installed = {
        item["dir_name"]: item["enabled"]
        for item in skill_library.list_user_skill_dirs(username)
    }

    store = skill_categories.load_store(username)
    category_names = _category_name_map(store)

    result = []
    for skill in skills:
        skill_dir = Path(skill["path"])
        metadata = _parse_skill_metadata(skill_dir)
        category_id = _resolve_row_category(store, skill_dir.name, metadata, skill_dir)
        result.append({
            "name": metadata["name"],
            "dir_name": skill_dir.name,
            "description": metadata["description"],
            "icon": metadata["icon"],
            "version": metadata.get("version", ""),
            "added": skill_dir.name in installed,
            "enabled": installed.get(skill_dir.name, False),
            "category_id": category_id,
            "category_name": category_names.get(category_id, skill_categories.DEFAULT_CATEGORY_NAME),
            "stats": skill_library.skill_stats(skill_dir),
        })

    return {"skills": result}


# ── 用户技能 ──────────────────────────────────────────────────────────


@router.get("/user-skills", summary="获取用户技能列表（含已停用）")
async def list_user_skills(
    username: Annotated[str, Depends(get_current_username)],
):
    """用户技能列表：启用（skills/）与停用（skills-disabled/）都会返回。

    每个技能带 ``enabled`` 与真实统计（文件数/大小/更新时间），供卡片与详情页展示。
    """
    user_skills_dir = _get_user_skills_dir(username)
    abs_user_skills_dir = str(user_skills_dir.absolute())

    store = skill_categories.load_store(username)
    user_skill_items = skill_library.list_user_skill_dirs(username)

    # 历史技能（早于「创建时自动归类」的技能）补录一次归属：分类一旦定稿就不再变动
    if _backfill_missing_assignments(username, store, user_skill_items):
        store = skill_categories.load_store(username)

    category_names = _category_name_map(store)

    result = []
    for item in user_skill_items:
        skill_dir = item["path"]
        metadata = _parse_skill_metadata(skill_dir)
        category_id = _resolve_row_category(store, skill_dir.name, metadata, skill_dir)
        result.append({
            "name": metadata["name"],
            "dir_name": skill_dir.name,
            "description": metadata["description"],
            "icon": metadata["icon"],
            "version": metadata.get("version", ""),
            "path": str(skill_dir),
            "enabled": item["enabled"],
            "category_id": category_id,
            "category_name": category_names.get(category_id, skill_categories.DEFAULT_CATEGORY_NAME),
            "stats": skill_library.skill_stats(skill_dir),
        })

    # 启用的排前面，其余按目录名
    result.sort(key=lambda row: (not row["enabled"], row["dir_name"]))
    enabled_count = sum(1 for row in result if row["enabled"])

    logger.info(
        f"📂 用户技能目录 | 用户: {username} | 目录: {abs_user_skills_dir} | "
        f"技能数量: {len(result)}（启用 {enabled_count}）"
    )
    return {"skills": result, "enabled_count": enabled_count}


# ── 技能分类 ──────────────────────────────────────────────────────────


@router.get("/categories", summary="获取技能类别列表与归属")
async def list_skill_categories(
    username: Annotated[str, Depends(get_current_username)],
):
    """类别列表 + 技能归属记录（供列表页的分类筛选使用）。

    分类在技能创建时自动归类**定稿**，之后不再变动（前端不提供手动调整）；``assignments``
    就是这份定稿记录，早于该规则的历史技能会在首次列出时补录一次。
    """
    store = skill_categories.load_store(username)
    return {
        "categories": skill_categories.list_categories(username),
        "default_category": {
            "id": skill_categories.DEFAULT_CATEGORY_ID,
            "name": skill_categories.DEFAULT_CATEGORY_NAME,
        },
        "assignments": dict(store["assignments"]),
    }


class CreateCategoryRequest(BaseModel):
    name: str


@router.post("/create-category", summary="新建技能类别")
async def create_category(
    request: CreateCategoryRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """新建自定义类别，追加在类别列表末尾。"""
    try:
        category = skill_categories.create_category(username, request.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "status": "ok",
        "category": category,
        "message": f"类别「{category['name']}」已创建",
    }


class RenameCategoryRequest(BaseModel):
    category_id: str
    name: str


@router.post("/rename-category", summary="重命名技能类别")
async def rename_category(
    request: RenameCategoryRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    try:
        category = skill_categories.rename_category(
            username, request.category_id, request.name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"类别 '{request.category_id}' 不存在")

    return {
        "status": "ok",
        "category": category,
        "message": f"类别已重命名为「{category['name']}」",
    }


class DeleteCategoryRequest(BaseModel):
    category_id: str


@router.post("/delete-category", summary="删除技能类别")
async def delete_category(
    request: DeleteCategoryRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """删除类别；该类别下技能的归属记录一并清空（回落到自动归类）。"""
    try:
        result = skill_categories.delete_category(username, request.category_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"类别 '{request.category_id}' 不存在")

    return {
        "status": "ok",
        "unassigned": result["unassigned"],
        "message": f"类别「{result['name']}」已删除",
    }


class ReorderCategoriesRequest(BaseModel):
    category_ids: list[str]


@router.post("/reorder-categories", summary="调整技能类别顺序")
async def reorder_categories(
    request: ReorderCategoriesRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """按传入 id 顺序重排类别；未列出的类别排在末尾（保持原有相对顺序）。"""
    categories = skill_categories.reorder_categories(username, request.category_ids)
    return {"status": "ok", "categories": categories}


class AssignCategoryRequest(BaseModel):
    dir_name: str
    #: 目标类别 id；空字符串表示清除显式归属、恢复自动归类
    category_id: str = ""


@router.post("/assign-category", summary="设置技能所属类别")
async def assign_category(
    request: AssignCategoryRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """把技能归入指定类别（公共技能与已添加的用户技能都可标注）。

    ``category_id`` 传空字符串即取消手动归类，恢复按内容自动判断。
    """
    root = _public_skills_root()
    is_user_skill = skill_library.find_user_skill(username, request.dir_name) is not None
    is_public_skill = root is not None and skill_library.is_skill_dir(
        root / request.dir_name
    )
    if not is_user_skill and not is_public_skill:
        raise HTTPException(status_code=404, detail=f"技能 '{request.dir_name}' 不存在")

    try:
        resolved = skill_categories.assign_skill(
            username, request.dir_name, request.category_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"类别 '{request.category_id}' 不存在"
        )

    store = skill_categories.load_store(username)
    if not resolved:
        # 取消手动归类：按技能内容重新推断，返回前端应展示的类别
        found = skill_library.find_user_skill(username, request.dir_name)
        if found:
            skill_dir = Path(found["path"])
        else:
            skill_dir = root / request.dir_name if root is not None else None
        if skill_dir is not None:
            metadata = _parse_skill_metadata(skill_dir)
            resolved = _resolve_row_category(store, request.dir_name, metadata, skill_dir)
    category_names = _category_name_map(store)
    return {
        "status": "ok",
        "dir_name": request.dir_name,
        "category_id": resolved,
        "category_name": category_names.get(
            resolved, skill_categories.DEFAULT_CATEGORY_NAME
        ),
        "message": "已更新技能类别",
    }


# ── 技能详情 / 启用停用 ────────────────────────────────────────────────


@router.get("/skill-detail", summary="获取技能详情（含 SKILL.md 正文与统计）")
async def get_skill_detail(
    dir_name: str,
    scope: str,
    username: Annotated[str, Depends(get_current_username)],
):
    """技能详情：正文 + 元数据 + 统计。

    Args:
        dir_name: 技能目录名
        scope: ``public``（公共技能库）或 ``user``（我的技能，含已停用）
    """
    if scope not in ("public", "user"):
        raise HTTPException(status_code=400, detail="scope 只能是 public 或 user")

    installed = {
        item["dir_name"]: item["enabled"]
        for item in skill_library.list_user_skill_dirs(username)
    }

    if scope == "public":
        root = _public_skills_root()
        if root is None:
            raise HTTPException(status_code=400, detail="系统未配置公共技能目录")
        skill_dir = root / dir_name
        if not skill_library.is_skill_dir(skill_dir):
            raise HTTPException(status_code=404, detail=f"公共技能 '{dir_name}' 不存在")
        enabled = installed.get(dir_name, False)
        path = None
    else:
        found = skill_library.find_user_skill(username, dir_name)
        if not found:
            raise HTTPException(status_code=404, detail=f"技能 '{dir_name}' 不存在")
        skill_dir = found["path"]
        enabled = found["enabled"]
        path = str(skill_dir)

    metadata = _parse_skill_metadata(skill_dir)
    doc_file, content = skill_library.read_skill_document(skill_dir)

    store = skill_categories.load_store(username)
    category_id = _resolve_row_category(store, dir_name, metadata, skill_dir)

    return {
        "name": metadata["name"],
        "dir_name": dir_name,
        "scope": scope,
        "description": metadata["description"],
        "icon": metadata["icon"],
        "version": metadata.get("version", ""),
        "category_id": category_id,
        "category_name": _category_name_map(store).get(
            category_id, skill_categories.DEFAULT_CATEGORY_NAME
        ),
        "doc_file": doc_file,
        "content": content,
        "added": dir_name in installed,
        "enabled": enabled,
        "stats": skill_library.skill_stats(skill_dir),
        "path": path,
    }


class ToggleSkillRequest(BaseModel):
    dir_name: str


def _toggle_user_skill(username: str, dir_name: str, enable: bool) -> dict:
    """启用/停用用户技能，并让缓存中的 Agent 失效（下次对话即按新状态加载）。"""
    try:
        result = skill_library.set_skill_enabled(username, dir_name, enable)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"技能 '{dir_name}' 不存在")
    except FileExistsError:
        raise HTTPException(
            status_code=409,
            detail=f"技能 '{dir_name}' 已在目标位置，请刷新后重试",
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="权限不足，无法移动技能目录")

    invalidate_user_agents(username)
    return {
        "status": "ok",
        "dir_name": dir_name,
        "enabled": result["enabled"],
        "message": f"技能 '{dir_name}' 已{'启用' if enable else '停用'}",
    }


@router.post("/enable-skill", summary="启用技能（移回 Agent 加载目录）")
async def enable_skill(
    request: ToggleSkillRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    return _toggle_user_skill(username, request.dir_name, True)


@router.post("/disable-skill", summary="停用技能（移出 Agent 加载目录）")
async def disable_skill(
    request: ToggleSkillRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    return _toggle_user_skill(username, request.dir_name, False)


# ── 添加技能 ──────────────────────────────────────────────────────────


class AddSkillRequest(BaseModel):
    dir_name: str  # 公共技能的目录名（如 "pdf", "xlsx"）


@router.post("/add-skill", summary="添加公共技能到用户目录")
async def add_skill_to_user(
    request: AddSkillRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """将公共技能文件夹完整复制到用户 workspace/{username}/skills/ 目录下"""
    # 公共技能可能有多个候选位置（CWD 下的 ./skills、项目根下的 ./skills），
    # 统一由 skill_library 判定，避免「换启动目录就找不到技能」。
    source_dir = skill_library.public_skill_source(request.dir_name)
    if source_dir is None:
        searched = "；".join(
            str(item) for item in skill_library.public_skills_dir_candidates()
        ) or "（未配置 tools.skills_dir）"
        raise HTTPException(
            status_code=404,
            detail=f"公共技能 '{request.dir_name}' 不存在。已查找: {searched}",
        )

    try:
        # 原子复制（与「专家依赖技能自动补装」共用同一份实现，见 skill_library）
        target_dir = skill_library.install_public_skill_to_user(
            username, request.dir_name
        )["path"]

        # 已缓存的 Agent 持有旧技能列表（技能在 Agent 构造时固化），必须失效重建，
        # 否则新添加的技能要等 Agent 因其它原因重建或进程重启才生效
        invalidate_user_agents(username)

        # 技能创建时按内容自动归类（用户之后可在技能中心手动调整）
        category_id = skill_categories.auto_assign_skill_dir(
            username, request.dir_name, target_dir
        )

        abs_target = target_dir.resolve()
        logger.info(
            f"技能添加成功 | 用户: {username} | 技能: {request.dir_name} | "
            f"目标绝对路径: {abs_target} | 类别: {category_id}"
        )
        return {
            "status": "ok",
            "category_id": category_id,
            "message": f"技能 '{request.dir_name}' 添加成功",
        }

    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"技能 '{request.dir_name}' 已添加，请勿重复操作")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"权限不足，无法复制技能文件: {e}")
    except Exception as e:
        logger.error(f"技能添加失败 | 用户: {username} | 技能: {request.dir_name} | 错误: {e}")
        raise HTTPException(status_code=500, detail=f"添加技能失败: {e}")


# ── 移除技能 ──────────────────────────────────────────────────────────


class RemoveSkillRequest(BaseModel):
    dir_name: str


@router.post("/remove-skill", summary="从用户目录移除技能（含已停用）")
async def remove_skill_from_user(
    request: RemoveSkillRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """彻底删除用户技能：启用目录与停用目录里的同名技能都会被移除。"""
    if not skill_library.find_user_skill(username, request.dir_name):
        raise HTTPException(status_code=404, detail=f"技能 '{request.dir_name}' 不存在")

    try:
        skill_library.remove_user_skill(username, request.dir_name)
        invalidate_user_agents(username)
        return {"status": "ok", "message": f"技能 '{request.dir_name}' 已移除"}
    except PermissionError:
        raise HTTPException(status_code=403, detail="权限不足，无法删除技能文件")
    except Exception as e:
        logger.error(f"技能移除失败 | 用户: {username} | 技能: {request.dir_name} | 错误: {e}")
        raise HTTPException(status_code=500, detail=f"移除技能失败: {e}")


# ── 导入技能（zip） ─────────────────────────────────────────────────────

MAX_ZIP_SIZE = 50 * 1024 * 1024  # 压缩包上限 50MB
MAX_EXTRACT_SIZE = 200 * 1024 * 1024  # 解压后上限 200MB
MAX_FILE_COUNT = 1000  # 压缩包内条目上限


@router.post("/import-skill", summary="导入技能压缩包(zip)")
async def import_skill_zip(
    username: Annotated[str, Depends(get_current_username)],
    file: UploadFile = File(...),
):
    """接收 zip 压缩包，解压到用户 workspace/{username}/skills/ 目录。

    支持两种压缩包结构：
    - 单层目录：my-skill/SKILL.md（技能名取目录名）
    - 扁平结构：SKILL.md（技能名取 zip 文件名）
    """
    filename = file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持 .zip 压缩包")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="压缩包为空")
    if len(content) > MAX_ZIP_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"压缩包过大，上限 {MAX_ZIP_SIZE // 1024 // 1024}MB",
        )

    # 解析 zip
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="无效的 zip 文件")

    names = zf.namelist()
    if len(names) > MAX_FILE_COUNT:
        raise HTTPException(status_code=400, detail=f"压缩包内条目过多，上限 {MAX_FILE_COUNT}")

    # 安全校验：路径穿越 / 绝对路径 / Windows 盘符
    for name in names:
        if not name:
            continue
        if name.startswith(("/", "\\")):
            raise HTTPException(status_code=400, detail=f"压缩包含非法路径: {name}")
        if len(name) >= 2 and name[1] == ":":
            raise HTTPException(status_code=400, detail=f"压缩包含非法路径: {name}")
        if ".." in Path(name).parts:
            raise HTTPException(status_code=400, detail=f"压缩包含非法路径: {name}")

    user_skills_dir = _get_user_skills_dir(username)
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = user_skills_dir / f".tmp_import_{uuid.uuid4().hex[:8]}"

    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        # 解压并累计解压后大小，防 zip bomb
        total_size = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            total_size += info.file_size
            if total_size > MAX_EXTRACT_SIZE:
                raise HTTPException(status_code=400, detail="解压后内容过大")
            zf.extract(info, tmp_dir)

        # 清理 macOS 压缩产生的垃圾
        for junk in list(tmp_dir.iterdir()):
            if junk.name == "__MACOSX" or junk.name.startswith("._"):
                if junk.is_dir():
                    shutil.rmtree(str(junk), ignore_errors=True)
                else:
                    junk.unlink(missing_ok=True)

        top = [p for p in tmp_dir.iterdir() if not p.name.startswith(".")]
        skill_src = None
        skill_name = None

        # 情况1：顶层仅一个目录 -> 技能名取该目录名
        single_dir = [p for p in top if p.is_dir()]
        if len(single_dir) == 1 and len(top) == 1:
            skill_src = single_dir[0]
            skill_name = single_dir[0].name
        elif any(p.name in ("SKILL.md", "README.md") for p in top):
            # 情况2：顶层直接含 SKILL.md/README.md（扁平结构）-> 用 zip 文件名
            skill_src = tmp_dir
            skill_name = Path(filename).stem
        else:
            # 情况3：多个顶层目录，尝试找唯一含 SKILL.md/README.md 的
            valid = [
                p for p in single_dir
                if (p / "SKILL.md").exists() or (p / "README.md").exists()
            ]
            if len(valid) == 1:
                skill_src = valid[0]
                skill_name = valid[0].name

        if skill_src is None or skill_name is None:
            raise HTTPException(
                status_code=400,
                detail="未找到有效技能目录（需包含 SKILL.md 或 README.md）",
            )

        # 校验技能目录确实含 SKILL.md / README.md
        if not (skill_src / "SKILL.md").exists() and not (skill_src / "README.md").exists():
            raise HTTPException(
                status_code=400,
                detail="技能目录必须包含 SKILL.md 或 README.md",
            )

        # 规范化技能目录名（仅允许字母数字下划线连字符）
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", skill_name).strip("_")
        if not safe_name:
            raise HTTPException(status_code=400, detail="技能目录名无效")

        target_dir = user_skills_dir / safe_name
        if target_dir.exists():
            raise HTTPException(
                status_code=409,
                detail=f"技能 '{safe_name}' 已存在，请先移除或重命名压缩包",
            )

        # 原子落盘
        if skill_src == tmp_dir:
            # 扁平结构：逐项移动到目标目录
            target_dir.mkdir(parents=True, exist_ok=True)
            for item in tmp_dir.iterdir():
                if item.name.startswith("."):
                    continue
                shutil.move(str(item), str(target_dir / item.name))
        else:
            skill_src.rename(target_dir)

        # 与「添加公共技能」保持一致：导入后让缓存 Agent 失效，并自动归类
        invalidate_user_agents(username)
        category_id = skill_categories.auto_assign_skill_dir(
            username, safe_name, target_dir
        )

        logger.info(
            f"技能导入成功 | 用户: {username} | 技能: {safe_name} | 类别: {category_id}"
        )
        return {
            "status": "ok",
            "dir_name": safe_name,
            "category_id": category_id,
            "message": f"技能 '{safe_name}' 导入成功",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"技能导入失败 | 用户: {username} | 错误: {e}")
        raise HTTPException(status_code=500, detail=f"导入技能失败: {e}")
    finally:
        if tmp_dir.exists():
            shutil.rmtree(str(tmp_dir), ignore_errors=True)


# ── 下载技能（zip） ─────────────────────────────────────────────────────

@router.get("/download-skill/{dir_name}", summary="下载技能压缩包(zip)")
async def download_skill_zip(
    dir_name: str,
    username: Annotated[str, Depends(get_current_username)],
):
    """将用户 workspace/{username}/skills/{dir_name} 打包为 zip 下载。"""
    # 校验目录名合法（防路径穿越）
    if not dir_name or ".." in Path(dir_name).parts or "/" in dir_name or "\\" in dir_name:
        raise HTTPException(status_code=400, detail="无效的技能目录名")

    user_skills_dir = _get_user_skills_dir(username)
    skill_dir = user_skills_dir / dir_name
    if not skill_dir.exists() or not skill_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"技能 '{dir_name}' 不存在")

    # 校验是有效技能
    if not (skill_dir / "SKILL.md").exists() and not (skill_dir / "README.md").exists():
        raise HTTPException(status_code=400, detail=f"'{dir_name}' 不是有效的技能目录")

    # 打包 zip 到内存
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in skill_dir.rglob("*"):
            if not item.is_file():
                continue
            arcname = item.relative_to(skill_dir)
            # 跳过隐藏文件（.开头的临时/系统文件）
            if any(part.startswith(".") for part in arcname.parts):
                continue
            zf.write(item, arcname)
    buf.seek(0)

    logger.info(f"技能下载 | 用户: {username} | 技能: {dir_name}")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{dir_name}.zip"'},
    )
