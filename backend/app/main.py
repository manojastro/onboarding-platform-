from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import policy
from app.api import routes_connections, routes_datasets, routes_meta
from app.api.limits import BodySizeLimitMiddleware
from app.config import settings
from app.errors import install_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(
        title="Onboarding Platform API",
        version="0.1.0",
        description="AI-assisted customer data onboarding. Deterministic code decides; the LLM only suggests.",
    )
    app.add_middleware(BodySizeLimitMiddleware,
                       max_body_bytes=policy.MAX_UPLOAD_BYTES + policy.MULTIPART_OVERHEAD_BYTES)
    # Added last, so it is outermost: even an early 413 carries CORS headers.
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["*"])
    install_error_handlers(app)
    for router in (routes_meta.router, routes_connections.router, routes_datasets.router):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
