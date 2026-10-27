"""知识库模块配置（扁平结构）。

上游为本地部署的 Ragflow v0.26.3 开源服务（标准官方 HTTP API），地址与
凭据由环境变量占位注入：``RAGFLOW_BASE_URL``、``RAGFLOW_API_KEY``。
二者均无默认回退：模块启用时若任一为空或残留未解析占位符，按
fail-closed 路径报错并提示填写。

只认这些 YAML 键：``implementation``（透传不校验）/ ``enabled`` /
``base_url`` / ``api_key`` / ``shared_dataset_id``（指定既有共享数据集，
空则按名称查找、没有时自动创建）/ ``embedding.model``（未配置时回退
``RAGFLOW_EMBEDDING_MODEL``，留空由 Ragflow 用其默认模型）/ ``audit`` /
``limits`` / ``source_sync``（研报接入：文档 SQL 增量同步，含 enabled 总开关）。
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
from easy_agent.bootstrap.env import env_str

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


class EmbeddingConfig(BaseModel):
    # 可选；空字符串时 create_dataset 传 None，由 Ragflow 使用其默认 embedding
    model: str = ""


class AuditConfig(BaseModel):
    enabled: bool = True
    max_details_bytes: int = Field(default=4096, ge=256, le=65536)


class LimitsConfig(BaseModel):
    max_file_size_mb: int = Field(default=100, gt=0)
    max_request_size_mb: int = Field(default=110, gt=0)
    max_filename_utf8_bytes: int = Field(default=127, gt=0)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: ["pdf", "doc", "docx", "txt", "md", "xls", "xlsx", "csv", "ppt", "pptx"]
    )
    max_documents_per_query: int = Field(default=500, gt=0)
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
    查询语句与列映射固定在代码内，表名通过 ``mysql.table`` 覆盖。
    """

    enabled: bool = False
    owner_username: str = "admin"
    # 磁盘文件根目录（绝对路径，必填）：按文档 SQL 的 batchid 在该目录下匹配同名文件
    file_root: str = ""
    # 指定既有知识库 ID（可选）：同名库有多个时用来锁定具体目标；留空则按 base_name 查找
    base_id: str = ""
    base_name: str = "报告库"
    lookback_days: int = Field(default=7, ge=1, le=365)
    # 首次回填起始日期（YYYY-MM-DD）；为空时只处理 lookback 窗口
    start_date: str = ""
    # 启动后延迟多少分钟执行第一次同步（0 = 启动即执行）
    start_delay_minutes: int = Field(default=0, ge=0, le=1440)
    interval_minutes: int = Field(default=60, ge=0)
    max_inflight: int = Field(default=10, ge=1, le=100)
    poll_interval_seconds: float = Field(default=15.0, ge=1)
    max_attempts: int = Field(default=3, ge=1, le=10)
    mysql: SourceSyncMySQLConfig = Field(default_factory=SourceSyncMySQLConfig)

    @model_validator(mode="after")
    def _validate_sync(self) -> "SourceSyncConfig":
        if self.start_date:
            try:
                datetime.strptime(self.start_date, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("source_sync.start_date 必须为 YYYY-MM-DD") from exc
        return self


class KnowledgeConfig(BaseModel):
    """校验后的知识库配置（应用启动时加载一次）。"""

    enabled: bool = False
    base_url: str = ""
    api_key: SecretStr = SecretStr("")  # Bearer 凭据（RAGFLOW_API_KEY）
    # 指定既有共享数据集 ID（RAGFLOW_SHARED_DATASET_ID）：非空时共享库
    # 直接使用该数据集，不按名称查找、不自动创建；为空维持 find-or-create
    shared_dataset_id: str = ""
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
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
        """从 EasyAgent 主配置 YAML 的 knowledge 段加载；文件缺失时退回纯环境变量构造。"""
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
        """从已展开占位符的 knowledge 配置段构造；未识别的键直接忽略。"""
        section = dict(section or {})
        source_section = _as_mapping(section.get("source_sync"))
        if not source_section:
            source_section = {
                "enabled": _env_bool("KNOWLEDGE_SOURCE_SYNC_ENABLED", False),
            }
        else:
            source_section["mysql"] = _as_mapping(source_section.get("mysql"))
        limits_section = _as_mapping(section.get("limits"))
        timeout_env = env_str("KNOWLEDGE_PARSE_TIMEOUT_SECONDS")
        if "parse_timeout_seconds" not in limits_section and timeout_env:
            limits_section["parse_timeout_seconds"] = timeout_env
        payload = {
            "enabled": section.get("enabled", _env_bool("KNOWLEDGE_ENABLED", False)),
            "base_url": section.get("base_url") or os.environ.get("RAGFLOW_BASE_URL") or "",
            "api_key": section.get("api_key") or os.environ.get("RAGFLOW_API_KEY") or "",
            "shared_dataset_id": str(
                section.get("shared_dataset_id")
                or os.environ.get("RAGFLOW_SHARED_DATASET_ID")
                or ""
            ),
            "embedding": {
                "model": str(
                    _as_mapping(section.get("embedding")).get("model")
                    or os.environ.get("RAGFLOW_EMBEDDING_MODEL")
                    or ""
                )
            },
            "audit": _as_mapping(section.get("audit")),
            "limits": limits_section,
            "source_sync": source_section,
        }
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
        env_value = os.environ.get(match.group(1))
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
    "UPSTREAM_DOCUMENT_STATUS_MAPPING",
]
