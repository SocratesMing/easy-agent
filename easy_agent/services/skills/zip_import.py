"""技能压缩包（zip）校验、安全解压与落盘安装（从 api/skill_center.py 下沉到服务层）。"""

from __future__ import annotations

import io
import shutil
import uuid
import zipfile
from pathlib import Path

from easy_agent.agent.skills import MAX_SKILL_NAME_CHARS, SKILL_NAME_RE
from easy_agent.models.exceptions import BadRequestError, ConflictError

MAX_ZIP_SIZE = 50 * 1024 * 1024  # 压缩包上限 50MB
MAX_EXTRACT_SIZE = 200 * 1024 * 1024  # 解压后上限 200MB
MAX_FILE_COUNT = 1000  # 压缩包内条目上限


def is_unsafe_zip_path(name: str) -> bool:
    """路径穿越 / 绝对路径 / Windows 盘符。"""
    return (
        name.startswith(("/", "\\"))
        or (len(name) >= 2 and name[1] == ":")
        or ".." in Path(name).parts
    )


def open_skill_zip(content: bytes) -> zipfile.ZipFile:
    """校验上传内容并打开 zip：大小、条目数上限与条目路径安全。"""
    if not content:
        raise BadRequestError("压缩包为空")
    if len(content) > MAX_ZIP_SIZE:
        raise BadRequestError(
            f"压缩包过大，上限 {MAX_ZIP_SIZE // 1024 // 1024}MB"
        )
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise BadRequestError("无效的 zip 文件") from exc

    names = zf.namelist()
    if len(names) > MAX_FILE_COUNT:
        raise BadRequestError(f"压缩包内条目过多，上限 {MAX_FILE_COUNT}")
    for name in names:
        if name and is_unsafe_zip_path(name):
            raise BadRequestError(f"压缩包含非法路径: {name}")
    return zf


def extract_skill_zip(zf: zipfile.ZipFile, tmp_dir: Path) -> None:
    """解压到临时目录：累计解压后大小防 zip bomb，并清理 macOS 垃圾文件。"""
    total_size = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        total_size += info.file_size
        if total_size > MAX_EXTRACT_SIZE:
            raise BadRequestError("解压后内容过大")
        zf.extract(info, tmp_dir)

    for junk in list(tmp_dir.iterdir()):
        if junk.name == "__MACOSX" or junk.name.startswith("._"):
            if junk.is_dir():
                shutil.rmtree(str(junk), ignore_errors=True)
            else:
                junk.unlink(missing_ok=True)


def has_skill_doc(path: Path) -> bool:
    return (path / "SKILL.md").exists() or (path / "README.md").exists()


def locate_skill_root(tmp_dir: Path, zip_filename: str) -> tuple[Path, str]:
    """在解压结果里定位技能根目录，返回 ``(目录, 技能名)``。"""
    top = [p for p in tmp_dir.iterdir() if not p.name.startswith(".")]
    dirs = [p for p in top if p.is_dir()]
    if len(dirs) == 1 and len(top) == 1:
        skill_src, skill_name = dirs[0], dirs[0].name
    elif any(p.name in ("SKILL.md", "README.md") for p in top):
        skill_src, skill_name = tmp_dir, Path(zip_filename).stem
    else:
        valid = [p for p in dirs if has_skill_doc(p)]
        if len(valid) != 1:
            raise BadRequestError(
                "未找到有效技能目录（需包含 SKILL.md 或 README.md）"
            )
        skill_src, skill_name = valid[0], valid[0].name

    if not has_skill_doc(skill_src):
        raise BadRequestError("技能目录必须包含 SKILL.md 或 README.md")
    return skill_src, skill_name


def validate_skill_dir_name(skill_name: str) -> str:
    """校验技能目录名，不合规抛出 BadRequestError。"""
    name = (skill_name or "").strip()
    if not name:
        raise BadRequestError("技能目录名无效")
    if len(name) > MAX_SKILL_NAME_CHARS:
        raise BadRequestError(
            f"技能目录名「{name}」超过 {MAX_SKILL_NAME_CHARS} 个字符"
        )
    if not SKILL_NAME_RE.match(name):
        raise BadRequestError(
            f"技能目录名「{name}」只能包含小写字母、数字和单个连字符（-）"
        )
    return name


def install_skill_dir(
    user_skills_dir: Path, skill_src: Path, skill_name: str, tmp_dir: Path
) -> tuple[str, Path]:
    """将技能目录落盘到用户技能库，返回 ``(规范化目录名, 目标目录)``；同名已存在抛 ConflictError。"""
    safe_name = validate_skill_dir_name(skill_name)
    target_dir = user_skills_dir / safe_name
    if target_dir.exists():
        raise ConflictError(
            f"技能 '{safe_name}' 已存在，请先移除或重命名压缩包"
        )

    if skill_src == tmp_dir:
        target_dir.mkdir(parents=True, exist_ok=True)
        for item in tmp_dir.iterdir():
            if item.name.startswith("."):
                continue
            shutil.move(str(item), str(target_dir / item.name))
    else:
        skill_src.rename(target_dir)
    return safe_name, target_dir


def import_skill_zip_bytes(
    user_skills_dir: Path, filename: str, content: bytes
) -> tuple[str, Path]:
    """完整执行 zip 校验 → 临时目录解压 → 定位根目录 → 原子落盘并清理临时目录。"""
    zf = open_skill_zip(content)
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = user_skills_dir / f".tmp_import_{uuid.uuid4().hex[:8]}"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        extract_skill_zip(zf, tmp_dir)
        skill_src, skill_name = locate_skill_root(tmp_dir, filename)
        return install_skill_dir(user_skills_dir, skill_src, skill_name, tmp_dir)
    finally:
        zf.close()
        if tmp_dir.exists():
            shutil.rmtree(str(tmp_dir), ignore_errors=True)
