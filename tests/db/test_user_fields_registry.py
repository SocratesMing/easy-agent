"""db ↔ personnel 解耦注册表测试（ARCH-03 Phase 1/2）。"""

from pathlib import Path

import pytest

from easy_agent.db import user_fields_registry as reg

ROOT = Path(__file__).resolve().parents[2]


def test_user_field_hooks_are_dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(reg, "_save", lambda db, cursor, user: calls.append("save"))
    monkeypatch.setattr(reg, "_read", lambda row: {"display_name": "x"})
    monkeypatch.setattr(reg, "_ensure", lambda db, cursor: calls.append("ensure"))

    reg.ensure_user_fields(None, None)
    reg.save_user_fields(None, None, None)
    assert calls == ["ensure", "save"]
    assert reg.read_user_fields({}) == {"display_name": "x"}


def test_missing_hooks_are_safe_or_explicit(monkeypatch):
    monkeypatch.setattr(reg, "_save", None)
    monkeypatch.setattr(reg, "_read", None)
    monkeypatch.setattr(reg, "_ensure", None)
    monkeypatch.setattr(reg, "_default_admin", None)

    reg.ensure_user_fields(None, None)
    reg.save_user_fields(None, None, None)
    assert reg.read_user_fields({}) == {}
    with pytest.raises(RuntimeError):
        reg.get_or_create_default_admin(None)


def test_db_layer_has_no_static_personnel_import():
    users_src = (ROOT / "easy_agent/db/repositories/users.py").read_text(encoding="utf-8")
    schema_src = (ROOT / "easy_agent/db/schema.py").read_text(encoding="utf-8")
    assert "from ...personnel" not in users_src
    assert "from ..personnel" not in schema_src
