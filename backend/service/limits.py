from starlette.exceptions import HTTPException


def route_path(scope):
    path=scope.get('path','')
    root=scope.get('root_path','').rstrip('/')
    return path[len(root):] if root and path.startswith(root+'/') else path


class UploadBodyLimit:
    """Bound chunked multipart bodies before they can fill the temporary spool."""
    def __init__(self, app, settings, paths=("/api/v1/analyze",)):
        self.app = app
        self.settings = settings
        self.paths = paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or route_path(scope) not in self.paths:
            await self.app(scope, receive, send)
            return
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.settings.max_upload_bytes+1024*1024:
                    scope.setdefault("state", {})["body_limit_exceeded"] = True
                    raise HTTPException(413, "Upload exceeds the configured limit")
            return message

        await self.app(scope, limited_receive, send)
