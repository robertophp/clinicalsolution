"""
Detección ligera: cita para titular vs. otra persona (refuerzo de prompt, no reemplaza al LLM).
"""
from __future__ import annotations

import re
import unicodedata


def _fold(text: str) -> str:
    t = (text or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


_BOOKING_FOR_OTHER_RE = re.compile(
    r"(?:"
    r"\bno\s+es\s+para\s+mi\b"
    r"|\bpara\s+(?:mi|m[ií])\s+(?:madre|mama|mamá|padre|papa|papá|herman[oa]|hij[oa]|espos[oa]|"
    r"marido|mujer|novi[oa]|abuel[oa]|t[ií]o|t[ií]a|sobrin[oa]|niet[oa]|primo|prima)\b"
    r"|\bpara\s+otra\s+persona\b"
    r"|\bfor\s+my\s+(?:mother|mom|father|dad|brother|sister|son|daughter|wife|husband)\b"
    r"|\bfor\s+someone\s+else\b"
    r"|\bnot\s+for\s+me\b"
    r")",
    re.IGNORECASE,
)

_BOOKING_FOR_SELF_RE = re.compile(
    r"(?:"
    r"\bpara\s+mi\b"
    r"|\bes\s+para\s+mi\b"
    r"|\bfor\s+me\b"
    r"|\bpara\s+m[ií]\s+mismo\b"
    r"|\bpara\s+m[ií]\s+misma\b"
    r")",
    re.IGNORECASE,
)


def message_signals_booking_for_other(message: str) -> bool:
    folded = _fold(message)
    if not folded:
        return False
    return bool(_BOOKING_FOR_OTHER_RE.search(folded))


def message_signals_booking_for_self(message: str) -> bool:
    folded = _fold(message)
    if not folded:
        return False
    if message_signals_booking_for_other(message):
        return False
    return bool(_BOOKING_FOR_SELF_RE.search(folded))


def build_booking_beneficiary_hint(
    message: str,
    *,
    language: str,
    stored_first_name: str | None,
) -> str | None:
    """Bloque opcional para inyectar en el system prompt del turno."""
    if message_signals_booking_for_other(message):
        if (language or "").startswith("en"):
            return (
                "[BOOKING FOR SOMEONE ELSE: The contact indicated this appointment is NOT for themselves. "
                "Ask for the full name of the person who will attend. Use agendar_cita with es_para_tercero=true, "
                "nombre=<beneficiary full name>, and nombre_titular only if you know the contact's name. "
                "Do NOT overwrite the contact profile with the beneficiary name.]"
            )
        return (
            "[CITA PARA OTRA PERSONA: El contacto indicó que la cita NO es para él/ella. "
            "Pide el nombre completo de quien asistirá. Usa agendar_cita con es_para_tercero=true, "
            "nombre=<nombre completo del beneficiario>, y nombre_titular solo si conoces el nombre del titular. "
            "NO confundas el nombre del beneficiario con el del titular del WhatsApp.]"
        )
    if message_signals_booking_for_self(message) and stored_first_name:
        if (language or "").startswith("en"):
            return (
                f"[BOOKING FOR SELF: The contact confirmed the appointment is for themselves. "
                f"If you already know them as {stored_first_name}, use es_para_tercero=false and their full name in nombre.]"
            )
        return (
            f"[CITA PARA SÍ MISMO: El contacto confirmó que la cita es para él/ella. "
            f"Si ya lo conoces como {stored_first_name}, usa es_para_tercero=false y su nombre completo en nombre.]"
        )
    return None


__all__ = [
    "build_booking_beneficiary_hint",
    "message_signals_booking_for_other",
    "message_signals_booking_for_self",
]
