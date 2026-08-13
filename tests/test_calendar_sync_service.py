"""Tests unitarios del módulo calendar_sync_service (sin BigQuery real)."""

from datetime import date, time
from unittest.mock import MagicMock, patch

from backend.services.calendar_sync_service import sync_clinic_calendar_to_bigquery


def test_sync_marks_cancelled_when_event_missing():
    db = MagicMock()
    cita = MagicMock()
    cita.calendar_id = "cal@x"
    cita.calendar_event_id = "ev_missing"
    cita.fecha_cita = date(2025, 6, 1)
    cita.hora_cita = time(10, 0)

    with patch(
        "backend.services.calendar_sync_service.list_activa_citas_with_calendar_link",
        return_value=[cita],
    ), patch(
        "backend.services.calendar_sync_service.calendar_service.get_event",
        return_value=None,
    ), patch(
        "backend.services.calendar_sync_service.update_cita_status"
    ) as mock_status:
        result = sync_clinic_calendar_to_bigquery(
            db,
            clinic_id="demo_clinic_1",
            default_calendar_id="cal@x",
        )

    assert result["examined"] == 1
    assert result["marked_cancelled"] == 1
    assert result["datetime_updates"] == 0
    mock_status.assert_called_once()


def test_sync_updates_datetime_when_calendar_changed():
    db = MagicMock()
    cita = MagicMock()
    cita.calendar_id = ""
    cita.calendar_event_id = "ev1"
    cita.fecha_cita = date(2025, 6, 1)
    cita.hora_cita = time(10, 0)

    event = {
        "status": "confirmed",
        "start": {"dateTime": "2025-06-02T14:30:00-06:00", "timeZone": "America/El_Salvador"},
    }

    with patch(
        "backend.services.calendar_sync_service.list_activa_citas_with_calendar_link",
        return_value=[cita],
    ), patch(
        "backend.services.calendar_sync_service.calendar_service.get_event",
        return_value=event,
    ), patch(
        "backend.services.calendar_sync_service.update_cita_fecha_hora_from_calendar"
    ) as mock_upd:
        result = sync_clinic_calendar_to_bigquery(
            db,
            clinic_id="demo_clinic_1",
            default_calendar_id="default@cal",
        )

    assert result["datetime_updates"] == 1
    mock_upd.assert_called_once()
    call_kw = mock_upd.call_args.kwargs
    assert call_kw["fecha_cita"] == date(2025, 6, 2)
    assert (call_kw["hora_cita"].hour, call_kw["hora_cita"].minute) == (14, 30)
