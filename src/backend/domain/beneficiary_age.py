"""
Resolución de edad del beneficiario para citas de terceros (pediátricas).

La edad se persiste en BigQuery y se muestra en Google Calendar sin depender de Gemini.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .pediatric_policy import extract_mentioned_age
from .urgency_calendar import _MENOR_SUFFIX_RE

MAX_BENEFICIARY_AGE = 18


def parse_edad_from_suffix_urgencia(raw: str | None) -> int | None:
    """Extrae años de un sufijo menor_X_anios."""
    s = (raw or "").strip().lower().replace("-", "_")
    if not s:
        return None
    m = _MENOR_SUFFIX_RE.match(s)
    if not m:
        return None
    age = int(m.group(1))
    if 0 <= age <= MAX_BENEFICIARY_AGE:
        return age
    return None


def parse_beneficiario_edad_arg(raw: Any) -> int | None:
    """Normaliza beneficiario_edad de args de agendar_cita."""
    if raw is None or isinstance(raw, bool):
        return None
    if not isinstance(raw, (int, float, str)):
        return None
    try:
        age = int(raw)
    except (TypeError, ValueError):
        return None
    if 0 <= age <= MAX_BENEFICIARY_AGE:
        return age
    return None


def extract_beneficiary_age_from_messages(
    messages: Sequence[Mapping[str, str]] | None,
) -> int | None:
    """Última edad mencionada por el usuario en el hilo reciente."""
    for msg in reversed(messages or []):
        if (msg.get("role") or "").strip().lower() != "user":
            continue
        age = extract_mentioned_age((msg.get("content") or "").strip())
        if age is not None:
            return age
    return None


def resolve_beneficiario_edad(
    *,
    args: Mapping[str, Any],
    es_para_tercero: bool,
    metadata: Mapping[str, Any] | None = None,
    chat_history: Sequence[Mapping[str, str]] | None = None,
) -> int | None:
    """
    Resuelve edad del beneficiario (solo citas para tercero).

    Prioridad: args.beneficiario_edad → args.suffix_urgencia → Firestore metadata → historial.
    """
    if not es_para_tercero:
        return None

    age = parse_beneficiario_edad_arg(args.get("beneficiario_edad"))
    if age is not None:
        return age

    age = parse_edad_from_suffix_urgencia(str(args.get("suffix_urgencia") or ""))
    if age is not None:
        return age

    if metadata:
        age = parse_beneficiario_edad_arg(metadata.get("beneficiario_edad"))
        if age is not None:
            return age

    return extract_beneficiary_age_from_messages(chat_history)


def minor_suffix_for_age(age: int | None) -> str | None:
    """Sufijo de calendario coherente con la edad resuelta."""
    if age is None or age < 0 or age > MAX_BENEFICIARY_AGE:
        return None
    return f"menor_{age}_anios"


__all__ = [
    "extract_beneficiary_age_from_messages",
    "minor_suffix_for_age",
    "parse_beneficiario_edad_arg",
    "parse_edad_from_suffix_urgencia",
    "resolve_beneficiario_edad",
]
