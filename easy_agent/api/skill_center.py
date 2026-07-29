"""技能中心 API：公共技能列表、用户技能列表、添加/移除技能"""

import logging
import re
import shutil
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import Config
from ..db import Database, get_database
from ..middleware import get_current_username
from ..skills import discover_skills
from ..services import get_agent_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skill-center", tags=["Skill Center"])


def _parse_skill_metadata(skill_dir: Path) -> dict:
    """从 SKILL.md 或 README.md 的 YAML frontmatter 中解析技能元数据"""
    metadata = {
        "name": skill_dir.name,
        "description": "",
        "icon": "",
    }

    for filename in ("SKILL.md", "README.md"):
        filepath = skill_dir / filename
        if not filepath.exists():
            continue

        content = filepath.read_text(encoding="utf-8")

        # 解析 YAML frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            for line in fm_text.split("\n"):
                if line.startswith("name:"):
                    metadata["name"] = line.split(":", 1)[1].strip().strip('"').strip("'")
                elif line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    # 处理引号包裹的描述
                    if desc.startswith('"') and desc.endswith('"'):
                        desc = desc[1:-1]
                    elif desc.startswith("'") and desc.endswith("'"):
                        desc = desc[1:-1]
                    metadata["description"] = desc
                elif line.startswith("icon:"):
                    metadata["icon"] = line.split(":", 1)[1].strip().strip('"').strip("'")

        # 如果没有 frontmatter description，取第一段非标题文本
        if not metadata["description"]:
            lines = content.split("\n")
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                    metadata["description"] = stripped[:200]
                    break

        break  # 只读第一个找到的文件

    return metadata


def _get_user_skills_dir(username: str) -> Path:
    """获取用户的技能目录路径，遵循配置 agent.workspace_dir"""
    return Config.get_user_workspace_dir(username) / "skills"


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

    user_skills_dir = _get_user_skills_dir(username)
    user_skill_names = set()
    if user_skills_dir.exists():
        for d in user_skills_dir.iterdir():
            if d.is_dir() and (d / "SKILL.md").exists() or (d / "README.md").exists():
                user_skill_names.add(d.name)

    result = []
    for skill in skills:
        skill_dir = Path(skill["path"])
        metadata = _parse_skill_metadata(skill_dir)
        result.append({
            "name": metadata["name"],
            "dir_name": skill_dir.name,
            "description": metadata["description"],
            "icon": metadata["icon"],
            "added": skill_dir.name in user_skill_names,
        })

    return {"skills": result}


# ── 用户技能 ──────────────────────────────────────────────────────────


@router.get("/user-skills", summary="获取用户技能列表")
async def list_user_skills(
    username: Annotated[str, Depends(get_current_username)],
):
    """从用户 workspace/{username}/skills/ 目录加载技能列表"""
    user_skills_dir = _get_user_skills_dir(username)
    abs_user_skills_dir = str(user_skills_dir.absolute())

    if not user_skills_dir.exists():
        logger.info(
            f"📂 用户技能目录不存在 | 用户: {username} | 目录: {abs_user_skills_dir} | 技能数量: 0"
        )
        return {"skills": []}

    result = []
    for skill_dir in sorted(user_skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not (skill_dir / "SKILL.md").exists() and not (skill_dir / "README.md").exists():
            continue

        metadata = _parse_skill_metadata(skill_dir)
        result.append({
            "name": metadata["name"],
            "dir_name": skill_dir.name,
            "description": metadata["description"],
            "icon": metadata["icon"],
            "path": str(skill_dir),
        })

    logger.info(
        f"📂 用户技能目录 | 用户: {username} | 目录: {abs_user_skills_dir} | 技能数量: {len(result)}"
    )
    return {"skills": result}


# ── 添加技能 ──────────────────────────────────────────────────────────


class AddSkillRequest(BaseModel):
    dir_name: str  # 公共技能的目录名（如 "pdf", "xlsx"）


@router.post("/add-skill", summary="添加公共技能到用户目录")
async def add_skill_to_user(
    request: AddSkillRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """将公共技能文件夹完整复制到用户 workspace/{username}/skills/ 目录下"""
    _cfg = get_agent_config()
    skills_root = _cfg["config"].tools.skills_dir if _cfg else "./skills"

    if not skills_root:
        raise HTTPException(status_code=400, detail="系统未配置公共技能目录")

    # 查找源技能目录
    source_dir = Path(skills_root) / request.dir_name
    if not source_dir.exists() or not source_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"公共技能 '{request.dir_name}' 不存在")

    # 检查是否为合法的技能目录
    if not (source_dir / "SKILL.md").exists() and not (source_dir / "README.md").exists():
        raise HTTPException(status_code=400, detail=f"'{request.dir_name}' 不是有效的技能目录")

    # 目标目录
    user_skills_dir = _get_user_skills_dir(username)
    target_dir = user_skills_dir / request.dir_name

    # 如果已存在，先删除
    if target_dir.exists():
        raise HTTPException(status_code=409, detail=f"技能 '{request.dir_name}' 已添加，请勿重复操作")

    try:
        # 确保用户技能目录存在
        user_skills_dir.mkdir(parents=True, exist_ok=True)

        # 原子复制：先复制到临时目录，再重命名
        temp_dir = user_skills_dir / f".tmp_{request.dir_name}"
        if temp_dir.exists():
            shutil.rmtree(str(temp_dir))

        shutil.copytree(str(source_dir), str(temp_dir))
        temp_dir.rename(target_dir)

        abs_target = target_dir.resolve()
        logger.info(
            f"技能添加成功 | 用户: {username} | 技能: {request.dir_name} | 目标绝对路径: {abs_target}"
        )
        return {"status": "ok", "message": f"技能 '{request.dir_name}' 添加成功"}

    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"权限不足，无法复制技能文件: {e}")
    except Exception as e:
        # 清理临时目录
        temp_dir = user_skills_dir / f".tmp_{request.dir_name}"
        if temp_dir.exists():
            shutil.rmtree(str(temp_dir), ignore_errors=True)
        logger.error(f"技能添加失败 | 用户: {username} | 技能: {request.dir_name} | 错误: {e}")
        raise HTTPException(status_code=500, detail=f"添加技能失败: {e}")


# ── 移除技能 ──────────────────────────────────────────────────────────


class RemoveSkillRequest(BaseModel):
    dir_name: str


@router.post("/remove-skill", summary="从用户目录移除技能")
async def remove_skill_from_user(
    request: RemoveSkillRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """从用户 workspace/{username}/skills/ 目录下移除指定技能"""
    user_skills_dir = _get_user_skills_dir(username)
    target_dir = user_skills_dir / request.dir_name

    if not target_dir.exists():
        raise HTTPException(status_code=404, detail=f"技能 '{request.dir_name}' 不存在")

    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"'{request.dir_name}' 不是有效的技能目录")

    try:
        shutil.rmtree(str(target_dir))
        logger.info(f"技能移除成功 | 用户: {username} | 技能: {request.dir_name}")
        return {"status": "ok", "message": f"技能 '{request.dir_name}' 已移除"}
    except PermissionError:
        raise HTTPException(status_code=403, detail="权限不足，无法删除技能文件")
    except Exception as e:
        logger.error(f"技能移除失败 | 用户: {username} | 技能: {request.dir_name} | 错误: {e}")
        raise HTTPException(status_code=500, detail=f"移除技能失败: {e}")
