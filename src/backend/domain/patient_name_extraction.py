"""
Extracción del nombre del paciente tras preguntarlo en la primera interacción.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..services.gemini_service import (
    REPLY_MAX_OUTPUT_TOKENS_RETRY,
    SHORT_JSON_MAX_OUTPUT_TOKENS,
    GeminiService,
    GeminiServiceError,
)

_NAME_INTRO_PATTERNS = (
    re.compile(r"(?:me llamo|mi nombre es|soy)\s+(.+)", re.IGNORECASE),
    re.compile(r"(?:my name is|i['']?m|i am)\s+(.+)", re.IGNORECASE),
)

_SKIP_WORDS = frozenset(
    {
        "hola",
        "hello",
        "hi",
        "buenos",
        "días",
        "dias",
        "tardes",
        "noches",
        "gracias",
        "thanks",
        "ok",
        "sí",
        "si",
        "no",
        "yes",
        "vale",
        "claro",
        "perfecto",
        "bien",
        "qué",
        "que",
        "cuánto",
        "cuanto",
        "dónde",
        "donde",
        "cómo",
        "como",
        "cuándo",
        "cuando",
    }
)


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


def _normalize_name(raw: str) -> str | None:
    name = re.sub(r"\s+", " ", (raw or "").strip())
    name = re.sub(r"[.,!?;:]+$", "", name).strip()
    if not name or len(name) < 2:
        return None
    words = name.split()
    if any(w.lower() in _SKIP_WORDS for w in words):
        return None
    if len(words) > 6:
        return None
    if "?" in name:
        return None
    return name


def extract_patient_name_from_message(message: str) -> str | None:
    """Heurística rápida: patrones «me llamo», «soy», respuesta corta con nombre."""
    msg = (message or "").strip()
    if not msg:
        return None

    for pattern in _NAME_INTRO_PATTERNS:
        m = pattern.match(msg)
        if m:
            return _normalize_name(m.group(1))

    words = msg.split()
    if 1 <= len(words) <= 4 and "?" not in msg:
        if all(w.lower() not in _SKIP_WORDS for w in words):
            if all(re.match(r"^[\wáéíóúüñÁÉÍÓÚÜÑ'-]+$", w) for w in words):
                return _normalize_name(msg)
    return None


def extract_patient_name_with_gemini(
    gemini: GeminiService,
    message: str,
    language: str,
) -> str | None:
    """Fallback Gemini cuando la heurística no detecta nombre."""
    msg = (message or "").strip()
    if not msg:
        return None

    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        instructions = (
            "The assistant just asked the patient for their name. "
            "From the patient's reply below, extract ONLY their full name if they provided it.\n"
            'Reply with ONE JSON object: {"nombre": "Full Name"} or {"nombre": null} if no name was given.\n'
            "Do not invent a name."
        )
    else:
        instructions = (
            "El asistente acaba de pedir el nombre al paciente. "
            "Del mensaje del paciente abajo, extrae SOLO su nombre completo si lo proporcionó.\n"
            'Responde con UN objeto JSON: {"nombre": "Nombre Completo"} o {"nombre": null} si no dio nombre.\n'
            "No inventes un nombre."
        )

    try:
        raw = gemini.generate_reply(
            system_prompt=instructions + f"\n\nMensaje del paciente:\n{msg}\n\nJSON:",
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
    if not data:
        return None
    return _normalize_name(str(data.get("nombre") or ""))


def try_extract_patient_name(
    gemini: GeminiService | None,
    message: str,
    language: str,
) -> str | None:
    """Intenta extraer nombre del mensaje (heurística + Gemini opcional)."""
    name = extract_patient_name_from_message(message)
    if name:
        return name
    if gemini is not None:
        return extract_patient_name_with_gemini(gemini, message, language)
    return None


__all__ = [
    "extract_patient_name_from_message",
    "extract_patient_name_with_gemini",
    "try_extract_patient_name",
]
