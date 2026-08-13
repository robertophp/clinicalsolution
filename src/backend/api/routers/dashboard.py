"""
Router del dashboard de métricas por clínica (solo lectura, multi-tenant).

- ``GET  /dashboard``            -> sirve la página (login + app).
- ``POST /dashboard/login``      -> valida credenciales y emite cookie de sesión firmada.
- ``POST /dashboard/logout``     -> limpia la cookie.
- ``GET  /dashboard/api/me``     -> info de sesión (clínica, rango por defecto, años).
- ``GET  /dashboard/api/summary``
- ``GET  /dashboard/api/timeseries``
- ``GET  /dashboard/api/by-service``

Todos los endpoints ``/api/*`` dependen de la sesión y filtran SIEMPRE por el
``clinic_id`` del token firmado (nunca por un parámetro del cliente).
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse

from ...config import settings
from ...repositories.metrics_repository import (
    MetricsRepository,
    default_date_range,
    normalize_granularity,
)
from ...services.dashboard_security import sign_session
from ...services.dashboard_users import DashboardUserRepository
from ..dashboard_auth import DASHBOARD_COOKIE_NAME, require_dashboard_session
from ..dashboard_page import render_dashboard_page

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

_user_repo = DashboardUserRepository()


def _clinic_name(clinic_id: str) -> str:
    from ...bootstrap import CLINICS_BY_ID

    cfg = CLINICS_BY_ID.get(clinic_id)
    return getattr(cfg, "name", None) or clinic_id


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return fallback


def _resolve_range(start: str | None, end: str | None) -> tuple[date, date]:
    default_start, default_end = default_date_range()
    s = _parse_date(start, default_start)
    e = _parse_date(end, default_end)
    if e < s:
        s, e = e, s
    return s, e


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page() -> HTMLResponse:
    return HTMLResponse(content=render_dashboard_page())


@router.post("/dashboard/login")
async def dashboard_login(payload: dict) -> JSONResponse:
    secret = (settings.DASHBOARD_SESSION_SECRET or "").strip()
    if not secret:
        return JSONResponse({"ok": False, "error": "not_configured"}, status_code=503)

    username = str((payload or {}).get("username") or "")
    password = str((payload or {}).get("password") or "")
    auth = _user_repo.authenticate(username, password)
    if not auth:
        return JSONResponse({"ok": False, "error": "invalid_credentials"}, status_code=401)

    token = sign_session(
        clinic_id=auth["clinic_id"],
        username=auth["username"],
        secret=secret,
        ttl_minutes=settings.DASHBOARD_SESSION_TTL_MIN,
    )
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key=DASHBOARD_COOKIE_NAME,
        value=token,
        max_age=settings.DASHBOARD_SESSION_TTL_MIN * 60,
        httponly=True,
        secure=bool(settings.DASHBOARD_COOKIE_SECURE),
        samesite="lax",
        path="/dashboard",
    )
    return resp


@router.post("/dashboard/logout")
async def dashboard_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(key=DASHBOARD_COOKIE_NAME, path="/dashboard")
    return resp


@router.get("/dashboard/api/me")
async def dashboard_me(clinic_id: str = Depends(require_dashboard_session)) -> JSONResponse:
    start, end = default_date_range()
    current_year = start.year
    years = list(range(current_year, current_year - 5, -1))
    return JSONResponse(
        {
            "clinic_id": clinic_id,
            "clinic_name": _clinic_name(clinic_id),
            "default_start": start.isoformat(),
            "default_end": end.isoformat(),
            "years": years,
        }
    )


@router.get("/dashboard/api/summary")
async def dashboard_summary(
    clinic_id: str = Depends(require_dashboard_session),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> JSONResponse:
    s, e = _resolve_range(start, end)
    try:
        data = MetricsRepository().get_summary(clinic_id=clinic_id, start=s, end=e)
    except Exception:
        logger.exception("Error consultando summary del dashboard (clinic=%s)", clinic_id)
        return JSONResponse({"ok": False, "error": "query_failed"}, status_code=502)
    return JSONResponse({"ok": True, "start": s.isoformat(), "end": e.isoformat(), "data": data})


@router.get("/dashboard/api/timeseries")
async def dashboard_timeseries(
    clinic_id: str = Depends(require_dashboard_session),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    granularity: str = Query(default="day"),
) -> JSONResponse:
    s, e = _resolve_range(start, end)
    g = normalize_granularity(granularity)
    try:
        data = MetricsRepository().get_timeseries(clinic_id=clinic_id, start=s, end=e, granularity=g)
    except Exception:
        logger.exception("Error consultando timeseries del dashboard (clinic=%s)", clinic_id)
        return JSONResponse({"ok": False, "error": "query_failed"}, status_code=502)
    return JSONResponse(
        {"ok": True, "start": s.isoformat(), "end": e.isoformat(), "granularity": g, "data": data}
    )


@router.get("/dashboard/api/by-service")
async def dashboard_by_service(
    clinic_id: str = Depends(require_dashboard_session),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> JSONResponse:
    s, e = _resolve_range(start, end)
    try:
        data = MetricsRepository().get_by_service(clinic_id=clinic_id, start=s, end=e)
    except Exception:
        logger.exception("Error consultando by-service del dashboard (clinic=%s)", clinic_id)
        return JSONResponse({"ok": False, "error": "query_failed"}, status_code=502)
    return JSONResponse({"ok": True, "start": s.isoformat(), "end": e.isoformat(), "data": data})
