"""
Señales de urgencia/dolor en el mensaje del paciente.

Usado en ``bootstrap`` para priorizar el flujo de citas (consultar_primer_dia_disponible)
sobre la derivación a especialista humano en el mismo turno.
"""
from __future__ import annotations

import re

# Palabras/frases que activan el flujo DOLOR/URGENCIA (ES + EN).
_URGENCY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\burgent[eo]s?\b",
        r"\burgencia\b",
        r"\bemergenc",
        r"\bemergency\b",
        r"\bdolor\b",
        r"\bme duele\b",
        r"\bme está doliendo\b",
        r"\bme esta doliendo\b",
        r"\btengo dolor\b",
        r"\bmolesti",
        r"\binflam",
        r"\bswelling\b",
        r"\bpain\b",
        r"\bhurt(s|ing)?\b",
        r"\b(atiend\w*|atend\w*|verme|ver)\w*\s+hoy\b",
        r"\bhoy\s+(es\s+)?urgent",
        r"\b(cita|turno|appointment)\s+(para\s+)?hoy\b",
        r"\bneed\s+(to\s+)?see\s+(you|a\s+dentist)\s+today\b",
        r"\btoday\b.*\burgent",
        r"\basap\b",
        r"\blo antes posible\b",
        r"\bcuanto antes\b",
    )
)


def message_signals_urgency(message: str) -> bool:
    """
    True si el mensaje actual sugiere dolor, urgencia o necesidad de cita hoy/ya.

    No mira historial: solo el texto del turno actual.
    """
    text = (message or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _URGENCY_PATTERNS)


__all__ = ["message_signals_urgency"]
