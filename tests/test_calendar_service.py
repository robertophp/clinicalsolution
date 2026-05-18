from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import MagicMock, patch

from backend.services.calendar_service import CalendarService, CALENDAR_SUMMARY_MAX_LEN


def test_get_available_hourly_slots_only_returns_hourly_starts():
    svc = CalendarService()
    day = date(2026, 4, 20)
    opening = [(time(8, 0), time(12, 0))]
    tz_sv = timezone(timedelta(hours=-6))
    busy = [
        (
            datetime(2026, 4, 20, 9, 0, tzinfo=tz_sv),
            datetime(2026, 4, 20, 10, 0, tzinfo=tz_sv),
        )
    ]
    with patch.object(svc, "list_busy_intervals", return_value=busy):
        slots = svc.get_available_hourly_slots(
            calendar_id="demo@calendar",
            day=day,
            opening_ranges=opening,
            slot_minutes=60,
        )
    assert [s.strftime("%H:%M") for s in slots] == ["08:00", "10:00", "11:00"]


def test_get_available_hourly_slots_respects_close_boundary():
    svc = CalendarService()
    day = date(2026, 4, 20)
    # Abre 08:00 y cierra 17:00 => última cita válida: 16:00.
    opening = [(time(8, 0), time(17, 0))]
    with patch.object(svc, "list_busy_intervals", return_value=[]):
        slots = svc.get_available_hourly_slots(
            calendar_id="demo@calendar",
            day=day,
            opening_ranges=opening,
            slot_minutes=60,
        )
    hhmm = [s.strftime("%H:%M") for s in slots]
    assert "16:00" in hhmm
    assert "17:00" not in hhmm


def test_get_available_hourly_slots_without_calendar_busy_skips_api():
    svc = CalendarService()
    day = date(2026, 4, 20)
    opening = [(time(8, 0), time(12, 0))]
    with patch.object(svc, "list_busy_intervals") as mock_list:
        slots = svc.get_available_hourly_slots(
            calendar_id="any@id",
            day=day,
            opening_ranges=opening,
            slot_minutes=60,
            use_calendar_busy=False,
        )
    mock_list.assert_not_called()
    assert [s.strftime("%H:%M") for s in slots] == ["08:00", "09:00", "10:00", "11:00"]


def test_truncate_calendar_summary_caps_length():
    long = "A" * (CALENDAR_SUMMARY_MAX_LEN + 80)
    out = CalendarService._truncate_calendar_summary(long)
    assert len(out) == CALENDAR_SUMMARY_MAX_LEN
    assert out.endswith("…")


def test_build_event_payload_includes_suffix_in_summary():
    cita = MagicMock()
    cita.fecha_cita = date(2026, 6, 1)
    cita.hora_cita = time(10, 0)
    cita.paciente_nombre = "María Pérez"
    cita.razon_cita = "evaluacion"
    cita.telefono = "+50370000000"
    cita.clinic_id = "demo_clinic_1"
    body = CalendarService._build_event_payload(
        cita=cita,
        clinic_name="Clínica",
        assistant_name="Bot",
        servicio_display="Evaluación",
        calendar_suffix="dolor post cita",
    )
    assert "María Pérez" in body["summary"]
    assert "Evaluación" in body["summary"]
    assert "dolor post cita" in body["summary"]
    assert "evaluacion" in body["description"]
