from __future__ import annotations

from datetime import date as date_type, datetime, time as time_type, timedelta, timezone

from ..database import SessionLocal
from ..repositories.cita_repository import count_activa_citas_at_slot
from ..schemas.clinic import ClinicConfig
from ..services.calendar_service import calendar_service


def max_appointments_per_slot_for_clinic(clinic: ClinicConfig) -> int:
    """Cupo por hora de inicio (site.json); mínimo 1."""
    raw = getattr(clinic, "max_appointments_per_slot", None)
    try:
        return max(1, int(raw if raw is not None else 1))
    except (TypeError, ValueError):
        return 1


def _timezone_el_salvador() -> timezone:
    """Zona horaria de El Salvador (UTC-6, sin horario de verano; alineada con calendario y BQ)."""
    return timezone(timedelta(hours=-6))


def _today_in_el_salvador() -> date_type:
    return datetime.now(_timezone_el_salvador()).date()


def _is_within_opening_hours(clinic: ClinicConfig, fecha: str, hora: str) -> bool:
    """
    Valida si la fecha (YYYY-MM-DD) y hora (HH:MM) caen dentro del horario de atención
    configurado para la clínica.
    """
    opening_hours = getattr(clinic, "opening_hours", None) or {}
    if not opening_hours:
        return True

    fecha = (fecha or "").strip()
    hora = (hora or "").strip()
    if not fecha or not hora:
        return False

    try:
        d = datetime.strptime(fecha, "%Y-%m-%d").date()
        t = datetime.strptime(hora, "%H:%M").time()
    except ValueError:
        return True

    if t.minute != 0 or t.second != 0:
        return False

    weekday_codes = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_code = weekday_codes[d.weekday()]

    slot_delta = timedelta(minutes=60)
    for block in opening_hours.values():
        days = block.get("days", [])
        start = (block.get("from") or "").strip()
        end = (block.get("to") or "").strip()
        if not days or not start or not end:
            continue
        if day_code not in days:
            continue
        try:
            start_t = datetime.strptime(start, "%H:%M").time()
            end_t = datetime.strptime(end, "%H:%M").time()
        except ValueError:
            continue
        start_dt = datetime.combine(d, start_t)
        end_dt = datetime.combine(d, end_t)
        t_dt = datetime.combine(d, t)
        if start_dt <= t_dt and (t_dt + slot_delta) <= end_dt:
            return True

    return False


def _opening_ranges_for_day(clinic: ClinicConfig, day: date_type) -> list[tuple[time_type, time_type]]:
    opening_hours = getattr(clinic, "opening_hours", None) or {}
    if not opening_hours:
        return []
    weekday_codes = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_code = weekday_codes[day.weekday()]
    ranges: list[tuple[time_type, time_type]] = []
    for block in opening_hours.values():
        days = block.get("days", [])
        start = (block.get("from") or "").strip()
        end = (block.get("to") or "").strip()
        if day_code not in days or not start or not end:
            continue
        try:
            start_t = datetime.strptime(start, "%H:%M").time()
            end_t = datetime.strptime(end, "%H:%M").time()
        except ValueError:
            continue
        ranges.append((start_t, end_t))
    return ranges


def _available_hourly_slots_for_clinic(clinic: ClinicConfig, fecha: str) -> list[str]:
    if not (clinic.calendar_sync_enabled and clinic.calendar_id):
        return []
    try:
        d = datetime.strptime((fecha or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return []
    opening_ranges = _opening_ranges_for_day(clinic, d)
    if not opening_ranges:
        return []
    cap = max_appointments_per_slot_for_clinic(clinic)
    slots = calendar_service.get_available_hourly_slots(
        calendar_id=clinic.calendar_id,
        day=d,
        opening_ranges=opening_ranges,
        slot_minutes=60,
        max_appointments_per_slot=cap,
    )
    return [t.strftime("%H:%M") for t in slots]


def _filter_slots_by_bq_capacity(
    clinic: ClinicConfig,
    fecha: str,
    hour_labels: list[str],
) -> list[str]:
    """Quita horas donde las citas activas en BQ ya alcanzaron el cupo (sin Calendar)."""
    cap = max_appointments_per_slot_for_clinic(clinic)
    if cap <= 0 or not hour_labels:
        return hour_labels
    cid = (clinic.id or "").strip()
    if not cid:
        return hour_labels
    db = SessionLocal()
    try:
        out: list[str] = []
        for h in hour_labels:
            try:
                n = count_activa_citas_at_slot(db, clinic_id=cid, fecha=fecha, hora=h)
            except Exception:
                n = 0
            if n < cap:
                out.append(h)
        return out
    finally:
        db.close()


def _theoretical_hourly_slots_for_clinic(clinic: ClinicConfig, fecha: str) -> list[str]:
    """Horario teórico en punto; si no hay Calendar, filtra por cupo según citas activas en BQ."""
    try:
        d = datetime.strptime((fecha or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return []
    opening_ranges = _opening_ranges_for_day(clinic, d)
    if not opening_ranges:
        return []
    slots = calendar_service.get_available_hourly_slots(
        calendar_id="",
        day=d,
        opening_ranges=opening_ranges,
        slot_minutes=60,
        use_calendar_busy=False,
        max_appointments_per_slot=max_appointments_per_slot_for_clinic(clinic),
    )
    labels = [t.strftime("%H:%M") for t in slots]
    if clinic.calendar_sync_enabled and clinic.calendar_id:
        return labels
    return _filter_slots_by_bq_capacity(clinic, fecha, labels)
