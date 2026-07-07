"""
Flujo cirugía maxilofacial: información desde catálogo vs derivación directa para citas/horarios.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence

from ..schemas.clinic_policies import MaxillofacialPolicies
from .catalog import _service_display_label, _services_for_clinic

MaxillofacialIntent = Literal["info", "booking"]


def _fold(text: str) -> str:
    s = (text or "").strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


_BOOKING_RE = re.compile(
    r"\b("
    r"agendar|agenda|cita|turno|horario|horarios|disponibilidad|disponible|"
    r"cu[aá]ndo\s+(tienen|hay|pueden)|quiero\s+(una\s+)?cita|"
    r"reservar|reserva|se\s+puede\s+agendar|me\s+gustar[ií]a\s+agendar|"
    r"book(?:ing)?|schedule|appointment|available\s+slot|"
    r"consultar_disponibilidad|primer\s+d[ií]a\s+disponible"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MaxillofacialContextResult:
    is_active: bool
    intent: MaxillofacialIntent | None


def _term_in_folded(folded: str, term: str) -> bool:
    t = _fold(term)
    return len(t) >= 3 and t in folded


def message_signals_maxillofacial_context(
    message: str,
    policy: MaxillofacialPolicies,
    *,
    last_discussed_service_id: str | None = None,
) -> bool:
    if not policy.enabled:
        return False
    sid = (last_discussed_service_id or "").strip()
    if sid and sid in set(policy.target_service_ids):
        return True
    folded = _fold(message)
    if not folded:
        return False
    for term in policy.trigger_terms:
        if _term_in_folded(folded, term):
            return True
    return False


def history_signals_maxillofacial_context(
    history: Sequence[Mapping[str, str]] | None,
    policy: MaxillofacialPolicies,
) -> bool:
    if not policy.enabled:
        return False
    for msg in history or []:
        content = (msg.get("content") or "").strip()
        if content and message_signals_maxillofacial_context(content, policy):
            return True
    return False


def message_signals_maxillofacial_booking(message: str) -> bool:
    folded = _fold(message)
    if not folded:
        return False
    return bool(_BOOKING_RE.search(folded))


def classify_maxillofacial_context(
    message: str,
    history: Sequence[Mapping[str, str]] | None,
    policy: MaxillofacialPolicies,
    *,
    last_discussed_service_id: str | None = None,
) -> MaxillofacialContextResult:
    active = message_signals_maxillofacial_context(
        message,
        policy,
        last_discussed_service_id=last_discussed_service_id,
    ) or history_signals_maxillofacial_context(history, policy)

    if not active:
        return MaxillofacialContextResult(is_active=False, intent=None)

    if message_signals_maxillofacial_booking(message):
        return MaxillofacialContextResult(is_active=True, intent="booking")

    sid = (last_discussed_service_id or "").strip()
    if sid in set(policy.target_service_ids) and message_signals_maxillofacial_booking(message):
        return MaxillofacialContextResult(is_active=True, intent="booking")

    return MaxillofacialContextResult(is_active=True, intent="info")


def is_maxillofacial_service_id(service_id: str | None, policy: MaxillofacialPolicies | None) -> bool:
    if not policy or not policy.enabled:
        return False
    sid = (service_id or "").strip()
    return bool(sid and sid in set(policy.target_service_ids))


def _catalog_lines_for_policy(clinic_id: str, policy: MaxillofacialPolicies, language: str) -> list[str]:
    ids = set(policy.target_service_ids)
    lines: list[str] = []
    for svc in _services_for_clinic(clinic_id):
        sid = (svc.get("id") or "").strip()
        if sid not in ids:
            continue
        label = _service_display_label(clinic_id, sid, language)
        price = (svc.get("price") or "").strip()
        currency = (svc.get("currency") or "USD").strip()
        if price:
            lines.append(f"- {label}: referencia ${price} {currency} (precio orientativo; evaluación define plan).")
        else:
            lines.append(f"- {label}")
    return lines


def format_maxillofacial_info_prompt_block(
    *,
    language: str,
    clinic_id: str,
    policy: MaxillofacialPolicies,
) -> str:
    use_en = (language or "").strip().lower().startswith("en")
    catalog_lines = _catalog_lines_for_policy(clinic_id, policy, language)
    catalog_block = "\n".join(catalog_lines) if catalog_lines else "- (see services catalog)"

    if use_en:
        return (
            "\n\n[MAXILLOFACIAL — INFORMATION ONLY THIS TURN]\n"
            "The patient is asking about maxillofacial / oral surgery topics.\n"
            "- Answer warmly using ONLY the catalog reference below. Do NOT escalate to a human for info-only questions.\n"
            "- Do NOT call agendar_cita, reagendar_cita, consultar_disponibilidad, or consultar_primer_dia_disponible "
            "for maxillofacial services — the maxillofacial team schedules those appointments.\n"
            "- If they ask for an appointment or availability, tell them you will connect them with the maxillofacial team "
            "(the system handles routing on the next turn when they confirm booking intent).\n"
            f"Catalog (maxillofacial):\n{catalog_block}\n"
        )
    return (
        "\n\n[MAXILOFACIAL — SOLO INFORMACIÓN ESTE TURNO]\n"
        "El paciente consulta sobre cirugía maxilofacial / maxilo.\n"
        "- Responde con calidez usando SOLO la referencia del catálogo abajo. NO derives a humano por preguntas informativas.\n"
        "- NO llames agendar_cita, reagendar_cita, consultar_disponibilidad ni consultar_primer_dia_disponible "
        "para servicios maxilofaciales — esas citas las coordina el equipo de maxilofacial.\n"
        "- Si piden cita u horarios, indica que los conectarás con el equipo maxilofacial "
        "(el sistema deriva en el turno siguiente cuando confirmen que quieren agendar).\n"
        f"Catálogo (maxilofacial):\n{catalog_block}\n"
    )


def maxillofacial_booking_blocked_message(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return (
            "Maxillofacial appointments are scheduled directly by our oral surgery team. "
            "I can't book those slots here — ask me to connect you with the maxillofacial team "
            "and I'll route your request."
        )
    return (
        "Las citas de cirugía maxilofacial las coordina directamente nuestro equipo especializado. "
        "No puedo agendar esos horarios aquí — pídeme conectar con el equipo maxilofacial "
        "y te derivo la solicitud."
    )


def patient_prompt_maxillofacial_transfer_sent(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return (
            "Happy to help 🙂 I've shared your request with our maxillofacial surgery team. "
            "They will contact you shortly with more details about your case and appointment availability."
        )
    return (
        "Con gusto te apoyo 🙂 Ya compartí tu solicitud con nuestro equipo de cirugía maxilofacial. "
        "En un momento se pondrán en contacto contigo para brindarte mayores detalles sobre tu caso "
        "y la disponibilidad de horarios."
    )


def patient_prompt_maxillofacial_followup(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return (
            "You're welcome! If you need anything else about other services, prices, or general appointments, "
            "just write to me. I'm here for you."
        )
    return (
        "¡Con gusto! Si necesitas algo más sobre otros servicios, precios o citas generales, escríbeme. "
        "Quedo a la orden."
    )


__all__ = [
    "MaxillofacialContextResult",
    "MaxillofacialIntent",
    "classify_maxillofacial_context",
    "format_maxillofacial_info_prompt_block",
    "history_signals_maxillofacial_context",
    "is_maxillofacial_service_id",
    "maxillofacial_booking_blocked_message",
    "message_signals_maxillofacial_booking",
    "message_signals_maxillofacial_context",
    "patient_prompt_maxillofacial_followup",
    "patient_prompt_maxillofacial_transfer_sent",
]
