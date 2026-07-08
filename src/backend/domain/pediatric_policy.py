"""
Política de edad mínima para pacientes pediátricos.

Detecta menciones de niños/niñas en la conversación, extrae la edad si la mencionan,
y construye el bloque obligatorio de prompt para Gemini con mensajes empáticos y con emojis.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from typing import Mapping, Sequence

from ..schemas.clinic_policies import PediatricAgePolicies


def _fold(text: str) -> str:
    s = (text or "").strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


# Números hasta 18 escritos en palabras (ES)
_AGE_WORDS_ES: dict[str, int] = {
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
}

_AGE_WORDS_EN: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
}

# Patrones para detectar edad numérica — se aplican sobre texto ya normalizado (_fold)
# por lo que ñ→n, é→e, etc. Solo necesitamos la forma sin tilde (anos, anitos).
_AGE_DIGIT_RE = re.compile(
    r"(?:"
    # "tiene 5 anos / anitos / anos de edad"
    r"tiene\s+(\d{1,2})\s*(?:anito?s?|anos?(?:\s+de\s+edad)?)\b"
    r"|de\s+(\d{1,2})\s*(?:anito?s?|anos?)\b"
    r"|(\d{1,2})\s*(?:anito?s?|anos?(?:\s+de\s+edad)?)\b"
    r"|tiene\s+(\d{1,2})\b"
    # EN: "is 5 years old / years / y.o."
    r"|(?:is|are|he'?s|she'?s|they'?re)\s+(\d{1,2})\s*(?:years?\s+old|years?|y\.?o\.?)\b"
    r"|(\d{1,2})\s*(?:years?\s+old|y\.?o\.?)\b"
    r")",
    re.IGNORECASE,
)


@dataclass
class PediatricAgeResult:
    is_pediatric: bool
    mentioned_age: int | None
    age_eligible: bool | None  # None = no age stated; True = >= min_age; False = < min_age


def message_signals_pediatric_context(
    message: str,
    policy: PediatricAgePolicies,
) -> bool:
    """True si el mensaje contiene términos pediátricos configurados."""
    if not policy.enabled:
        return False
    folded = _fold(message)
    if not folded:
        return False
    for term in policy.trigger_terms:
        t = _fold(term)
        if len(t) >= 3 and t in folded:
            return True
    return False


def extract_mentioned_age(message: str) -> int | None:
    """Extrae la edad en años mencionada en el mensaje (0–18). None si no se detecta."""
    msg = (message or "").strip()
    if not msg:
        return None

    # Fold first so ñ→n, é→e, etc., then apply regex
    folded_msg = _fold(msg)
    m = _AGE_DIGIT_RE.search(folded_msg)
    if m:
        for g in m.groups():
            if g is not None:
                age = int(g)
                if 0 <= age <= 18:
                    return age

    # Word-based fallback (ES + EN)
    folded = folded_msg
    combined = {**_AGE_WORDS_ES, **_AGE_WORDS_EN}
    for word, val in combined.items():
        pattern = re.compile(
            r"(?:tiene|de|is|are|he'?s|she'?s)\s+" + re.escape(word) + r"\b",
            re.IGNORECASE,
        )
        if pattern.search(folded):
            return val

    return None


def classify_pediatric_context(
    message: str,
    policy: PediatricAgePolicies,
    *,
    history: Sequence[Mapping[str, str]] | None = None,
) -> PediatricAgeResult:
    """Clasifica el contexto pediátrico del mensaje (y del hilo reciente si aplica)."""
    is_ped = message_signals_pediatric_context(message, policy)
    if not is_ped and history:
        for msg in history:
            if (msg.get("role") or "").strip().lower() != "user":
                continue
            if message_signals_pediatric_context((msg.get("content") or "").strip(), policy):
                is_ped = True
                break

    if not is_ped:
        return PediatricAgeResult(is_pediatric=False, mentioned_age=None, age_eligible=None)

    age = extract_mentioned_age(message)
    if age is None and history:
        for msg in reversed(history):
            if (msg.get("role") or "").strip().lower() != "user":
                continue
            age = extract_mentioned_age((msg.get("content") or "").strip())
            if age is not None:
                break

    if age is None:
        return PediatricAgeResult(is_pediatric=True, mentioned_age=None, age_eligible=None)

    eligible = age >= policy.min_age
    return PediatricAgeResult(is_pediatric=True, mentioned_age=age, age_eligible=eligible)


def pediatric_ineligibility_result(
    message: str,
    policy: PediatricAgePolicies | None,
    *,
    history: Sequence[Mapping[str, str]] | None = None,
    stored_beneficiario_edad: int | None = None,
) -> PediatricAgeResult | None:
    """
    Si el hilo indica un niño/niña menor de edad mínima, devuelve el resultado con age_eligible=False.
    """
    if not policy or not policy.enabled:
        return None

    result = classify_pediatric_context(message, policy, history=history)
    if result.is_pediatric and result.age_eligible is False:
        return result

    if stored_beneficiario_edad is not None and stored_beneficiario_edad < policy.min_age:
        if result.is_pediatric or history_signals_pediatric_thread(history, policy):
            return PediatricAgeResult(
                is_pediatric=True,
                mentioned_age=stored_beneficiario_edad,
                age_eligible=False,
            )

    return None


def history_signals_pediatric_thread(
    history: Sequence[Mapping[str, str]] | None,
    policy: PediatricAgePolicies,
) -> bool:
    if not history:
        return False
    for msg in history:
        if (msg.get("role") or "").strip().lower() != "user":
            continue
        if message_signals_pediatric_context((msg.get("content") or "").strip(), policy):
            return True
    return False


def patient_prompt_pediatric_decline(language: str, policy: PediatricAgePolicies) -> str:
    """Mensaje fijo de declive cuando el menor no cumple edad mínima (sin pasar por Gemini)."""
    use_en = (language or "").strip().lower().startswith("en")
    min_age = policy.min_age
    if use_en:
        return (
            f"We're very sorry 💛 Our clinic currently sees children **{min_age} years and older**. "
            "If you have any other questions, I'm happy to help. 😊"
        )
    return (
        f"Lo sentimos mucho 💛 En este momento la clínica solo atiende a niños y niñas de "
        f"**{min_age} añitos en adelante**. Si tienes alguna otra consulta, con gusto te ayudo. 😊"
    )


def _render(template: str, min_age: int) -> str:
    return template.replace("{min_age}", str(min_age))


def format_pediatric_prompt_block(
    *,
    language: str,
    policy: PediatricAgePolicies,
    result: PediatricAgeResult,
) -> str:
    """Bloque obligatorio de instrucciones para Gemini cuando hay contexto pediátrico."""
    if not policy.enabled or not result.is_pediatric:
        return ""

    use_en = (language or "").strip().lower().startswith("en")
    min_age = policy.min_age
    welcome = _render(policy.welcome_en if use_en else policy.welcome_es, min_age)
    decline = _render(policy.decline_en if use_en else policy.decline_es, min_age)

    if result.age_eligible is False:
        # Edad declarada < mínimo → declinar
        if use_en:
            return (
                f"\n\n[PEDIATRIC POLICY — MANDATORY THIS TURN]\n"
                f"The patient mentioned a child under {min_age} years old (age: {result.mentioned_age}).\n"
                f"- Respond ONLY with: «{decline}»\n"
                f"- Do NOT ask questions. Do NOT offer evaluation, cleaning, or any appointment.\n"
                f"- Do NOT offer to book an appointment for this child.\n"
                f"- Do NOT call any booking tools (consultar_disponibilidad, agendar_cita, etc.).\n"
                f"- Stay warm and empathetic. Use exactly the decline message above.\n"
            )
        return (
            f"\n\n[POLÍTICA PEDIÁTRICA — OBLIGATORIO ESTE TURNO]\n"
            f"El paciente mencionó un niño/niña menor de {min_age} años (edad: {result.mentioned_age}).\n"
            f"- Responde ÚNICAMENTE con: «{decline}»\n"
            f"- NO hagas preguntas. NO ofrezcas evaluación, limpieza ni ninguna cita.\n"
            f"- NO ofrezcas agendar cita para este/a niño/niña.\n"
            f"- NO llames herramientas de citas (consultar_disponibilidad, agendar_cita, etc.).\n"
            f"- Mantente empático/a y cálido/a. Usa exactamente el mensaje de arriba.\n"
        )

    # Edad >= mínimo o no declarada → bienvenida + flujo de agendamiento tercero
    age_context = ""
    suffix_hint = ""
    if result.mentioned_age is not None:
        age_str = f"{result.mentioned_age}"
        if use_en:
            age_context = f" The child is {age_str} years old."
            suffix_hint = (
                f"- When calling agendar_cita, pass suffix_urgencia=\"menor_{age_str}_anios\" "
                f"so the calendar marks this as a minor patient.\n"
            )
        else:
            age_context = f" El/la niño/niña tiene {age_str} años."
            suffix_hint = (
                f"- Al llamar agendar_cita, pasa suffix_urgencia=\"menor_{age_str}_anios\" "
                f"para que el calendario marque esta cita como paciente menor de edad.\n"
            )

    if use_en:
        return (
            f"\n\n[PEDIATRIC POLICY — MANDATORY THIS TURN]\n"
            f"The patient is asking about a child appointment.{age_context}\n"
            f"- Respond warmly: «{welcome}»\n"
            f"- Then ask for the child's full name to book their appointment.\n"
            f"- Use es_para_tercero=true: nombre=<child's full name>, nombre_titular=<parent name if known>.\n"
            f"- The child is the beneficiary (nombre_secundario in our records). The parent stays as the WhatsApp contact.\n"
            f"{suffix_hint}"
            f"- If the child's age is unknown, ask warmly: «How old is your child? 😊»\n"
        )
    return (
        f"\n\n[POLÍTICA PEDIÁTRICA — OBLIGATORIO ESTE TURNO]\n"
        f"El contacto está consultando sobre una cita para un niño/niña.{age_context}\n"
        f"- Responde con calidez: «{welcome}»\n"
        f"- Luego pide el nombre completo del niño/niña para agendar su cita.\n"
        f"- Usa es_para_tercero=true: nombre=<nombre completo del niño/niña>, nombre_titular=<nombre del padre/madre si lo conoces>.\n"
        f"- El/la niño/niña es el beneficiario (nombre_secundario en nuestro sistema). El padre/madre sigue como contacto del WhatsApp.\n"
        f"{suffix_hint}"
        f"- Si no se conoce la edad, pregunta con calidez: «¿Cuántos añitos tiene tu niño/niña? 😊»\n"
    )


__all__ = [
    "PediatricAgeResult",
    "classify_pediatric_context",
    "extract_mentioned_age",
    "format_pediatric_prompt_block",
    "history_signals_pediatric_thread",
    "message_signals_pediatric_context",
    "patient_prompt_pediatric_decline",
    "pediatric_ineligibility_result",
]
