from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..config import settings
from ..domain.runtime_env import (
    effective_bigquery_dataset,
    effective_firestore_database_id,
)
from .routers import chat, dashboard, health, jobs, whatsapp_meta, whatsapp_twilio
from .startup_checks import validate_production_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    validate_production_settings()
    logger.info(
        "Runtime: APP_ENV=%s BigQuery dataset=%s Firestore database=%s",
        settings.APP_ENV,
        effective_bigquery_dataset(
            app_env=settings.APP_ENV,
            configured=settings.BIGQUERY_DATASET,
        ),
        effective_firestore_database_id(
            app_env=settings.APP_ENV,
            configured=settings.FIRESTORE_DATABASE_ID,
        ),
    )
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Clinica Assistant Agent", version="0.1.0", lifespan=_lifespan)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(whatsapp_twilio.router)
    app.include_router(whatsapp_meta.router)
    app.include_router(jobs.router)
    app.include_router(dashboard.router)
    return app
