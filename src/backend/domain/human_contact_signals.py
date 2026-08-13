"""
Petición explícita de hablar con doctor/a, encargado/a u otro humano.

Prioridad en ``bootstrap`` sobre urgencia/dolor: deriva al especialista sin intentar agendar citas.
"""
from __future__ import annotations

import re

_HUMAN_CONTACT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bhablar\s+con\s+(el\s+|la\s+|un\s+)?(doctor|doctora|encargad|responsable|humano)\w*",
        r"\b(quiero|necesito|puedo|podr[ií]a)\s+hablar\s+con\b",
        r"\bcomunicar(me)?\s+con\s+(el\s+|la\s+|un\s+)?(doctor|doctora|encargad|responsable)",
        r"\b(me\s+)?comunic\w*\s+con\s+(el\s+|la\s+|un\s+)?(doctor|doctora|encargad|responsable)",
        r"\bpasar(me)?\s+con\s+(el\s+|la\s+|un\s+)?(doctor|doctora|encargad|responsable)",
        r"\bcontactar\s+con\s+(el\s+|la\s+|un\s+)?(doctor|doctora|encargad|responsable)",
        r"\bconectar(me)?\s+con\s+(un\s+)?(encargad|responsable|humano)\w*",
        r"\bpersona\s+real\b",
        r"\batenci[oó]n\s+humana\b",
        r"\bhablar\s+con\s+alguien\b",
        r"\bspeak\s+(to|with)\s+(the\s+)?(doctor|dentist|manager|supervisor|a\s+human)\b",
        r"\btalk\s+to\s+(the\s+)?(doctor|dentist|manager|supervisor|someone|a\s+person)\b",
        r"\b(i\s+)?need\s+to\s+speak\s+(to|with)\b",
        r"\b(i\s+)?want\s+to\s+speak\s+(to|with)\b",
        r"\bhuman\s+(agent|representative|staff)\b",
    )
)


def message_signals_human_contact_request(message: str) -> bool:
    """
    True si el paciente pide explícitamente contacto con personal humano (doctor, encargado, etc.).

    Solo el mensaje del turno actual; no usa historial.
    """
    text = (message or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _HUMAN_CONTACT_PATTERNS)


__all__ = ["message_signals_human_contact_request"]
