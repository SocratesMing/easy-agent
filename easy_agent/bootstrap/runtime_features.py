"""多副本运行形态特性开关与严格门禁（Phase 0）。

默认全部保持现有单机/RWX 行为；`EASY_MULTI_REPLICA_STRICT=1` 时，
生产多副本若仍使用尚未外置的 stream/run/file/sandbox 能力，启动失败，
避免在 Phase 2/3 完成前误宣称多副本安全。

注意：本模块只做“能力声明 + 校验”，不实现外置；实际实现见 Phase 1/2。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Mapping

from easy_agent.bootstrap.env import env_bool, env_str
from easy_agent.utils.deployment import detect_worker_count

FEATURE_ENV = "EASY_MULTI_REPLICA_STRICT"

_DEFAULTS = {
    "stream_backend": "db",  # Phase 2A：DB stream_events 已实现
    "run_queue": "none",
    "file_backend": "local",
    "sandbox": "local",
}

# Phase 2/3 完成后在此登记真正支持多副本的取值；Phase 0/1 故意保持为空。
_IMPLEMENTED_MULTI_REPLICA = {
    "stream_backend": {"db"},
    "run_queue": set(),
    "file_backend": set(),
    "sandbox": set(),
}


@dataclass(frozen=True, slots=True)
class RuntimeFeatures:
    stream_backend: str
    run_queue: str
    file_backend: str
    sandbox: str
    strict: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _value(name: str, default: str, env: Mapping[str, str] | None = None) -> str:
    if env is not None:
        return str(env.get(name, default) or default).strip().lower()
    return env_str(name, default).strip().lower()


def load_runtime_features(env: Mapping[str, str] | None = None) -> RuntimeFeatures:
    return RuntimeFeatures(
        stream_backend=_value("EASY_STREAM_BACKEND", _DEFAULTS["stream_backend"], env),
        run_queue=_value("EASY_RUN_QUEUE", _DEFAULTS["run_queue"], env),
        file_backend=_value("EASY_FILE_BACKEND", _DEFAULTS["file_backend"], env),
        sandbox=_value("EASY_SANDBOX", _DEFAULTS["sandbox"], env),
        strict=(env_bool(FEATURE_ENV) if env is None else str(env.get(FEATURE_ENV, "0")).lower() in ("1", "true", "yes", "on")),
    )


def _checkpoint_shared_across_replicas() -> bool:
    """当前配置下 HITL checkpointer 是否跨副本共享（决定严格门禁要不要报错）。

    判断依据与 ``persistence/checkpoints.py::resolve_checkpoint_backend`` 的 ``auto``
    语义一致：只有 ``mysql_saver`` 是共享的；``sqlite`` 是每 Pod 一个文件、``legacy``
    是进程内内存，两者都不能跨副本。

    这里**在配置加载之前**就可能被调用（``check_multi_replica_startup`` 在 lifespan
    靠前位置），所以只能读环境变量；业务库类型拿不到时按"不共享"处理——严格模式本身
    是 opt-in 门禁，宁可多报一条也不要静默放行。生产按部署清单会显式写
    ``EASY_CHECKPOINT_BACKEND=mysql_saver``，那条路径不受影响。
    """
    backend = env_str("EASY_CHECKPOINT_BACKEND", "auto").strip().lower()
    if backend in {"mysql_saver", "mysql"}:
        return True
    if backend == "auto":
        # 只能用环境变量判断：本模块是 L0，不能 import ``easy_agent.persistence``（L1）；
        # 读的是 config.yaml ``database.type: ${DB_TYPE:-mysql}`` 同一个变量。
        # 变量没显式给值时按"不共享"处理（见上面 docstring）。
        return env_str("DB_TYPE", "").strip().lower() == "mysql"
    return False


def multi_replica_errors(
    *,
    agent_env: str,
    workers: int | None = None,
    features: RuntimeFeatures | None = None,
) -> list[str]:
    """返回严格模式下的阻塞原因；空列表表示可以启动。"""
    features = features or load_runtime_features()
    if not features.strict:
        return []
    if str(agent_env).strip().lower() not in {"prod", "production"}:
        return []
    count = workers if workers is not None else detect_worker_count()
    if count <= 1:
        return []

    # Store 在业务库里（SqlStore），副本之间本来就共享；HITL checkpointer 现在有
    # mysql_saver（4 张表共享）与 sqlite（每 Pod 一个文件）/legacy（进程内）两类，
    # 只有前者能跨副本，所以这条错误要按后端判断，不能写死。
    errors: list[str] = []
    if not _checkpoint_shared_across_replicas():
        errors.append(
            "HITL checkpointer 当前后端不能跨副本共享（sqlite 每 Pod 一个文件 / legacy 进程内），"
            "多副本请用 EASY_CHECKPOINT_BACKEND=mysql_saver"
        )

    for attr, label in (
        ("stream_backend", "EASY_STREAM_BACKEND"),
        ("run_queue", "EASY_RUN_QUEUE"),
        ("file_backend", "EASY_FILE_BACKEND"),
        ("sandbox", "EASY_SANDBOX"),
    ):
        value = getattr(features, attr)
        if value not in _IMPLEMENTED_MULTI_REPLICA[attr]:
            errors.append(f"{label}={value} 尚未实现多副本外置（Phase 2/3 未完成）")
    return errors


def check_multi_replica_startup(agent_env: str, workers: int | None = None) -> RuntimeFeatures:
    """严格模式下启动校验；失败抛 RuntimeError。"""
    features = load_runtime_features()
    errors = multi_replica_errors(agent_env=agent_env, workers=workers, features=features)
    if errors:
        raise RuntimeError("多副本严格模式校验失败：\n- " + "\n- ".join(errors))
    return features
