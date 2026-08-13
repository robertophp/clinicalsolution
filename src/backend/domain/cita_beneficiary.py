"""
Titular del WhatsApp vs. beneficiario de la cita (quien asiste).

Firestore ``patient_name`` = contacto/titular (saludos).
BigQuery ``paciente_nombre`` = titular en la fila de cita (opcional si es_para_tercero).
BigQuery ``nombre_secundario`` = beneficiario cuando es_para_tercero=true.
"""
from __future__ import annotations

from typing import Any, Mapping


def parse_es_para_tercero(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"true", "1", "yes", "si", "sí"}
    return False


def resolve_booking_fields_from_args(args: Mapping[str, Any]) -> dict[str, Any]:
    """
    Mapea args de agendar_cita a campos de BigQuery.

    ``nombre`` = siempre el beneficiario (quien asiste).
    ``nombre_titular`` = opcional; titular del teléfono si es_para_tercero.
    """
    beneficiary = (args.get("nombre") or "").strip()
    es_para_tercero = parse_es_para_tercero(args.get("es_para_tercero"))
    titular = (args.get("nombre_titular") or "").strip() or None

    if es_para_tercero:
        return {
            "es_para_tercero": True,
            "paciente_nombre": titular,
            "nombre_secundario": beneficiary,
            "beneficiary_display": beneficiary,
        }
    return {
        "es_para_tercero": False,
        "paciente_nombre": beneficiary,
        "nombre_secundario": None,
        "beneficiary_display": beneficiary,
    }


def cita_es_para_tercero(cita: Any) -> bool:
    return bool(getattr(cita, "es_para_tercero", False))


def cita_attendee_display_name(cita: Any) -> str:
    """Nombre de quien asiste a la cita (Calendar, confirmaciones, listados)."""
    if cita_es_para_tercero(cita):
        sec = (getattr(cita, "nombre_secundario", None) or "").strip()
        if sec:
            return sec
    primary = (getattr(cita, "paciente_nombre", None) or "").strip()
    if primary and primary.lower() not in {"sin nombre", "paciente sin nombre"}:
        return primary
    sec = (getattr(cita, "nombre_secundario", None) or "").strip()
    return sec or "Sin nombre"


def cita_contact_display_name(cita: Any) -> str | None:
    """Titular del WhatsApp registrado en la fila, si aplica."""
    if not cita_es_para_tercero(cita):
        return None
    t = (getattr(cita, "paciente_nombre", None) or "").strip()
    return t or None


__all__ = [
    "cita_attendee_display_name",
    "cita_contact_display_name",
    "cita_es_para_tercero",
    "parse_es_para_tercero",
    "resolve_booking_fields_from_args",
]
