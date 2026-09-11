"""Regression checks for secrets required by the supported dev launchers."""

from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "script_name",
    ["start.dev.keychain.sh", "start.dev.knowledge.keychain.sh"],
)
def test_dev_keychain_launcher_persists_and_exports_jwt_secret(script_name: str):
    content = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")

    assert "EASY_AGENT_JWT_KEYCHAIN_SERVICE" in content
    assert "security add-generic-password -U" in content
    assert "EASY_JWT_SECRET" in content
    export_line = next(
        line for line in content.splitlines() if line.startswith("export DEEPSEEK_API_KEY")
    )
    assert "EASY_JWT_SECRET" in export_line
