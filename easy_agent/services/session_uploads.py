"""会话上传文件的解析落盘与清单（记忆）维护。

背景
----
改造前：聊天接口把上传文件的解析全文塞进 system_prompt / 用户消息，只对
"本轮请求带文件"的对话有效；后续轮次请求不带文件，模型就再也看不到内容
（而且首轮内容挂在内存中的 Agent 缓存上，缓存一驱逐即丢失）。

改造后（本模块负责前两步）：
1. 上传即解析：``store_parsed_content`` 把解析文本落盘到会话工作区
   ``uploads/{文件名}.txt``，一次解析、全程复用，不再每轮重复解析；
2. 上传即记清单：``append_manifest`` 把文件名/大小/上传时间/解析状态/正文相对路径
   写入 ``uploads/index.md``；该文件由 ``agent.py`` 作为**记忆文件**注入 system
   prompt，因此模型每轮都知道用户传过哪些文件、正文在哪；
3. 正文不注入：模型按需用内置的 grep / read_file 工具在 ``uploads/`` 目录检索，
   避免每轮把全文塞进上下文。

路径口径与会话工作区完全一致（见 ``agent.py`` 的 workspace_dir 解析）::

    {config.agent.workspace_dir}/{safe_username}/session/{workspace_name}

清单里一律写**相对工作区的路径**（``uploads/xxx.txt``），因为工作区可能被 LLM
重命名（``_maybe_rename_workspace``），相对路径在重命名后依然有效。
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from easy_agent.bootstrap.config import Config
from easy_agent.utils.file_parser import parse_file_content
from easy_agent.services.upload_storage import (
    UploadStorageError,
    is_store_uri,
    materialize_upload,
)

logger = logging.getLogger("easy_agent.session_uploads")

UPLOADS_DIR_NAME = "uploads"
MANIFEST_FILENAME = "index.md"

# 清单头部：既是给模型的检索说明，也是"来源标注"要求的落点。
# 这段话每轮都在 system prompt 里（清单作为记忆注入），所以必须简短、无冗余。
MANIFEST_HEADER = """# 用户上传的文件（本会话）

> 本清单记录用户在本会话上传过的文件。
> - `uploads/{文件名}.txt` 是系统解析出的**正文副本**（工作区内，可用 `grep` /
>   `read_file` 检索）；它不是用户给的文件，**向用户展示来源时请说
>   "您上传的文件《原始文件名》"，绝不要提 uploads/*.txt 这类内部解析文件**。
> - **原始文件保存在服务端 `uploadfiles/` 目录，不在工作区内**，`grep`/`read_file`
>   读不到它。需要原始内容时（尤其清单标注"解析失败/未解析"时，可能只是当时解析
>   库不可用）调用 `read_uploaded_file(文件名)` 工具：它会到 `uploadfiles/` 重新
>   解析源文件、回填正文并更新本清单。
> - 回答与文件有关的问题前先检索，不要凭记忆或猜测作答；文件中确实没有相关
>   内容时，明确告知用户"上传的文件中未找到相关内容"，再给出答案并说明其来源
>   （网络搜索 / 知识库 / 模型自身知识）。
> - 回答涉及文件时标注来源，例如：来源：上传文件《文件名》。

"""


# ── 配置 ────────────────────────────────────────────────────────────────


def _file_context_config():
    """取运行期 file_context 配置；不可用时返回 None（调用方按默认值处理）。"""
    try:
        from easy_agent.bootstrap.runtime_config import get_runtime_agent_config

        cfg = get_runtime_agent_config()
        if cfg and cfg.get("config"):
            return getattr(cfg["config"], "file_context", None)
    except Exception as e:  # 配置未初始化（如单测直接调本模块）时不影响功能
        logger.warning(f"读取 file_context 配置失败（按默认值处理）: {e}")
    return None


def is_enabled() -> bool:
    """file_context 机制是否启用（配置不可用时按启用处理）。"""
    fc = _file_context_config()
    if fc is None:
        return True
    return bool(getattr(fc, "enabled", True))


def _cfg_int(name: str, default: int) -> int:
    fc = _file_context_config()
    if fc is None:
        return default
    try:
        return int(getattr(fc, name, default))
    except (TypeError, ValueError):
        return default


# ── 路径 ────────────────────────────────────────────────────────────────


def _session_store(db=None):
    """解析会话数据端口。

    接受 ``Database``，也接受暴露 ``get_session`` / ``get_session_files`` 的
    repository（``ChatRepository`` / ``ExpertRepository`` 等）；API 层因此可以
    直接传 repository，不必再向下取 ``repo.database``。不传时回退进程级
    ``get_database()``，既有直接调用点行为不变。
    """
    if db is not None:
        return db
    from easy_agent.db import get_database

    return get_database()


def get_session_workspace_dir(session_id: str, username: str, db=None) -> Path | None:
    """会话工作区目录，与 ``agent.py`` 的口径一致。

    ``workspace_name`` 为空时回退使用 ``session_id`` 作目录名（agent.py 同规则）。
    ``db`` 是会话数据端口（``Database`` 或 repository），见 ``_session_store``。
    """
    if not session_id or not username:
        return None
    workspace_name = ""
    try:
        database = _session_store(db)
        session = database.get_session(session_id)
        if session is not None:
            workspace_name = getattr(session, "workspace_name", "") or ""
    except Exception as e:
        logger.warning(f"读取会话 workspace_name 失败（回退 session_id）: {e}")
    dir_name = workspace_name or session_id
    try:
        return Config.get_user_workspace_dir(username) / "session" / dir_name
    except Exception as e:
        logger.warning(f"解析会话工作区目录失败: {e}")
        return None


def get_uploads_dir(session_id: str, username: str, db=None) -> Path | None:
    ws = get_session_workspace_dir(session_id, username, db)
    return None if ws is None else ws / UPLOADS_DIR_NAME


def get_manifest_path(session_id: str, username: str, db=None) -> Path | None:
    ws = get_session_workspace_dir(session_id, username, db)
    return None if ws is None else ws / UPLOADS_DIR_NAME / MANIFEST_FILENAME


# ── 工具函数 ────────────────────────────────────────────────────────────


def _safe_stem(filename: str) -> str:
    """把文件名转成安全的落盘名（保留可读性，去掉路径与非法字符）。"""
    base = Path(filename or "unknown").name
    stem = Path(base).stem or "unknown"
    stem = re.sub(r"[^\w\u4e00-\u9fff.\-]+", "_", stem, flags=re.UNICODE)
    return stem[:80] or "unknown"


def format_size(size: int) -> str:
    try:
        size = int(size)
    except (TypeError, ValueError):
        return "未知"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _strip_stored_header(text: str) -> str:
    """去掉落盘文件头部的元信息注释，只留解析正文。"""
    if text.startswith("<!--"):
        end = text.find("-->")
        if end != -1:
            return text[end + 3 :].lstrip("\n")
    return text


# 解析器在"解析不了"时返回的是方括号占位文本（例如
# `[PDF文件: x.pdf，未安装PDF解析库…]`），不是真正的正文——必须识别出来标成
# "解析失败"，否则清单会误报"已解析"，模型也会以为读到了内容。
_PLACEHOLDER_MARKERS = (
    "未安装",
    "无法解析",
    "解析失败",
    "建议转换为",
    "未能提取",
)


def _is_parser_placeholder(text: str) -> bool:
    body = (text or "").strip()
    if not (body.startswith("[") and body.endswith("]")):
        return False
    return any(marker in body for marker in _PLACEHOLDER_MARKERS)


def _body_indicates_failure(text: str) -> bool:
    """判断落盘正文是否是"失败说明"而非真实内容（含解析器占位与本地失败占位）。

    用于按需重解析：读到这类正文时应到 ``uploadfiles/`` 重新解析源文件，
    而不是把失败说明当成文件内容回答用户。
    """
    body = (text or "").strip()
    if not body:
        return True
    if _is_parser_placeholder(body):
        return True
    return body.startswith("[文件《") and (
        "解析失败" in body or "未提取正文" in body or "超过可解析上限" in body
    )


# ── 落盘 ────────────────────────────────────────────────────────────────


class UploadTooLargeError(Exception):
    """上传文件超过配置的单文件上限。"""

    def __init__(self, size: int, limit: int):
        self.size = size
        self.limit = limit
        super().__init__(f"文件大小 {size} bytes 超过上限 {limit} bytes")


def get_max_file_size_bytes() -> int | None:
    """读取配置中的单文件上传大小上限（字节）；None = 不限制。"""
    try:
        from easy_agent.bootstrap.runtime_config import get_runtime_agent_config

        cfg = get_runtime_agent_config()
        if cfg and cfg.get("config"):
            limits = getattr(cfg["config"], "upload_limits", None)
            if limits is None:
                return None
            if not getattr(limits, "enabled", True):
                return None
            mb = int(limits.max_file_size_mb)
            return mb * 1024 * 1024 if mb > 0 else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取上传限额配置失败（本次不做大小限制）: %s", exc)
    return None


_UNSET = object()


async def save_upload_streaming(
    upload,
    dest: Path,
    *,
    chunk_size: int = 1024 * 1024,
    max_bytes=_UNSET,
) -> int:
    """分块读取 UploadFile 并落盘，边读边计大小；超限立即中止并删除半成品。

    避免 ``await file.read()`` 把整个请求体读进内存（大文件/并发上传打爆内存）。
    ``max_bytes`` 不传时读取全局配置；显式传 ``None`` 表示本次不限制。
    返回写入字节数。
    """
    dest = Path(dest)
    limit = get_max_file_size_bytes() if max_bytes is _UNSET else max_bytes
    total = 0
    fd = open(dest, "wb")
    try:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if limit is not None and total > limit:
                fd.close()
                try:
                    dest.unlink()
                except OSError:
                    pass
                raise UploadTooLargeError(total, limit)
            fd.write(chunk)
    finally:
        if not fd.closed:
            fd.close()
    return total


def store_parsed_content(
    session_id: str,
    username: str,
    file_path: str,
    filename: str,
    size: int,
    file_type: str = "",
    db=None,
    source_name: str = "",
) -> dict | None:
    """解析单个上传文件并落盘到会话工作区 ``uploads/``。

    Args:
        source_name: 源文件在 ``uploadfiles/`` 下的保存名（``{file_id}_{原名}``），
            记入清单供追溯与按需重解析定位（也传 None 时回退用 DB 记录查找）。

    Returns:
        清单条目信息 dict（含 ``line`` 供 ``append_manifest`` 使用）；
        ``file_context`` 关闭或工作区不可用时返回 None（调用方跳过）。
    """
    if not is_enabled():
        return None

    uploads_dir = get_uploads_dir(session_id, username, db)
    if uploads_dir is None:
        logger.warning(f"无法解析会话工作区，跳过上传文件落盘 | {filename}")
        return None

    try:
        uploads_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"创建 uploads 目录失败: {uploads_dir} | {e}")
        return None

    stored_name = f"{_safe_stem(filename)}.txt"
    target = uploads_dir / stored_name
    is_update = target.exists()

    size_label = format_size(size)
    max_parse_mb = _cfg_int("parse_max_file_size_mb", 20)
    max_stored_chars = _cfg_int("max_stored_chars", 200000)

    if max_parse_mb > 0 and size > max_parse_mb * 1024 * 1024:
        status = f"未解析(超过 {max_parse_mb}MB 上限)"
        body = (
            f"[文件《{filename}》大小 {size_label}，超过可解析上限 {max_parse_mb}MB，"
            f"未提取正文。需要内容时请提示用户改用更小的文件。]"
        )
        logger.warning(
            f"上传文件超过解析上限，未解析 | {filename} | {size_label} | "
            f"上限 {max_parse_mb}MB"
        )
    else:
        body = ""
        # 对象存储模式（EASY_UPLOAD_BACKEND=store）：原始文件不在本地盘上，
        # 先 materialize 到本地缓存再解析。Store 故障直接抛出（不静默降级成
        # "解析失败"），由调用方记录明确原因。
        source_path = str(file_path)
        if is_store_uri(source_path):
            source_path = str(materialize_upload(source_path, username))
        try:
            body = parse_file_content(source_path) or ""
        except Exception as e:
            logger.warning(f"解析上传文件失败: {filename} | {e}")
            body = ""

        if not body.strip():
            status = "解析失败(无可提取文本)"
            body = (
                f"[文件《{filename}》解析失败：未能提取到文本（可能是加密/扫描件、"
                f"纯图片或格式不支持）。仅文件名与大小可用。]"
            )
            logger.warning(f"上传文件解析为空 | {session_id[-5:]} | {filename}")
        elif _is_parser_placeholder(body):
            # 解析器返回的是"解析不了"的占位说明，不是正文：如实标记为失败，
            # 让模型知道这个文件的内容不可用（而不是误以为已读到内容）。
            status = "解析失败(无可提取正文)"
            logger.warning(
                f"上传文件解析失败（解析器占位返回）| {session_id[-5:]} | {filename}"
            )
            body = (
                f"[文件《{filename}》未能提取正文内容，解析器返回：{body.strip()}]\n"
            )
        else:
            truncated = len(body) > max_stored_chars
            if truncated:
                body = body[:max_stored_chars]
            status = f"已解析({len(body)}字)" + ("，超长已截断" if truncated else "")
            logger.info(
                f"[{session_id[-5:]}] 📄 上传文件已解析落盘 | {filename} | "
                f"{size_label} | {status} | {target.name}"
            )

    header = (
        f"<!-- 源文件: {filename} | 大小: {size_label} | "
        f"类型: {file_type or 'unknown'} | 上传时间: {_now_label()} | {status} -->\n\n"
    )
    try:
        target.write_text(header + body, encoding="utf-8")
    except Exception as e:
        logger.warning(f"写入解析正文失败: {target} | {e}")
        return None

    rel_path = f"{UPLOADS_DIR_NAME}/{stored_name}"
    source_rel = f"uploadfiles/{source_name}" if source_name else ""
    line = f"- {filename} | {size_label} | {_now_label()} | {status} | "
    if source_rel:
        line += f"源文件: {source_rel} | "
    line += f"正文: {rel_path}"
    if is_update:
        line += "（本次已更新）"
    return {
        "filename": filename,
        "stored_path": rel_path,
        "source_path": source_rel,
        "status": status,
        "size": size,
        "line": line,
        "updated": is_update,
    }


# ── 清单 ────────────────────────────────────────────────────────────────


def append_manifest(
    session_id: str, username: str, entries: list[dict], db=None
) -> int:
    """把本批上传文件的条目追加进 ``uploads/index.md``（清单即记忆）。

    清单在写入时整体重排：保留头部说明 + 最新条目，超出 ``manifest_max_chars``
    时只丢最旧的条目（宁少不超，避免清单本身把上下文撑大）。

    Returns:
        写入后保留的条目数（跳过写入时返回 0）。
    """
    if not entries or not is_enabled():
        return 0
    manifest = get_manifest_path(session_id, username, db)
    if manifest is None:
        return

    old_lines: list[str] = []
    if manifest.exists():
        try:
            for raw in manifest.read_text(encoding="utf-8").splitlines():
                if raw.startswith("- "):
                    old_lines.append(raw)
        except Exception as e:
            logger.warning(f"读取上传清单失败: {manifest} | {e}")

    new_lines = old_lines + [e["line"] for e in entries if e.get("line")]
    max_chars = _cfg_int("manifest_max_chars", 2000)
    budget = max(0, max_chars - len(MANIFEST_HEADER))

    kept: list[str] = []
    used = 0
    for line in reversed(new_lines):
        cost = len(line) + 1
        if kept and used + cost > budget:
            break
        kept.append(line)
        used += cost
    kept.reverse()
    dropped = len(new_lines) - len(kept)

    text = MANIFEST_HEADER + "\n".join(kept) + "\n"
    if dropped:
        text += f"\n（较早的 {dropped} 个文件条目已省略）\n"
    try:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(text, encoding="utf-8")
    except Exception as e:
        logger.warning(f"写入上传清单失败: {manifest} | {e}")
        return 0
    logger.info(
        f"[{session_id[-5:]}] 📋 上传清单已更新 | 条目: {len(kept)}"
        f"{f'（省略 {dropped} 条）' if dropped else ''} | {manifest}"
    )
    return len(kept)


def read_manifest(session_id: str, username: str, db=None) -> str:
    """读取上传清单文本（供测试与调试使用）。"""
    manifest = get_manifest_path(session_id, username, db)
    if manifest is None or not manifest.exists():
        return ""
    try:
        return manifest.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"读取上传清单失败: {manifest} | {e}")
        return ""


# ── 按需重解析 ──────────────────────────────────────────────────────────


def _find_source_path(
    session_id: str, username: str, filename: str, db=None
) -> Path | None:
    """定位上传源文件（``uploadfiles/{file_id}_{原名}``，工作区之外）。

    优先用 DB 的 session_files 记录（上传时已落库 file_path），找不到再按
    `{file_id}_{原名}` 后缀模式扫描 uploadfiles 目录兜底。

    对象存储模式（``EASY_UPLOAD_BACKEND=store``）下 DB 里存的是
    ``store://uploads/{user}/{name}`` URI：此时 materialize 到本地缓存后返回本地
    路径；materialize 失败会把 ``UploadStorageError`` 原样抛出（不误报成"找不到
    源文件"，也不静默吞掉存储故障）。
    """
    try:
        database = _session_store(db)
        for row in database.get_session_files(session_id) or []:
            if (row.get("filename") or "") == filename and row.get("file_path"):
                stored = str(row["file_path"])
                if is_store_uri(stored):
                    # URI 自带归属校验：跨用户/非法 URI 会在 materialize 内被拒绝。
                    return Path(materialize_upload(stored, username))
                p = Path(stored)
                if p.exists():
                    return p
    except UploadStorageError:
        raise
    except Exception as e:
        logger.warning(f"从 DB 查找上传源文件失败: {filename} | {e}")

    # 兜底：扫 uploadfiles 目录，匹配 `{xxx}_{原名}`
    try:
        upload_dir = Config.get_user_workspace_dir(username) / "uploadfiles"
        if upload_dir.exists():
            matches = [
                p
                for p in upload_dir.iterdir()
                if p.is_file() and p.name.endswith(f"_{filename}")
            ]
            if len(matches) == 1:
                return matches[0]
            if matches:
                matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return matches[0]
    except Exception as e:
        logger.warning(f"扫描 uploadfiles 目录失败: {e}")
    return None


def _update_manifest_line(session_id: str, username: str, filename: str, new_line: str, db=None) -> None:
    """把清单中指定文件的条目行替换为新行（用于重解析后回填状态）。

    清单可能因超上限丢过旧条目：文件不在清单里时不新增（保持"清单=最新记录"）。
    """
    manifest = get_manifest_path(session_id, username, db)
    if manifest is None or not manifest.exists():
        return
    try:
        raw_lines = manifest.read_text(encoding="utf-8").splitlines()
        replaced = False
        for i, raw in enumerate(raw_lines):
            if raw.startswith("- ") and raw.split(" | ", 1)[0] == f"- {filename}":
                raw_lines[i] = new_line
                replaced = True
                break
        if replaced:
            manifest.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
            logger.info(
                f"[{session_id[-5:]}] 📋 上传清单状态已回填 | {filename} | {new_line}"
            )
    except Exception as e:
        logger.warning(f"回填上传清单失败: {manifest} | {e}")


def ensure_parsed_text(
    session_id: str, username: str, filename: str, db=None
) -> dict:
    """取文件解析正文；正文缺失或是失败占位时，到 ``uploadfiles/`` 重新解析源文件。

    重解析成功会把正文回填到 ``uploads/{文件名}.txt`` 并更新清单状态，之后各轮
    直接读正文即可。这是修复"首次解析失败后一直拿失败说明当答案"的关键路径：
    首次失败往往只是当时解析库不可用（如未装 pymupdf），提问时重试通常能成。

    Returns:
        dict(text, status, source_rel, reparsed)；
        ``text`` 为空表示无法取得正文（调用方应如实告知用户）。
    """
    result = {"text": "", "status": "", "source_rel": "", "reparsed": False}
    uploads_dir = get_uploads_dir(session_id, username, db)
    if uploads_dir is None:
        return result

    stored_name = f"{_safe_stem(filename)}.txt"
    stored = uploads_dir / stored_name

    # 1) 已有可用正文：直接返回
    if stored.exists():
        try:
            body = _strip_stored_header(stored.read_text(encoding="utf-8")).strip()
        except Exception as e:
            logger.warning(f"读取解析正文失败（按需重解析）: {stored} | {e}")
            body = ""
        if body and not _body_indicates_failure(body):
            result["text"] = body
            result["status"] = "已解析"
            result["source_rel"] = f"{UPLOADS_DIR_NAME}/{stored_name}"
            return result

    # 2) 正文缺失 / 是失败占位：重新解析源文件
    source = _find_source_path(session_id, username, filename, db)
    if source is None:
        logger.warning(
            f"[{session_id[-5:]}] 重解析失败：找不到上传源文件 | {filename}"
        )
        result["status"] = "找不到源文件"
        return result

    try:
        body = parse_file_content(str(source)) or ""
    except Exception as e:
        logger.warning(f"重解析上传文件失败: {filename} | {e}")
        body = ""

    source_rel = f"uploadfiles/{source.name}"
    result["source_rel"] = source_rel
    if not body.strip() or _is_parser_placeholder(body):
        result["status"] = "解析失败(重试后仍无可提取正文)"
        logger.warning(
            f"[{session_id[-5:]}] 重解析仍失败 | {filename} | 源文件: {source}"
        )
        return result

    truncated = False
    max_stored_chars = _cfg_int("max_stored_chars", 200000)
    if len(body) > max_stored_chars:
        body = body[:max_stored_chars]
        truncated = True
    try:
        src_size_label = format_size(source.stat().st_size)
    except OSError:
        src_size_label = "未知"
    status = f"已解析({len(body)}字)" + ("，超长已截断" if truncated else "")
    header = (
        f"<!-- 源文件: {filename} | 大小: {src_size_label} | "
        f"类型: {source.suffix.lstrip('.') or 'unknown'} | 上传时间: {_now_label()} | "
        f"{status} | 重解析时间: {_now_label()} -->\n\n"
    )
    try:
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text(header + body, encoding="utf-8")
    except Exception as e:
        logger.warning(f"回填解析正文失败: {stored} | {e}")
        result["text"] = body
        result["status"] = status
        result["reparsed"] = True
        return result

    new_line = (
        f"- {filename} | {src_size_label} | {_now_label()} | "
        f"{status} | 源文件: {source_rel} | 正文: {UPLOADS_DIR_NAME}/{stored_name}（重解析回填）"
    )
    _update_manifest_line(session_id, username, filename, new_line, db)
    logger.info(
        f"[{session_id[-5:]}] 📄 按需重解析成功 | {filename} | {status} | "
        f"源文件: {source}"
    )
    result["text"] = body
    result["status"] = status
    result["reparsed"] = True
    return result


# ── 本轮预览 ────────────────────────────────────────────────────────────


def build_turn_preview(
    session_id: str, username: str, files: list | None, db=None
) -> str:
    """构造"本轮上传文件"的开头预览块（注入本轮模型输入，不进落库正文）。

    通过 ``ensure_parsed_text`` 取正文：首次解析失败的文件在此自动重试重解析，
    重试仍失败则跳过（不把失败说明当预览注入）。预览总长受 ``preview_max_chars``
    限制（0 = 返回空串，完全不注入）。
    """
    if not files or not is_enabled():
        return ""
    limit = _cfg_int("preview_max_chars", 2000)
    if limit <= 0:
        return ""
    uploads_dir = get_uploads_dir(session_id, username, db)
    if uploads_dir is None:
        return ""

    sections: list[str] = []
    used = 0
    for file_info in files:
        filename = (file_info or {}).get("filename") or ""
        if not filename:
            continue
        try:
            parsed = ensure_parsed_text(session_id, username, filename, db)
        except Exception as e:
            logger.warning(f"按需解析异常（预览跳过该文件）| {filename} | {e}")
            parsed = {}
        text = (parsed.get("text") or "").strip()
        if not text:
            if parsed.get("status"):
                logger.warning(
                    f"[{session_id[-5:]}] 本轮预览跳过（无正文）| {filename} | "
                    f"{parsed.get('status')}"
                )
            continue
        remain = limit - used
        if remain <= 0:
            break
        chunk = text[:remain]
        sections.append(f"### {filename}\n{chunk}")
        used += len(chunk)
        if used >= limit:
            break

    if not sections:
        return ""
    return (
        "## 用户本轮上传的文件（开头预览，非全文）\n"
        "正文已解析到工作区 `uploads/` 目录，完整清单见 `uploads/index.md`；"
        "需要更多内容时用 grep/read_file 检索对应文件，不要在信息不足时臆测。"
        "向用户说明信息来源时，请说来自用户上传的原始文件，不要提内部解析文件路径。\n\n"
        + "\n\n".join(sections)
    )
