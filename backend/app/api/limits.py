"""Hard request-body limit, enforced before the multipart parser buffers anything.

Two paths:
* ``Content-Length`` present and too large -> 413 immediately, body never read.
* No ``Content-Length`` (chunked) -> bytes are counted as they arrive and the
  request is cut off with 413 the moment the limit is crossed.

The upload handler re-checks the file's own size against the exact limit.
"""

from __future__ import annotations

import json

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import policy


def _limit_message() -> str:
    return (f"Upload rejected: the request is larger than the {policy.MAX_UPLOAD_BYTES // (1024 * 1024)} MiB "
            "upload limit. Nothing was stored.")


class RequestBodyTooLarge(HTTPException):
    # An HTTPException so FastAPI's body parsing re-raises it instead of turning it into a 400.
    def __init__(self) -> None:
        super().__init__(status_code=413, detail={"code": "file_too_large", "message": _limit_message()})


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            await _send_413(send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise RequestBodyTooLarge()
            return message

        await self.app(scope, limited_receive, send)


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_413(send: Send) -> None:
    body = json.dumps({"error": {"code": "file_too_large", "message": _limit_message()}}).encode()
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close")],
    })
    await send({"type": "http.response.body", "body": body})
