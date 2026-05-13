"""
Derivación a especialista humano: detección con Gemini, resumen ejecutivo y clasificación de la respuesta del paciente.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from .gemini_service import GeminiService, GeminiServiceError
from .human_transfer_topics import TransferTopicDefinition, format_topics_for_prompt

logger = logging.getLogger(__name__)

PatientSummaryIntent = Literal["approve", "revise", "unclear"]

_TRANSFER_JSON_RE = re.compile(r"\{[\s\S]*\}")
_ROLE_PREFIX_RE = re.compile(r"^(Asistente|Usuario|Assistant|User)\s*:\s*", re.IGNORECASE)
_RESUMEN_HEADER_ONLY_RE = re.compile(r"^Resumen ejecutivo:?\s*$", re.IGNORECASE)


def sanitize_specialist_summary_body(text: str) -> str:
    """
    Limpia salidas del modelo que mezclan texto para el paciente (preguntas, prefijos de rol)
    con el resumen interno para el especialista. Evita duplicar la pregunta de confirmación.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    lines_out: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        s = _ROLE_PREFIX_RE.sub("", s).strip()
        if not s:
            continue
        if _RESUMEN_HEADER_ONLY_RE.match(s):
            continue
        low = s.lower()
        if low.startswith("resumen ejecutivo:"):
            s = s.split(":", 1)[-1].strip()
            if not s:
                continue
            low = s.lower()
        if low.startswith("he preparado este resumen") or low.startswith("he actualizado el resumen"):
            continue
        if "¿es correcto" in low or "¿te parece bien" in low or "deseas agregar algo más" in low:
            continue
        if "quieres agregar algo más" in low and s.endswith("?"):
            continue
        if s.endswith("?") and any(w in low for w in ("agregar algo", "correcto o deseas", "parece bien o")):
            continue
        lines_out.append(s)
    return "\n".join(lines_out).strip()


def parse_specialist_whatsapp_recipient(raw: str | None) -> str | None:
    """Normaliza a solo dígitos para el campo ``to`` de la Graph API de WhatsApp."""
    if not raw or not str(raw).strip():
        return None
    digits = "".join(c for c in str(raw) if c.isdigit())
    return digits or None


def format_patient_phone_display(from_number: str) -> str:
    """Presentación legible del teléfono del paciente en el mensaje al especialista."""
    s = (from_number or "").strip()
    if s.lower().startswith("whatsapp:"):
        s = s[9:].strip()
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return from_number or ""
    return f"+{digits}"


def build_specialist_derivation_message(*, patient_name: str, patient_phone_display: str, summary: str) -> str:
    """Plantilla con negritas WhatsApp (*texto*)."""
    name = (patient_name or "").strip() or "No indicado"
    phone = (patient_phone_display or "").strip() or "No indicado"
    summ = (summary or "").strip() or "Sin resumen"
    return (
        "*🚨 NUEVA DERIVACIÓN 🚨*\n"
        f"*Paciente:* {name}\n"
        f"*Teléfono:* {phone}\n"
        f"*Resumen:* {summ}"
    )


def _extract_json_object(text: str | Any) -> dict[str, Any] | None:
    if text is None or not isinstance(text, str):
        return None
    t = text.strip()
    if not t:
        return None
    if "```" in t:
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```$", "", t)
    match = _TRANSFER_JSON_RE.search(t)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _history_lines(history: Sequence[Mapping[str, str]] | None, *, max_turns: int = 12) -> str:
    rows: list[str] = []
    for m in list(history or [])[-max_turns:]:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        prefix = "Usuario" if role == "user" else "Asistente"
        rows.append(f"{prefix}: {content}")
    return "\n".join(rows)


@dataclass(frozen=True)
class HumanTransferDetection:
    matched_topics: tuple[str, ...]
    brief_reason: str


def detect_human_transfer_need(
    gemini: GeminiService,
    *,
    message: str,
    history: Sequence[Mapping[str, str]] | None,
    language: str,
    topics: tuple[TransferTopicDefinition, ...],
    resolution_context: str | None = None,
) -> HumanTransferDetection | None:
    """
    Usa Gemini para decidir si el mensaje actual (con contexto) debe derivarse a humano.
    Devuelve None si no aplica o si falla el parseo.
    """
    msg = (message or "").strip()
    if not msg or not topics:
        return None

    topics_block = format_topics_for_prompt(topics, language)
    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        instructions = (
            "You are a strict classifier for a dental clinic WhatsApp assistant.\n"
            "Decide if the patient's CURRENT message (with recent conversation context) should be "
            "escalated to a human specialist according to ANY of the topics below.\n\n"
            "Topics (keys):\n"
            f"{topics_block}\n\n"
            "POLICY — Prefer requires_human_transfer=false:\n"
            "- First-contact questions about payment options (cards, installments, 0% fee, which banks), "
            "price comparisons, or 'it seems expensive' seeking alternatives — if the assistant's "
            "standard catalog + payment policy (see context block when provided) can answer.\n"
            "POLICY — requires_human_transfer=true:\n"
            "- Escalation-worthy complaints: billing errors after clarification, refusal/manager demands, "
            "severe service incidents, repeated unresolved conflict in thread, topics clearly outside bot policy.\n\n"
            'Reply with ONE JSON object only, no markdown:\n'
            '{"requires_human_transfer": true|false, "matched_topics": ["key1"], '
            '"brief_reason": "short phrase"}\n'
            "- matched_topics must be a subset of the topic keys listed.\n"
            "- When in doubt, prefer false so the assistant can answer first.\n"
        )
    else:
        instructions = (
            "Eres un clasificador estricto para el asistente WhatsApp de una clínica dental.\n"
            "Decide si el mensaje ACTUAL del paciente (con el contexto reciente) debe derivarse "
            "a un especialista humano según ALGUNO de estos temas.\n\n"
            "Temas (clave):\n"
            f"{topics_block}\n\n"
            "POLÍTICA — Prefiere requires_human_transfer=false:\n"
            "- Primera consulta sobre medios de pago (tarjeta, cuotas, tasa 0, qué bancos), comparación de precios "
            "o «me parece caro» pidiendo opciones, cuando el asistente puede responder con el catálogo y la política "
            "de pagos de la clínica (ver bloque de contexto si viene incluido).\n"
            "POLÍTICA — requires_human_transfer=true:\n"
            "- Quejas graves o conflicto: cobro percibido como incorrecto tras aclarar, exigencia de responsable, "
            "mal servicio/atención muy serio, conflicto repetido sin resolver en el hilo, situación que claramente "
            "no cubre la información estándar.\n\n"
            "Responde SOLO un objeto JSON, sin markdown:\n"
            '{"requires_human_transfer": true|false, "matched_topics": ["clave"], '
            '"brief_reason": "frase corta"}\n'
            "- matched_topics debe ser un subconjunto de las claves indicadas.\n"
            "- Ante la duda, usa false para que el asistente intente resolver primero.\n"
        )

    history_text = _history_lines(history)
    prompt_parts = ["=== Instrucciones ===", instructions, ""]
    rc = (resolution_context or "").strip()
    if rc:
        prompt_parts += ["=== Contexto para NO derivar de más ===", rc, ""]
    if history_text:
        prompt_parts += ["=== Historial reciente ===", history_text, ""]
    prompt_parts += ["=== Mensaje actual del paciente ===", msg, "", "JSON:"]

    try:
        raw = gemini.generate_reply(
            system_prompt="\n".join(prompt_parts),
            chat_history=None,
            temperature=0.0,
            max_output_tokens=256,
        )
        if not isinstance(raw, str):
            return None
        data = _extract_json_object(raw)
        if not data:
            return None
        if not data.get("requires_human_transfer"):
            return None
        matched = data.get("matched_topics") or []
        if not isinstance(matched, list):
            matched = []
        keys = tuple(str(x).strip() for x in matched if str(x).strip())
        reason = str(data.get("brief_reason") or "").strip()
        if not keys:
            return None
        allowed = {t.key for t in topics}
        keys = tuple(k for k in keys if k in allowed)
        if not keys:
            return None
        return HumanTransferDetection(matched_topics=keys, brief_reason=reason)
    except GeminiServiceError as e:
        logger.warning("detect_human_transfer_need Gemini error: %s", e)
        return None


def generate_transfer_summary(
    gemini: GeminiService,
    *,
    history: Sequence[Mapping[str, str]] | None,
    current_message: str,
    language: str,
    detection: HumanTransferDetection | None,
    patient_correction: str | None = None,
    previous_summary: str | None = None,
) -> str:
    """Genera un resumen ejecutivo de 3–4 líneas para el especialista."""
    hist = _history_lines(history)
    extra_detection = ""
    if detection and detection.brief_reason:
        extra_detection = f"\nMotivo clasificación: {detection.brief_reason}"
        if detection.matched_topics:
            extra_detection += f"\nTemas: {', '.join(detection.matched_topics)}"

    use_en = (language or "").strip().lower().startswith("en")
    prev = (previous_summary or "").strip()
    corr = (patient_correction or "").strip()

    if use_en:
        correction_block = ""
        if prev and corr:
            correction_block = (
                f"\nPrevious summary (replace/improve it):\n{prev}\n"
                f"Patient asked to add or fix:\n{corr}\n"
            )
        elif corr:
            correction_block = f"\nPatient asked to add or fix:\n{corr}\n"

        instructions = (
            "Write ONLY the body text that an internal human specialist will read (English).\n"
            "- Maximum 3–4 short lines; warm-professional and empathetic (e.g. 'concern' over harsh wording).\n"
            "- No medical diagnosis; include concrete facts (dates, amounts, symptoms context).\n"
            "- Do NOT address the patient. Do NOT ask questions. Do NOT use labels like 'Executive summary:' "
            "or role prefixes like 'Assistant:'.\n"
            "- Do NOT repeat phrases such as 'I've prepared this summary' or 'Is it correct?'.\n"
            f"{correction_block}"
            f"{extra_detection}\n"
            "Plain text only.\n"
        )
    else:
        correction_block = ""
        if prev and corr:
            correction_block = (
                f"\nResumen anterior (mejóralo integrando lo siguiente):\n{prev}\n"
                f"Pedido del paciente:\n{corr}\n"
            )
        elif corr:
            correction_block = f"\nPedido del paciente para integrar:\n{corr}\n"

        instructions = (
            "Redacta SOLO el texto que leerá un especialista humano (uso interno), en español.\n"
            "- Máximo 3–4 líneas; tono profesional, cercano y empático: ante molestias usa "
            "\"inquietud\" o \"preocupación\" antes que \"insatisfacción\" cuando aplique.\n"
            "- Sin diagnóstico médico; incluye qué necesita el paciente y datos concretos (fechas, montos, contexto).\n"
            "- NO te dirijas al paciente. NO hagas preguntas. NO uses etiquetas tipo \"Resumen ejecutivo:\" "
            "ni prefijos \"Asistente:\" o \"Usuario:\".\n"
            "- NO repitas frases del bot como \"He preparado/actualizado el resumen\" ni preguntas como "
            "\"¿Es correcto o deseas agregar algo más?\".\n"
            f"{correction_block}"
            f"{extra_detection}\n"
            "Solo párrafos de texto plano.\n"
        )

    prompt_parts = ["=== Instrucciones ===", instructions, ""]
    if hist:
        prompt_parts += ["=== Historial ===", hist, ""]
    prompt_parts += ["=== Último mensaje del paciente ===", (current_message or "").strip(), "", "Resumen:"]

    text = gemini.generate_reply(
        system_prompt="\n".join(prompt_parts),
        chat_history=None,
        temperature=0.2,
        max_output_tokens=400,
    )
    if not isinstance(text, str):
        text = ""
    cleaned = sanitize_specialist_summary_body(text)
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    out = "\n".join(lines[:6]).strip()
    return out or cleaned or text.strip()


def classify_patient_summary_response(
    gemini: GeminiService,
    *,
    patient_message: str,
    language: str,
    current_summary: str,
) -> PatientSummaryIntent:
    """
    Aprueba envío, pide revisión o queda ambiguo.
    """
    msg = (patient_message or "").strip()
    if not msg:
        return "unclear"

    fb = _fallback_summary_feedback(msg, language)
    if fb is not None:
        return fb

    summ = (current_summary or "").strip()
    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        instructions = (
            "The assistant showed this summary to the patient and asked for confirmation:\n"
            f"---\n{summ}\n---\n"
            "Classify the patient's reply as exactly one label:\n"
            "- approve: they confirm it's OK to send (yes, ok, go ahead, send it).\n"
            "- revise: they say it's wrong, incomplete, or want to add details.\n"
            "- unclear: ambiguous or off-topic.\n"
            "Answer with ONLY one word: approve, revise, or unclear."
        )
    else:
        instructions = (
            "El asistente mostró este resumen al paciente y pidió confirmación:\n"
            f"---\n{summ}\n---\n"
            "Clasifica la respuesta del paciente en UNA etiqueta:\n"
            "- approve: confirma que está bien para enviar al especialista (sí, ok, envíalo, correcto).\n"
            "- revise: dice que no cuadra, falta algo o quiere agregar/cambiar datos.\n"
            "- unclear: ambigua o fuera de tema.\n"
            "Responde SOLO una palabra: approve, revise o unclear."
        )

    try:
        raw_out = gemini.generate_reply(
            system_prompt=instructions + f"\n\nPaciente: {msg}\nEtiqueta:",
            chat_history=None,
            temperature=0.0,
            max_output_tokens=8,
        )
        raw = raw_out.strip().lower() if isinstance(raw_out, str) else ""
    except GeminiServiceError:
        return "unclear"

    if "approve" in raw:
        return "approve"
    if "revise" in raw:
        return "revise"
    if "unclear" in raw:
        return "unclear"
    return "unclear"


def _fallback_summary_feedback(message: str, language: str) -> PatientSummaryIntent | None:
    """Heurística ligera cuando el mensaje es muy corto y claro."""
    m = (message or "").strip().lower()
    lang = (language or "").strip().lower()
    if lang.startswith("en"):
        if re.fullmatch(r"(yes|yeah|yep|ok|okay|sure|send it|go ahead|correct|fine)\.?", m):
            return "approve"
        if re.fullmatch(r"(no|nope|wrong|incorrect|add more|change it)\.?", m):
            return "revise"
        return None

    if re.fullmatch(
        r"(sí|si|ok|vale|correcto|esta bien|está bien|envía|envíalo|mandalo|mándalo|adelante|listo)\.?",
        m,
    ):
        return "approve"
    if re.match(r"^(agrega|añade|corrige|falta|incorpora|actualiza|mejor|complementa)\b", m):
        return "revise"
    return None


def patient_prompt_confirm_summary(summary: str, language: str) -> str:
    s = (summary or "").strip()
    if (language or "").strip().lower().startswith("en"):
        return (
            "Happy to help with this 🙂\n\n"
            "To speed things up, I'll share the summary below with our specialist:\n\n"
            f"{s}\n\n"
            "Does this look right, or would you like to add anything? 👉"
        )
    return (
        "Con gusto podemos apoyarte con este tema 🙂\n\n"
        "Para agilizar tu atención, le compartiré a nuestro especialista un resumen de lo que nos cuentas:\n\n"
        f"{s}\n\n"
        "¿Te parece bien o deseas agregar algo más? 👉"
    )


def patient_prompt_after_transfer(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return (
            "One of our specialists will contact you shortly. "
            "If you have any other questions, I'm here. Have a great day!"
        )
    return (
        "En un momento uno de nuestros especialistas se pondrá en contacto contigo. "
        "Cualquier otra duda estaré por acá. ¡Feliz día!"
    )


def patient_prompt_unclear_confirmation(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return (
            "Just to confirm: should I send this summary to our specialist as-is, "
            "or do you want to change or add something? Reply with yes or describe the change."
        )
    return (
        "Para confirmar: ¿envío este resumen al especialista tal cual, "
        "o quieres cambiar o agregar algo? Responde sí o cuéntame qué ajustar."
    )


def patient_prompt_transfer_send_failed(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return (
            "I couldn't notify the specialist automatically right now. "
            "Please try again in a few minutes, or say 'send it' again."
        )
    return (
        "Ahora no pude notificar al especialista automáticamente. "
        "Intenta de nuevo en unos minutos o escribe de nuevo \"envíalo\"."
    )


def patient_prompt_transfer_not_configured(language: str) -> str:
    if (language or "").strip().lower().startswith("en"):
        return (
            "This topic should be handled by a human specialist, but automatic routing is not configured. "
            "Please call the clinic directly."
        )
    return (
        "Este tema lo debe atender un especialista humano, pero la derivación automática no está configurada. "
        "Por favor llama directamente a la clínica."
    )


__all__ = [
    "HumanTransferDetection",
    "PatientSummaryIntent",
    "build_specialist_derivation_message",
    "classify_patient_summary_response",
    "detect_human_transfer_need",
    "format_patient_phone_display",
    "generate_transfer_summary",
    "parse_specialist_whatsapp_recipient",
    "sanitize_specialist_summary_body",
    "patient_prompt_after_transfer",
    "patient_prompt_confirm_summary",
    "patient_prompt_transfer_not_configured",
    "patient_prompt_transfer_send_failed",
    "patient_prompt_unclear_confirmation",
]
