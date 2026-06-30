"""
Requisito de radiografía panorámica para extracción de cordales (muelas del juicio).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal

from ..schemas.clinic_policies import CordalesPanoramicRequirementPolicies
from .catalog import _service_display_label, _services_for_clinic

CordalesXrayPhase = Literal["none", "asked", "has_panoramic", "needs_at_clinic"]
PatientXrayResponse = Literal["has_panoramic", "needs_at_clinic", "unclear"]


def _fold(text: str) -> str:
    s = (text or "").strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def message_signals_cordales_inquiry(
    message: str,
    policy: CordalesPanoramicRequirementPolicies,
    *,
    last_discussed_service_id: str | None = None,
) -> bool:
    """True si el mensaje o el servicio en contexto activan el flujo de cordales."""
    if not policy.enabled:
        return False

    folded = _fold(message)
    if folded:
        for term in policy.trigger_terms:
            t = _fold(term)
            if len(t) >= 4 and t in folded:
                return True

    sid = (last_discussed_service_id or "").strip()
    if sid and sid in set(policy.target_service_ids):
        return True

    return False


_HAS_XRAY_RE = re.compile(
    r"(?:"
    r"(?:^|\s)(?:si|sí)\s*,?\s*la\s+tengo|"
    r"ya\s+la\s+tengo|ya\s+tengo(?:\s+la)?|"
    r"si\s+tengo|sí\s+tengo|"
    r"ya\s+cuento|ya\s+tengo\s+(?:la\s+)?(?:radiografia|panoramica)|"
    r"(?:^|\s)la\s+tengo(?:\s|$)|"
    r"traigo\s+la|tengo\s+radiografia|"
    r"yes\s+i\s+have|already\s+have|i\s+have\s+it|i\s+have\s+one"
    r")",
    re.IGNORECASE,
)

_NEEDS_XRAY_RE = re.compile(
    r"\b("
    r"no\s+la\s+tengo|no\s+tengo|aun\s+no|aún\s+no|todavia\s+no|todavía\s+no|"
    r"no\s+cuento|me\s+la\s+pueden\s+tomar|tomarmela|tomármela|agendemos|agendar|"
    r"quiero\s+que\s+me\s+la\s+tomen|no\s+la\s+tengo|"
    r"no\s+i\s+don'?t|don'?t\s+have|need\s+one|schedule|book\s+an?\s+evaluation"
    r")\b",
    re.IGNORECASE,
)


def classify_patient_xray_response(message: str) -> PatientXrayResponse:
    """Clasifica si el paciente ya tiene panorámica o necesita tomarla en clínica."""
    folded = _fold(message)
    if not folded:
        return "unclear"
    if _NEEDS_XRAY_RE.search(folded):
        return "needs_at_clinic"
    if _HAS_XRAY_RE.search(folded):
        return "has_panoramic"
    return "unclear"


def _service_price_label(clinic_id: str, service_id: str, language: str) -> str:
    sid = (service_id or "").strip()
    for svc in _services_for_clinic(clinic_id):
        if (svc.get("id") or "").strip() != sid:
            continue
        name = _service_display_label(clinic_id, sid, language)
        price = (svc.get("price") or "").strip()
        currency = (svc.get("currency") or "USD").strip()
        if price:
            return f"{name} ({currency} {price})"
        return name
    return _service_display_label(clinic_id, sid, language) or sid


def format_cordales_panoramic_prompt_block(
    *,
    language: str,
    clinic_id: str,
    policy: CordalesPanoramicRequirementPolicies,
    cordales_xray_phase: CordalesXrayPhase,
) -> str:
    """Bloque de instrucciones obligatorias para el flujo cordales + panorámica."""
    if not policy.enabled:
        return ""

    use_en = (language or "").strip().lower().startswith("en")
    mandatory_q = (
        (policy.mandatory_question_en if use_en else policy.mandatory_question_es) or ""
    ).strip()
    reminder = (
        (policy.reminder_if_has_xray_en if use_en else policy.reminder_if_has_xray_es) or ""
    ).strip()
    eval_xray_label = _service_price_label(
        clinic_id, policy.evaluation_with_xray_service_id, language
    )
    eval_only_label = _service_price_label(
        clinic_id, policy.evaluation_only_service_id, language
    )

    default_offer_es = (
        f"Podemos agendar {eval_xray_label} para valorarte y tomarte la radiografía antes de planear la extracción."
    )
    default_offer_en = (
        f"We can schedule {eval_xray_label} to evaluate you and take the X-ray before planning the extraction."
    )
    offer_no_xray = (
        (policy.offer_if_no_xray_en if use_en else policy.offer_if_no_xray_es) or ""
    ).strip() or (default_offer_en if use_en else default_offer_es)

    phase_note = ""
    if cordales_xray_phase == "asked":
        phase_note = (
            "\n- The patient was already asked about panoramic X-ray; interpret their latest reply: "
            "if they have it, remind them to bring it and offer evaluation only; "
            "if not, offer the evaluation + X-ray package."
            if use_en
            else "\n- Ya preguntaste por la radiografía panorámica; interpreta la última respuesta: "
            "si ya la tiene, recuérdale traerla y ofrece solo evaluación; "
            "si no, ofrece el paquete evaluación + radiografía."
        )
    elif cordales_xray_phase == "has_panoramic":
        phase_note = (
            f"\n- Patient HAS panoramic X-ray. Remind: {reminder} Offer evaluation ({eval_only_label}) if booking."
            if use_en
            else f"\n- El paciente YA TIENE radiografía panorámica. Recuerda: {reminder} Ofrece evaluación ({eval_only_label}) si va a agendar."
        )
    elif cordales_xray_phase == "needs_at_clinic":
        phase_note = (
            f"\n- Patient needs X-ray at clinic. Default booking service: {eval_xray_label}."
            if use_en
            else f"\n- El paciente necesita radiografía en clínica. Servicio por defecto al agendar: {eval_xray_label}."
        )

    block_direct = ""
    if policy.block_direct_cordal_booking:
        cordal_label = _service_display_label(
            clinic_id, (policy.target_service_ids or ["exodoncia_de_cordal_piezas"])[0], language
        )
        block_direct = (
            f"\n- NEVER call agendar_cita for «{cordal_label}» / cordal extraction via chat. "
            "Extraction is planned after in-clinic evaluation. Book evaluation (+ X-ray if needed) only."
            if use_en
            else f"\n- NUNCA llames agendar_cita para «{cordal_label}» / extracción de cordal por chat. "
            "La extracción se planifica tras evaluación en clínica. Solo agenda evaluación (+ radiografía si aplica)."
        )

    if use_en:
        return (
            "\n\n[WISDOM TOOTH (CORDAL) + PANORAMIC X-RAY – MANDATORY when patient asks about cordal extraction]\n"
            "- Respond warmly: yes, the clinic can help with cordal extraction.\n"
            "- BEFORE booking anything, you MUST ask (exact intent):\n"
            f"  «{mandatory_q}»\n"
            f"- If they already have the X-ray: {reminder}\n"
            f"- If they do not: {offer_no_xray}\n"
            "- Mention the clinic has its own imaging center (German equipment) when relevant.\n"
            f"- Use catalog id `{policy.evaluation_with_xray_service_id}` for evaluation + panoramic; "
            f"`{policy.evaluation_only_service_id}` if they already have the X-ray.\n"
            "- Speak to the patient using readable service names only, never catalog ids."
            f"{phase_note}{block_direct}\n"
        )

    return (
        "\n\n[CORDALES + RADIOGRAFÍA PANORÁMICA – OBLIGATORIO cuando el paciente pide extracción de cordales]\n"
        "- Responde con empatía: sí, la clínica puede ayudarle con la extracción de cordales.\n"
        "- ANTES de agendar cualquier cosa, DEBES preguntar (misma intención):\n"
        f"  «{mandatory_q}»\n"
        f"- Si ya tiene la radiografía: {reminder}\n"
        f"- Si no la tiene: {offer_no_xray}\n"
        "- Menciona que contamos con centro de imágenes propio (equipo alemán) cuando encaje.\n"
        f"- Usa id de catálogo `{policy.evaluation_with_xray_service_id}` para evaluación + panorámica; "
        f"`{policy.evaluation_only_service_id}` si ya trae la radiografía.\n"
        "- Habla con el paciente solo con nombres legibles del servicio, nunca ids del catálogo."
        f"{phase_note}{block_direct}\n"
    )


def cordal_extraction_blocked_message(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return (
            "For wisdom tooth extraction we need a panoramic X-ray and an in-clinic evaluation first. "
            "I can't schedule the extraction directly here — let's book an evaluation (+ X-ray if you need it) "
            "so the doctor can review your case."
        )
    return (
        "Para extracción de cordales necesitamos radiografía panorámica y una evaluación en clínica primero. "
        "No puedo agendar la extracción directamente aquí — agendemos una evaluación (+ radiografía si la necesitas) "
        "para que la doctora revise tu caso."
    )


__all__ = [
    "CordalesXrayPhase",
    "PatientXrayResponse",
    "classify_patient_xray_response",
    "cordal_extraction_blocked_message",
    "format_cordales_panoramic_prompt_block",
    "message_signals_cordales_inquiry",
]
