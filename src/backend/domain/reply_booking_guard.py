"""
Evita respuestas al paciente que afirman cita guardada/reagendada/cancelada sin que
el backend haya ejecutado con éxito la herramienta correspondiente (hallucinación del modelo).
"""

from __future__ import annotations

import re


def claims_booking_saved_without_backend(text: str, *, language: str) -> bool:
    """
    True si el texto sugiere que la cita ya quedó en el sistema, sin ser un mensaje
    de error típico del backend.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    t = raw.lower()

    if "no pude agendar" in t or "couldn't schedule" in t or "i couldn't schedule" in t:
        return False
    if "no pude cancelar" in t or "no pude reagendar" in t:
        return False

    use_en = (language or "").strip().lower() == "en"
    if use_en:
        for pat in _PATTERNS_EN:
            if pat.search(t):
                return True
        return False

    for pat in _PATTERNS_ES:
        if pat.search(t):
            return True
    return False


def fallback_ask_explicit_confirm(language: str) -> str:
    if (language or "").strip().lower() == "en":
        return (
            "That change or appointment is **not saved in our system yet**. "
            "Please reply **yes** or **confirm** to the last message where I summarized **service, date and time** "
            "(a thank-you alone is not enough to register it). "
            "Or tell me if you want a different day or time."
        )
    return (
        "Ese cambio o esa cita **aún no está guardada en el sistema**. "
        "Responde por favor **sí** o **confirmo** al último mensaje donde te resumí **servicio, fecha y hora** "
        "(un mensaje solo de gracias o «muy amable» no basta para registrarla). "
        "O dime si prefieres otro día u hora."
    )


# Afirmaciones de “ya quedó” sin pasar por herramienta exitosa (es / en).
_PATTERNS_ES = (
    re.compile(r"ya\s+agend[eé]", re.I),
    re.compile(r"te\s+la\s+dej[eé]\s+agendada", re.I),
    re.compile(r"(he|hemos)\s+registrado\s+tu\s+cita", re.I),
    re.compile(r"¡\s*listo\s*!\s*he\s+agendado\s+tu\s+cita", re.I),
    re.compile(
        r"tu\s+cita.{0,80}\b(está|quedó|esta)\b.{0,40}\b(agendada|confirmada|lista|programada)\b",
        re.I | re.DOTALL,
    ),
    re.compile(r"ya\s+quedó.{0,60}\bcita\b", re.I | re.DOTALL),
    re.compile(
        r"con\s+gusto.{0,160}\b(he\s+)?(agendad[ao]|reagendad[ao]|agend[eé]|registrad[ao])",
        re.I | re.DOTALL,
    ),
    re.compile(r"ya\s+está\s+reagendad", re.I),
    re.compile(r"quedó\s+reagendad", re.I),
    re.compile(r"he\s+reagendad[oa]", re.I),
    re.compile(r"¡\s*listo\s*!\s*he\s+reagendad", re.I),
    re.compile(r"cita.{0,60}reagendad[ao]\b", re.I | re.DOTALL),
)

_PATTERNS_EN = (
    re.compile(r"i['']?ve\s+scheduled\s+your\s+appointment", re.I),
    re.compile(r"your\s+appointment\s+is\s+(now\s+)?(booked|set|confirmed|scheduled)", re.I),
    re.compile(r"all\s+set[!.]?.*appointment", re.I | re.DOTALL),
    re.compile(r"i['']?ve\s+rescheduled", re.I),
    re.compile(r"your\s+appointment\s+.{0,50}(has\s+been\s+)?reschedul", re.I | re.DOTALL),
    re.compile(r"it['']?s\s+been\s+reschedul", re.I),
)
