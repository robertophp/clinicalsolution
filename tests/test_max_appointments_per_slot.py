"""Cupo de citas por hora de inicio (site.json + availability)."""

from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from backend.domain.availability import max_appointments_per_slot_for_clinic
from backend.schemas.clinic import ClinicConfig
from backend.services.calendar_service import CalendarService


def test_max_appointments_per_slot_for_clinic_defaults_to_one():
    clinic = ClinicConfig(
        id="x",
        name="X",
        system_prompt="p",
    )
    assert max_appointments_per_slot_for_clinic(clinic) == 1


def test_max_appointments_per_slot_for_clinic_reads_value():
    clinic = ClinicConfig(
        id="x",
        name="X",
        system_prompt="p",
        max_appointments_per_slot=5,
    )
    assert max_appointments_per_slot_for_clinic(clinic) == 5


def test_five_overlapping_events_fill_slot_when_cap_is_five():
    svc = CalendarService()
    day = date(2026, 4, 20)
    opening = [(time(9, 0), time(12, 0))]
    tz_sv = timezone(timedelta(hours=-6))
    busy = [
        (datetime(2026, 4, 20, 10, 0, tzinfo=tz_sv), datetime(2026, 4, 20, 11, 0, tzinfo=tz_sv))
        for _ in range(5)
    ]
    with patch.object(svc, "list_busy_intervals", return_value=busy):
        slots = svc.get_available_hourly_slots(
            calendar_id="c@x",
            day=day,
            opening_ranges=opening,
            max_appointments_per_slot=5,
        )
    assert "10:00" not in [s.strftime("%H:%M") for s in slots]
