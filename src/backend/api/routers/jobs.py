from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from ...bootstrap import CLINICS_BY_ID
from ...config import settings
from ...database import SessionLocal

router = APIRouter(tags=["jobs"])


@router.post("/jobs/sync-calendar-to-bigquery", response_model=None)
async def job_sync_calendar_to_bigquery(
    token: str = Query("", description="Debe coincidir con SCHEDULER_SYNC_SECRET en .env"),
):
    """
    Job HTTP para Cloud Scheduler: reconcilia Google Calendar → BigQuery
    (citas activas con ``calendar_event_id`` del bot).

    Configura ``SCHEDULER_SYNC_SECRET`` y llama con ``?token=...``.
    """
    expected = (settings.SCHEDULER_SYNC_SECRET or "").strip()
    if not expected or token != expected:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    pairs = [
        (cid, (getattr(cfg, "calendar_id", None) or "").strip())
        for cid, cfg in CLINICS_BY_ID.items()
        if getattr(cfg, "calendar_sync_enabled", False) and (getattr(cfg, "calendar_id", None) or "").strip()
    ]

    db = SessionLocal()
    try:
        from ...services.calendar_sync_service import run_calendar_to_bigquery_sync

        result = run_calendar_to_bigquery_sync(db, clinic_calendar_pairs=pairs)
    finally:
        db.close()

    return result
