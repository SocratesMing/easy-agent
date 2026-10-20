"""SEC-02：SPA 兜底路由只能返回前端构建目录内的文件。

改造前直接 ``os.path.join(frontend_dist, full_path)``：遇到绝对路径会丢掉前缀
（``GET //etc/passwd``），``%2e%2e/`` 也能逃出目录，宿主机任意文件可读。
"""

from __future__ import annotations

import pytest

import easy_agent.app as app_module

SECRET = "sec02-host-secret"


@pytest.fixture()
def dist(tmp_path, monkeypatch):
    dist_dir = tmp_path / "dist"
    (dist_dir / "js").mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist_dir / "js" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    monkeypatch.setattr(app_module, "frontend_dist", str(dist_dir))
    return tmp_path


def test_static_files_and_spa_routes_still_served(client, dist):
    assert client.get("/js/app.js").text == "console.log(1)"
    page = client.get("/settings/profile")
    assert page.status_code == 200
    assert "spa" in page.text


@pytest.mark.parametrize(
    "path", ["/%2e%2e/secret.txt", "/js/%2e%2e/%2e%2e/secret.txt"]
)
def test_dot_segments_cannot_escape_dist(client, dist, path):
    resp = client.get(path)
    assert resp.status_code == 404
    assert SECRET not in resp.text


def test_absolute_path_cannot_escape_dist(client, dist):
    # 请求行 ``GET //tmp/.../secret.txt``：路由参数是绝对路径
    resp = client.get(f"http://testserver/{dist / 'secret.txt'}")
    assert resp.status_code == 404
    assert SECRET not in resp.text


def _write_large_asset(dist, name: str = "big.js") -> None:
    """构造超过 1KB 的产物：小于阈值的文件不压缩（压缩反而更大）。"""
    target = dist / "dist" / "js" / name
    target.write_text("console.log('gzip');\n" * 200, encoding="utf-8")


def test_static_assets_gzip_when_accepted(client, dist):
    _write_large_asset(dist)
    resp = client.get("/js/big.js", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in (resp.headers.get("vary") or "").lower()
    assert "console.log" in resp.text


def test_static_assets_not_gzipped_without_accept_encoding(client, dist):
    _write_large_asset(dist)
    resp = client.get("/js/big.js", headers={"Accept-Encoding": "identity"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") is None


def test_gzip_static_supports_etag_revalidation(client, dist):
    _write_large_asset(dist)
    first = client.get("/js/big.js", headers={"Accept-Encoding": "gzip"})
    etag = first.headers.get("etag")
    assert etag
    again = client.get(
        "/js/big.js", headers={"Accept-Encoding": "gzip", "If-None-Match": etag}
    )
    assert again.status_code == 304


def test_api_responses_are_not_gzipped(client, dist):
    """SSE/接口不能走静态压缩：全局 gzip 会把 text/event-stream 攒包。"""
    resp = client.get("/agent/health", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("content-encoding") is None
