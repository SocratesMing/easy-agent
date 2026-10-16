"""上传限额测试：单文件 10MB / 单次 5 个 / 总量 20MB（可在 config.yaml 的 upload_limits 段配置）。

覆盖范围：
- 配置解析：默认值、缺失该段、配置写歪（总量 < 单文件）时的告警；
- 限额下发接口 ``GET /agent/settings/upload-limits``；
- 后端单文件兜底：超限 413 且不落盘、边界值（正好等于上限放行）、``enabled=false`` 放行。

「5 个 / 20MB」属于**单次对话**的组合约束（输入框待发送附件），由前端在选文件时
拦截，上传接口是单文件无状态请求，看不到"一次选择"的边界，故不在此处断言；
设计见 docs/upload-limits-design.md。
"""

import logging
import os
from pathlib import Path

import pytest

from easy_agent.config import Config, UploadLimitsConfig

MB = 1024 * 1024

_MINIMAL_CONFIG = """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: sk-test
"""


def _write_config(tmp_path, extra: str = "") -> Path:
    """写一份最小可解析的配置（仅 model + models.api_key 为必填）。"""
    path = tmp_path / "config.yaml"
    path.write_text(_MINIMAL_CONFIG + extra, encoding="utf-8")
    return path


# ── 配置解析 ──────────────────────────────────────────────────────────


def test_upload_limits_defaults_when_section_missing(tmp_path):
    """老配置文件没有 upload_limits 段时应回落默认值，不影响启动。"""
    config = Config.from_yaml(_write_config(tmp_path))

    assert config.upload_limits.enabled is True
    assert config.upload_limits.max_file_size_mb == 10
    assert config.upload_limits.max_files_per_message == 5
    assert config.upload_limits.max_total_size_mb == 20


def test_upload_limits_parsed_from_yaml(tmp_path, monkeypatch):
    """支持 ${VAR:-default} 占位符，且各字段可独立配置。"""
    monkeypatch.setenv("UPLOAD_MAX_FILES", "3")
    config = Config.from_yaml(
        _write_config(
            tmp_path,
            extra="""
upload_limits:
  enabled: ${UPLOAD_LIMITS_ENABLED:-true}
  max_file_size_mb: ${UPLOAD_MAX_FILE_SIZE_MB:-7}
  max_files_per_message: ${UPLOAD_MAX_FILES:-5}
  max_total_size_mb: ${UPLOAD_MAX_TOTAL_SIZE_MB:-15}
""",
        )
    )

    assert config.upload_limits.max_file_size_mb == 7
    assert config.upload_limits.max_files_per_message == 3
    assert config.upload_limits.max_total_size_mb == 15


def test_upload_limits_warns_when_total_below_file_size(tmp_path, caplog):
    """总量 < 单文件上限 属于写歪的配置：只告警，不中断启动。"""
    with caplog.at_level(logging.WARNING):
        config = Config.from_yaml(
            _write_config(
                tmp_path,
                extra="""
upload_limits:
  max_file_size_mb: 30
  max_total_size_mb: 10
""",
            )
        )

    assert config.upload_limits.max_file_size_mb == 30
    assert config.upload_limits.max_total_size_mb == 10
    assert any("upload_limits" in record.message for record in caplog.records)


# ── 限额下发接口 ──────────────────────────────────────────────────────


def test_settings_upload_limits_endpoint(client):
    resp = client.get("/agent/settings/upload-limits")
    assert resp.status_code == 200
    data = resp.json()

    assert set(data) == {
        "enabled",
        "max_file_size_mb",
        "max_files_per_message",
        "max_total_size_mb",
    }
    assert isinstance(data["enabled"], bool)
    assert data["max_file_size_mb"] > 0
    assert data["max_files_per_message"] > 0
    assert data["max_total_size_mb"] > 0


# ── 后端单文件兜底 ────────────────────────────────────────────────────


def _create_session(client) -> str:
    resp = client.post("/agent/sessions", json={"title": "上传限额测试"})
    assert resp.status_code == 200
    return resp.json()["session_id"]


def _upload_dir() -> Path:
    return Path(os.environ["TEST_WORKSPACE_DIR"]) / "users" / "testuser" / "uploadfiles"


def _saved_names() -> set:
    path = _upload_dir()
    return {p.name for p in path.glob("*")} if path.exists() else set()


def _patch_limit(monkeypatch, limit_mb):
    """把后端单文件上限改小，避免用例真的构造 10MB+ 的请求体。"""
    import easy_agent.api.sessions as sessions_api

    monkeypatch.setattr(sessions_api, "_get_max_file_size_mb", lambda: limit_mb)


def _patch_limits_config(monkeypatch, limits: UploadLimitsConfig):
    """改全局配置里的 upload_limits（模拟 config.yaml 被修改）。"""
    import easy_agent.services.agent_manager as am

    if not am._agent_config or not am._agent_config.get("config"):
        pytest.skip("全局 Agent 配置未初始化")
    monkeypatch.setattr(am._agent_config["config"], "upload_limits", limits, raising=False)


def test_upload_rejected_over_default_limit_and_not_saved(client):
    """默认 10MB：10MB+1 字节被拒（413），且不落盘。"""
    sid = _create_session(client)
    before = _saved_names()

    resp = client.post(
        f"/agent/sessions/{sid}/upload",
        files={"file": ("big.bin", b"x" * (10 * MB + 1), "application/octet-stream")},
    )

    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert detail["code"] == "UPLOAD_FILE_TOO_LARGE"
    assert detail["limit_mb"] == 10
    assert detail["actual_mb"] == 10.0
    # 拒绝的请求不应留下任何文件
    assert _saved_names() == before


def test_upload_boundary_at_limit_is_accepted(client, monkeypatch):
    """边界：正好等于上限放行，超过 1 字节拒绝（判定用 > 而非 >=）。"""
    sid = _create_session(client)
    _patch_limit(monkeypatch, 1)

    ok = client.post(
        f"/agent/sessions/{sid}/upload",
        files={"file": ("exact.bin", b"y" * MB, "application/octet-stream")},
    )
    assert ok.status_code == 200
    assert ok.json()["size"] == MB

    too_big = client.post(
        f"/agent/sessions/{sid}/upload",
        files={"file": ("over.bin", b"y" * (MB + 1), "application/octet-stream")},
    )
    assert too_big.status_code == 413


def test_upload_unlimited_when_disabled(client, monkeypatch):
    """enabled=false 时不限制单文件大小（应急开关）。"""
    sid = _create_session(client)
    _patch_limits_config(
        monkeypatch,
        UploadLimitsConfig(enabled=False, max_file_size_mb=1, max_files_per_message=5, max_total_size_mb=20),
    )

    resp = client.post(
        f"/agent/sessions/{sid}/upload",
        files={"file": ("unlimited.bin", b"z" * (2 * MB), "application/octet-stream")},
    )
    assert resp.status_code == 200


def test_get_max_file_size_mb_follows_config(client, monkeypatch):
    """后端读取的上限来自全局配置的 upload_limits 段。"""
    from easy_agent.api.sessions import _get_max_file_size_mb

    _patch_limits_config(monkeypatch, UploadLimitsConfig(max_file_size_mb=3))
    assert _get_max_file_size_mb() == 3

    _patch_limits_config(monkeypatch, UploadLimitsConfig(enabled=False, max_file_size_mb=3))
    assert _get_max_file_size_mb() is None
