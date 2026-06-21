"""Dependencia de sesión del dashboard: valida la cookie firmada y expone el clinic_id."""
from __future__ import annotations

from fastapi import Cookie, HTTPException

from ..config import settings
from ..services.dashboard_security import verify_session

DASHBOARD_COOKIE_NAME = "dashboard_session"


def require_dashboard_session(
    dashboard_session: str | None = Cookie(default=None),
) -> str:
    """
    Valida la cookie de sesión y devuelve el ``clinic_id`` autenticado.

    El ``clinic_id`` SIEMPRE proviene del token firmado en la cookie, nunca de un
    parámetro del cliente: así un usuario solo puede ver datos de su propia clínica.
    """
    secret = (settings.DASHBOARD_SESSION_SECRET or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Dashboard no configurado")
    payload = verify_session(dashboard_session or "", secret=secret)
    if not payload:
        raise HTTPException(status_code=401, detail="No autenticado")
    return str(payload["clinic_id"])


__all__ = ["require_dashboard_session", "DASHBOARD_COOKIE_NAME"]
