"""OpenAPI 响应模型契约测试（ARCH-08 分批收口）。"""

from easy_agent.app import app

SETTINGS_PATHS = [
    "/agent/settings/memory",
    "/agent/settings/system-prompt",
    "/agent/settings/skills",
    "/agent/settings/models",
    "/agent/settings/web-search",
    "/agent/settings/upload-limits",
    "/agent/settings/knowledge",
]


def test_settings_routes_have_explicit_response_schema():
    spec = app.openapi()
    for path in SETTINGS_PATHS:
        operation = spec["paths"][path]["get"]
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema, path
        assert "$ref" in schema or schema.get("type"), path


def test_auth_config_has_response_schema():
    spec = app.openapi()
    operation = spec["paths"]["/agent/auth/config"]["get"]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema
