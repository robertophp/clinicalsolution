"""
Confirmación de agendado en backend: tras pedir sí/confirmo, ejecutar agendar_cita sin depender del LLM.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal, Mapping, Sequence

from ..services.gemini_service import (
    REPLY_MAX_OUTPUT_TOKENS_RETRY,
    SHORT_JSON_MAX_OUTPUT_TOKENS,
    GeminiService,
    GeminiServiceError,
)
from .beneficiary_age import parse_beneficiario_edad_arg
from .catalog import _services_for_clinic
from .cita_beneficiary import parse_es_para_tercero

BookingConfirmIntent = Literal["approve", "decline", "revise", "unclear"]

_ASSISTANT_ASKS_CONFIRM_RE = re.compile(
    r"(\*{1,2}s[ií]\*{1,2}|\*{1,2}confirmo\*{1,2}|"
    r"\bs[ií]\s+o\s+confirmo\b|"
    r"\bconfirm(o|es|ar)\b.{0,80}\b(guardar|guardarla|sistema)\b|"
    r"\bpara guardarla en el sistema\b|"
    r"\byes\s+or\s+\*?\*?confirm|\b"
    r"reply\s+with\s+\*?\*?(yes|confirm))",
    re.IGNORECASE | re.DOTALL,
)


def assistant_asks_booking_confirm(text: str) -> bool:
    """True si el mensaje del asistente pide confirmación explícita para guardar la cita."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_ASSISTANT_ASKS_CONFIRM_RE.search(t))


def classify_booking_confirm_response(message: str, language: str) -> BookingConfirmIntent:
    """Clasifica la respuesta del paciente a «¿sí o confirmo para guardar?»."""
    msg = (message or "").strip()
    if not msg:
        return "unclear"

    fb = _fallback_booking_confirm(msg, language)
    if fb is not None:
        return fb

    return "unclear"


def _fallback_booking_confirm(message: str, language: str) -> BookingConfirmIntent | None:
    m = message.strip().lower()
    lang = (language or "").strip().lower()
    if lang.startswith("en"):
        if re.fullmatch(r"(yes|yeah|yep|ok|okay|sure|confirm|confirmed|go ahead)\.?", m):
            return "approve"
        if re.fullmatch(r"(no|nope|not now|cancel|never mind)\.?", m):
            return "decline"
        if re.match(r"^(change|different|another time|wrong)\b", m):
            return "revise"
        return None

    if re.fullmatch(
        r"(sí|si|confirmo|ok|vale|claro|listo|de acuerdo|adelante|perfecto|excelente|correcto)\.?",
        m,
    ):
        return "approve"
    if re.search(
        r"\b(no quiero|no deseo|mejor no|prefiero no|no gracias|cancelar|cancela)\b",
        m,
    ):
        return "decline"
    if re.match(r"^(cambia|cambiar|otra hora|otro día|otro dia|mejor|corrige)\b", m):
        return "revise"
    if re.fullmatch(r"no\.?", m):
        return "decline"
    return None


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _normalize_hora(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})", s)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if mi != 0 or h < 0 or h > 23:
        return None
    return f"{h:02d}:00"


def normalize_pending_booking_args(data: Mapping[str, Any]) -> dict[str, str] | None:
    """Valida y normaliza args pendientes para ``agendar_cita``."""
    nombre = (data.get("nombre") or "").strip()
    fecha = (data.get("fecha") or "").strip()
    hora = _normalize_hora(str(data.get("hora") or ""))
    servicio = (data.get("servicio") or "").strip()
    if not all([nombre, fecha, hora, servicio]):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
        return None
    out: dict[str, str] = {
        "nombre": nombre,
        "fecha": fecha,
        "hora": hora,
        "servicio": servicio,
    }
    suffix = (data.get("suffix_urgencia") or "").strip()
    if suffix:
        out["suffix_urgencia"] = suffix
    if parse_es_para_tercero(data.get("es_para_tercero")):
        out["es_para_tercero"] = "true"
        titular = (data.get("nombre_titular") or "").strip()
        if titular:
            out["nombre_titular"] = titular
        edad = parse_beneficiario_edad_arg(data.get("beneficiario_edad"))
        if edad is not None:
            out["beneficiario_edad"] = str(edad)
    return out


def extract_pending_booking_from_conversation(
    gemini: GeminiService,
    *,
    history: Sequence[Mapping[str, str]] | None,
    assistant_confirmation_message: str,
    language: str,
    clinic_id: str,
    default_patient_name: str | None = None,
    last_discussed_service_id: str | None = None,
) -> dict[str, str] | None:
    """
    Extrae nombre, fecha, hora y servicio (id catálogo) del hilo cuando el asistente pidió confirmación.
    """
    lines: list[str] = []
    for msg in history or []:
        role = msg.get("role", "user")
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    if (assistant_confirmation_message or "").strip():
        lines.append(f"assistant: {assistant_confirmation_message.strip()}")
    thread = "\n".join(lines[-24:])
    if not thread.strip():
        return None

    svc_ids = [
        (s.get("id") or "").strip()
        for s in _services_for_clinic(clinic_id)
        if (s.get("id") or "").strip()
    ]
    sample_ids = ", ".join(svc_ids[:40])
    service_hint = ""
    sid_hint = (last_discussed_service_id or "").strip()
    if sid_hint and sid_hint in svc_ids:
        service_hint = f"\nIf the patient did not name a different service, prefer catalog id: {sid_hint!r}\n"
    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        default_hint = (
            f"Default beneficiary name for SELF booking only if thread confirms es_para_tercero=false: {default_patient_name!r}\n"
            if default_patient_name
            else "Do not invent a name. If beneficiary name is missing, return {\"incomplete\": true}.\n"
        )
        instructions = (
            "From this WhatsApp thread, extract the appointment the assistant asked the contact to confirm.\n"
            f"{default_hint}"
            "nombre = full name of who will ATTEND (beneficiary). "
            "es_para_tercero = true if appointment is for someone other than the contact.\n"
            "nombre_titular = optional contact name when es_para_tercero=true.\n"
            f"Service must be one of these catalog ids (pick the best match): {sample_ids}\n"
            f"{service_hint}"
            "Reply with ONE JSON object only, no markdown:\n"
            '{"nombre": "...", "fecha": "YYYY-MM-DD", "hora": "HH:00", "servicio": "catalog_id", '
            '"es_para_tercero": false, "nombre_titular": null or "...", '
            '"suffix_urgencia": null or "dolor_post_cita" or "dolor_intenso"}\n'
            "If date/time/service/beneficiary name are not clear enough to book, return {\"incomplete\": true}."
        )
    else:
        default_hint = (
            f"Nombre del beneficiario por defecto SOLO si el hilo confirma cita propia (es_para_tercero=false): {default_patient_name!r}\n"
            if default_patient_name
            else "No inventes un nombre. Si falta el nombre del beneficiario, devuelve {\"incomplete\": true}.\n"
        )
        instructions = (
            "Del hilo WhatsApp, extrae la cita que el asistente pidió confirmar.\n"
            f"{default_hint}"
            "nombre = nombre completo de quien ASISTE (beneficiario). "
            "es_para_tercero = true si la cita es para otra persona distinta al contacto.\n"
            "nombre_titular = opcional, nombre del titular del WhatsApp si es_para_tercero=true.\n"
            f"El servicio debe ser un id de este catálogo (elige el más adecuado): {sample_ids}\n"
            f"{service_hint}"
            "Responde SOLO un objeto JSON, sin markdown:\n"
            '{"nombre": "...", "fecha": "YYYY-MM-DD", "hora": "HH:00", "servicio": "id_catalogo", '
            '"es_para_tercero": false, "nombre_titular": null o "...", '
            '"suffix_urgencia": null o "dolor_post_cita" o "dolor_intenso"}\n'
            'Si no hay fecha/hora/servicio/nombre del beneficiario claros, devuelve {"incomplete": true}.'
        )

    try:
        raw = gemini.generate_reply(
            system_prompt=instructions + f"\n\n=== Hilo ===\n{thread}\n\nJSON:",
            chat_history=None,
            temperature=0.0,
            max_output_tokens=SHORT_JSON_MAX_OUTPUT_TOKENS,
            low_thinking=True,
            retry_max_output_tokens=REPLY_MAX_OUTPUT_TOKENS_RETRY,
        )
    except GeminiServiceError:
        return None
    if not isinstance(raw, str):
        return None
    data = _extract_json_object(raw)
    if not data or data.get("incomplete"):
        return None
    if not (data.get("nombre") or "").strip():
        if default_patient_name and not parse_es_para_tercero(data.get("es_para_tercero")):
            data = dict(data)
            data["nombre"] = default_patient_name
        else:
            return None
    return normalize_pending_booking_args(data)


def patient_prompt_booking_declined(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return (
            "No problem — I won't save that appointment. "
            "Tell me if you'd like a different day, time, or service."
        )
    return (
        "Entendido, no guardo esa cita. "
        "Cuéntame si prefieres otro día, otra hora u otro tipo de servicio."
    )


def patient_prompt_booking_unclear(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return (
            "To save the appointment in our system, please reply **yes** or **confirm** "
            "(a thank-you alone isn't enough). Or tell me what you'd like to change."
        )
    return (
        "Para guardar la cita en el sistema, responde **sí** o **confirmo** "
        "(un mensaje solo de gracias no basta). O dime qué quieres cambiar."
    )


def patient_prompt_offer_response_unclear(
    language: str,
    *,
    service_name: str | None = None,
) -> str:
    """Reconfirmación cuando la respuesta a una oferta de cita es ambigua (p. ej. «Excelente me encantaría»)."""
    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        if service_name:
            return (
                f"Great! Just to confirm: would you like to book an appointment for {service_name}? "
                "Reply **yes** or tell me if you'd prefer an evaluation first."
            )
        return (
            "Great! Just to confirm: would you like to book an appointment? "
            "Reply **yes** or tell me what you'd prefer."
        )
    if service_name:
        return (
            f"¡Qué bueno! Para confirmar: ¿quieres agendar la cita de {service_name}? "
            "Responde **sí** o dime si prefieres una evaluación primero."
        )
    return (
        "¡Qué bueno! Para confirmar: ¿quieres agendar la cita? "
        "Responde **sí** o cuéntame qué prefieres."
    )


__all__ = [
    "BookingConfirmIntent",
    "assistant_asks_booking_confirm",
    "classify_booking_confirm_response",
    "extract_pending_booking_from_conversation",
    "normalize_pending_booking_args",
    "patient_prompt_booking_declined",
    "patient_prompt_booking_unclear",
    "patient_prompt_offer_response_unclear",
]
