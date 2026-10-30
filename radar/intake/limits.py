"""Bound the complete multipart body before Starlette spools owner uploads."""

from starlette.responses import PlainTextResponse

from radar.intake.parsers import MAX_BATCH_BYTES


class AlertBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in ("/inbox/import", "/inbox/import-data", "/inbox/manual")
        ):
            return await self.app(scope, receive, send)
        data = []
        size = 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body = event.get("body", b"")
            size += len(body)
            if size > MAX_BATCH_BYTES + 65536:
                return await PlainTextResponse(
                    "Alert upload exceeds 3MB plus multipart allowance", status_code=413
                )(scope, receive, send)
            data.append(body)
            if not event.get("more_body", False):
                break

        async def replay():
            if data:
                body = b"".join(data)
                data.clear()
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)
