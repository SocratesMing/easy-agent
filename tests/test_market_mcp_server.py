import pytest
import json
from pathlib import Path

from easy_agent.mcp_servers.market import server as market_server
from easy_agent.mcp_servers.market import seed_mysql
from easy_agent.config import DatabaseConfig, MySQLConfig


MCP_EXAMPLE_PATH = (
    Path(__file__).resolve().parent.parent
    / "easy_agent"
    / "mcp_servers"
    / "market"
    / "mcp.json.example"
)


def test_parse_market_question_detects_gold():
    parsed = market_server.parse_market_question("查询最新的贵金属行情，黄金的行情")

    assert parsed["category"] == "precious_metals"
    assert parsed["symbols"] == ["XAUUSD"]


def test_parse_market_question_defaults_to_forex_for_currency_question():
    parsed = market_server.parse_market_question("看一下美元兑人民币汇率")

    assert parsed["category"] == "forex"
    assert parsed["symbols"] == ["USDCNH"]


def test_invalid_api_key_is_rejected(monkeypatch):
    monkeypatch.setattr(market_server, "_fetch_one", lambda sql, params: None)

    with pytest.raises(PermissionError):
        market_server.authenticate_api_key("invalid-key")


def test_valid_api_key_returns_username(monkeypatch):
    monkeypatch.setattr(
        market_server,
        "_fetch_one",
        lambda sql, params: {"username": "szm"},
    )

    assert market_server.authenticate_api_key("valid-key") == "szm"


def test_position_query_is_scoped_to_authenticated_user(monkeypatch):
    captured = {}

    def fake_fetch_all(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(market_server, "_fetch_all", fake_fetch_all)
    monkeypatch.setattr(market_server, "authenticate_api_key", lambda key: "szm")

    market_server.get_my_positions()

    assert "username" in captured["sql"]
    assert "szm" in captured["params"]


def test_ask_market_returns_structured_gold_quote(monkeypatch):
    quote = {
        "symbol": "XAUUSD",
        "name": "黄金/美元",
        "category": "precious_metals",
        "price": 2385.4,
        "change_percent": 0.42,
        "updated_at": "2026-09-03 10:00:00",
    }
    monkeypatch.setattr(
        market_server,
        "authenticate_api_key",
        lambda key: "szm",
    )
    monkeypatch.setattr(
        market_server,
        "_fetch_all",
        lambda sql, params: [quote],
    )

    result = market_server.ask_market("查询最新的贵金属行情，黄金的行情")

    assert result["category"] == "precious_metals"
    assert result["quotes"] == [quote]
    assert "XAUUSD" in result["answer"]
    assert "2385.4" in result["answer"]


def test_demo_market_rows_include_gold_and_usdcnh():
    quotes = seed_mysql.demo_market_rows()
    symbols = {quote["symbol"] for quote in quotes}

    assert "XAUUSD" in symbols
    assert "USDCNH" in symbols


def test_demo_position_rows_are_user_scoped():
    positions = seed_mysql.demo_position_rows()
    usernames = {position["username"] for position in positions}

    assert usernames == {"szm", "zr6"}


def test_mcp_example_hides_database_configuration():
    config = json.loads(MCP_EXAMPLE_PATH.read_text(encoding="utf-8"))
    env = config["servers"]["market-data"]["env"]

    assert not any(key.startswith("MYSQL_") for key in env)
    assert set(env) == {"MARKET_MCP_API_KEY"}


def test_mysql_config_loads_project_env(monkeypatch):
    loaded = []
    monkeypatch.setattr(
        market_server,
        "load_project_env",
        lambda: loaded.append("called") or ["MYSQL_HOST"],
    )
    monkeypatch.setenv("MYSQL_HOST", "db.internal")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_USER", "market")
    monkeypatch.setenv("MYSQL_PASSWORD", "hidden")
    monkeypatch.setenv("MYSQL_DATABASE", "market")

    config = market_server._mysql_config()

    assert loaded == ["called"]
    assert config["host"] == "db.internal"
    assert config["port"] == 3307


def test_mysql_config_uses_explicit_application_config(monkeypatch):
    monkeypatch.setenv("MYSQL_PASSWORD", "")

    config = market_server._mysql_config(
        {
            "host": "configured-host",
            "port": 3307,
            "user": "app-user",
            "password": "app-password",
            "database": "app-database",
            "pool": {"pool_size": 5},
        }
    )

    assert config["host"] == "configured-host"
    assert config["port"] == 3307
    assert config["user"] == "app-user"
    assert config["password"] == "app-password"
    assert config["database"] == "app-database"
    assert "pool" not in config


def test_mysql_config_prefers_application_mysql_config_by_default(monkeypatch):
    mysql = MySQLConfig(
        host="configured-host",
        port=3307,
        user="app-user",
        password="app-password",
        database="app-database",
    )

    class FakeConfig:
        @classmethod
        def load(cls):
            return cls()

        database = DatabaseConfig(type="mysql", mysql=mysql)

    monkeypatch.setattr(market_server, "Config", FakeConfig)

    config = market_server._mysql_config()

    assert config["host"] == "configured-host"
    assert config["port"] == 3307
    assert config["user"] == "app-user"
    assert config["password"] == "app-password"
    assert config["database"] == "app-database"


def test_issue_api_key_stores_hash_not_plaintext(monkeypatch):
    executed = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, sql, params=()):
            executed.append((sql, params))

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(
        market_server,
        "_get_connection",
        lambda mysql_config=None: FakeConnection(),
    )

    api_key = market_server.issue_api_key("szm")

    assert api_key
    assert len(executed) == 2
    assert "CREATE TABLE IF NOT EXISTS market_mcp_api_keys" in executed[0][0]
    assert "INSERT INTO market_mcp_api_keys" in executed[1][0]
    assert "ON DUPLICATE KEY UPDATE" in executed[1][0]
    assert executed[1][1][0] == "szm"
    assert executed[1][1][1] == market_server.hash_api_key(api_key)
    assert api_key not in executed[1][1]
