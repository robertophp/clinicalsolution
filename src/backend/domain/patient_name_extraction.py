"""
Extracción del nombre del paciente tras preguntarlo en la primera interacción.
"""
from __future__ import annotations

import json
import re
import unicodedata
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

_CORRECTION_SIGNAL_RE = re.compile(
    r"(?:"
    r"me equivoqu[eé]|equivoque|equivoqué|"
    r"correg(?:ir|e|irme|ido)|"
    r"cambia|cambiar|actualiza|actualizar|"
    r"nombre correcto|"
    r"escrib[ií]\s+mal|mal escrito|"
    r"no es .+ es |"
    r"no me (?:digas|llames|llama)|"
    r"correct my name|my correct name|misspelled|wrong name|"
    r"it'?s not .+ it'?s "
    r")",
    re.IGNORECASE,
)

_CORRECTION_NAME_PATTERNS = (
    # "no es X, me llamo Y" → captura solo Y (evita capturar "me llamo Y" como nombre)
    re.compile(
        r"no\s+(?:me\s+llames?|me\s+llamas?|es)\s+\S+[^,;.!?]*?[,;]?\s*"
        r"(?:(?:me\s+llamo|mi\s+nombre\s+es|soy|es)\s+)(.+)",
        re.IGNORECASE,
    ),
    re.compile(r"nombre correcto es\s+(.+)", re.IGNORECASE),
    re.compile(
        r"(?:me equivoqu[eé]|equivoque)[^,.!?]*[,.]?\s*(?:mi nombre es|me llamo|soy|es)\s+(.+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"correg(?:ir|e|irme)?[^,.!?]*\s*mi nombre es\s+(.+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:corrige|cambia|cambiar|actualiza|actualizar)\s+(?:mi nombre|el nombre)(?:\s+a|\s+por|:|\s+es)?\s*(.+)",
        re.IGNORECASE,
    ),
    re.compile(r"mi nombre es\s+(.+)", re.IGNORECASE),
    re.compile(r"(?:correct my name(?: to)?|my correct name is)\s+(.+)", re.IGNORECASE),
    re.compile(r"it'?s not .+?,?\s*it'?s\s+(.+)", re.IGNORECASE),
)

_CORRECTION_CONTEXT_KEYWORDS = (
    "nombre",
    "name",
    "llamas",
    "llaman",
    "llamo",
    "llama ",
    "correg",
    "equivo",
    "actualiz",
    "cambiar",
    "cambia ",
    "mal escrito",
    "wrong name",
    "mi nombre es",
    "me llamo",
    "my name is",
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


def _fold(text: str) -> str:
    s = (text or "").strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
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


_NAME_INTRO_RE = re.compile(
    r"^(?:me\s+llamo|mi\s+nombre\s+es|soy|my\s+name\s+is|i'?m|llamo)\s+",
    re.IGNORECASE,
)

def _normalize_name(raw: str) -> str | None:
    name = re.sub(r"\s+", " ", (raw or "").strip())
    name = re.sub(r"[.,!?;:]+$", "", name).strip()
    # Strip accidental "me llamo X" prefix captured by regex groups
    name = _NAME_INTRO_RE.sub("", name).strip()
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


_NAME_ASK_PATTERNS = (
    re.compile(r"con\s+qui[eé]n\s+tengo\s+el\s+gusto", re.IGNORECASE),
    re.compile(r"(?:me\s+compartes|comparte)\s+(?:tu\s+)?nombre", re.IGNORECASE),
    re.compile(r"(?:cu[aá]l\s+es\s+)?tu\s+nombre", re.IGNORECASE),
    re.compile(r"c[oó]mo\s+te\s+llamas", re.IGNORECASE),
    re.compile(r"(?:what(?:'s|\s+is)\s+)?your\s+(?:full\s+)?name", re.IGNORECASE),
    re.compile(r"may\s+i\s+(?:have|get)\s+your\s+name", re.IGNORECASE),
)


def message_signals_name_correction(message: str) -> bool:
    """True si hay señal explícita de corrección de nombre (fast path regex)."""
    msg = (message or "").strip()
    if not msg:
        return False
    return bool(_CORRECTION_SIGNAL_RE.search(msg))


def message_may_be_name_correction(
    message: str,
    *,
    stored_name: str | None = None,
    stored_first_name: str | None = None,
) -> bool:
    """
    Puerta amplia: el mensaje podría ser una corrección de nombre.
    Usada antes del fallback LLM cuando ya hay nombre guardado.
    """
    if message_signals_name_correction(message):
        return True

    folded = _fold(message)
    if not folded:
        return False

    if not any(k in folded for k in _CORRECTION_CONTEXT_KEYWORDS):
        return False

    signals = 0
    if "nombre" in folded or "name" in folded:
        signals += 1
    if any(k in folded for k in ("llamas", "llaman", "llamo", "llama ", "call me", "calls me")):
        signals += 1
    if any(k in folded for k in ("correg", "equivo", "actualiz", "cambiar", "cambia ")):
        signals += 1
    if any(k in folded for k in ("mi nombre es", "me llamo", "soy ", "my name is")):
        signals += 1

    for stored in ((stored_first_name or "").strip(), (stored_name or "").strip()):
        sf = _fold(stored)
        if len(sf) >= 3 and sf in folded:
            signals += 1
            break

    return signals >= 2


def _names_equivalent(a: str, b: str) -> bool:
    return _fold(a) == _fold(b)


def extract_corrected_name_from_message(
    message: str,
    *,
    stored_name: str | None = None,
    stored_first_name: str | None = None,
) -> str | None:
    """Extrae el nombre corregido (regex fast path) cuando hay contexto de corrección."""
    if not message_may_be_name_correction(
        message,
        stored_name=stored_name,
        stored_first_name=stored_first_name,
    ):
        return None

    msg = (message or "").strip()
    for pattern in _CORRECTION_NAME_PATTERNS:
        m = pattern.search(msg)
        if m:
            name = _normalize_name(m.group(1))
            if name and not _names_equivalent(name, stored_name or ""):
                if stored_first_name and _names_equivalent(name, stored_first_name):
                    continue
                return name

    for pattern in _NAME_INTRO_PATTERNS:
        m = pattern.search(msg)
        if m:
            name = _normalize_name(m.group(1))
            if name and not _names_equivalent(name, stored_name or ""):
                if stored_first_name and _names_equivalent(name, stored_first_name):
                    continue
                return name

    return None


def extract_corrected_name_with_gemini(
    gemini: GeminiService,
    message: str,
    language: str,
    *,
    stored_name: str | None = None,
    stored_first_name: str | None = None,
) -> str | None:
    """Fallback LLM para corrección de nombre cuando la puerta amplia está abierta."""
    msg = (message or "").strip()
    if not msg or not message_may_be_name_correction(
        msg,
        stored_name=stored_name,
        stored_first_name=stored_first_name,
    ):
        return None

    stored = (stored_name or stored_first_name or "").strip()
    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        instructions = (
            "The WhatsApp contact is correcting how we address them"
            + (f" (name on file: {stored!r})" if stored else "")
            + ".\n"
            "Decide if they want to update their name and extract the corrected full name.\n"
            'Reply with ONE JSON object: {"is_name_correction": true|false, "nombre": "Full Name" or null}.\n'
            "Set is_name_correction=false if they are NOT correcting their name.\n"
            "Do not invent a name."
        )
    else:
        instructions = (
            "El contacto de WhatsApp está corrigiendo cómo lo llamamos"
            + (f" (nombre guardado: {stored!r})" if stored else "")
            + ".\n"
            "Decide si quiere actualizar su nombre y extrae el nombre completo corregido.\n"
            'Responde con UN objeto JSON: {"is_name_correction": true|false, "nombre": "Nombre Completo" o null}.\n'
            "Usa is_name_correction=false si NO está corrigiendo su nombre.\n"
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
    if data.get("is_name_correction") is False:
        return None
    name = _normalize_name(str(data.get("nombre") or ""))
    if name and not _names_equivalent(name, stored_name or ""):
        if stored_first_name and _names_equivalent(name, stored_first_name):
            return None
        return name
    return None


def try_extract_name_correction(
    gemini: GeminiService | None,
    message: str,
    language: str,
    *,
    stored_name: str | None = None,
    stored_first_name: str | None = None,
) -> str | None:
    """Intenta extraer nombre corregido: regex fast path, luego LLM si la puerta amplia abre."""
    name = extract_corrected_name_from_message(
        message,
        stored_name=stored_name,
        stored_first_name=stored_first_name,
    )
    if name:
        return name
    if gemini is not None and message_may_be_name_correction(
        message,
        stored_name=stored_name,
        stored_first_name=stored_first_name,
    ):
        return extract_corrected_name_with_gemini(
            gemini,
            message,
            language,
            stored_name=stored_name,
            stored_first_name=stored_first_name,
        )
    return None


def assistant_asked_for_name(reply_text: str) -> bool:
    """True si la respuesta del asistente pidió el nombre del paciente."""
    text = (reply_text or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in _NAME_ASK_PATTERNS)


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
    "assistant_asked_for_name",
    "extract_corrected_name_from_message",
    "extract_corrected_name_with_gemini",
    "extract_patient_name_from_message",
    "extract_patient_name_with_gemini",
    "message_may_be_name_correction",
    "message_signals_name_correction",
    "try_extract_name_correction",
    "try_extract_patient_name",
]
