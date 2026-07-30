"""使用 LibreOffice 将 Office 文档转换为 PDF，用于前端在线预览。

相比前端 @vue-office/* 系列组件，LibreOffice 转换能稳定处理含图片/复杂排版的
pptx/xlsx/docx，输出为标准 PDF，由浏览器内置查看器渲染（多页、缩略图、搜索）。

转换结果按「源文件路径 + mtime」哈希缓存到 data/converted/，避免重复转换。
"""

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path("./data/converted")

# 支持转换为 PDF 的源文件类型
SUPPORTED_EXT = frozenset({
    ".pptx", ".ppt", ".potx", ".ppsx",
    ".docx", ".doc", ".dotx",
    ".xlsx", ".xls", ".xltx",
    ".odp", ".odt", ".ods",
})


def _find_soffice() -> str | None:
    """查找 LibreOffice 可执行文件（libreoffice 或 soffice）。"""
    for cand in ("libreoffice", "soffice"):
        path = shutil.which(cand)
        if path:
            return path
    return None


def convert_to_pdf(src: str | Path, target: str = "pdf") -> Path:
    """将 Office 文档转换为 PDF，返回生成的 PDF 路径。

    结果缓存：若缓存的 PDF 比源文件更新则直接复用。

    Raises:
        ValueError: 文件类型不支持。
        RuntimeError: 未安装 LibreOffice 或转换执行失败。
    """
    src = Path(src)
    ext = src.suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise ValueError(f"不支持转换为 PDF 的文件类型: {ext}")

    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError("未找到 LibreOffice（libreoffice/soffice），无法转换文档用于预览")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # 以「源文件绝对路径 + mtime」做缓存键，源文件更新后自动失效
    cache_key = hashlib.sha256(
        f"{src.resolve()}:{src.stat().st_mtime}".encode()
    ).hexdigest()[:24]
    out_dir = CACHE_DIR / cache_key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{src.stem}.{target}"

    # 命中缓存且未过期
    if out_pdf.exists() and out_pdf.stat().st_mtime >= src.stat().st_mtime:
        logger.info(f"复用已转换的 PDF 缓存 | {out_pdf}")
        return out_pdf

    # 每个转换任务使用独立的 LibreOffice 用户配置目录，避免并发实例的 profile 锁冲突
    user_profile = out_dir / "lo_profile"
    try:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                "--nofirststartwizard",
                f"-env:UserInstallation=file://{user_profile.resolve()}",
                "--convert-to", target,
                "--outdir", str(out_dir),
                str(src),
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", "ignore") if e.stderr else ""
        logger.error(f"LibreOffice 转换失败 | {src} | {stderr}")
        raise RuntimeError(f"文档转换失败: {stderr[:200] or e}")
    except subprocess.TimeoutExpired as e:
        logger.error(f"LibreOffice 转换超时 | {src} | {e}")
        raise RuntimeError("文档转换超时（请稍后重试或下载后查看）")

    generated = list(out_dir.glob(f"*.{target}"))
    if not generated:
        raise RuntimeError("文档转换未生成目标文件")

    # 归一化输出文件名（LibreOffice 默认以源 stem 命名，通常已一致）
    if generated[0] != out_pdf:
        generated[0].replace(out_pdf)
    return out_pdf
