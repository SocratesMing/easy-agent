"""知识库模块配置（扁平结构）。

上游为本地部署的 Ragflow v0.26.3 开源服务（标准官方 HTTP API），地址与
凭据由环境变量占位注入：``RAGFLOW_BASE_URL``、``RAGFLOW_API_KEY``。
二者均无默认回退：模块启用时若任一为空或残留未解析占位符，按
fail-closed 路径报错并提示填写。

只认这些 YAML 键：``implementation``（透传不校验）/ ``enabled`` /
``base_url`` / ``api_key`` / ``shared_dataset_id``（指定既有共享数据集，
空则按名称查找、没有时自动创建）/ ``audit`` / ``limits`` /
``source_sync``（研报接入：文档 SQL 增量同步，含 enabled 总开关）。

embedding 模型**不再**通过 YAML 或 ``RAGFLOW_EMBEDDING_MODEL`` 环境变量
显式配置——统一从上游 RAGFlow 实际读取：若指定了 ``shared_dataset_id``
就用该数据集绑定的模型；否则在自动创建/查找共享库时拿到其模型；
极端情况下 API Key 下一个数据集都没有则让 RAGFlow 使用其租户默认。

解析策略、检索参数与上传安全开关为代码内固定值。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator

from easy_agent.bootstrap.config import Config
from easy_agent.bootstrap.env import is_unset_placeholder

logger = logging.getLogger(__name__)

# 上游文档 run 状态（官方 list documents 接口）映射为本地 DocumentStatus 值
UPSTREAM_DOCUMENT_STATUS_MAPPING: dict[str, str] = {
    "0": "pending",
    "UNSTART": "pending",
    "1": "processing",
    "RUNNING": "processing",
    "2": "cancelled",
    "CANCEL": "cancelled",
    "3": "ready",
    "DONE": "ready",
    "4": "failed",
    "FAIL": "failed",
}


class KnowledgeConfigError(ValueError):
    """知识库配置不可用时抛出。"""


class AuditConfig(BaseModel):
    enabled: bool = True
    max_details_bytes: int = Field(default=4096, ge=256, le=65536)


class RetrievalConfig(BaseModel):
    """检索参数（全局生效）。

    ``rerank_model`` 为空时行为与旧版一致（不传 rerank_id）；配置后先宽召回
    ``page_size`` 条候选，再由 RAGFlow rerank 精排，最终仍只取 ``top_n`` 条证据。
    """

    # 主阈值：低于该分不进入证据（无关问题返回空证据）
    similarity_threshold: float = Field(default=0.5, ge=0, le=1)
    # 主阈值 0 命中时的降级阈值，与 minimum_evidence_score 保持一致
    fallback_similarity_threshold: float = Field(default=0.4, ge=0, le=1)
    # 计数/汇总类问题的召回阈值（名单容易被切碎，阈值需更低）
    aggregation_similarity_threshold: float = Field(default=0.4, ge=0, le=1)
    # 本地兜底过滤：低于该分的 chunk 直接丢弃
    minimum_evidence_score: float = Field(default=0.4, ge=0, le=1)
    # 向量/关键词混合权重（0=纯关键词，1=纯向量）
    vector_similarity_weight: float = Field(default=0.5, ge=0, le=1)
    # rerank 之前参与打分的候选 chunk 数
    top_k: int = Field(default=1024, ge=1)
    # 检索接口返回的候选条数（宽召回）；rerank 精排后仍只取 top_n 条证据
    page_size: int = Field(default=30, ge=1, le=100)
    # rerank 模型 ID，格式 "model_name"@"model_factory"；空 = 不启用 rerank
    rerank_model: str = ""
    # 每个知识库至少保留的证据条数（0 = 不做配额，纯按分数截断）
    per_base_quota: int = Field(default=2, ge=0)
    # 单轮最终进入提示词的证据条数
    top_n: int = Field(default=8, ge=1, le=30)


class LimitsConfig(BaseModel):
    max_file_size_mb: int = Field(default=100, gt=0)
    max_request_size_mb: int = Field(default=110, gt=0)
    max_filename_utf8_bytes: int = Field(default=127, gt=0)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: ["pdf", "doc", "docx", "txt", "md", "xls", "xlsx", "csv"]
    )
    max_documents_per_query: int = Field(default=500, gt=0)
    # 单个知识库可存储的文档总数上限（上传前拦截）。0 = 不限制。
    max_documents_per_base: int = Field(default=0, ge=0)
    # 解析绝对超时秒数；0 = 不限制（批量同步场景推荐）。
    # 计时起点为 parse_started_at（首次观察到上游 RUNNING），不含排队时间；
    # 超时即失败终态，暂停/恢复/重试会清空旧计时重新开始。
    parse_timeout_seconds: float = Field(default=1800.0, ge=0)


class VirusScanConfig(BaseModel):
    enabled: bool = False
    required: bool = False
    command: str = "clamdscan"
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)


class UploadSecurityConfig(BaseModel):
    """上传安全校验参数（file_validation 使用；YAML 不再配置，值为代码内固定）。"""

    reject_empty_files: bool = True
    verify_magic: bool = False
    reject_office_macros: bool = True
    virus_scan: VirusScanConfig = Field(default_factory=VirusScanConfig)


class SourceSyncMySQLConfig(BaseModel):
    """研报 SQL 所在 MySQL 连接（host / database / table 必填，不做主库兜底）。

    ``table`` 必须是源库中已存在的表。
    """

    host: str = ""
    port: int = Field(default=3306, gt=0)
    user: str = ""
    password: SecretStr = SecretStr("")
    database: str = ""
    charset: str = "utf8mb4"
    table: str = ""


class SourceSyncConfig(BaseModel):
    """文档 SQL → 知识库批量同步配置。

    每次按 ``publishDate`` 取 [当前时刻-lookback_days, 当前时刻] 的记录，
    与本地已入库的 source_record_id 做差集；上传按滑动窗口限流。
    配置 ``staging_dir`` 时先把本批文件拷贝到暂存目录，解析 READY 后删除副本；
    查询语句与列映射固定在代码内，表名通过 ``mysql.table`` 覆盖。
    """

    enabled: bool = False
    owner_username: str = "admin"
    # 磁盘文件根目录（绝对路径，必填）：按文档 SQL 的 batchid 在该目录下匹配同名文件
    file_root: str = ""
    # 暂存目录（绝对路径，可选）：设置后先把本批要入库的文件拷贝到这里，
    # 解析 READY 后删除暂存副本；留空则维持直接读取 file_root 的旧行为。
    staging_dir: str = ""
    # 暂存残留清理阈值（小时）：每轮同步开始时删除超过该时长未清理的暂存文件
    staging_retention_hours: int = Field(default=24, ge=1, le=24 * 30)
    # 指定既有知识库 ID（可选）：同名库有多个时用来锁定具体目标；留空则按 base_name 查找
    base_id: str = ""
    base_name: str = "报告库"
    lookback_days: int = Field(default=7, ge=1, le=365)
    # 首次触发时间（HH:MM:SS，24 小时制，服务器本地时间）；仅首次生效，
    # 之后按 interval_minutes 触发。留空 = 启动后立即执行第一次。
    start_time: str = ""
    interval_minutes: int = Field(default=60, ge=0)
    max_inflight: int = Field(default=10, ge=1, le=100)
    mysql: SourceSyncMySQLConfig = Field(default_factory=SourceSyncMySQLConfig)

    @model_validator(mode="after")
    def _validate_sync(self) -> "SourceSyncConfig":
        if self.start_time:
            try:
                datetime.strptime(self.start_time, "%H:%M:%S")
            except ValueError as exc:
                raise ValueError(
                    "source_sync.start_time 必须为 HH:MM:SS（24 小时制）"
                ) from exc
        staging = self.staging_dir.strip()
        if staging:
            if not Path(staging).is_absolute():
                raise ValueError("source_sync.staging_dir 必须为绝对路径")
            # 暂存目录与源目录相同会导致 READY 后误删业务原件，直接拒绝。
            try:
                if self.file_root.strip() and Path(staging).resolve() == Path(
                    self.file_root.strip()
                ).resolve():
                    raise ValueError(
                        "source_sync.staging_dir 不能与 file_root 相同"
                    )
            except OSError as exc:
                raise ValueError(
                    "source_sync.staging_dir / file_root 路径无法解析"
                ) from exc
        return self


class KnowledgeConfig(BaseModel):
    """校验后的知识库配置（应用启动时加载一次）。

    注意：embedding 模型不再从本配置读取，而是在首次需要共享数据集时
    从上游 RAGFlow 的 ``shared_dataset_id``（或自动创建/查找的
    ``EA_SHARED``）数据集上解析得到。
    """

    enabled: bool = False
    base_url: str = ""
    api_key: SecretStr = SecretStr("")  # Bearer 凭据（RAGFLOW_API_KEY）
    # 指定既有共享数据集 ID（RAGFLOW_SHARED_DATASET_ID）：非空时共享库
    # 直接使用该数据集，不按名称查找、不自动创建；为空维持 find-or-create
    shared_dataset_id: str = ""
    # 保留字段说明：embedding 模型不在配置里，运行时从上游共享数据集解析。
    audit: AuditConfig = Field(default_factory=AuditConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    source_sync: SourceSyncConfig = Field(default_factory=SourceSyncConfig)

    @model_validator(mode="after")
    def _endpoint_required_when_enabled(self) -> "KnowledgeConfig":
        if self.enabled:
            if not self.base_url.strip():
                raise ValueError("启用知识库模块必须配置 RAGFLOW_BASE_URL（Ragflow 服务地址）")
            if not self.api_key.get_secret_value():
                raise ValueError("启用知识库模块必须配置 RAGFLOW_API_KEY")
        return self

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "KnowledgeConfig":
        """从 EasyAgent 主配置 YAML 的 knowledge 段加载。

        所有字段均可被环境变量覆盖（优先级：环境变量 > YAML > 代码默认值）；
        配置文件缺失时退回纯环境变量构造。
        """
        if config_path is not None:
            path = Path(config_path)
            if not path.is_file():
                raise KnowledgeConfigError("知识库配置文件不存在")
        else:
            path = Config.resolve_config_path()
            if not path.is_file():
                path = None
        if path is None:
            return cls.from_mapping({})
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise KnowledgeConfigError(f"配置文件包含无效 YAML: {exc}") from None
        if not isinstance(raw, dict):
            raise KnowledgeConfigError("应用配置必须是 YAML mapping")
        return cls.from_mapping(_expand_env_recursive(raw.get("knowledge")))

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any] | None) -> "KnowledgeConfig":
        """从已展开占位符的 knowledge 配置段构造；未识别的键直接忽略。

        所有知识库参数都支持环境变量覆盖：环境变量显式设置且不是模板哨兵值时，
        优先于 YAML 中的字面值（YAML 里继续保留 ``${VAR:-默认值}`` 写法兼容）。
        """
        section = dict(section or {})
        source_section = _as_mapping(section.get("source_sync"))
        mysql_section = _as_mapping(source_section.get("mysql"))
        limits_section = _as_mapping(section.get("limits"))
        audit_section = _as_mapping(section.get("audit"))
        retrieval_section = _as_mapping(section.get("retrieval"))

        # ---- 知识库总开关与 RAGFlow 接入 ----
        payload: dict[str, Any] = {
            "enabled": section.get("enabled", False),
            "base_url": str(section.get("base_url") or ""),
            "api_key": str(section.get("api_key") or ""),
            "shared_dataset_id": str(section.get("shared_dataset_id") or ""),
            "audit": audit_section,
            "limits": limits_section,
            "retrieval": retrieval_section,
            "source_sync": source_section,
        }
        _env_scalar_overrides(payload, {"enabled": "KNOWLEDGE_ENABLED"})
        _env_string_overrides(
            payload,
            {
                "base_url": "RAGFLOW_BASE_URL",
                "api_key": "RAGFLOW_API_KEY",
                "shared_dataset_id": "RAGFLOW_SHARED_DATASET_ID",
            },
        )

        # ---- 入库参数 ----
        # allowed_extensions 允许 YAML 写成逗号分隔字符串，归一为列表
        if isinstance(limits_section.get("allowed_extensions"), str):
            limits_section["allowed_extensions"] = [
                part.strip().lower()
                for part in str(limits_section["allowed_extensions"]).split(",")
                if part.strip()
            ]

        # ---- 检索参数 ----
        _env_string_overrides(
            retrieval_section, {"rerank_model": "RAGFLOW_RERANK_MODEL"}
        )

        # ---- 研报同步（文档 SQL → 知识库） ----
        _env_scalar_overrides(
            source_section,
            {
                "enabled": "KNOWLEDGE_SOURCE_SYNC_ENABLED",
                "staging_retention_hours": (
                    "KNOWLEDGE_SOURCE_SYNC_STAGING_RETENTION_HOURS"
                ),
                "lookback_days": "KNOWLEDGE_SOURCE_SYNC_LOOKBACK_DAYS",
                "interval_minutes": "KNOWLEDGE_SOURCE_SYNC_INTERVAL_MINUTES",
                "max_inflight": "KNOWLEDGE_SOURCE_SYNC_MAX_INFLIGHT",
            },
        )
        _env_string_overrides(
            source_section,
            {
                "owner_username": "KNOWLEDGE_SOURCE_SYNC_OWNER_USERNAME",
                "file_root": "SOURCE_SYNC_FILE_ROOT",
                "staging_dir": "SOURCE_SYNC_STAGING_DIR",
                "base_id": "KNOWLEDGE_SOURCE_SYNC_BASE_ID",
                "base_name": "KNOWLEDGE_SOURCE_SYNC_BASE_NAME",
                "start_time": "KNOWLEDGE_SOURCE_SYNC_START_TIME",
            },
        )

        # ---- 研报 SQL 源库连接 ----
        _env_string_overrides(
            mysql_section,
            {
                "host": "COL_SQL_HOST",
                "user": "COL_SQL_USER",
                "password": "COL_SQL_PASSWORD",
                "database": "COL_SQL_DB",
                "charset": "COL_SQL_CHARSET",
                "table": "COL_SQL_TABLE",
            },
        )
        _env_scalar_overrides(mysql_section, {"port": "COL_SQL_PORT"})
        source_section["mysql"] = mysql_section

        # fail-closed：模块启用时禁止残留未解析的 ${VAR} 占位符
        # （防止 "${RAGFLOW_API_KEY}" 字面量被当作真实密钥静默放行）
        if payload.get("enabled"):
            unresolved = _find_unresolved(payload)
            if unresolved:
                raise KnowledgeConfigError(
                    "知识库已启用但配置存在未解析的环境变量占位符: " + ", ".join(sorted(unresolved))
                )
        try:
            return cls.model_validate(payload)
        except ValidationError as exc:
            issues = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
                for error in exc.errors(include_url=False, include_context=False, include_input=False)
            )
            raise KnowledgeConfigError(f"知识库配置无效: {issues}") from None


# ---------------------------------------------------------------------------
# ${VAR:-default} 占位符解析
# ---------------------------------------------------------------------------
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_INT_RE = re.compile(r"^[+-]?(?:0|[1-9][0-9]*)$")
_FLOAT_RE = re.compile(r"^[+-]?(?:(?:[0-9]+\.[0-9]*)|(?:[0-9]*\.[0-9]+)|[0-9]+)$")


def _as_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _env_value(name: str) -> str | None:
    """读取环境变量：未设置、空值或模板哨兵值均视为未配置。"""

    raw = os.environ.get(name)
    if raw is None or is_unset_placeholder(raw):
        return None
    return raw


def _env_scalar_overrides(
    section: dict[str, Any], mapping: Mapping[str, str]
) -> None:
    """环境变量覆盖数值/布尔字段（显式设置优先于 YAML）。"""

    for key, env_name in mapping.items():
        raw = _env_value(env_name)
        if raw is None:
            continue
        section[key] = _parse_env_scalar(raw.strip())


def _env_string_overrides(
    section: dict[str, Any], mapping: Mapping[str, str]
) -> None:
    """环境变量覆盖字符串字段（密钥/路径保留原样）。"""

    for key, env_name in mapping.items():
        raw = _env_value(env_name)
        if raw is None:
            continue
        section[key] = raw


def _parse_env_scalar(value: str) -> object:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none", "~"}:
        return None
    if _INT_RE.fullmatch(value):
        return int(value)
    if _FLOAT_RE.fullmatch(value):
        return float(value)
    return value


def _expand_env_string(value: str) -> object:
    full_match = _ENV_VAR_RE.fullmatch(value)

    def replace(match: re.Match[str]) -> str:
        # 模板哨兵值（EASY_AGENT_SET_ME__*）视作未配置，避免占位值成为真实凭据
        env_value = _env_value(match.group(1))
        if env_value is not None:
            return env_value
        default = match.group(2)
        return default if default is not None else match.group(0)

    expanded = _ENV_VAR_RE.sub(replace, value)
    if full_match and not _ENV_VAR_RE.search(expanded):
        return _parse_env_scalar(expanded)
    return expanded


def _expand_env_recursive(value: object) -> object:
    if isinstance(value, str):
        return _expand_env_string(value)
    if isinstance(value, dict):
        return {key: _expand_env_recursive(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env_recursive(item) for item in value]
    return value


def _find_unresolved(value: object, path: str = "") -> set[str]:
    """递归收集仍残留的 ``${VAR}`` 占位符，返回 ``路径(${VAR})`` 描述集合。"""
    found: set[str] = set()
    if isinstance(value, str):
        for match in _ENV_VAR_RE.finditer(value):
            found.add(f"{path or '<root>'}(${{{match.group(1)}}})")
    elif isinstance(value, dict):
        for key, item in value.items():
            found.update(_find_unresolved(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(_find_unresolved(item, f"{path}[{index}]"))
    return found


__all__ = [
    "KnowledgeConfig",
    "KnowledgeConfigError",
    "RetrievalConfig",
    "UPSTREAM_DOCUMENT_STATUS_MAPPING",
]
