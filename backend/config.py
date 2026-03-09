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
    # Si creaste una base de datos Firestore con nombre (ej. "agentmemory"), pon su ID aquí. Si usas la base por defecto, déjalo vacío.
    FIRESTORE_DATABASE_ID: Optional[str] = None

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
