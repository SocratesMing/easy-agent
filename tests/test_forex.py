"""Forex 外汇业务接口测试。

接口底层依赖 LLM 解析，无有效模型时会优雅失败并返回含 code 字段的结构。
测试重点：接口可用、返回契约正确（dict 且含 code 字段）。
"""

from fastapi.testclient import TestClient


def test_option_quote(client: TestClient):
    resp = client.post(
        "/api/forex/option_quote",
        json={"msgId": "1", "content": "EURUSD call strike 1.1"},
    )
    assert resp.status_code == 200
    assert "code" in resp.json()


def test_bond_bot(client: TestClient):
    # bond_bot 的 content / msg_id 为查询参数
    resp = client.post(
        "/api/forex/bond_bot",
        params={"content": "对手方报价 EURUSD 1.1", "msg_id": 0},
    )
    assert resp.status_code == 200
    assert "code" in resp.json()
