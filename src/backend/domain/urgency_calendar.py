from __future__ import annotations

# Flujo dolor / urgencia: sufijo corto en Google Calendar (solo con servicio evaluacion).
URGENCY_CALENDAR_SUFFIX_KEYS = frozenset({"dolor_post_cita", "dolor_intenso"})
URGENCY_CALENDAR_SUFFIX_LABEL_ES = {
    "dolor_post_cita": "dolor post cita",
    "dolor_intenso": "dolor intenso",
}
EVALUACION_ID_SIMPLE = "evaluacion"


def _normalize_suffix_urgencia_param(raw: str | None) -> str | None:
    """Normaliza suffix_urgencia de la herramienta a una clave interna."""
    s = (raw or "").strip().lower().replace("-", "_")
    if s in URGENCY_CALENDAR_SUFFIX_KEYS:
        return s
    return None


def _calendar_suffix_label_for_cita(servicio_id: str, args: dict) -> str | None:
    """
    Texto corto para el título del evento en Calendar.
    Solo aplica si servicio es evaluacion simple y suffix_urgencia es válido.
    """
    if (servicio_id or "").strip() != EVALUACION_ID_SIMPLE:
        return None
    key = _normalize_suffix_urgencia_param(args.get("suffix_urgencia"))
    if not key:
        return None
    return URGENCY_CALENDAR_SUFFIX_LABEL_ES.get(key)
