from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    PROJECT_ID: str
    LOCATION: str = "us-central1"
    TWILIO_AUTH: Optional[str] = None

    # Conversation memory (Firestore)
    CONVERSATION_TTL_MINUTES: int = 30  # Messages older than this are not used as context (inactivity)
    CONVERSATION_MAX_HISTORY: int = 5   # Max messages to send to Gemini as context
    CONVERSATION_MAX_STORED: int = 20   # Max messages to keep per user per clinic (trim older)

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

