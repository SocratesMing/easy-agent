"""Bloom 金融数据接口测试（业务场景：彭博数据查询/分析/导入）。

说明：
- queryBloom / queryBloomStockIndex / queryBloomLineChart / queryBloomStockIndexChart
  通过 JSON Body 接收参数（list[dict] 或 dict），结构与具体业务字段相关；
  无数据时返回空列表或默认结构。
- queryBloomAnalysis / importBloom / reAnalysisBloom 通过 query 参数接收。
测试重点：接口可用、请求/响应契约正确。
"""

from fastapi.testclient import TestClient


def test_query_bloom(client: TestClient):
    resp = client.post("/api/bloom/queryBloom", json=[{"type": "短期基准利率", "region": "瑞士"}])
    assert resp.status_code == 200
    assert isinstance(resp.json(), (list, dict))


def test_query_bloom_stock_index(client: TestClient):
    resp = client.post(
        "/api/bloom/queryBloomStockIndex",
        json={"type": "股指价格", "region": "道琼斯指数"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), (list, dict))


def test_query_bloom_line_chart(client: TestClient):
    resp = client.post(
        "/api/bloom/queryBloomLineChart",
        json={
            "type": "短期基准利率",
            "region": ["瑞士"],
            "startDate": "2025-06-01",
            "endDate": "2025-06-15",
        },
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), (list, dict))


def test_query_bloom_stock_index_chart(client: TestClient):
    resp = client.post(
        "/api/bloom/queryBloomStockIndexChart",
        json={
            "type": "股指价格",
            "bloomCodeCN": ["道琼斯指数"],
            "startDate": "2025-06-01",
            "endDate": "2025-06-15",
        },
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), (list, dict))


def test_query_bloom_analysis(client: TestClient):
    resp = client.post(
        "/api/bloom/queryBloomAnalysis",
        params={"pair": "EURUSD", "startDate": "2025-06-27", "endDate": "2025-06-27"},
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), (list, dict))


def test_import_bloom(client: TestClient):
    # 数据目录不存在时导入为 no-op，返回 True
    resp = client.post(
        "/api/bloom/importBloom",
        params={"startDate": "20250627", "endDate": "20250628"},
    )
    assert resp.status_code == 200


def test_reanalysis_bloom(client: TestClient):
    resp = client.post(
        "/api/bloom/reAnalysisBloom", params={"analysisDate": "20250627"}
    )
    assert resp.status_code == 200
