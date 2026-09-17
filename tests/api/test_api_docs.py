from starlette.requests import Request

from easy_agent.app import app


async def test_api_docs_use_redoc():
    docs_route = next(route for route in app.routes if route.path == "/docs")
    request = Request(scope={"type": "http", "root_path": ""})
    html = await docs_route.endpoint(request)

    assert "redoc.standalone.js" in html.body.decode()
    assert "swagger-ui" not in html.body.decode()
