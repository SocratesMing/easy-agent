"""Strict configuration contract for the knowledge module."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

import easy_agent.knowledge.config as config_module
from easy_agent.knowledge.config import KnowledgeConfig, KnowledgeConfigError
from easy_agent.knowledge.domain import DocumentStatus


TEMPLATE = Path(__file__).parents[1] / "fixtures" / "knowledge-config.yaml"


def _template_data() -> dict:
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "knowledge.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAGFLOW_BASE_URL", "https://ragflow.example.test")
    monkeypatch.setenv("RAGFLOW_API_KEY", "top-secret-test-value")
    monkeypatch.setenv("RAGFLOW_EMBEDDING_MODEL", "bank-embedding-model")


def test_load_without_selected_file_is_safely_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        config_module.Config,
        "resolve_config_path",
        classmethod(lambda cls: tmp_path / "missing.yaml"),
    )

    config = KnowledgeConfig.load()

    assert config.enabled is False
    assert config.endpoint is None


def test_disabled_template_allows_unresolved_deployment_values():
    config = KnowledgeConfig.from_yaml(TEMPLATE)

    assert config.enabled is False
    assert config.endpoint is not None
    assert config.endpoint.base_url == "${RAGFLOW_BASE_URL}"


def test_enabled_template_loads_typed_env_and_redacts_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data = _template_data()
    data["enabled"] = True
    path = _write_yaml(tmp_path, data)
    _set_required_env(monkeypatch)
    monkeypatch.setenv("RAGFLOW_VERIFY_SSL", "false")

    config = KnowledgeConfig.from_yaml(path)

    assert config.enabled is True
    assert config.tls.verify is False
    assert config.endpoint is not None
    assert len(config.endpoint.operations) == 12
    assert config.defaults.dataset.permission.value == "me"
    assert config.operations.deletion.durable_outbox is False
    assert config.operations.upload.concurrency == 1
    assert config.limits.max_files_per_request == 1
    assert str(config.auth.credential) == "**********"
    assert "top-secret-test-value" not in repr(config)
    assert config.compatibility.map_document_status("3") == DocumentStatus.READY
    assert config.compatibility.map_document_status("unexpected") == DocumentStatus.UNKNOWN


def test_application_config_injects_small_model_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    knowledge = _template_data()
    knowledge["enabled"] = True
    knowledge["defaults"]["dataset"]["embedding_model"] = ""
    knowledge["defaults"]["retrieval"]["rerank_id"] = ""
    app_config = {
        "small_models": {
            "embedding": {"provider": "ragflow", "model": "embedding-from-registry"},
            "reranker": {"provider": "ragflow", "model": "reranker-from-registry"},
        },
        "knowledge": knowledge,
    }
    _set_required_env(monkeypatch)

    config = KnowledgeConfig.from_yaml(_write_yaml(tmp_path, app_config))

    assert config.defaults.dataset.embedding_model == "embedding-from-registry"
    assert config.defaults.retrieval.rerank_id == "reranker-from-registry"


def test_env_scalar_conversion_is_explicit_and_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data = _template_data()
    data["enabled"] = True
    data["transport"]["timeout_seconds"]["connect"] = "${RAGFLOW_CONNECT_TIMEOUT}"
    _set_required_env(monkeypatch)
    monkeypatch.setenv("RAGFLOW_CONNECT_TIMEOUT", "7.5")

    config = KnowledgeConfig.from_yaml(_write_yaml(tmp_path, data))

    assert config.transport.timeout_seconds.connect == 7.5


def test_bank_parser_layout_and_local_bridge_are_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data = _template_data()
    data["enabled"] = True
    _set_required_env(monkeypatch)

    config = KnowledgeConfig.from_yaml(_write_yaml(tmp_path, data))

    standard = config.defaults.parsing.profiles["standard"]
    table = config.defaults.parsing.profiles["table"]
    assert standard.parser_config["layout_recognize"] is True
    assert table.parser_config["layout_recognize"] is True
    assert config.adapter.local_v017_bridge.layout_recognize_value == "Plain Text"


def test_direct_bank_retrieval_requires_aaas_subscription_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data = _template_data()
    data["enabled"] = True
    data["bank_api"]["aaas_retrieval"]["auth_token"] = ""
    data["adapter"]["local_v017_bridge"]["enabled"] = False
    _set_required_env(monkeypatch)

    with pytest.raises(KnowledgeConfigError, match="AaaS retrieval auth token"):
        KnowledgeConfig.from_yaml(_write_yaml(tmp_path, data))


def test_non_boolean_env_word_is_not_coerced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data = _template_data()
    data["enabled"] = "${KNOWLEDGE_ENABLED}"
    monkeypatch.setenv("KNOWLEDGE_ENABLED", "yes")

    with pytest.raises(KnowledgeConfigError, match="valid boolean"):
        KnowledgeConfig.from_yaml(_write_yaml(tmp_path, data))


def test_enabled_config_rejects_unresolved_environment_without_secret_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data = _template_data()
    data["enabled"] = True
    path = _write_yaml(tmp_path, data)
    monkeypatch.delenv("RAGFLOW_API_KEY", raising=False)
    monkeypatch.delenv("RAGFLOW_BASE_URL", raising=False)
    monkeypatch.delenv("RAGFLOW_EMBEDDING_MODEL", raising=False)

    with pytest.raises(KnowledgeConfigError) as exc_info:
        KnowledgeConfig.from_yaml(path)

    message = str(exc_info.value)
    assert "auth.credential" in message
    assert "endpoint.base_url" in message
    assert "defaults.dataset.embedding_model" in message
    assert "top-secret" not in message


def test_unknown_key_is_rejected_even_when_disabled(tmp_path: Path):
    data = _template_data()
    data["bank_magic_passthrough"] = True

    with pytest.raises(KnowledgeConfigError, match="Extra inputs are not permitted"):
        KnowledgeConfig.from_yaml(_write_yaml(tmp_path, data))


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda data: data["endpoint"]["operations"]["retrieve"].update(
                {"path": "//attacker.example/retrieval"}
            ),
            "exactly one slash",
        ),
        (
            lambda data: data["auth"]["fixed_headers"].update({"Host": "evil"}),
            "reserved/auth headers",
        ),
        (
            lambda data: data["transport"]["retry"]["operations"].append(
                "delete_documents"
            ),
            "non-idempotent operations",
        ),
        (
            lambda data: data["endpoint"].update(
                {"base_url": "https://user:password@ragflow.example.test"}
            ),
            "credentials/query/fragment",
        ),
        (
            lambda data: data["security"].update(
                {"allowed_base_url_schemes": ["ftp"]}
            ),
            "only http/https",
        ),
        (
            lambda data: data["auth"].update({"scheme": "Bearer\nInjected"}),
            "scheme contains a newline",
        ),
        (
            lambda data: data["compatibility"][
                "fixed_body_by_operation"
            ].update({"unknown_operation": {"bank_flag": True}}),
            "reference unknown operations",
        ),
    ],
)
def test_enabled_config_rejects_unsafe_transport_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutator,
    expected: str,
):
    data = deepcopy(_template_data())
    data["enabled"] = True
    mutator(data)
    _set_required_env(monkeypatch)

    with pytest.raises(KnowledgeConfigError, match=expected):
        KnowledgeConfig.from_yaml(_write_yaml(tmp_path, data))


def test_explicit_missing_mount_fails_instead_of_disabling(tmp_path: Path):
    with pytest.raises(KnowledgeConfigError, match="does not exist"):
        KnowledgeConfig.load(tmp_path / "not-mounted.yaml")


def test_http_endpoint_requires_exact_host_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    data = _template_data()
    data["enabled"] = True
    data["security"]["allowed_base_url_schemes"] = ["http"]
    _set_required_env(monkeypatch)
    monkeypatch.setenv("RAGFLOW_BASE_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("RAGFLOW_BANK_RETRIEVAL_BASE_URL", "http://127.0.0.1:8080")

    with pytest.raises(KnowledgeConfigError, match="insecure_http_hosts"):
        KnowledgeConfig.from_yaml(_write_yaml(tmp_path, data))

    data["security"]["insecure_http_hosts"] = ["127.0.0.1"]
    config = KnowledgeConfig.from_yaml(_write_yaml(tmp_path, data))
    assert config.endpoint is not None
    assert config.endpoint.base_url == "http://127.0.0.1:8080"


@pytest.mark.parametrize(
    ("original_updates", "expected"),
    [
        ({"required_for_upload": False}, "required for every upload"),
        (
            {
                "driver": "mounted_fs",
                "filesystem": {"root_path": "relative/nas"},
            },
            "mounted_fs root_path must be absolute",
        ),
    ],
)
def test_original_storage_rejects_unsafe_or_non_dual_write_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_updates: dict,
    expected: str,
):
    data = _template_data()
    data["enabled"] = True
    for key, value in original_updates.items():
        if key == "filesystem":
            data["original_storage"]["filesystem"].update(value)
        else:
            data["original_storage"][key] = value
    _set_required_env(monkeypatch)

    with pytest.raises(KnowledgeConfigError, match=expected):
        KnowledgeConfig.from_yaml(_write_yaml(tmp_path, data))
