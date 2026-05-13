"""
Repositorio de citas: insert/update en BigQuery (tabla clinica_datos.citas).
Estados de cita: activa (por defecto), cancelada, reagendada.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session

from ..database import Cita

# Estados de la cita en la tabla
CITA_STATUS_ACTIVA = "activa"       # cita en pie (default para citas nuevas)
CITA_STATUS_CANCELADA = "cancelada"  # el cliente la canceló
CITA_STATUS_REAGENDADA = "reagendada"  # se reagendó; la nueva cita queda activa


def _parse_fecha_hora(fecha: str, hora: str) -> datetime:
    """Combina fecha (YYYY-MM-DD) y hora (HH:MM o HH:MM:SS) en un datetime."""
    fecha = (fecha or "").strip()
    hora = (hora or "").strip()
    if not fecha or not hora:
        raise ValueError("fecha y hora son obligatorios")
    try:
        d = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        try:
            d = datetime.strptime(fecha, "%d/%m/%Y").date()
        except ValueError:
            raise ValueError(f"Formato de fecha no válido: {fecha}. Usa YYYY-MM-DD.")
    try:
        t = datetime.strptime(hora, "%H:%M").time()
    except ValueError:
        try:
            t = datetime.strptime(hora, "%H:%M:%S").time()
        except ValueError:
            raise ValueError(f"Formato de hora no válido: {hora}. Usa HH:MM.")
    return datetime.combine(d, t)


def _now_el_salvador() -> datetime:
    """
    Devuelve la fecha/hora actual en la zona horaria de El Salvador (UTC-6).

    Se usa para el campo creado_en de la tabla, de modo que refleje la hora local
    de la clínica en El Salvador en lugar de UTC.
    """
    tz_salvador = timezone(timedelta(hours=-6))
    return datetime.now(tz_salvador)


def create_cita(
    db: Session,
    *,
    clinic_id: str,
    paciente_nombre: str,
    telefono: str,
    fecha: str,
    hora: str,
    razon_cita: str | None = None,
    status: str | None = None,
    origen_reserva: str | None = None,
    agendado_por: str | None = None,
) -> Cita:
    """
    Inserta una cita en BigQuery (tabla clinica_datos.citas).
    Por defecto status=activa. Esquema: paciente_nombre, telefono, fecha_cita (DATE), hora_cita (TIME), razon_cita (servicio), clinica_id, status, creado_en.
    """
    dt = _parse_fecha_hora(fecha, hora)
    cita = Cita(
        clinic_id=clinic_id,
        paciente_nombre=(paciente_nombre or "").strip() or "Sin nombre",
        telefono=(telefono or "").strip() or "Sin teléfono",
        fecha_cita=dt.date(),
        hora_cita=dt.time(),
        razon_cita=(razon_cita or "").strip() or None,
        status=(status or "").strip() or CITA_STATUS_ACTIVA,
        origen_reserva=(origen_reserva or "").strip() or None,
        agendado_por=(agendado_por or "").strip() or None,
        timestamp=_now_el_salvador(),
    )
    db.add(cita)
    db.commit()
    return cita


def update_cita_status(db: Session, cita: Cita, new_status: str) -> Cita:
    """
    Actualiza el estado de una cita (ej. cancelada, reagendada).
    Útil para: cancelar una cita o marcar la anterior como reagendada al crear una nueva.
    """
    cita.status = (new_status or "").strip() or CITA_STATUS_ACTIVA
    db.add(cita)
    db.commit()
    db.refresh(cita)
    return cita


def get_latest_cita_for_phone(
    db: Session,
    *,
    clinic_id: str,
    telefono: str,
) -> Cita | None:
    """
    Devuelve la cita más reciente (por creado_en) para un paciente (teléfono) en una clínica dada.
    Se usa para recuperar el nombre del paciente cuando ya tuvo citas previas.
    """
    tel = (telefono or "").strip()
    if not tel:
        return None

    return (
        db.query(Cita)
        .filter(Cita.clinic_id == clinic_id, Cita.telefono == tel)
        .order_by(Cita.timestamp.desc())
        .first()
    )


def list_activa_citas_with_calendar_link(
    db: Session,
    *,
    clinic_id: str,
) -> list[Cita]:
    """
    Citas activas del bot con vínculo a Google Calendar (calendar_event_id rellenado).
    Base para reconciliar Calendar → BigQuery (solo citas originadas por el asistente).
    """
    cid = (clinic_id or "").strip()
    if not cid:
        return []

    return (
        db.query(Cita)
        .filter(
            Cita.clinic_id == cid,
            Cita.status == CITA_STATUS_ACTIVA,
            Cita.calendar_event_id.isnot(None),
            Cita.calendar_event_id != "",
        )
        .all()
    )


def update_cita_fecha_hora_from_calendar(
    db: Session,
    cita: Cita,
    *,
    fecha_cita: date,
    hora_cita: time,
) -> Cita:
    """Actualiza fecha/hora de la cita tras detectar cambio en Google Calendar."""
    cita.fecha_cita = fecha_cita
    cita.hora_cita = hora_cita.replace(microsecond=0)
    cita.sync_status = "ok"
    cita.sync_error_message = None
    db.add(cita)
    db.commit()
    db.refresh(cita)
    return cita


def get_latest_activa_cita_for_phone(
    db: Session,
    *,
    clinic_id: str,
    telefono: str,
) -> Cita | None:
    """
    Devuelve la cita activa más reciente para este teléfono y clínica.
    Se usa para cancelar o reagendar (solo se cancela/reagenda la cita en pie).
    """
    tel = (telefono or "").strip()
    if not tel:
        return None

    return (
        db.query(Cita)
        .filter(
            Cita.clinic_id == clinic_id,
            Cita.telefono == tel,
            Cita.status == CITA_STATUS_ACTIVA,
        )
        .order_by(Cita.timestamp.desc())
        .first()
    )
