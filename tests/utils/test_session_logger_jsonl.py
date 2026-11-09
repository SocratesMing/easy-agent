"""SessionLogger append-only JSONL 回归测试（PERF-05）。

旧实现每写一条就全量重写 JSON（O(n²)）；新实现每条只追加一行。
"""

import json

from easy_agent.utils import session_logger as sl


def _make_logger(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "_resolve_session_log_dir", lambda *a, **k: tmp_path)
    return sl.SessionLogger(session_id="sess-1", username="alice")


def test_append_only_jsonl_and_entries(tmp_path, monkeypatch):
    logger = _make_logger(tmp_path, monkeypatch)
    logger.log_user_message("hello", message_id="m1")
    logger.log_tool_call("ls", "tc1", {"path": "/"}, result="ok", step=1)
    logger.log_assistant_response("world", thinking="think", message_id="m1")

    path = tmp_path / "sess-1.jsonl"
    assert path.exists()

    lines = path.read_text(encoding="utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    types = [item.get("type") for item in parsed]
    assert types.count("session_meta") >= 1
    assert types.count("user_message") == 1
    assert types.count("tool_call") == 1
    assert types.count("assistant_response") == 1

    # 追加写：10 条 entry 后文件行数 = meta 行数 + 10，且不含 JSON 数组包裹
    before = len(lines)
    for i in range(10):
        logger.log_user_message(f"msg-{i}", message_id=f"m{i}")
    after_lines = path.read_text(encoding="utf-8").splitlines()
    assert len(after_lines) == before + 10
    assert not path.read_text(encoding="utf-8").lstrip().startswith("[")


def test_legacy_json_is_migrated(tmp_path, monkeypatch):
    legacy = tmp_path / "sess-2.json"
    legacy.write_text(
        json.dumps(
            {
                "session_id": "sess-2",
                "username": "alice",
                "entries": [
                    {"type": "user_message", "content": "old"},
                    {"type": "assistant_response", "content": "reply"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sl, "_resolve_session_log_dir", lambda *a, **k: tmp_path)
    sl.SessionLogger(session_id="sess-2", username="alice")

    lines = (tmp_path / "sess-2.jsonl").read_text(encoding="utf-8").splitlines()
    contents = [json.loads(line) for line in lines]
    assert any(item.get("content") == "old" for item in contents)
    assert any(item.get("content") == "reply" for item in contents)
    # 旧文件保留
    assert legacy.exists()
