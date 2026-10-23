"""上传原始文件的对象存储适配层（对象存储 A：LangGraph StoreBackend）。

范围边界
--------
只覆盖 ``data/uploads`` 对应能力，即 ``POST /agent/files/upload`` 落库的**原始
上传文件**：

- ``local``（``EASY_UPLOAD_BACKEND=local``，默认）：行为与改造前完全一致，文件仍
  写 ``data/uploads/{safe_username}/{saved_name}``，DB/接口里记本地绝对路径。
- ``store``（``EASY_UPLOAD_BACKEND=store``）：文件字节写入 LangGraph Store
  （官方 ``deepagents.backends.StoreBackend`` + ``get_agent_store()``，即业务库
  ``SqlStore``），DB/接口里记
  ``store://uploads/{safe_username}/{saved_name}``；读取时按需 materialize 到
  本地缓存 ``data/uploads_cache/{safe_username}/{saved_name}``。

**未覆盖**（仍是本地文件系统，见 ``docs/archive/design/2026-10-18-object-store-uploads.md``）：
workspace / 生成文件 / 会话聊天上传 ``uploadfiles/``。

设计要点
--------
1. 不依赖 graph runtime：``StoreBackend`` 显式传 ``store=get_agent_store()``，
   命名空间工厂不读 ``Runtime``，因此 HTTP 请求线程、``asyncio.to_thread``
   后台线程、脚本里都能直接调用。
2. Fail-closed：Store 不可用/写入失败一律抛异常，由 API 映射成 HTTP 503；
   绝不"降级写本地"或静默丢文件（否则 DB 记录与真实副本会不一致）。
3. 跨用户隔离：URI 里的用户名必须与调用者一致，否则拒绝；
   materialize 的目标路径只由「URI 用户名 + 文件名」决定，不做任意路径拼接。
4. 长度上限：``langgraph_store.item_key`` 是 ``VARCHAR(191)``（MySQL 索引口径），
   超长保存名按「保留 ``{file_id}_`` 前缀 + 扩展名」截断。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path

from deepagents.backends import StoreBackend

from easy_agent.bootstrap.config import Config
from easy_agent.bootstrap.env import env_str
from easy_agent.persistence.stores import get_agent_store

logger = logging.getLogger("easy_agent.upload_storage")

# ── 开关 ────────────────────────────────────────────────────────────────

ENV_UPLOAD_BACKEND = "EASY_UPLOAD_BACKEND"
BACKEND_LOCAL = "local"
BACKEND_STORE = "store"
BACKEND_CHOICES = (BACKEND_LOCAL, BACKEND_STORE)
DEFAULT_BACKEND = BACKEND_LOCAL

# ── URI / Store 口径 ────────────────────────────────────────────────────

STORE_URI_SCHEME = "store"
STORE_URI_PREFIX = f"{STORE_URI_SCHEME}://"
UPLOADS_ROOT = "uploads"

# 本地 materialize 缓存根目录（可被测试 monkeypatch）。
BASE_CACHE_DIR = Path("./data/uploads_cache")

# langgraph_store.item_key 是 VARCHAR(191)；key 形如 "/{saved_name}"，故名字预算
# 为 190。截断保留扩展名，避免解析器/MIME 判断丢失类型信息。
MAX_STORE_KEY_LEN = 191
_STORE_NAME_BUDGET = MAX_STORE_KEY_LEN - 1

# StoreBackend 的命名空间分量只允许 ASCII 安全字符（防通配符注入）。
_NAMESPACE_SAFE_RE = re.compile(r"^[A-Za-z0-9\-_.@+:~]+$")

# 文件名里不允许出现会破坏 key / URI / 路径语义的字符。
_NAME_FORBIDDEN = ("/", "\\", "\x00", "\r", "\n", "\t")


# ── 异常 ────────────────────────────────────────────────────────────────


class UploadStorageError(RuntimeError):
    """上传对象存储的基类异常。"""


class UploadStoreUnavailable(UploadStorageError):
    """Store 后端不可用 / 读写失败：API 映射 HTTP 503，不静默丢文件。"""


class UploadStoreAccessDenied(UploadStorageError):
    """URI 非法或跨用户访问：API 映射 HTTP 403。"""


class UploadStoreNotFound(UploadStorageError):
    """Store 中不存在该对象：API 映射 HTTP 404。"""


# ── 开关解析 ────────────────────────────────────────────────────────────


def upload_backend_kind(env=None) -> str:
    """当前上传后端取值（``local`` / ``store``）。

    非法取值直接抛错（fail-closed）：静默按 local 处理会让"以为存了对象存储"的
    部署悄悄退回本地盘，比启动/上传报错更难发现。
    """
    if env is not None:
        raw = env.get(ENV_UPLOAD_BACKEND)
    else:
        raw = env_str(ENV_UPLOAD_BACKEND)
    value = (raw or "").strip().lower()
    if not value:
        return DEFAULT_BACKEND
    if value not in BACKEND_CHOICES:
        raise UploadStorageError(
            f"未知的 {ENV_UPLOAD_BACKEND}={value!r}"
            f"（可选 {'/'.join(BACKEND_CHOICES)}）"
        )
    return value


def store_mode_enabled(env=None) -> bool:
    """是否启用 Store 模式（默认 False）。"""
    return upload_backend_kind(env) == BACKEND_STORE


# ── 用户 / 名称 ─────────────────────────────────────────────────────────


def safe_username(username: str) -> str:
    """用户名 → 可做 Store 命名空间、也可做缓存目录名的安全名。

    与本地 uploads 目录同口径用 ``Config.sanitize_username``；但该函数保留
    Unicode 字母（例如中文用户名），而 ``StoreBackend`` 的命名空间只接受 ASCII
    安全字符，因此非 ASCII 名再映射成稳定哈希别名（确定性，跨进程一致）。
    """
    raw = str(username or "").strip()
    if not raw:
        raise UploadStoreAccessDenied("对象存储操作需要明确的用户名（不能为空）")
    safe = Config.sanitize_username(raw)
    if _NAMESPACE_SAFE_RE.match(safe):
        return safe
    alias = "u_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    logger.warning(
        f"用户名 {raw!r} 含非 ASCII 字符，对象存储命名空间/缓存目录使用别名 {alias}"
    )
    return alias


def _clean_stored_name(saved_name: str) -> str:
    """保存名清洗：去掉路径成分与不可见字符，并压到 Store 主键长度以内。"""
    raw = str(saved_name or "").replace("\\", "/")
    name = os.path.basename(raw)
    for ch in _NAME_FORBIDDEN:
        name = name.replace(ch, "_")
    name = name.strip().strip(".")
    if not name:
        raise UploadStorageError(f"非法的上传文件名: {saved_name!r}")

    if len(name) <= _STORE_NAME_BUDGET:
        return name
    stem, ext = os.path.splitext(name)
    if len(ext) >= _STORE_NAME_BUDGET:
        return name[:_STORE_NAME_BUDGET]
    keep = _STORE_NAME_BUDGET - len(ext)
    bounded = stem[:keep] + ext
    logger.warning(
        f"上传文件名过长，已截断以适配 Store 主键（<={_STORE_NAME_BUDGET}）: "
        f"{name} -> {bounded}"
    )
    return bounded


def store_key(saved_name: str) -> str:
    """Store 内的 item key（以 ``/`` 开头，兼容 StoreBackend 的 ls/delete 语义）。"""
    return "/" + _clean_stored_name(saved_name)


def build_store_uri(safe_user: str, saved_name: str) -> str:
    """构造 ``store://uploads/{safe_username}/{saved_name}``。"""
    return f"{STORE_URI_PREFIX}{UPLOADS_ROOT}/{safe_user}/{saved_name}"


# ── 后端构造 ────────────────────────────────────────────────────────────


def _namespace_factory(safe_user: str):
    """命名空间工厂：刻意不读 Runtime，脱离图执行也能用。"""

    def _namespace(_runtime) -> tuple[str, ...]:
        return (UPLOADS_ROOT, safe_user)

    return _namespace


def upload_backend(username: str = "") -> StoreBackend | None:
    """当前上传后端实例。

    - ``local`` 模式返回 ``None``（调用方继续走本地文件）；
    - ``store`` 模式返回绑定该用户命名空间的 ``StoreBackend``；用户名缺失、
      Store 不可用一律抛异常（不允许"拿不到 Store 就当本地写"）。
    """
    if upload_backend_kind() != BACKEND_STORE:
        return None
    safe = safe_username(username)
    try:
        store = get_agent_store()
    except Exception as exc:  # noqa: BLE001 - 统一转成明确的不可用异常
        raise UploadStoreUnavailable(
            f"无法获取 LangGraph Store（{type(exc).__name__}: {exc}）；"
            f"{ENV_UPLOAD_BACKEND}=store 时上传必须落库，已拒绝本次操作"
        ) from exc
    if store is None:
        raise UploadStoreUnavailable(
            f"LangGraph Store 未初始化；{ENV_UPLOAD_BACKEND}=store 时上传必须落库"
        )
    return StoreBackend(store=store, namespace=_namespace_factory(safe))


def preflight_upload_store(env=None) -> str:
    """启动期自检：local 模式为空操作；store 模式拿不到 Store 直接抛错（fail-closed）。"""
    kind = upload_backend_kind(env)
    if kind != BACKEND_STORE:
        logger.info(
            f"上传原始文件存储: local（{ENV_UPLOAD_BACKEND}=local，行为不变）"
        )
        return kind
    try:
        store = get_agent_store()
    except Exception as exc:  # noqa: BLE001
        raise UploadStoreUnavailable(
            f"{ENV_UPLOAD_BACKEND}=store 但无法获取 LangGraph Store"
            f"（{type(exc).__name__}: {exc}），拒绝启动"
        ) from exc
    if store is None:
        raise UploadStoreUnavailable(
            f"{ENV_UPLOAD_BACKEND}=store 但 LangGraph Store 未初始化，拒绝启动"
        )
    logger.info(
        f"上传原始文件存储: store（{ENV_UPLOAD_BACKEND}=store，LangGraph Store 可用）"
    )
    return kind


# ── URI 解析 ────────────────────────────────────────────────────────────


def is_store_uri(path) -> bool:
    """是否是对象存储 URI（``store://...``；对 ``store:/...`` 也容错）。"""
    return isinstance(path, str) and path.strip().lower().startswith(
        f"{STORE_URI_SCHEME}:"
    )


def _validate_uri_username(value: str, raw: str) -> str:
    if not value or not _NAMESPACE_SAFE_RE.match(value):
        raise UploadStoreAccessDenied(f"非法的对象存储 URI（用户名段）: {raw!r}")
    return value


def _validate_uri_name(value: str, raw: str) -> str:
    if not value or value in (".", ".."):
        raise UploadStoreAccessDenied(f"非法的对象存储 URI（文件名段）: {raw!r}")
    for ch in _NAME_FORBIDDEN:
        if ch in value:
            raise UploadStoreAccessDenied(f"非法的对象存储 URI（文件名字符）: {raw!r}")
    if len(value) > _STORE_NAME_BUDGET:
        raise UploadStoreAccessDenied(f"非法的对象存储 URI（文件名过长）: {raw!r}")
    return value


def parse_upload_uri(path: str) -> tuple[str, str]:
    """解析 ``store://uploads/{safe_username}/{saved_name}`` → (用户名, 文件名)。

    容忍 ``store:/uploads/...``（某些代理/网关会把 ``//`` 折叠成 ``/``），
    但只接受形如 ``uploads/{user}/{name}`` 的三段结构，其余一律拒绝。
    """
    raw = str(path or "").strip()
    if not is_store_uri(raw):
        raise UploadStoreAccessDenied(f"不是对象存储 URI: {raw!r}")
    rest = raw[len(STORE_URI_SCHEME) + 1 :].lstrip("/")
    parts = rest.split("/")
    if len(parts) != 3 or parts[0] != UPLOADS_ROOT:
        raise UploadStoreAccessDenied(f"非法的对象存储 URI: {raw!r}")
    user = _validate_uri_username(parts[1], raw)
    name = _validate_uri_name(parts[2], raw)
    return user, name


# ── 写入 / 读取 / 删除 ──────────────────────────────────────────────────


def save_upload_to_store(local_path, username: str, saved_name: str) -> str:
    """把本地临时文件写入 Store，返回 ``store://uploads/{user}/{name}``。

    Raises:
        UploadStoreUnavailable: Store 不可用或写入失败（调用方应回滚并返回 503）。
        UploadStorageError: 非 store 模式 / 本地文件读不了 / 文件名非法。
    """
    backend = upload_backend(username)
    if backend is None:
        raise UploadStorageError(
            f"save_upload_to_store 只能在 {ENV_UPLOAD_BACKEND}=store 时调用"
        )
    safe = safe_username(username)
    name = _clean_stored_name(saved_name)
    key = "/" + name

    try:
        data = Path(local_path).read_bytes()
    except OSError as exc:
        raise UploadStorageError(
            f"读取待写入对象存储的临时文件失败: {local_path} | {exc}"
        ) from exc

    try:
        responses = backend.upload_files([(key, data)])
    except Exception as exc:  # noqa: BLE001
        raise UploadStoreUnavailable(
            f"写入对象存储失败 | {key} | {type(exc).__name__}: {exc}"
        ) from exc

    errors = []
    for item in responses or []:
        error = getattr(item, "error", None)
        if error is None and isinstance(item, dict):
            error = item.get("error")
        if error:
            errors.append(str(error))
    if errors or not responses:
        detail = "; ".join(errors) or "Store 返回空结果"
        raise UploadStoreUnavailable(f"写入对象存储失败 | {key} | {detail}")

    uri = build_store_uri(safe, name)
    logger.info(f"上传文件已写入对象存储 | {uri} | {len(data)} bytes")
    return uri


def cache_path(safe_user: str, name: str) -> Path:
    """materialize 的本地缓存路径。"""
    return Path(BASE_CACHE_DIR) / safe_user / name


def materialize_upload(path: str, username: str) -> Path:
    """把 Store 对象下载到本地缓存并返回本地路径（跨用户直接拒绝）。

    写入是原子的（临时文件 + ``os.replace``），文件权限 0600、目录 0700。
    缓存命中直接返回：上传文件名带 ``{file_id}_`` 前缀，实际不可变。
    """
    safe_user, name = parse_upload_uri(path)
    caller = safe_username(username)
    if caller != safe_user:
        raise UploadStoreAccessDenied(
            f"拒绝跨用户访问对象存储文件: {path!r}（当前用户 {caller}）"
        )

    target = cache_path(safe_user, name)
    # 命中即返回：原子写保证 target 要么不存在，要么是完整副本（含 0 字节文件）。
    try:
        if target.is_file():
            return target
    except OSError:  # 缓存探测失败不影响重新下载
        pass

    backend = upload_backend(username)
    if backend is None:
        raise UploadStorageError(
            f"materialize_upload 只能在 {ENV_UPLOAD_BACKEND}=store 时调用"
        )

    try:
        responses = backend.download_files([store_key(name)])
    except Exception as exc:  # noqa: BLE001
        raise UploadStoreUnavailable(
            f"读取对象存储失败 | {path} | {type(exc).__name__}: {exc}"
        ) from exc

    content = None
    not_found = False
    for item in responses or []:
        error = getattr(item, "error", None)
        if error is None and isinstance(item, dict):
            error = item.get("error")
        if error:
            not_found = str(error) == "file_not_found"
            continue
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
    if content is None:
        if not_found:
            raise UploadStoreNotFound(f"对象存储中不存在该文件: {path}")
        raise UploadStoreUnavailable(f"读取对象存储失败（无内容）| {path}")

    _atomic_write(target, content)
    logger.info(f"对象存储文件已 materialize | {path} -> {target} | {len(content)} bytes")
    return target


def _atomic_write(target: Path, content: bytes) -> None:
    """原子写缓存文件：临时文件写满 + fsync + os.replace。"""
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        os.chmod(parent, 0o700)
    except OSError as exc:
        logger.warning(f"创建上传缓存目录失败（继续尝试写入）: {parent} | {exc}")

    tmp = parent / f".{target.name}.{uuid.uuid4().hex}.part"
    try:
        with open(tmp, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except OSError as exc:
        raise UploadStorageError(f"写入本地缓存失败: {target} | {exc}") from exc
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def list_stored_uploads(username: str) -> list[dict]:
    """列出某用户名下的上传对象（资产页 store 模式）。

    返回 ``[{saved_name, size, created_at}]``；size 按 StoreBackend value 的
    真实 content 计算（base64 长度不能直接当文件大小）。
    """
    safe = safe_username(username)
    try:
        store = get_agent_store()
    except Exception as exc:  # noqa: BLE001
        raise UploadStoreUnavailable(f"无法获取 LangGraph Store: {exc}") from exc
    if store is None:
        raise UploadStoreUnavailable("LangGraph Store 未初始化")
    try:
        items = store.search((UPLOADS_ROOT, safe))
    except Exception as exc:  # noqa: BLE001
        raise UploadStorageError(f"列举上传对象失败: {exc}") from exc
    result: list[dict] = []
    for item in items:
        try:
            value = item.value or {}
            name = str(item.key).lstrip("/")
            encoding = str(value.get("encoding") or "utf-8")
            content = value.get("content")
            if content is None:
                continue
            if encoding == "base64":
                import base64

                size = len(base64.b64decode(str(content)))
            else:
                size = len(str(content).encode("utf-8"))
            result.append(
                {
                    "saved_name": name,
                    "size": size,
                    "created_at": str(value.get("created_at") or item.created_at or ""),
                    "file_path": build_store_uri(safe, name),
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("跳过无法解析的上传对象 %s: %s", getattr(item, "key", "?"), exc)
    return result


def delete_stored_upload(path: str, username: str = "") -> bool:
    """删除 Store 对象与本地缓存（尽力而为）。

    Returns:
        True = Store 对象已删除；False = Store 中本来就没有该对象。

    Raises:
        UploadStoreAccessDenied / UploadStoreUnavailable: URI 非法、跨用户或 Store 故障。
    """
    safe_user, name = parse_upload_uri(path)
    if username:
        caller = safe_username(username)
        if caller != safe_user:
            raise UploadStoreAccessDenied(
                f"拒绝跨用户删除对象存储文件: {path!r}（当前用户 {caller}）"
            )

    backend = upload_backend(username or safe_user)
    if backend is None:
        raise UploadStorageError(
            f"delete_stored_upload 只能在 {ENV_UPLOAD_BACKEND}=store 时调用"
        )

    try:
        result = backend.delete(store_key(name))
    except Exception as exc:  # noqa: BLE001
        raise UploadStoreUnavailable(
            f"删除对象存储文件失败 | {path} | {type(exc).__name__}: {exc}"
        ) from exc

    error = getattr(result, "error", None)
    if error is None and isinstance(result, dict):
        error = result.get("error")
    removed = not error
    if error:
        logger.warning(f"对象存储文件删除未生效（可能已不存在）| {path} | {error}")

    try:
        cache_path(safe_user, name).unlink()
    except OSError:
        pass
    return removed
