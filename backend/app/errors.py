"""One error envelope for every failure: ``{"error": {"code", "message", ...}}``."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_LOCATION_PREFIXES = ("body", "query", "path", "form")


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {
                "field": ".".join(str(p) for p in err["loc"] if p not in _LOCATION_PREFIXES) or None,
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        summary = "; ".join(f"{p['field'] or 'request'}: {p['message']}" for p in problems)
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_request", "message": f"Invalid request: {summary}",
                               "errors": problems}},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            error = exc.detail
        else:
            error = {"code": f"http_{exc.status_code}", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": error}, headers=exc.headers)
