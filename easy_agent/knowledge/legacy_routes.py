"""Keep existing knowledge clients working with the host /agent API prefix."""

class LegacyHostRoutes:
    PREFIXES = ("auth", "chat", "sessions", "files", "settings", "prompts", "skills", "scheduled-tasks", "terminal", "knowledge")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] in {"http", "websocket"}:
            for prefix in self.PREFIXES:
                old = f"/api/{prefix}"
                if path == old or path.startswith(old + "/"):
                    scope = dict(scope)
                    scope["path"] = "/agent/" + path[5:]
                    if "raw_path" in scope:
                        scope["raw_path"] = scope["raw_path"].replace(b"/api/", b"/agent/", 1)
                    break
        await self.app(scope, receive, send)
