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
