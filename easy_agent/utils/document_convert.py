"""使用 LibreOffice 转换 Office 文档，供在线预览及旧版 DOC 正文解析使用。

相比前端 @vue-office/* 系列组件，LibreOffice 转换能稳定处理含图片/复杂排版的
pptx/xlsx/docx，输出为标准 PDF，由浏览器内置查看器渲染（多页、缩略图、搜索）。

转换结果按源文件路径、mtime、大小及目标格式缓存到 data/converted/。
"""

import hashlib
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from easy_agent.bootstrap.env import env_str

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
    """优先 PATH，再检查 macOS / Windows 常见安装目录。"""
    candidates = ("soffice.com", "libreoffice", "soffice") if sys.platform == "win32" else (
        "libreoffice", "soffice"
    )
    for cand in candidates:
        path = shutil.which(cand)
        if path:
            return path
    if sys.platform == "darwin":
        for app_dir in (Path("/Applications"), Path.home() / "Applications"):
            executable = app_dir / "LibreOffice.app/Contents/MacOS/soffice"
            if executable.is_file():
                return str(executable)
    elif sys.platform == "win32":
        for env_name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            root = env_str(env_name, strip=False)
            if not root:
                continue
            for binary in ("soffice.com", "soffice.exe"):
                executable = Path(root) / "LibreOffice" / "program" / binary
                if executable.is_file():
                    return str(executable)
    return None


def convert_office_document(src: str | Path, target: str = "pdf") -> Path:
    """转换为 PDF、DOCX 或 HTML，返回完整生成并缓存的文件路径。

    Raises:
        ValueError: 文件类型不支持。
        RuntimeError: 未安装 LibreOffice 或转换执行失败。
    """
    if target not in {"pdf", "docx", "html"}:
        raise ValueError(f"不支持的转换目标格式: {target}")
    src = Path(src).resolve()
    ext = src.suffix.lower()
    allowed = SUPPORTED_EXT if target == "pdf" else {".doc", ".docx", ".dotx", ".odt"}
    if ext not in allowed:
        raise ValueError(f"不支持转换为 {target.upper()} 的文件类型: {ext}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stat = src.stat()
    cache_key = hashlib.sha256(
        f"{src}:{stat.st_mtime_ns}:{stat.st_size}:{target}".encode()
    ).hexdigest()[:24]
    out_dir = CACHE_DIR / cache_key
    out_dir.mkdir(parents=True, exist_ok=True)
    cached = out_dir / f"{src.stem}.{target}"

    if cached.is_file() and cached.stat().st_size > 0:
        logger.info("复用文档转换缓存 | %s", cached)
        return cached

    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError(
            "未找到 LibreOffice（libreoffice/soffice），请安装 LibreOffice Writer 并加入 PATH"
        )

    # 同一源文件的并发转换也必须隔离配置和输出；完整生成后再原子发布到缓存。
    try:
        with TemporaryDirectory(prefix=".convert-", dir=out_dir) as temp_dir:
            work_dir = Path(temp_dir).resolve()
            profile_uri = (work_dir / "lo_profile").as_uri()
            output_format = {
                "docx": "docx:Office Open XML Text",
                "html": "html:XHTML Writer File",
                "pdf": "pdf",
            }[target]
            result = subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--norestore",
                    "--nofirststartwizard",
                    f"-env:UserInstallation={profile_uri}",
                    "--convert-to", output_format,
                    "--outdir", str(work_dir),
                    str(src),
                ],
                check=True,
                capture_output=True,
                timeout=180,
            )
            generated = work_dir / f"{src.stem}.{target}"
            if not generated.is_file() or generated.stat().st_size == 0:
                raw = (result.stderr or b"").decode("utf-8", "replace").strip()
                lines = [line.strip() for line in raw.splitlines() if line.strip()]
                picked = [line for line in lines if line.lower().startswith("error")] or lines
                detail = " / ".join(picked)[:200]
                logger.error("LibreOffice 未生成有效目标文件 | %s | %s", src, detail)
                suffix = f"（{detail}）" if detail else ""
                raise RuntimeError(f"文档转换未生成有效目标文件{suffix}")
            generated.replace(cached)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", "ignore") if e.stderr else ""
        logger.error(f"LibreOffice 转换失败 | {src} | {stderr}")
        raise RuntimeError(f"文档转换失败: {stderr[:200] or e}") from e
    except subprocess.TimeoutExpired as e:
        logger.error(f"LibreOffice 转换超时 | {src} | {e}")
        raise RuntimeError("文档转换超时（请稍后重试）") from e
    except OSError as e:
        raise RuntimeError(f"文档转换无法执行: {e}") from e
    return cached


def convert_to_pdf(src: str | Path, target: str = "pdf") -> Path:
    """在线预览使用的兼容入口。"""
    return convert_office_document(src, target)
