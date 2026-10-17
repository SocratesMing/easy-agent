from easy_agent.services.health import ping_database, workspace_writable


def test_ping_database_missing():
    ok, detail = ping_database(None)
    assert ok is False
    assert "not initialized" in detail


def test_workspace_writable(tmp_path):
    ok, detail = workspace_writable(str(tmp_path / "ws"))
    assert ok is True
    assert detail == "ok"
