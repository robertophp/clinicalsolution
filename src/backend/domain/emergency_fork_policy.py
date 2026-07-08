"""
Flujo de emergencia / dolor grave: elección entre cita mañana a primera hora o contacto del equipo médico.

Solo se activa con señales graves (``message_signals_severe_urgency``). Dolor leve o urgencia
rutinaria sigue el flujo normal de Gemini sin este paso intermedio.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal, Sequence

from ..schemas.clinic_policies import EmergencyForkPolicies

EmergencyChoice = Literal["appointment", "team_contact", "unclear"]

# Señales graves: emergencia explícita o dolor/urgencia con intensidad alta.
# NO incluye «dolor» o «molestia» leves sin intensificadores.
_SEVERE_URGENCY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bemergenc",
        r"\bemergency\b",
        r"\burgent[eo]s?\b",
        r"\burgencia\b",
        r"\b(muy|mucho|demasiado)\s+(dolor|duele|intenso|fuerte)",
        r"\bdolor\s+(muy|mucho|demasiado|intenso|fuerte|insoportable)",
        r"\bduele\s+mucho",
        r"\bdemasiado\s+intenso",
        r"\binsoportable",
        r"\bno\s+aguanto",
        r"\bsevere\s+pain",
        r"\bextreme\s+pain",
        r"\bunbearable",
        r"\b(asap|lo antes posible|cuanto antes)\b.*\b(dolor|pain|urgent)",
        r"\b(dolor|pain|urgent).*\b(asap|lo antes posible|cuanto antes)\b",
    )
)

_APPOINTMENT_RE = re.compile(
    r"\b("
    r"cita|agendar|agenda|mañana|manana|primera\s+hora|horario|horarios|evaluaci[oó]n|evaluacion|"
    r"turno|slot|appointment|book|schedule|"
    r"opci[oó]n\s*1|opcion\s*1|\b1\b"
    r")\b",
    re.IGNORECASE,
)

_TEAM_CONTACT_RE = re.compile(
    r"\b("
    r"equipo|m[eé]dico|medico|contacto|contacten|llamen|comuniquen|comunicar|"
    r"humano|doctor|doctora|encargad|especialista|"
    r"opci[oó]n\s*2|opcion\s*2|\b2\b|"
    r"que\s+me\s+llamen|me\s+contacten|me\s+comuniquen"
    r")\b",
    re.IGNORECASE,
)


def _fold(text: str) -> str:
    s = (text or "").strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def message_signals_severe_urgency(message: str) -> bool:
    """True si el mensaje sugiere emergencia o dolor/urgencia grave (no dolor leve)."""
    text = (message or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _SEVERE_URGENCY_PATTERNS)


def history_signals_severe_urgency(
    history: Sequence[dict[str, str]] | None,
) -> bool:
    if not history:
        return False
    for msg in history:
        content = (msg.get("content") or "").strip()
        if content and message_signals_severe_urgency(content):
            return True
    return False


def classify_emergency_choice_response(message: str) -> EmergencyChoice:
    """Clasifica la respuesta del paciente cuando está en fase awaiting_choice."""
    folded = _fold(message)
    if not folded:
        return "unclear"
    appt = bool(_APPOINTMENT_RE.search(folded))
    team = bool(_TEAM_CONTACT_RE.search(folded))
    if appt and not team:
        return "appointment"
    if team and not appt:
        return "team_contact"
    if appt and team:
        return "unclear"
    return "unclear"


def _policy_text(policy: EmergencyForkPolicies, field_es: str, field_en: str, language: str) -> str:
    use_en = (language or "").strip().lower().startswith("en")
    raw = getattr(policy, field_en if use_en else field_es, None)
    return (raw or "").strip()


def patient_prompt_emergency_choice(
    language: str,
    policy: EmergencyForkPolicies,
    *,
    first_name: str | None = None,
) -> str:
    custom = _policy_text(policy, "choice_prompt_es", "choice_prompt_en", language)
    if custom:
        return custom
    use_en = (language or "").strip().lower().startswith("en")
    name_bit = f", {first_name}" if first_name else ""
    if use_en:
        return (
            f"I'm so sorry you're going through this{name_bit} 🦷💛 How would you like us to help?\n\n"
            "📅 **Schedule an emergency evaluation tomorrow at the first available time**, or\n"
            "📞 **Share your case with our medical team** so they can contact you as soon as possible.\n\n"
            "Which works better for you?"
        )
    return (
        f"Lamento mucho que estés pasando por esto{name_bit} 🦷💛 ¿Cómo prefieres que te apoyemos?\n\n"
        "📅 **Agendarte una cita de emergencia mañana a primera hora**, o\n"
        "📞 **Compartir tu caso con nuestro equipo médico** para que te contacten a la brevedad.\n\n"
        "¿Cuál te viene mejor?"
    )


def patient_prompt_emergency_choice_unclear(
    language: str,
    policy: EmergencyForkPolicies,
) -> str:
    custom = _policy_text(policy, "choice_unclear_es", "choice_unclear_en", language)
    if custom:
        return custom
    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        return (
            "I want to make sure I help you the right way 🙂 "
            "Would you prefer 📅 an emergency appointment tomorrow at the first available time, "
            "or 📞 that our medical team contact you as soon as possible?"
        )
    return (
        "Quiero ayudarte de la mejor forma 🙂 "
        "¿Prefieres 📅 una cita de emergencia mañana a primera hora, "
        "o 📞 que nuestro equipo médico te contacte a la brevedad?"
    )


def patient_prompt_emergency_transfer_sent(
    language: str,
    policy: EmergencyForkPolicies,
    *,
    clinic_phone: str | None,
) -> str:
    custom = _policy_text(policy, "transfer_sent_es", "transfer_sent_en", language)
    phone = (clinic_phone or "").strip()
    if custom:
        if phone and phone not in custom:
            return f"{custom}\n\n📞 {phone}"
        return custom
    use_en = (language or "").strip().lower().startswith("en")
    phone_line = f"\n\nIf you prefer, you can also call us at **{phone}** 📞" if phone else ""
    if use_en:
        return (
            "Your message has been forwarded to our medical team ✅ "
            "Please stay alert — they will contact you as soon as possible."
            f"{phone_line}\n\n"
            "If you have any other questions, I'm happy to help 😊"
        )
    phone_line_es = f"\n\nSi lo prefieres, también puedes llamarnos al **{phone}** 📞" if phone else ""
    return (
        "Tu mensaje ya fue direccionado a nuestro equipo médico ✅ "
        "Queda pendiente de su contacto a la brevedad."
        f"{phone_line_es}\n\n"
        "Cualquier otra consulta, con gusto te ayudo 😊"
    )


def patient_prompt_emergency_followup(language: str) -> str:
    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        return (
            "You're welcome! If you need anything else about other services, prices, or general appointments, "
            "just write to me. I'm here for you."
        )
    return (
        "¡Con gusto! Si necesitas algo más sobre otros servicios, precios o citas generales, escríbeme. "
        "Quedo a la orden."
    )


def format_emergency_appointment_prompt_block(language: str) -> str:
    """Bloque extra cuando el paciente ya eligió cita de emergencia."""
    if (language or "").strip().lower().startswith("en"):
        return (
            "\n\n[EMERGENCY APPOINTMENT — PATIENT CHOSE FIRST AVAILABLE TOMORROW]\n"
            "- They already chose an emergency evaluation appointment tomorrow at the first available time.\n"
            "- Respond with brief empathy; do NOT ask again whether they want team contact or booking.\n"
            "- You MUST call `consultar_primer_dia_disponible` NOW in this turn before replying. "
            "Do NOT say you will look up availability — call the tool and show actual HH:00 slots.\n"
            "- Offer only `primeras_tres_horas` (up to three HH:00 starts).\n"
            "- Use `servicio`=`evaluacion` and `suffix_urgencia`=`dolor_intenso` when booking.\n"
        )
    return (
        "\n\n[EMERGENCIA — PACIENTE ELIGIÓ CITA MAÑANA A PRIMERA HORA]\n"
        "- El paciente ya eligió cita de evaluación de emergencia mañana a primera hora disponible.\n"
        "- Responde con empatía breve; NO vuelvas a preguntar si prefiere contacto del equipo o agendar.\n"
        "- DEBES llamar `consultar_primer_dia_disponible` AHORA en este turno antes de responder. "
        "NO digas que vas a consultar horarios — llama la herramienta y muestra HH:00 reales.\n"
        "- Ofrece solo las horas de `primeras_tres_horas` (máximo tres HH:00).\n"
        "- Al agendar usa `servicio`=`evaluacion` y `suffix_urgencia`=`dolor_intenso`.\n"
    )


def should_trigger_emergency_fork(
    *,
    policy_enabled: bool,
    emergency_phase: str,
    booking_phase: str,
    message: str,
    force_human_contact: bool = False,
    maxillofacial_booking: bool = False,
) -> bool:
    """
    True si debe mostrarse el paso de elección (cita vs equipo médico).
    Requiere fase none y dolor/urgencia grave; no dispara si appointment_chosen persiste.
    """
    if not policy_enabled:
        return False
    if (emergency_phase or "none").strip() != "none":
        return False
    if (booking_phase or "none").strip() == "awaiting_confirm":
        return False
    if force_human_contact or maxillofacial_booking:
        return False
    return message_signals_severe_urgency(message)


__all__ = [
    "EmergencyChoice",
    "classify_emergency_choice_response",
    "format_emergency_appointment_prompt_block",
    "history_signals_severe_urgency",
    "message_signals_severe_urgency",
    "patient_prompt_emergency_choice",
    "patient_prompt_emergency_choice_unclear",
    "patient_prompt_emergency_followup",
    "patient_prompt_emergency_transfer_sent",
    "should_trigger_emergency_fork",
]
