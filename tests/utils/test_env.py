"""统一环境变量读取 helper 测试（ARCH-09）。"""

from easy_agent.bootstrap.env import env_bool, env_int, env_list, env_str


def test_env_str(monkeypatch):
    monkeypatch.delenv("E_TEST", raising=False)
    assert env_str("E_TEST", "default") == "default"
    monkeypatch.setenv("E_TEST", "  value  ")
    assert env_str("E_TEST") == "value"
    monkeypatch.setenv("E_TEST", "  raw  ")
    assert env_str("E_TEST", strip=False) == "  raw  "


def test_env_bool(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("E_BOOL", value)
        assert env_bool("E_BOOL") is True, value
    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("E_BOOL", value)
        assert env_bool("E_BOOL") is False, value
    monkeypatch.setenv("E_BOOL", "unexpected")
    assert env_bool("E_BOOL", default=True) is True


def test_env_int_and_list(monkeypatch):
    monkeypatch.setenv("E_INT", "3")
    assert env_int("E_INT") == 3
    monkeypatch.setenv("E_INT", "not-an-int")
    assert env_int("E_INT", 7) == 7
    monkeypatch.delenv("E_INT", raising=False)
    assert env_int("E_INT", 9) == 9

    monkeypatch.setenv("E_LIST", " a, b ,,c ")
    assert env_list("E_LIST") == ("a", "b", "c")
    monkeypatch.delenv("E_LIST", raising=False)
    assert env_list("E_LIST", ("x",)) == ("x",)
