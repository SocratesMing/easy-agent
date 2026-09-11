from pathlib import Path

import pytest

from scripts import knowledge_backup


def test_backup_runtime_does_not_require_unrelated_llm_or_ragflow_secrets(
    tmp_path: Path,
):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
database:
  type: sqlite
  fallback_to_sqlite: false
  sqlite:
    path: "${BACKUP_SQLITE_PATH:-./agent.db}"
  mysql: {}
models:
  deepseek:
    api_key: "${MISSING_LLM_SECRET}"
knowledge:
  auth:
    credential: "${MISSING_RAGFLOW_SECRET}"
  original_storage:
    enabled: "${BACKUP_STORAGE_ENABLED:-true}"
    required_for_upload: true
    driver: local_fs
    filesystem:
      root_path: "${BACKUP_NAS_ROOT:-./data/nas-sim}"
      path_prefix: easyagent-knowledge
""",
        encoding="utf-8",
    )

    database, original_storage = knowledge_backup._runtime(config)

    assert database.type == "sqlite"
    assert database.sqlite.path == "./agent.db"
    assert original_storage.filesystem.root_path == "./data/nas-sim"
    assert original_storage.filesystem.path_prefix == "easyagent-knowledge"
    assert original_storage.enabled is True


def test_database_client_error_is_diagnostic_and_redacts_password(monkeypatch):
    class Failed:
        returncode = 2
        stderr = b"access denied for password super-secret"

    monkeypatch.setattr(knowledge_backup.subprocess, "run", lambda *args, **kwargs: Failed())

    with pytest.raises(RuntimeError, match="exit code 2") as caught:
        knowledge_backup._run_database_client(
            ["mysqldump"], password="super-secret"
        )

    assert "access denied" in str(caught.value)
    assert "super-secret" not in str(caught.value)
    assert "<redacted>" in str(caught.value)
