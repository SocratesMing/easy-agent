"""旧版 .doc 文档到 .docx 的懒转换（供在线预览使用）。

设计目标：可移植、不侵入上传链路。
- 仅当预览接口收到 convert=docx 且目标为 .doc 时按需转换；
- 转换结果落盘缓存（data/docx_cache/），同一文档内容只转换一次；
- 引擎探测按可移植优先级：
  1. LibreOffice soffice（Linux/Docker/Windows 通用，推荐部署时安装，
     可用环境变量 SOFFICE_PATH 指定可执行文件路径）；
  2. Windows 上的 Word COM（需安装 Office 与 pywin32）；
  3. Windows 上的 WPS COM（KWPS.Application / wps.Application）。
全部不可用或转换失败时抛 DocConverterError，由接口层转为 415 提示。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = _PROJECT_ROOT / "data" / "docx_cache"

_SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/opt/libreoffice/program/soffice",
)

_COM_PROGIDS = ("Word.Application", "KWPS.Application", "wps.Application")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class DocConverterError(RuntimeError):
    """转换不可用或失败，message 面向最终用户。"""


def _find_soffice() -> str | None:
    override = os.environ.get("SOFFICE_PATH", "").strip()
    if override and Path(override).exists():
        return override
    found = shutil.which("soffice") or shutil.which("soffice.exe")
    if found:
        return found
    for candidate in _SOFFICE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _convert_with_soffice(exe: str, src: Path, dst: Path) -> None:
    result = subprocess.run(  # noqa: S603
        [exe, "--headless", "--convert-to", "docx", "--outdir", str(dst.parent), str(src)],
        capture_output=True,
        timeout=180,
    )
    produced = dst.parent / f"{src.stem}.docx"
    if result.returncode != 0 or not produced.exists():
        raise DocConverterError("LibreOffice 转换 .doc 失败，请确认文件未损坏。")


def _convert_with_com(progid: str, src: Path, dst: Path) -> None:
    import pythoncom  # noqa: PLC0415
    import win32com.client  # noqa: PLC0415

    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.DispatchEx(progid)
        app.Visible = False
        try:
            app.DisplayAlerts = 0
        except Exception:  # WPS 部分版本无此属性
            pass
        doc = app.Documents.Open(str(src), ReadOnly=True, AddToRecentFiles=False)
        try:
            doc.SaveAs2(str(dst), FileFormat=16)  # 16 = docx
        finally:
            try:
                doc.Close(False)
            except Exception:
                pass  # SaveAs2 已写盘，Close 失败不影响转换结果
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass  # Quit 阶段 RPC 失败不影响已完成的转换
        pythoncom.CoUninitialize()


def _safe_stem(name: str) -> str:
    stem = re.split(r"[\\/]", name or "document")[-1]
    stem = re.sub(r'[\\/:*?"<>|]', "_", stem).strip() or "document"
    return stem[-60:]


def convert_doc_to_docx_bytes(name: str, data: bytes) -> bytes:
    """把 .doc 二进制转换为 .docx 二进制；无可用引擎或失败时抛 DocConverterError。"""
    if not data:
        raise DocConverterError("文档内容为空，无法转换。")
    with tempfile.TemporaryDirectory(prefix="doc2docx-") as tmp:
        tmp_dir = Path(tmp)
        stem = _safe_stem(name)
        src = tmp_dir / f"{stem}.doc"
        dst = tmp_dir / f"{stem}.docx"
        src.write_bytes(data)

        soffice = _find_soffice()
        if soffice:
            try:
                _convert_with_soffice(soffice, src, dst)
                return dst.read_bytes()
            except DocConverterError:
                pass  # soffice 失败后继续尝试 COM

        errors: list[str] = []
        for progid in _COM_PROGIDS:
            try:
                _convert_with_com(progid, src, dst)
                return dst.read_bytes()
            except ImportError:
                break  # 非 Windows：pywin32 不可用，COM 路径整体跳过
            except Exception as exc:  # COM 调用失败换下一个 progid
                errors.append(f"{progid}: {exc}")

        detail = "；".join(errors[:3])
        raise DocConverterError(
            "当前环境未安装 LibreOffice 或 Word/WPS，暂不支持 .doc 在线预览，请下载后查看。"
            + (f"（{detail}）" if detail else "")
        )


def docx_cache_key(document_id: str, sha256: str | None, size_bytes: int) -> str:
    raw = f"{sha256 or document_id}:{size_bytes}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def docx_cache_get(key: str) -> bytes | None:
    try:
        path = CACHE_DIR / f"{key}.docx"
        return path.read_bytes() if path.exists() else None
    except OSError:
        return None


def docx_cache_put(key: str, data: bytes) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"{key}.docx").write_bytes(data)
    except OSError:
        pass  # 缓存写失败不影响本次预览
