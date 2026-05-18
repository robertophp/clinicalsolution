from functools import lru_cache
import os
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    PROJECT_ID: str
    LOCATION: str = "us-central1"
    TWILIO_AUTH: Optional[str] = None

    # Ruta al JSON de cuenta de servicio GCP (Vertex AI, Firestore). Resuelta desde el cwd al arrancar.
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = None

    # Conversation memory (Firestore)
    CONVERSATION_TTL_MINUTES: int = 30  # Messages older than this are not used as context (inactivity)
    CONVERSATION_MAX_HISTORY: int = 5   # Max messages to send to Gemini as context
    CONVERSATION_MAX_STORED: int = 20   # Max messages to keep per user per clinic (trim older)
    # Firestore: si no está en el entorno del proceso, se elige por APP_ENV
    # (development → agentmemory, production → agentmemory-prod). Ver domain/runtime_env.py.
    FIRESTORE_DATABASE_ID: Optional[str] = None

    # BigQuery: dataset de la tabla citas. Si no está en el entorno, se elige por APP_ENV
    # (development → clinica_datos, production → clinica_datos_prod).
    BIGQUERY_DATASET: str = "clinica_datos"

    # Cloud Scheduler (u otro job): POST /jobs/sync-calendar-to-bigquery?token=...
    # Si no está definido, el endpoint rechaza todas las peticiones (401).
    SCHEDULER_SYNC_SECRET: Optional[str] = None

    # WhatsApp Cloud API (Meta) — webhook y Graph API
    META_APP_SECRET: Optional[str] = None
    META_WABA_ID: Optional[str] = None
    META_WHATSAPP_ACCESS_TOKEN: Optional[str] = None
    META_WEBHOOK_VERIFY_TOKEN: Optional[str] = None
    META_GRAPH_API_VERSION: str = "v21.0"
    # Solo desarrollo local: no validar firma (no usar en producción).
    META_WEBHOOK_SKIP_SIGNATURE_VERIFY: bool = False

    # Entorno de despliegue: en ``production`` / ``prod`` se valida configuración crítica al arrancar.
    APP_ENV: str = "development"

    # API key para ``POST /chat`` y rutas ``GET /health/gcp``, ``GET /health/meta``.
    # En producción debe estar definida (ver validación en ``create_app``). Si está vacía, esas rutas quedan abiertas (solo desarrollo).
    INTERNAL_API_KEY: Optional[str] = None

    # Vertex / Gemini: timeout por llamada a ``generate_content`` y reintentos solo en fallos transitorios.
    GEMINI_GENERATE_TIMEOUT_SECONDS: float = 60.0
    GEMINI_MAX_GENERATION_ATTEMPTS: int = 3  # primer intento + hasta 2 reintentos con backoff

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()


settings = get_settings()

# Aplicar credenciales GCP al entorno para que Firestore y Vertex AI las usen
if settings.GOOGLE_APPLICATION_CREDENTIALS:
    cred_path = Path(settings.GOOGLE_APPLICATION_CREDENTIALS).resolve()
    if cred_path.exists():
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred_path)
