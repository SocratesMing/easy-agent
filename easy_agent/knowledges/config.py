"""新版知识库模块配置。

上游为本地部署的 Ragflow v0.26.3 开源服务（标准官方 HTTP API），地址与
凭据由环境变量占位注入：``RAGFLOW_BASE_URL``、``RAGFLOW_API_KEY``。
二者均无默认回退：模块启用时若任一为空，按 fail-closed 路径报错并提示
填写。旧版配置中的 bridge/local compat、quality/reconciliation、
transport/observability 等段已裁剪。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, ValidationError, model_validator

from ..config import Config

logger = logging.getLogger(__name__)

# 认证方式写死为 Bearer（Ragflow 官方 API 标准）
AUTH_HEADER = "Authorization"
AUTH_SCHEME = "Bearer"

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


class DatasetDefaultsConfig(BaseModel):
    permission: str = "me"           # 上游知识库保持私有，授权以本地为准
    embedding_model: str = ""       # 形如 "bge-OA@Xinference"


class ParsingDefaultsConfig(BaseModel):
    chunk_method: str = "naive"
    parser_config: dict[str, Any] = Field(
        default_factory=lambda: {
            "chunk_token_num": 512,
            "layout_recognize": True,
            "html4excel": False,
            "delimiter": "\n!?;。；！？",
            "task_page_size": 12,
            "method": "minerullm",
            "parent_retrieval": False,
            "raptor": {"use_raptor": False},
        }
    )


class RetrievalDefaultsConfig(BaseModel):
    page_size: int = Field(default=10, ge=1)
    similarity_threshold: float = Field(default=0.2, ge=0, le=1)
    vector_similarity_weight: float = Field(default=0.3, ge=0, le=1)
    top_k: int = Field(default=1024, ge=1)
    rerank_id: str = ""


class DefaultsConfig(BaseModel):
    dataset: DatasetDefaultsConfig = Field(default_factory=DatasetDefaultsConfig)
    parsing: ParsingDefaultsConfig = Field(default_factory=ParsingDefaultsConfig)
    retrieval: RetrievalDefaultsConfig = Field(default_factory=RetrievalDefaultsConfig)


class LimitsConfig(BaseModel):
    max_file_size_mb: int = Field(default=100, gt=0)
    max_request_size_mb: int = Field(default=110, gt=0)
    max_files_per_request: int = Field(default=20, gt=0)
    max_dataset_name_utf8_bytes: int = Field(default=127, gt=0)
    max_filename_utf8_bytes: int = Field(default=127, gt=0)
    allowed_extensions: list[str] = Field(
        default_factory=lambda: ["pdf", "doc", "docx", "txt", "md", "xls", "xlsx", "csv", "ppt", "pptx"]
    )
    max_datasets_per_query: int = Field(default=20, gt=0)
    max_documents_per_query: int = Field(default=500, gt=0)


class AuditConfig(BaseModel):
    enabled: bool = True
    retention_days: int = Field(default=365, ge=30)
    max_details_bytes: int = Field(default=4096, ge=256, le=65536)


class VirusScanConfig(BaseModel):
    enabled: bool = False
    required: bool = False
    command: str = "clamdscan"
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)


class UploadSecurityConfig(BaseModel):
    reject_empty_files: bool = True
    verify_magic: bool = True
    reject_office_macros: bool = True
    virus_scan: VirusScanConfig = Field(default_factory=VirusScanConfig)


class KnowledgeConfig(BaseModel):
    """校验后的知识库配置（应用启动时加载一次）。

    端点与凭据优先取主配置 YAML ``knowledges``（兼容旧版 ``knowledge``）段，
    支持 ``${VAR:-默认}`` 占位；其次取环境变量。无默认地址回退：模块启用时
    base_url / api_key 任一为空即 fail-closed 报错，提示填写。
    """

    enabled: bool = False
    base_url: str = ""
    api_key: SecretStr = SecretStr("")  # Bearer 凭据（RAGFLOW_API_KEY）
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    upload_security: UploadSecurityConfig = Field(default_factory=UploadSecurityConfig)

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
        """从 EasyAgent 主配置 YAML 加载；文件缺失时退回纯环境变量构造。"""
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
        # 新版优先读 knowledges 段；兼容旧版 knowledge 段中的已知字段
        section = raw.get("knowledges") if isinstance(raw.get("knowledges"), dict) else raw.get("knowledge")
        return cls.from_mapping(_expand_env_recursive(section), _expand_env_recursive(raw.get("small_models")))

    @classmethod
    def from_mapping(
        cls, section: Mapping[str, Any] | None, small_models: Mapping[str, Any] | None = None
    ) -> "KnowledgeConfig":
        """从已展开占位符的 knowledge 配置段构造；未识别的旧版字段直接忽略。"""
        section = dict(section or {})
        endpoint = _as_mapping(section.get("endpoint"))
        auth = _as_mapping(section.get("auth"))
        defaults = dict(_as_mapping(section.get("defaults")))
        dataset = dict(_as_mapping(defaults.get("dataset")))
        retrieval = dict(_as_mapping(defaults.get("retrieval")))
        # 兼容旧版：embedding / rerank 未显式配置时回填 small_models 段
        small_models = _as_mapping(small_models)
        if not dataset.get("embedding_model") and _as_mapping(small_models.get("embedding")).get("model"):
            dataset["embedding_model"] = small_models["embedding"]["model"]
        if not retrieval.get("rerank_id") and _as_mapping(small_models.get("reranker")).get("model"):
            retrieval["rerank_id"] = small_models["reranker"]["model"]
        defaults["dataset"] = dataset
        defaults["retrieval"] = retrieval
        payload = {
            "enabled": section.get("enabled", _env_bool("KNOWLEDGE_ENABLED", False)),
            "base_url": (
                section.get("base_url")
                or endpoint.get("base_url")  # 兼容旧版 endpoint.base_url
                or os.environ.get("RAGFLOW_BASE_URL")
                or ""
            ),
            "api_key": (
                section.get("api_key")
                or auth.get("credential")  # 兼容旧版 auth.credential
                or os.environ.get("RAGFLOW_API_KEY")
                or ""
            ),
            "defaults": defaults,
            "limits": _as_mapping(section.get("limits")),
            "audit": _as_mapping(section.get("audit")),
            "upload_security": _as_mapping(section.get("upload_security")),
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
# ${VAR:-default} 占位符解析（与旧版语法一致）
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
    "AUTH_HEADER",
    "AUTH_SCHEME",
    "KnowledgeConfig",
    "KnowledgeConfigError",
    "UPSTREAM_DOCUMENT_STATUS_MAPPING",
]
