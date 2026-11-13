"""Request body size limit (pure ASGI middleware).

Caddy refuses large bodies for requests from the internet (``request_body max_size``), but the
bot talks to the backend directly over ``vav-internal``; this limit covers both paths. It checks
``Content-Length`` up front and also counts chunked bodies while they are read.
"""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 64 * 1024

_TOO_LARGE_BODY = json.dumps({"detail": "Слишком большой запрос"}, ensure_ascii=False).encode()


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self.max_bytes
            except ValueError:
                too_large = True
            if too_large:
                await _reject(send)
                return

        received = 0
        rejected = False

        async def limited_receive() -> Message:
            # A chunked body is counted as it arrives; past the limit the client gets 413 and
            # the app sees a disconnect (its own error response is dropped below).
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes and not rejected:
                    rejected = True
                    await _reject(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not rejected:
                await send(message)

        await self.app(scope, limited_receive, guarded_send)


async def _reject(send: Send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(_TOO_LARGE_BODY)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _TOO_LARGE_BODY})
