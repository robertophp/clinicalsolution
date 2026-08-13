"""
Flujo cita mismo día: elección entre agendar mañana a primera hora o contacto del equipo.

Se activa cuando el paciente pide cita para hoy (imposible por canal WhatsApp).
"""
from __future__ import annotations

from ..schemas.clinic_policies import SameDayForkPolicies
from .emergency_fork_policy import (
    EmergencyChoice,
    classify_emergency_choice_response,
)
from .urgency_signals import message_signals_same_day_request

SameDayChoice = EmergencyChoice


def _policy_text(policy: SameDayForkPolicies, field_es: str, field_en: str, language: str) -> str:
    use_en = (language or "").strip().lower().startswith("en")
    raw = getattr(policy, field_en if use_en else field_es, None)
    return (raw or "").strip()


def classify_same_day_choice_response(message: str) -> SameDayChoice:
    """Clasifica la respuesta del paciente cuando está en fase awaiting_choice."""
    return classify_emergency_choice_response(message)


def patient_prompt_same_day_choice(
    language: str,
    policy: SameDayForkPolicies,
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
            f"We understand you need this soon{name_bit} 🙏 Through this channel, appointments require "
            "at least one day's notice, so we can't book for today — but we can connect you with our team "
            "to check if same-day slots are available.\n\n"
            "How would you like us to help?\n\n"
            "**Schedule you as soon as possible for tomorrow at the first available time**, or\n"
            "**Share your case with our team** so they can contact you as soon as possible.\n\n"
            "Which do you prefer?"
        )
    return (
        f"Entendemos que lo necesitas pronto{name_bit} 🙏 Te cuento que por este canal nuestras citas se "
        "agendan con al menos un día de anticipación, por lo que no podemos para hoy, sin embargo podemos "
        "direccionarte con alguien de nuestro equipo para ver si hay cupos disponibles hoy mismo.\n\n"
        "¿Cómo prefieres que te apoyemos?\n\n"
        "**Agendarte lo antes posible para mañana a primera hora**, o\n"
        "**Compartir tu caso con nuestro equipo** para que te contacten a la brevedad.\n\n"
        "¿Cuál prefieres?"
    )


def patient_prompt_same_day_choice_unclear(
    language: str,
    policy: SameDayForkPolicies,
) -> str:
    custom = _policy_text(policy, "choice_unclear_es", "choice_unclear_en", language)
    if custom:
        return custom
    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        return (
            "I want to help you the right way 🙂 "
            "Would you prefer the first available time tomorrow, "
            "or that our team contact you as soon as possible?"
        )
    return (
        "Quiero ayudarte de la mejor forma 🙂 "
        "¿Prefieres lo antes posible mañana a primera hora, "
        "o que nuestro equipo te contacte a la brevedad?"
    )


def patient_prompt_same_day_transfer_sent(
    language: str,
    policy: SameDayForkPolicies,
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
            "Your message has been forwarded to our team ✅ "
            "Please stay alert — they will contact you as soon as possible."
            f"{phone_line}\n\n"
            "If you have any other questions, I'm happy to help."
        )
    phone_line_es = f"\n\nSi lo prefieres, también puedes llamarnos al **{phone}** 📞" if phone else ""
    return (
        "Tu mensaje ya fue direccionado a nuestro equipo ✅ "
        "Queda pendiente de su contacto a la brevedad."
        f"{phone_line_es}\n\n"
        "Cualquier otra consulta, con gusto te ayudo."
    )


def patient_prompt_same_day_followup(language: str) -> str:
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


def format_same_day_appointment_prompt_block(language: str) -> str:
    """Bloque extra cuando el paciente ya eligió cita para mañana a primera hora."""
    if (language or "").strip().lower().startswith("en"):
        return (
            "\n\n[SAME-DAY REQUEST — PATIENT CHOSE FIRST AVAILABLE TOMORROW]\n"
            "- They asked for today but chose to book tomorrow at the first available time.\n"
            "- Respond with brief empathy; do NOT ask again whether they want team contact or booking.\n"
            "- You MUST call `consultar_primer_dia_disponible` NOW in this turn before replying. "
            "Do NOT say you will look up availability — call the tool and show actual HH:00 slots.\n"
            "- Offer only `primeras_tres_horas` (up to three HH:00 starts).\n"
            "- Use `servicio`=`evaluacion` unless they clearly chose another service.\n"
        )
    return (
        "\n\n[CITA HOY — PACIENTE ELIGIÓ MAÑANA A PRIMERA HORA]\n"
        "- El paciente pidió cita para hoy pero eligió agendar mañana a primera hora disponible.\n"
        "- Responde con empatía breve; NO vuelvas a preguntar si prefiere contacto del equipo o agendar.\n"
        "- DEBES llamar `consultar_primer_dia_disponible` AHORA en este turno antes de responder. "
        "NO digas que vas a consultar horarios — llama la herramienta y muestra HH:00 reales.\n"
        "- Ofrece solo las horas de `primeras_tres_horas` (máximo tres HH:00).\n"
        "- Usa `servicio`=`evaluacion` salvo que haya elegido otro servicio con claridad.\n"
    )


def should_trigger_same_day_fork(
    *,
    policy_enabled: bool,
    same_day_phase: str,
    emergency_phase: str,
    booking_phase: str,
    message: str,
    force_human_contact: bool = False,
    maxillofacial_booking: bool = False,
) -> bool:
    """True si debe mostrarse el paso de elección para solicitud de cita hoy."""
    if not policy_enabled:
        return False
    if (same_day_phase or "none").strip() != "none":
        return False
    if (emergency_phase or "none").strip() != "none":
        return False
    if (booking_phase or "none").strip() == "awaiting_confirm":
        return False
    if force_human_contact or maxillofacial_booking:
        return False
    return message_signals_same_day_request(message)


__all__ = [
    "SameDayChoice",
    "classify_same_day_choice_response",
    "format_same_day_appointment_prompt_block",
    "patient_prompt_same_day_choice",
    "patient_prompt_same_day_choice_unclear",
    "patient_prompt_same_day_followup",
    "patient_prompt_same_day_transfer_sent",
    "should_trigger_same_day_fork",
]
