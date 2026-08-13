from __future__ import annotations

import re

# Flujo dolor / urgencia: sufijo corto en Google Calendar (solo con servicio evaluacion).
URGENCY_CALENDAR_SUFFIX_KEYS = frozenset({"dolor_post_cita", "dolor_intenso"})
URGENCY_CALENDAR_SUFFIX_LABEL_ES = {
    "dolor_post_cita": "dolor post cita",
    "dolor_intenso": "dolor intenso",
}
EVALUACION_ID_SIMPLE = "evaluacion"

# Patrón para sufijos de citas pediátricas: menor_X_anios (cualquier servicio)
_MENOR_SUFFIX_RE = re.compile(r"^menor[_\s](\d{1,2})[_\s]a(?:n|ñ)io?s?$", re.IGNORECASE)


def _normalize_suffix_urgencia_param(raw: str | None) -> str | None:
    """Normaliza suffix_urgencia de la herramienta a una clave interna."""
    s = (raw or "").strip().lower().replace("-", "_")
    if s in URGENCY_CALENDAR_SUFFIX_KEYS:
        return s
    # Pasar sufijos pediátricos tal cual para que calendar_service los detecte
    if _MENOR_SUFFIX_RE.match(s):
        return s
    return None


def _calendar_suffix_label_for_cita(servicio_id: str, args: dict) -> str | None:
    """
    Texto corto para el evento en Calendar.

    - Flujos de dolor: solo con servicio=evaluacion.
    - Flujos pediátricos (menor_X_anios): aplica con cualquier servicio.
    """
    raw = args.get("suffix_urgencia")
    key = _normalize_suffix_urgencia_param(raw)
    if not key:
        return None

    # Sufijo pediátrico → devolver tal cual (calendar_service lo interpreta)
    if _MENOR_SUFFIX_RE.match(key):
        return key

    # Sufijos de urgencia/dolor → solo para evaluacion
    if (servicio_id or "").strip() != EVALUACION_ID_SIMPLE:
        return None
    return URGENCY_CALENDAR_SUFFIX_LABEL_ES.get(key)
