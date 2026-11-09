"""记忆文件跨进程锁与原子写（从 ``memory_store`` 拆出，纯移动）。

本模块只包含与记忆条目模型无关的基础设施：

- ``store_path_for`` / ``memory_lock_path``：Markdown 与 JSONL sidecar 的路径推导；
- ``memory_lock``：进程内可重入 + POSIX ``fcntl.flock`` 跨进程互斥（Windows 降级）；
- ``atomic_write_text``：临时文件 + ``os.replace`` 原子写。

依赖方向：本模块不 import ``memory_store``；``memory_store`` 反过来 re-export 这里
的公开名字，保持既有导入路径与对象身份不变。
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path

try:  # POSIX 跨进程文件锁；Windows 等平台没有 fcntl，走进程内降级
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - 平台分支
    _fcntl = None

# 沿用原模块 logger 名：既有日志过滤与测试断言依赖该名字（纯移动，不改行为）。
logger = logging.getLogger("easy_agent.memory.store")

STORE_SUFFIX = ".jsonl"
LOCK_SUFFIX = ".lock"


def store_path_for(memory_file: str | Path) -> Path:
    """由 Markdown 记忆文件推导 sidecar 路径：``memory.md -> memory.jsonl``。"""
    path = Path(memory_file)
    if path.suffix == STORE_SUFFIX:
        return path
    return path.with_suffix(STORE_SUFFIX)


# ---------------------------------------------------------------------------
# 跨进程文件锁（fcntl.flock）
# ---------------------------------------------------------------------------
#
# 锁文件由 JSONL sidecar 路径推导：``memory.md`` / ``memory.jsonl`` 共用
# ``.memory.jsonl.lock``，因此 Markdown 与 sidecar 的读写可以落在同一把锁里。
# 设计取舍：
#
# - **进程内可重入**：同一线程嵌套 ``memory_lock`` 只递增深度，不重复 flock，
#   避免 ``write_structured_memory -> MemoryStore.save`` 这类嵌套自锁死；
# - **进程间互斥**：POSIX 用阻塞式 ``fcntl.flock``；锁随 fd 关闭 / 进程退出释放；
# - **Windows 降级**：没有 fcntl 时不阻塞写入，只保留进程内互斥并 warning 一次，
#   多进程并发写同一文件的覆盖风险由调用方承担；
# - **锁文件保留**：释放后不删除锁文件（删除会让第三个进程锁到新的 inode，
#   破坏互斥），空锁文件几乎不占空间。

_LOCK_WARNING_EMITTED = False
_FLOCK_BROKEN = False
# 弱引用注册表：只为「当前有人持有/等待」的路径保留 RLock，避免长跑服务
# 随会话数增长而无界累积锁对象。
_LOCK_REGISTRY: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)
_LOCK_REGISTRY_GUARD = threading.Lock()
_LOCK_STATE = threading.local()


def memory_lock_path(target: str | Path) -> Path:
    """返回目标记忆文件对应的锁文件路径（Markdown / JSONL 共用）。"""
    sidecar = store_path_for(target)
    return sidecar.parent / f".{sidecar.name}{LOCK_SUFFIX}"


def cross_process_lock_supported() -> bool:
    """当前平台是否支持跨进程文件锁（Windows 为 False）。"""
    return _fcntl is not None and not _FLOCK_BROKEN


def _warn_lock_degraded(reason: str) -> None:
    """跨进程锁不可用时只告警一次，避免刷日志 / 阻塞写入。"""
    global _LOCK_WARNING_EMITTED
    if _LOCK_WARNING_EMITTED:
        return
    _LOCK_WARNING_EMITTED = True
    logger.warning(
        "跨进程记忆文件锁不可用，降级为进程内串行 | 原因: %s | "
        "影响: 多进程/多实例并发写同一记忆文件时仍可能后写覆盖",
        reason,
    )


def _lock_for(key: str) -> threading.RLock:
    with _LOCK_REGISTRY_GUARD:
        lock = _LOCK_REGISTRY.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCK_REGISTRY[key] = lock
        return lock


def _lock_depths() -> dict[str, int]:
    depths = getattr(_LOCK_STATE, "depths", None)
    if depths is None:
        depths = {}
        _LOCK_STATE.depths = depths
    return depths


def _acquire_flock(handle, *, exclusive: bool) -> bool:
    """尝试取 flock；平台不支持或失败时降级并返回 False。"""
    global _FLOCK_BROKEN
    if _fcntl is None:
        _warn_lock_degraded("当前平台无 fcntl 模块")
        return False
    if _FLOCK_BROKEN:
        _warn_lock_degraded("fcntl.flock 不可用")
        return False
    operation = _fcntl.LOCK_EX if exclusive else _fcntl.LOCK_SH
    try:
        _fcntl.flock(handle.fileno(), operation)
    except (OSError, AttributeError) as exc:
        _FLOCK_BROKEN = True
        _warn_lock_degraded(f"flock 失败: {exc}")
        return False
    return True


def _release_flock(handle) -> None:
    if _fcntl is None:
        return
    try:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
    except (OSError, AttributeError):
        pass


@contextmanager
def memory_lock(target: str | Path, *, exclusive: bool = True):
    """对一份记忆文件（Markdown + JSONL）加跨进程锁。

    用法::

        with memory_lock(memory_file):
            store = MemoryStore(store_path_for(memory_file)).load()
            ...
            store.save()

    同一线程可重入；不同线程 / 进程串行。Windows 无 fcntl 时降级为进程内
    互斥并 warning，不抛异常，保证记忆写入在受限平台上仍可用。
    """
    lock_path = memory_lock_path(target)
    key = os.path.abspath(str(lock_path))
    depths = _lock_depths()

    # 同一线程已持锁：只增加深度，不重复 flock。
    if depths.get(key, 0) > 0:
        depths[key] += 1
        try:
            yield lock_path
        finally:
            depths[key] -= 1
            if depths[key] <= 0:
                depths.pop(key, None)
        return

    thread_lock = _lock_for(key)
    thread_lock.acquire()
    handle = None
    depths[key] = 1
    try:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(lock_path, "a+b")
        except OSError as exc:
            _warn_lock_degraded(f"无法创建锁文件 {lock_path.name}: {exc}")
        else:
            _acquire_flock(handle, exclusive=exclusive)
        yield lock_path
    finally:
        depths.pop(key, None)
        if handle is not None:
            _release_flock(handle)
            try:
                handle.close()
            except OSError:
                pass
        thread_lock.release()


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_text(path: Path, text: str) -> None:
    """临时文件 + ``os.replace`` 原子写；同目录保证 replace 不跨文件系统。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _fsync_directory(path.parent)


def atomic_write_text(path: str | Path, text: str) -> None:
    """原子写文本（临时文件 + ``os.replace``）。

    供 Markdown 权威文件复用：与 sidecar 使用同一实现，读方永远看不到半截文件。
    """
    _atomic_write_text(Path(path), text)


__all__ = [
    "LOCK_SUFFIX",
    "STORE_SUFFIX",
    "atomic_write_text",
    "cross_process_lock_supported",
    "memory_lock",
    "memory_lock_path",
    "store_path_for",
]
