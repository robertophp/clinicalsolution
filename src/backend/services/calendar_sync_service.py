"""
Reconciliación Google Calendar → BigQuery (polling, p. ej. Cloud Scheduler).

Solo citas **activas** creadas por el bot con ``calendar_event_id`` rellenado.
- Evento borrado o ``status=cancelled`` en Calendar → marca la cita como cancelada en BQ.
- Cambio de inicio del evento → actualiza ``fecha_cita`` / ``hora_cita`` en BQ.

No importa ``main`` ni la config de clínicas: recibe pares ``(clinic_id, calendar_id)`` desde el caller.
"""
from __future__ import annotations

import logging
from datetime import date, time
from typing import Any

from sqlalchemy.orm import Session

from ..database import Cita
from ..repositories.cita_repository import (
    CITA_STATUS_CANCELADA,
    list_activa_citas_with_calendar_link,
    update_cita_fecha_hora_from_calendar,
    update_cita_status,
)
from .calendar_service import CalendarServiceError, calendar_service

logger = logging.getLogger(__name__)


def _hora_minuto(t: time | None) -> tuple[int, int]:
    if t is None:
        return (-1, -1)
    return (t.hour, t.minute)


def _fecha_hora_cambio(cita: Cita, nueva_fecha: date, nueva_hora: time) -> bool:
    if cita.fecha_cita != nueva_fecha:
        return True
    return _hora_minuto(cita.hora_cita) != _hora_minuto(nueva_hora)


def sync_clinic_calendar_to_bigquery(
    db: Session,
    *,
    clinic_id: str,
    default_calendar_id: str,
) -> dict[str, Any]:
    """
    Para una clínica con Calendar habilitado: alinea citas activas enlazadas por ``calendar_event_id``.

    ``default_calendar_id`` es el de configuración; cada fila puede tener ``calendar_id`` propio.
    """
    default_calendar_id = (default_calendar_id or "").strip()
    out: dict[str, Any] = {
        "clinic_id": clinic_id,
        "examined": 0,
        "datetime_updates": 0,
        "marked_cancelled": 0,
        "errors": 0,
        "unchanged": 0,
    }
    if not default_calendar_id:
        return out

    citas = list_activa_citas_with_calendar_link(db, clinic_id=clinic_id)
    out["examined"] = len(citas)

    for cita in citas:
        cal_id = (cita.calendar_id or "").strip() or default_calendar_id
        eid = (cita.calendar_event_id or "").strip()
        if not eid:
            continue

        try:
            event = calendar_service.get_event(calendar_id=cal_id, event_id=eid)
        except CalendarServiceError as exc:
            out["errors"] += 1
            logger.warning(
                "calendar_sync: error leyendo evento clinic=%s event=%s: %s",
                clinic_id,
                eid,
                exc,
            )
            continue

        if event is None:
            update_cita_status(db, cita, CITA_STATUS_CANCELADA)
            out["marked_cancelled"] += 1
            logger.info(
                "calendar_sync: evento ausente → cita cancelada en BQ clinic=%s event=%s",
                clinic_id,
                eid,
            )
            continue

        if (event.get("status") or "").lower() == "cancelled":
            update_cita_status(db, cita, CITA_STATUS_CANCELADA)
            out["marked_cancelled"] += 1
            logger.info(
                "calendar_sync: evento cancelled en Calendar → BQ clinic=%s event=%s",
                clinic_id,
                eid,
            )
            continue

        parsed = calendar_service.event_start_to_sv_date_time(event)
        if not parsed:
            out["errors"] += 1
            logger.warning(
                "calendar_sync: no se pudo interpretar start del evento clinic=%s event=%s",
                clinic_id,
                eid,
            )
            continue

        new_date, new_time = parsed
        if _fecha_hora_cambio(cita, new_date, new_time):
            update_cita_fecha_hora_from_calendar(
                db,
                cita,
                fecha_cita=new_date,
                hora_cita=new_time,
            )
            out["datetime_updates"] += 1
            logger.info(
                "calendar_sync: fecha/hora actualizada desde Calendar clinic=%s event=%s",
                clinic_id,
                eid,
            )
        else:
            out["unchanged"] += 1

    return out


def run_calendar_to_bigquery_sync(
    db: Session,
    *,
    clinic_calendar_pairs: list[tuple[str, str]],
) -> dict[str, Any]:
    """
    Ejecuta la reconciliación para cada par ``(clinic_id, calendar_id)`` (solo clínicas con sync activo).
    """
    details: list[dict[str, Any]] = []
    totals = {
        "clinics_processed": 0,
        "examined": 0,
        "datetime_updates": 0,
        "marked_cancelled": 0,
        "errors": 0,
        "unchanged": 0,
    }

    for clinic_id, cal_id in clinic_calendar_pairs:
        cid = (clinic_id or "").strip()
        if not cid or not (cal_id or "").strip():
            continue
        row = sync_clinic_calendar_to_bigquery(db, clinic_id=cid, default_calendar_id=cal_id)
        details.append(row)
        totals["clinics_processed"] += 1
        totals["examined"] += int(row.get("examined") or 0)
        totals["datetime_updates"] += int(row.get("datetime_updates") or 0)
        totals["marked_cancelled"] += int(row.get("marked_cancelled") or 0)
        totals["errors"] += int(row.get("errors") or 0)
        totals["unchanged"] += int(row.get("unchanged") or 0)

    return {"ok": True, "totals": totals, "by_clinic": details}
