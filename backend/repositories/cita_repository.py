"""
Repositorio de citas: insert en BigQuery (tabla clinica_datos.citas).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..database import Cita


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


def create_cita(
    db: Session,
    *,
    clinic_id: str,
    paciente_nombre: str,
    telefono: str,
    fecha: str,
    hora: str,
    sintoma: str | None = None,
) -> Cita:
    """
    Inserta una cita en BigQuery (tabla clinica_datos.citas).
    Esquema: paciente_nombre, telefono, fecha_cita (DATE), hora_cita (TIME), clinica_id, status, creado_en.
    """
    dt = _parse_fecha_hora(fecha, hora)
    cita = Cita(
        clinic_id=clinic_id,
        paciente_nombre=(paciente_nombre or "").strip() or "Sin nombre",
        telefono=(telefono or "").strip() or "Sin teléfono",
        fecha_cita=dt.date(),
        hora_cita=dt.time(),
        status="agendada",
        timestamp=datetime.now(timezone.utc),
    )
    db.add(cita)
    db.commit()
    return cita
