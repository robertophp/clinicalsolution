from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .routers import chat, health, jobs, whatsapp_meta, whatsapp_twilio
from .startup_checks import validate_production_settings


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    validate_production_settings()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Clinica Assistant Agent", version="0.1.0", lifespan=_lifespan)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(whatsapp_twilio.router)
    app.include_router(whatsapp_meta.router)
    app.include_router(jobs.router)
    return app
