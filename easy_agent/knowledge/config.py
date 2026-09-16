"""Strict configuration contract for the independent knowledge module.

The schema and validation remain module-owned, while values are read from the
``knowledge`` section of EasyAgent's active YAML configuration. This keeps one
deployment configuration file without coupling the knowledge implementation to
EasyAgent's general ``Config`` model.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from ..config import Config
from ..small_model import SmallModelsConfig
from .domain import DocumentStatus


_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_INT_RE = re.compile(r"^[+-]?(?:0|[1-9][0-9]*)$")
_FLOAT_RE = re.compile(
    r"^[+-]?(?:(?:[0-9]+\.[0-9]*)|(?:[0-9]*\.[0-9]+)|(?:[0-9]+[eE][+-]?[0-9]+))$"
)
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")

REQUIRED_UPSTREAM_OPERATIONS = frozenset(
    {
        "create_dataset",
        "list_datasets",
        "update_dataset",
        "delete_datasets",
        "upload_documents",
        "list_documents",
        "update_document",
        "download_document",
        "delete_documents",
        "start_parsing",
        "stop_parsing",
        "retrieve",
    }
)


class KnowledgeConfigError(ValueError):
    """Raised when the independent knowledge configuration is unusable."""


class StrictConfigModel(BaseModel):
    """Base class that rejects silently ignored deployment keys."""

    model_config = ConfigDict(extra="forbid")


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class RequestEncoding(str, Enum):
    QUERY = "query"
    JSON = "json"
    MULTIPART = "multipart"
    NONE = "none"


class ResponseContract(str, Enum):
    DATASET = "dataset"
    DATASET_LIST = "dataset_list"
    DOCUMENT_LIST = "document_list"
    EMPTY_OR_BOOLEAN = "empty_or_boolean"
    RETRIEVAL_RESULT = "retrieval_result"
    BINARY = "binary"


class VersionProbeMode(str, Enum):
    NONE = "none"
    RESPONSE_HEADER = "response_header"
    ENDPOINT = "endpoint"


class AuthMode(str, Enum):
    BEARER = "bearer"
    API_KEY_HEADER = "api_key_header"
    NONE = "none"


class DatasetPermission(str, Enum):
    ME = "me"
    TEAM = "team"


class VersionProbeConfig(StrictConfigModel):
    mode: VersionProbeMode = VersionProbeMode.NONE
    require_match: StrictBool = False
    header_name: str = ""
    operation: str = ""


class CapabilitiesConfig(StrictConfigModel):
    declared: list[str] = Field(default_factory=list)
    required: list[str] = Field(default_factory=list)
    probe_on_startup: StrictBool = False

    @model_validator(mode="after")
    def required_are_declared(self) -> "CapabilitiesConfig":
        missing = set(self.required) - set(self.declared)
        if missing:
            raise ValueError(
                "required capabilities are not declared: " + ", ".join(sorted(missing))
            )
        return self


class AdapterConfig(StrictConfigModel):
    profile: str = "bank_custom_v1"
    expected_version: str = "bank_custom_v1"
    contract_fixture_set: str = "bank_custom_v1"
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig)
    version_probe: VersionProbeConfig = Field(default_factory=VersionProbeConfig)
    local_v017_bridge: "LocalV017BridgeConfig" = Field(
        default_factory=lambda: LocalV017BridgeConfig()
    )


class LocalV017BridgeConfig(StrictConfigModel):
    """Development-only bridge from the bank contract to local RAGFlow v0.17."""

    enabled: StrictBool = False
    base_url: str = "http://127.0.0.1:9380"
    api_prefix: str = "/api/v1"
    operation_paths: dict[str, str] = Field(
        default_factory=lambda: {"retrieve": "/retrieval"}
    )
    strip_headers: list[str] = Field(
        default_factory=lambda: [
            "stdAuthToken",
            "stdRqtSyt",
            "stdRspSys",
            "stdTransId",
            "stdApplySystTmtp",
            "stdBankNum",
        ]
    )
    omit_embedding_model_on_create: StrictBool = True
    layout_recognize_value: str = "Plain Text"
    strip_parser_config_fields: list[str] = Field(
        default_factory=lambda: ["method", "paternity", "parent_retrieval", "stuctured"]
    )
    # Official/local RAGFlow v0.17 returns application code 102 ("Not
    # supported yet!") when a presentation document is updated before parsing.
    # Keep this compatibility quirk in the detachable local bridge so the bank
    # contract continues to configure every document normally.
    skip_document_configuration_extensions: list[str] = Field(default_factory=list)

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        return _validate_relative_path(value, label="local bridge api_prefix").rstrip("/")

    @field_validator("operation_paths")
    @classmethod
    def validate_operation_paths(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            name: _validate_relative_path(path, label=f"local bridge path {name}")
            for name, path in value.items()
        }

    @field_validator("strip_headers")
    @classmethod
    def validate_strip_headers(cls, value: list[str]) -> list[str]:
        if any(not _HEADER_NAME_RE.fullmatch(name) for name in value):
            raise ValueError("local bridge strip_headers contains an invalid header")
        return value

    @field_validator("skip_document_configuration_extensions")
    @classmethod
    def validate_skip_document_configuration_extensions(
        cls, value: list[str]
    ) -> list[str]:
        normalized = [item.strip().lower().lstrip(".") for item in value]
        if any(not item or not re.fullmatch(r"[a-z0-9]+", item) for item in normalized):
            raise ValueError(
                "local bridge skip_document_configuration_extensions contains "
                "an invalid extension"
            )
        return list(dict.fromkeys(normalized))


def _validate_relative_path(value: str, *, label: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError(f"{label} must not contain newlines")
    if not value.startswith("/") or value.startswith("//"):
        raise ValueError(f"{label} must start with exactly one slash")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError(f"{label} must be a relative path without query or fragment")
    if any(part == ".." for part in parsed.path.split("/")):
        raise ValueError(f"{label} must not contain '..' segments")
    return value


class UpstreamOperationConfig(StrictConfigModel):
    method: HttpMethod
    path: str
    request_encoding: RequestEncoding
    response_contract: ResponseContract
    base_url: str = ""

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_relative_path(value, label="operation path")


class EndpointConfig(StrictConfigModel):
    base_url: str
    api_prefix: str = "/api/v1"
    operations: dict[str, UpstreamOperationConfig]

    @field_validator("api_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        return _validate_relative_path(value, label="api_prefix").rstrip("/")

    @field_validator("operations")
    @classmethod
    def reject_unknown_operations(
        cls, value: dict[str, UpstreamOperationConfig]
    ) -> dict[str, UpstreamOperationConfig]:
        unknown = set(value) - REQUIRED_UPSTREAM_OPERATIONS
        if unknown:
            raise ValueError("unknown operations: " + ", ".join(sorted(unknown)))
        return value


class PrincipalHeadersConfig(StrictConfigModel):
    tenant_id: str = ""
    organization_id: str = ""
    request_id: str = "X-Request-Id"


class AuthConfig(StrictConfigModel):
    mode: AuthMode = AuthMode.BEARER
    header_name: str = "Authorization"
    scheme: str = "Bearer"
    credential: SecretStr = Field(default_factory=lambda: SecretStr(""))
    fixed_headers: dict[str, str] = Field(default_factory=dict)
    principal_headers: PrincipalHeadersConfig = Field(
        default_factory=PrincipalHeadersConfig
    )


class AaasRetrievalConfig(StrictConfigModel):
    enabled: StrictBool = True
    auth_token: SecretStr = Field(default_factory=lambda: SecretStr(""))
    request_system: str = "EASYAGENT"
    response_system: str = "AIGCQA"
    bank_number: str = "001"
    transaction_id_prefix: str = "EASYAGENT"


class BankApiConfig(StrictConfigModel):
    system_code: str = "EASYAGENT"
    aaas_retrieval: AaasRetrievalConfig = Field(default_factory=AaasRetrievalConfig)


class TLSConfig(StrictConfigModel):
    verify: StrictBool = True
    ca_bundle: str = ""
    client_cert: str = ""
    client_key: str = ""


class TimeoutConfig(StrictConfigModel):
    connect: StrictFloat = Field(default=5.0, gt=0)
    read: StrictFloat = Field(default=60.0, gt=0)
    write: StrictFloat = Field(default=60.0, gt=0)
    pool: StrictFloat = Field(default=5.0, gt=0)
    upload_wall_clock: StrictFloat = Field(default=300.0, gt=0)


class PoolConfig(StrictConfigModel):
    max_connections: StrictInt = Field(default=50, gt=0)
    max_keepalive_connections: StrictInt = Field(default=20, ge=0)
    keepalive_expiry_seconds: StrictFloat = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def keepalive_does_not_exceed_total(self) -> "PoolConfig":
        if self.max_keepalive_connections > self.max_connections:
            raise ValueError("max_keepalive_connections exceeds max_connections")
        return self


class RetryPolicyConfig(StrictConfigModel):
    attempts: StrictInt = Field(default=3, ge=1, le=10)
    base_backoff_seconds: StrictFloat = Field(default=0.5, gt=0)
    max_backoff_seconds: StrictFloat = Field(default=8.0, gt=0)
    jitter: StrictBool = True
    respect_retry_after: StrictBool = True
    retry_statuses: list[StrictInt] = Field(
        default_factory=lambda: [429, 502, 503, 504]
    )
    operations: list[str] = Field(default_factory=list)

    @field_validator("operations")
    @classmethod
    def retry_only_safe_operations(cls, value: list[str]) -> list[str]:
        allowed = {"list_datasets", "list_documents", "retrieve"}
        unsafe = set(value) - allowed
        if unsafe:
            raise ValueError(
                "retry policy contains non-idempotent operations: "
                + ", ".join(sorted(unsafe))
            )
        return value


class TransportConfig(StrictConfigModel):
    trust_env: StrictBool = False
    follow_redirects: StrictBool = False
    proxy: str = ""
    timeout_seconds: TimeoutConfig = Field(default_factory=TimeoutConfig)
    pool: PoolConfig = Field(default_factory=PoolConfig)
    retry: RetryPolicyConfig = Field(default_factory=RetryPolicyConfig)


class EnvelopeConfig(StrictConfigModel):
    code_field: str = "code"
    data_field: str = "data"
    message_field: str = "message"
    success_codes: list[StrictInt] = Field(default_factory=lambda: [0])
    authentication_codes: list[StrictInt] = Field(default_factory=lambda: [109])
    data_false_with_message_is_error: StrictBool = True


class ResponseConfig(StrictConfigModel):
    envelope: EnvelopeConfig = Field(default_factory=EnvelopeConfig)
    accepted_json_content_types: list[str] = Field(
        default_factory=lambda: ["application/json", "application/*+json"]
    )
    require_operation_contract_match: StrictBool = True
    field_aliases: dict[str, str] = Field(default_factory=dict)
    error_code_map: dict[str, str] = Field(default_factory=dict)


class RequestFieldAliasesConfig(StrictConfigModel):
    parse_document_ids: str = "document_ids"
    retrieval_document_ids: str = "document_ids"
    retrieval_dataset_ids: str = "dataset_ids"


class CompletedDocumentProbeConfig(StrictConfigModel):
    """Fail-closed compatibility check for customized RAGFlow deployments.

    Some v0.17.2 deployments finish indexing tasks but leave the document row
    in RUNNING.  A document is only accepted as complete when both upstream
    counters are non-zero and an exact-document retrieval returns real text.
    """

    enabled: StrictBool = False
    grace_seconds: StrictFloat = Field(default=10.0, ge=0)
    min_chunk_count: StrictInt = Field(default=1, ge=1)
    min_token_count: StrictInt = Field(default=1, ge=1)
    probe_question: str = "文档主要内容"


def _default_document_status_mapping() -> dict[str, DocumentStatus]:
    return {
        "0": DocumentStatus.PENDING,
        "UNSTART": DocumentStatus.PENDING,
        "1": DocumentStatus.PROCESSING,
        "RUNNING": DocumentStatus.PROCESSING,
        "2": DocumentStatus.CANCELLED,
        "CANCEL": DocumentStatus.CANCELLED,
        "3": DocumentStatus.READY,
        "DONE": DocumentStatus.READY,
        "4": DocumentStatus.FAILED,
        "FAIL": DocumentStatus.FAILED,
    }


class CompatibilityConfig(StrictConfigModel):
    fixed_query_by_operation: dict[str, dict[str, Any]] = Field(default_factory=dict)
    fixed_body_by_operation: dict[str, dict[str, Any]] = Field(default_factory=dict)
    request_field_aliases: RequestFieldAliasesConfig = Field(
        default_factory=RequestFieldAliasesConfig
    )
    document_status_mapping: dict[str, DocumentStatus] = Field(
        default_factory=_default_document_status_mapping
    )
    completed_document_probe: CompletedDocumentProbeConfig = Field(
        default_factory=CompletedDocumentProbeConfig
    )

    def map_document_status(self, upstream_status: object) -> DocumentStatus:
        """Map unknown upstream values to a fail-closed non-ready state."""
        return self.document_status_mapping.get(
            str(upstream_status), DocumentStatus.UNKNOWN
        )


class DatasetDefaultsConfig(StrictConfigModel):
    permission: DatasetPermission = DatasetPermission.ME
    embedding_model: str = ""


class ParsingProfileConfig(StrictConfigModel):
    chunk_method: str
    parser_config: dict[str, Any] = Field(default_factory=dict)


class ParsingRoutingRuleConfig(StrictConfigModel):
    extensions: list[str]
    profile: str


class ParsingDefaultsConfig(StrictConfigModel):
    default_profile: str = "standard"
    profiles: dict[str, ParsingProfileConfig] = Field(default_factory=dict)
    routing_rules: list[ParsingRoutingRuleConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def referenced_profiles_exist(self) -> "ParsingDefaultsConfig":
        if self.profiles and self.default_profile not in self.profiles:
            raise ValueError("default parsing profile is not defined")
        missing = {r.profile for r in self.routing_rules} - set(self.profiles)
        if missing:
            raise ValueError(
                "routing rules reference unknown profiles: "
                + ", ".join(sorted(missing))
            )
        return self


class RetrievalDefaultsConfig(StrictConfigModel):
    page_size: StrictInt = Field(default=10, gt=0)
    similarity_threshold: StrictFloat = Field(default=0.2, ge=0, le=1)
    vector_similarity_weight: StrictFloat = Field(default=0.3, ge=0, le=1)
    top_k: StrictInt = Field(default=1024, gt=0)
    rerank_id: str = ""
    require_non_empty_document_filter: StrictBool = True
    enforce_single_embedding_model: StrictBool = True
    document_filter_overflow_strategy: str = "reject"

    @field_validator("document_filter_overflow_strategy")
    @classmethod
    def supported_overflow_strategy(cls, value: str) -> str:
        if value not in {"reject", "partition_and_merge"}:
            raise ValueError("unsupported document filter overflow strategy")
        return value


class DefaultsConfig(StrictConfigModel):
    dataset: DatasetDefaultsConfig = Field(default_factory=DatasetDefaultsConfig)
    parsing: ParsingDefaultsConfig = Field(default_factory=ParsingDefaultsConfig)
    retrieval: RetrievalDefaultsConfig = Field(default_factory=RetrievalDefaultsConfig)


class UploadOperationConfig(StrictConfigModel):
    per_file: StrictBool = True
    concurrency: StrictInt = Field(default=3, gt=0, le=20)


class ParsingPollConfig(StrictConfigModel):
    interval_seconds: StrictFloat = Field(default=2.0, gt=0)
    max_duration_seconds: StrictFloat = Field(default=1800.0, gt=0)


class DeletionOperationConfig(StrictConfigModel):
    remote_strategy: str = "delete"
    durable_outbox: StrictBool = False
    forbid_empty_ids: StrictBool = True


class TaskWorkerConfig(StrictConfigModel):
    mode: Literal["queue", "inline"] = "queue"
    poll_interval_seconds: StrictFloat = Field(default=1.0, gt=0, le=60)
    heartbeat_interval_seconds: StrictFloat = Field(default=10.0, gt=0, le=300)
    stale_after_seconds: StrictFloat = Field(default=90.0, gt=0)
    task_timeout_seconds: StrictFloat = Field(default=300.0, gt=0)
    max_attempts: StrictInt = Field(default=3, ge=1, le=20)
    retry_backoff_seconds: StrictFloat = Field(default=5.0, gt=0)


class OperationsConfig(StrictConfigModel):
    upload: UploadOperationConfig = Field(default_factory=UploadOperationConfig)
    parsing_poll: ParsingPollConfig = Field(default_factory=ParsingPollConfig)
    deletion: DeletionOperationConfig = Field(
        default_factory=DeletionOperationConfig
    )
    worker: TaskWorkerConfig = Field(default_factory=TaskWorkerConfig)


class LimitsConfig(StrictConfigModel):
    max_file_size_mb: StrictInt = Field(default=100, gt=0)
    max_request_size_mb: StrictInt = Field(default=110, gt=0)
    max_files_per_request: StrictInt = Field(default=20, gt=0)
    max_dataset_name_utf8_bytes: StrictInt = Field(default=127, gt=0)
    max_filename_utf8_bytes: StrictInt = Field(default=127, gt=0)
    allowed_extensions: list[str] = Field(default_factory=list)
    max_datasets_per_query: StrictInt = Field(default=20, gt=0)
    max_documents_per_query: StrictInt = Field(default=500, gt=0)

    @model_validator(mode="after")
    def request_can_contain_one_max_file(self) -> "LimitsConfig":
        if self.max_request_size_mb < self.max_file_size_mb:
            raise ValueError("max_request_size_mb is smaller than max_file_size_mb")
        return self


class FeaturesConfig(StrictConfigModel):
    reparse_completed_document: StrictBool = False
    retry_failed_document: StrictBool = False
    disable_document: StrictBool = False
    ragflow_chat: StrictBool = False
    ragflow_agent: StrictBool = False
    expose_chunk_management_to_business_users: StrictBool = False


class HealthConfig(StrictConfigModel):
    upstream_required_for_easyagent_startup: StrictBool = False
    upstream_required_for_readiness: StrictBool = False
    upstream_probe_operation: str = "list_datasets"
    upstream_probe_query: dict[str, Any] = Field(
        default_factory=lambda: {"page": 1, "page_size": 1}
    )
    mask_endpoint: StrictBool = True


class ReconciliationConfig(StrictConfigModel):
    enabled: StrictBool = True
    interval_seconds: StrictFloat = Field(default=300.0, gt=0)
    max_documents_per_run: StrictInt = Field(default=2000, ge=1, le=100000)
    verify_sha256: StrictBool = True
    auto_repair_status: StrictBool = True


class AuditConfig(StrictConfigModel):
    enabled: StrictBool = True
    retention_days: StrictInt = Field(default=365, ge=30)
    max_details_bytes: StrictInt = Field(default=4096, ge=256, le=65536)


class VirusScanConfig(StrictConfigModel):
    enabled: StrictBool = False
    required: StrictBool = False
    command: str = "clamdscan"
    timeout_seconds: StrictFloat = Field(default=60.0, gt=0, le=600)

    @model_validator(mode="after")
    def required_scanner_must_be_enabled(self) -> "VirusScanConfig":
        if self.required and not self.enabled:
            raise ValueError("required virus scanner must be enabled")
        if not self.command.strip():
            raise ValueError("virus scanner command must not be blank")
        return self


class UploadSecurityConfig(StrictConfigModel):
    reject_empty_files: StrictBool = True
    verify_magic: StrictBool = True
    reject_office_macros: StrictBool = True
    virus_scan: VirusScanConfig = Field(default_factory=VirusScanConfig)


class ObservabilityConfig(StrictConfigModel):
    task_backlog_warning: StrictInt = Field(default=50, ge=1)
    failed_task_warning: StrictInt = Field(default=1, ge=1)
    worker_heartbeat_grace_seconds: StrictFloat = Field(default=45.0, gt=0)
    metrics_token: SecretStr = SecretStr("")


class QualityBaselineConfig(StrictConfigModel):
    parser_version: str = "bank-configured-v1"
    embedding_version: str = "config-small-model"
    prompt_version: str = "knowledge-answer-v1"
    no_answer_text: str = "当前授权知识范围内没有足够证据回答该问题。"
    minimum_evidence_score: StrictFloat = Field(default=0.0, ge=0, le=1)


class OriginalStorageDriver(str, Enum):
    LOCAL_FS = "local_fs"
    MOUNTED_FS = "mounted_fs"


class OriginalStorageFilesystemConfig(StrictConfigModel):
    root_path: str = "./data/nas-sim"
    path_prefix: str = "easyagent-knowledge"
    chunk_size_mb: StrictInt = Field(default=8, gt=0, le=64)
    fsync: StrictBool = True
    directory_mode: str = "0750"
    file_mode: str = "0640"

    @field_validator("path_prefix")
    @classmethod
    def safe_prefix(cls, value: str) -> str:
        value = value.strip().strip("/")
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("path_prefix must be one safe path segment")
        return value

    @field_validator("directory_mode", "file_mode")
    @classmethod
    def valid_octal_mode(cls, value: str) -> str:
        if not re.fullmatch(r"0[0-7]{3}", value):
            raise ValueError("filesystem mode must be a four-digit octal string")
        return value


class OriginalStorageReadConfig(StrictConfigModel):
    legacy_fallback_to_ragflow: StrictBool = True
    max_agent_file_size_mb: StrictInt = Field(default=100, gt=0)
    max_agent_total_size_mb: StrictInt = Field(default=200, gt=0)
    materialized_file_ttl_seconds: StrictInt = Field(default=3600, gt=0)


class OriginalStorageDeletionConfig(StrictConfigModel):
    policy: str = "quarantine"
    retention_days: StrictInt = Field(default=30, ge=0)

    @field_validator("policy")
    @classmethod
    def supported_policy(cls, value: str) -> str:
        if value not in {"quarantine", "retain", "purge"}:
            raise ValueError("unsupported original-storage deletion policy")
        return value


class OriginalStorageHealthConfig(StrictConfigModel):
    required_for_readiness: StrictBool = True
    probe_on_startup: StrictBool = True


class OriginalStorageConfig(StrictConfigModel):
    enabled: StrictBool = False
    required_for_upload: StrictBool = True
    driver: OriginalStorageDriver = OriginalStorageDriver.LOCAL_FS
    filesystem: OriginalStorageFilesystemConfig = Field(
        default_factory=OriginalStorageFilesystemConfig
    )
    reads: OriginalStorageReadConfig = Field(default_factory=OriginalStorageReadConfig)
    deletion: OriginalStorageDeletionConfig = Field(
        default_factory=OriginalStorageDeletionConfig
    )
    health: OriginalStorageHealthConfig = Field(
        default_factory=OriginalStorageHealthConfig
    )

    @model_validator(mode="after")
    def validate_storage_contract(self) -> "OriginalStorageConfig":
        if self.enabled and not self.required_for_upload:
            raise ValueError(
                "enabled original storage must be required for every upload"
            )
        if (
            self.enabled
            and self.driver == OriginalStorageDriver.MOUNTED_FS
            and not Path(self.filesystem.root_path).is_absolute()
        ):
            raise ValueError("mounted_fs root_path must be absolute")
        return self


class SecurityConfig(StrictConfigModel):
    forbid_frontend_passthrough: StrictBool = True
    fail_when_enabled_and_secret_missing: StrictBool = True
    allowed_base_url_schemes: list[str] = Field(default_factory=lambda: ["https"])
    insecure_http_hosts: list[str] = Field(default_factory=list)
    forbid_base_url_userinfo_query_fragment: StrictBool = True
    operation_paths_must_be_relative: StrictBool = True
    reject_network_path_and_dot_segments: StrictBool = True
    reject_header_newlines: StrictBool = True
    reserved_headers: list[str] = Field(
        default_factory=lambda: [
            "Authorization",
            "Host",
            "Content-Length",
            "Transfer-Encoding",
        ]
    )
    redact_keys: list[str] = Field(default_factory=list)

    @field_validator("allowed_base_url_schemes")
    @classmethod
    def only_http_schemes(cls, value: list[str]) -> list[str]:
        normalized = [scheme.lower() for scheme in value]
        if not normalized or set(normalized) - {"http", "https"}:
            raise ValueError("allowed_base_url_schemes supports only http/https")
        return normalized


class KnowledgeConfig(StrictConfigModel):
    """Validated knowledge configuration loaded once during app lifespan."""

    schema_version: StrictInt = 1
    enabled: StrictBool = False
    adapter: AdapterConfig = Field(default_factory=AdapterConfig)
    endpoint: EndpointConfig | None = None
    auth: AuthConfig = Field(default_factory=AuthConfig)
    bank_api: BankApiConfig = Field(default_factory=BankApiConfig)
    tls: TLSConfig = Field(default_factory=TLSConfig)
    transport: TransportConfig = Field(default_factory=TransportConfig)
    response: ResponseConfig = Field(default_factory=ResponseConfig)
    compatibility: CompatibilityConfig = Field(default_factory=CompatibilityConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    operations: OperationsConfig = Field(default_factory=OperationsConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    reconciliation: ReconciliationConfig = Field(default_factory=ReconciliationConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    upload_security: UploadSecurityConfig = Field(default_factory=UploadSecurityConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    quality_baseline: QualityBaselineConfig = Field(default_factory=QualityBaselineConfig)
    original_storage: OriginalStorageConfig = Field(default_factory=OriginalStorageConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @model_validator(mode="after")
    def validate_enabled_configuration(self) -> "KnowledgeConfig":
        if self.schema_version != 1:
            raise ValueError("unsupported knowledge configuration schema_version")
        if not self.enabled:
            return self

        if (
            self.operations.worker.mode == "queue"
            and not self.original_storage.enabled
        ):
            raise ValueError(
                "queued knowledge tasks require authoritative original storage"
            )

        if self.endpoint is None:
            raise ValueError("endpoint is required when knowledge engineering is enabled")

        missing_operations = REQUIRED_UPSTREAM_OPERATIONS - set(
            self.endpoint.operations
        )
        if missing_operations:
            raise ValueError(
                "missing required operations: "
                + ", ".join(sorted(missing_operations))
            )

        self._validate_endpoint()
        self._validate_headers()

        if self.auth.mode != AuthMode.NONE and not self.auth.credential.get_secret_value():
            raise ValueError("auth credential is required when knowledge engineering is enabled")
        if not re.fullmatch(r"[A-Z][A-Z0-9_-]*", self.bank_api.system_code):
            raise ValueError("bank_api.system_code must be an uppercase system identifier")
        aaas = self.bank_api.aaas_retrieval
        if aaas.enabled:
            for label, value in (
                ("request_system", aaas.request_system),
                ("response_system", aaas.response_system),
                ("transaction_id_prefix", aaas.transaction_id_prefix),
            ):
                if not re.fullmatch(r"[A-Z][A-Z0-9_-]*", value):
                    raise ValueError(f"bank AaaS {label} is invalid")
            if not re.fullmatch(r"[0-9]+", aaas.bank_number):
                raise ValueError("bank AaaS bank_number is invalid")
            if (
                not self.adapter.local_v017_bridge.enabled
                and not aaas.auth_token.get_secret_value()
            ):
                raise ValueError("bank AaaS retrieval auth token is required")
            if any(
                marker in aaas.auth_token.get_secret_value()
                for marker in ("\r", "\n")
            ):
                raise ValueError("bank AaaS retrieval auth token is invalid")
        if not self.defaults.dataset.embedding_model:
            raise ValueError("defaults.dataset.embedding_model is required when enabled")
        if bool(self.tls.client_cert) != bool(self.tls.client_key):
            raise ValueError("tls.client_cert and tls.client_key must be configured together")
        if self.health.upstream_probe_operation not in self.endpoint.operations:
            raise ValueError("health probe operation is not configured")
        version_probe = self.adapter.version_probe
        if version_probe.mode == VersionProbeMode.NONE and version_probe.require_match:
            raise ValueError("version match cannot be required when version probe is disabled")
        if (
            version_probe.mode == VersionProbeMode.RESPONSE_HEADER
            and not version_probe.header_name
        ):
            raise ValueError("version response-header probe requires header_name")
        if version_probe.mode == VersionProbeMode.ENDPOINT:
            if not version_probe.operation:
                raise ValueError("version endpoint probe requires operation")
            if version_probe.operation not in self.endpoint.operations:
                raise ValueError("version probe operation is not configured")

        configured_compatibility_operations = set(
            self.compatibility.fixed_query_by_operation
        ) | set(self.compatibility.fixed_body_by_operation)
        unknown_compatibility_operations = configured_compatibility_operations - set(
            self.endpoint.operations
        )
        if unknown_compatibility_operations:
            raise ValueError(
                "compatibility parameters reference unknown operations: "
                + ", ".join(sorted(unknown_compatibility_operations))
            )

        if self.transport.trust_env or self.transport.follow_redirects:
            raise ValueError("trust_env and follow_redirects must remain false")
        if not self.security.forbid_frontend_passthrough:
            raise ValueError("frontend passthrough cannot be enabled")
        if not self.operations.deletion.forbid_empty_ids:
            raise ValueError("empty-ID deletion protection cannot be disabled")
        if not self.defaults.retrieval.require_non_empty_document_filter:
            raise ValueError("non-empty document filtering cannot be disabled")
        if not self.defaults.retrieval.enforce_single_embedding_model:
            raise ValueError("single embedding-model enforcement cannot be disabled")
        if self.original_storage.enabled:
            root = self.original_storage.filesystem.root_path.strip()
            if not root or "\x00" in root:
                raise ValueError("original_storage.filesystem.root_path is invalid")
        return self

    def _validate_endpoint(self) -> None:
        assert self.endpoint is not None
        self._validate_base_url(self.endpoint.base_url, "endpoint.base_url")
        for name, operation in self.endpoint.operations.items():
            if operation.base_url:
                self._validate_base_url(
                    operation.base_url, f"endpoint.operations.{name}.base_url"
                )
        bridge = self.adapter.local_v017_bridge
        if bridge.enabled:
            self._validate_base_url(
                bridge.base_url, "adapter.local_v017_bridge.base_url"
            )
            unknown = set(bridge.operation_paths) - set(self.endpoint.operations)
            if unknown:
                raise ValueError(
                    "local bridge paths reference unknown operations: "
                    + ", ".join(sorted(unknown))
                )

    def _validate_base_url(self, value: str, label: str) -> None:
        if "\r" in value or "\n" in value:
            raise ValueError(f"{label} must not contain newlines")
        parsed = urlsplit(value)
        allowed_schemes = {s.lower() for s in self.security.allowed_base_url_schemes}
        if parsed.scheme.lower() not in allowed_schemes or not parsed.hostname:
            raise ValueError(f"{label} has a disallowed scheme or no host")
        if (
            parsed.scheme.lower() == "http"
            and parsed.hostname not in set(self.security.insecure_http_hosts)
        ):
            raise ValueError(
                f"HTTP host for {label} must be explicitly listed in insecure_http_hosts"
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"{label} must not contain credentials/query/fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError(f"{label} path must be empty; use an operation path")

    def _validate_headers(self) -> None:
        reserved = {header.lower() for header in self.security.reserved_headers}
        auth_header = self.auth.header_name.lower()
        if not _HEADER_NAME_RE.fullmatch(self.auth.header_name):
            raise ValueError("auth.header_name is invalid")
        if "\r" in self.auth.scheme or "\n" in self.auth.scheme:
            raise ValueError("auth.scheme contains a newline")

        configured: list[tuple[str, str]] = list(self.auth.fixed_headers.items())
        configured.extend(
            (name, "")
            for name in (
                self.auth.principal_headers.tenant_id,
                self.auth.principal_headers.organization_id,
                self.auth.principal_headers.request_id,
            )
            if name
        )
        for name, value in configured:
            if not _HEADER_NAME_RE.fullmatch(name):
                raise ValueError("configured header name is invalid")
            if "\r" in value or "\n" in value:
                raise ValueError("configured header value contains a newline")
            if name.lower() in reserved or name.lower() == auth_header:
                raise ValueError("configured headers cannot override reserved/auth headers")

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "KnowledgeConfig":
        """Load from EasyAgent's active YAML (``config.dev.yaml`` in dev)."""

        explicit = config_path is not None
        selected = Path(config_path) if explicit else Config.resolve_config_path()
        if not selected.is_file():
            if explicit:
                raise KnowledgeConfigError("knowledge configuration file does not exist")
            return cls()
        return cls.from_yaml(selected)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "KnowledgeConfig":
        path = Path(config_path)
        if not path.is_file():
            raise KnowledgeConfigError("knowledge configuration file does not exist")

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            location = f" at line {mark.line + 1}" if mark else ""
            raise KnowledgeConfigError(
                f"knowledge configuration contains invalid YAML{location}"
            ) from None

        if not isinstance(raw, dict):
            raise KnowledgeConfigError("application configuration must be a YAML mapping")

        # Application configs wrap this module under ``knowledge``. Accepting a
        # bare knowledge mapping keeps contract fixtures simple and backwards
        # compatible, but runtime always passes the active EasyAgent YAML.
        is_application_config = "knowledge" in raw
        knowledge_raw = raw.get("knowledge", {}) if is_application_config else raw
        if not isinstance(knowledge_raw, dict):
            raise KnowledgeConfigError("knowledge must be a YAML mapping")
        expanded = _expand_env_recursive(knowledge_raw)

        if is_application_config:
            try:
                small_models = SmallModelsConfig.from_mapping(
                    _expand_env_recursive(raw.get("small_models"))
                )
            except (ValueError, ValidationError) as exc:
                raise KnowledgeConfigError(
                    f"invalid small_models configuration: {exc}"
                ) from None
            defaults = expanded.setdefault("defaults", {})
            dataset = defaults.setdefault("dataset", {})
            retrieval = defaults.setdefault("retrieval", {})
            if small_models.embedding.model:
                dataset["embedding_model"] = small_models.embedding.model
            if small_models.reranker.model:
                retrieval["rerank_id"] = small_models.reranker.model
        if expanded.get("enabled") is True:
            unresolved = sorted(_find_unresolved_paths(expanded))
            if unresolved:
                raise KnowledgeConfigError(
                    "enabled knowledge configuration has unresolved environment variables at: "
                    + ", ".join(unresolved)
                )

        try:
            return cls.model_validate(expanded)
        except ValidationError as exc:
            issues = []
            for error in exc.errors(include_url=False, include_context=False, include_input=False):
                location = ".".join(str(part) for part in error["loc"])
                issues.append(f"{location or '<root>'}: {error['msg']}")
            raise KnowledgeConfigError(
                "invalid knowledge configuration: " + "; ".join(issues)
            ) from None


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


def _find_unresolved_paths(value: object, path: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, str) and _ENV_VAR_RE.search(value):
        found.add(path or "<root>")
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            found.update(_find_unresolved_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{path}[{index}]"
            found.update(_find_unresolved_paths(item, child))
    return found


__all__ = [
    "DocumentStatus",
    "KnowledgeConfig",
    "KnowledgeConfigError",
    "REQUIRED_UPSTREAM_OPERATIONS",
]
