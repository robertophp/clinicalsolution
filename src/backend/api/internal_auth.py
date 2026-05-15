"""Protección opcional por API key para rutas internas (/chat, diagnósticos /health/*)."""

from __future__ import annotations

from fastapi import Header, HTTPException

from ..config import settings


def require_internal_api_key(
    authorization: str | None = Header(None),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
) -> None:
    """
    Si ``INTERNAL_API_KEY`` está definido en entorno, exige coincidencia vía:

    - ``Authorization: Bearer <token>``, o
    - ``X-API-Key: <token>``

    Si la variable no está definida o está vacía, no se aplica control (modo desarrollo local).
    """
    expected = (settings.INTERNAL_API_KEY or "").strip()
    if not expected:
        return
    token: str | None = None
    if authorization:
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token and x_api_key:
        token = (x_api_key or "").strip()
    if not token or token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
