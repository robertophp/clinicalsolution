from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Mapping, Sequence


class Intent(str, Enum):
    """Intenciones de alto nivel que el asistente puede manejar."""

    CITA = "cita"  # agendar, cancelar, reagendar, cambiar horario
    SERVICIOS = "servicios"  # precios, tipos de tratamiento, qué incluye cada servicio
    CLINICA_INFO = "clinica_info"  # dirección, horarios, formas de pago, contacto
    SEGUIMIENTO_CITA = "seguimiento_cita"  # llegar tarde, confirmar asistencia, dudas sobre cita concreta
    SMALL_TALK = "small_talk"  # saludos, gracias, despedidas breves
    UNINTELLIGIBLE = "unintelligible"  # jerga/ruido: no se puede determinar intención
    OUT_OF_DOMAIN = "out_of_domain"  # tema ajeno coherente (pizza, mecánica, etc.)


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def _fold_for_match(text: str) -> str:
    """Minúsculas + sin acentos para comparaciones tolerantes (WhatsApp, typos)."""
    return _strip_accents(_normalize(text))


# Consultas de precio / costo (variantes coloquiales ES/EN).
_PRICE_INQUIRY_RE = re.compile(
    r"(?:"
    r"\b(?:cuanto|cuánto|que|qué|q|cual|cuál)\s+"
    r"(?:cuesta|cuestan|vale|valen|sale|salen|cobra|cobran|es|son|tiene|tienen)\b"
    r"|"
    r"\b(?:precio|precios|costo|costos|tarifa|tarifas|valor|valores|cobro|cobran)\b"
    r"|"
    r"\b(?:a\s+cuanto|a\s+cuánto|por\s+cuanto|por\s+cuánto)\b"
    r"|"
    r"\bhow\s+much\b|\bwhat(?:'s|\s+is|\s+does|\s+do)\s+.*\s+cost\b|\bprice\s+of\b|\bcost\s+of\b"
    r")",
    re.IGNORECASE,
)

# Preguntas sobre un servicio / tratamiento (información, no solo precio).
_SERVICE_INFO_RE = re.compile(
    r"\b(?:"
    r"que\s+es|qué\s+es|que\s+incluye|qué\s+incluye|"
    r"informacion\s+sobre|información\s+sobre|info\s+(?:de|del|sobre)|"
    r"hacen|ofrecen|tienen|realizan|trabajan|manejan|"
    r"me\s+interesa|quiero\s+saber|mas\s+info|más\s+info|"
    r"cuentame|cuéntame|explicame|explícame|"
    r"what\s+is|what\s+does|tell\s+me\s+about|do\s+you\s+(?:do|offer|have)"
    r")\b",
    re.IGNORECASE,
)

# Palabras genéricas que aparecen en los títulos del manual pero no sirven como
# keyword discriminante (evita falsos positivos triviales en el fallback de reglas).
_KB_TITLE_STOPWORDS = {
    "dental",
    "dentales",
    "alta",
    "estetica",
    "interno",
    "profunda",
    "tecnologia",
    "tecnologica",
}


def _msg_contains_term(msg_folded: str, term: str) -> bool:
    """True si ``term`` aparece en ``msg_folded`` (ambos sin acentos, minúsculas)."""
    t = _fold_for_match(term)
    if len(t) < 3:
        return False
    return t in msg_folded


def _msg_matches_any_keyword(msg_folded: str, keywords: Sequence[str]) -> bool:
    return any(_msg_contains_term(msg_folded, k) for k in keywords if k and k.strip())


def message_signals_price_inquiry(message: str) -> bool:
    """True si el mensaje pregunta precio, costo o valor (formas coloquiales incluidas)."""
    folded = _fold_for_match(message)
    if not folded:
        return False
    return bool(_PRICE_INQUIRY_RE.search(folded))


def message_signals_service_info_inquiry(message: str) -> bool:
    """True si el mensaje pide información sobre un servicio o tratamiento."""
    folded = _fold_for_match(message)
    if not folded:
        return False
    return bool(_SERVICE_INFO_RE.search(folded))


def extract_knowledge_base_topics(knowledge_base: str | None) -> list[str]:
    """
    Extrae los títulos de sección (``## ...``) del manual de la clínica como temas limpios.

    Convierte encabezados tipo ``## **5\\. ENDODONCIA**`` en ``"ENDODONCIA"``. Sirve para
    que los clasificadores de intención traten esos temas como dentro de dominio.
    """
    if not knowledge_base:
        return []
    topics: list[str] = []
    for raw_line in knowledge_base.splitlines():
        line = raw_line.strip()
        if not line.startswith("## "):
            continue
        title = line.lstrip("#").strip()
        title = title.replace("**", "").strip()
        # Quitar numeración tipo "5\. " o "5. " al inicio.
        title = re.sub(r"^\d+\s*\\?\.\s*", "", title).strip()
        title = title.strip("*").strip()
        if title:
            topics.append(title)
    return topics


def knowledge_base_service_keywords(knowledge_base: str | None) -> list[str]:
    """
    Deriva keywords (con y sin acentos) a partir de los títulos del manual para el
    clasificador por reglas. Mantiene tokens significativos (>= 5 letras) que no sean
    palabras genéricas, de modo que tratamientos como endodoncia, carillas, laminados,
    gingivectomía o invisalign se reconozcan como dentro de dominio.
    """
    keywords: list[str] = []
    seen: set[str] = set()
    for topic in extract_knowledge_base_topics(knowledge_base):
        for token in re.split(r"[^0-9A-Za-zÁÉÍÓÚáéíóúÜüÑñ]+", topic):
            token = token.strip().lower()
            if len(token) < 5:
                continue
            base = _strip_accents(token)
            if base in _KB_TITLE_STOPWORDS:
                continue
            for variant in (token, base):
                if variant and variant not in seen:
                    seen.add(variant)
                    keywords.append(variant)
    return keywords


# Rechazos corteses y despedidas: deben ser SMALL_TALK, nunca OUT_OF_DOMAIN.
_POLITE_DECLINE_EXACT = frozenset(
    {
        "no gracias",
        "no, gracias",
        "nop",
        "no thanks",
        "no thank you",
        "está bien gracias",
        "esta bien gracias",
        "todo bien gracias",
        "por ahora no",
        "no por ahora",
        "mejor no",
        "no quiero",
        "ok gracias",
        "vale gracias",
        "perfecto gracias",
        "listo gracias",
        "de acuerdo gracias",
    }
)
_FAREWELL_EXACT = frozenset(
    {
        "bye",
        "ok bye",
        "goodbye",
        "chao",
        "nos vemos",
        "hasta luego",
        "adiós",
        "adios",
        "que tengas buen día",
        "que tengas buen dia",
        "buen día",
        "buen dia",
    }
)
_GREETING_OR_THANKS_PREFIX = (
    "hola",
    "buenos días",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
    "gracias",
    "muchas gracias",
)
_OFFER_CONTEXT_MARKERS = (
    "agendar",
    "agenda",
    "evaluación",
    "evaluacion",
    "cita de evaluación",
    "cita de evaluacion",
    "¿te gustaría",
    "te gustaría",
    "te gustaria",
    "agendar una cita",
    "agendar tu cita",
    "reservar",
    "reserva",
    "ver horarios",
    "horarios disponibles",
    "qué día",
    "que dia",
    "qué hora",
    "que hora",
    "confirmo",
    "confirmar",
    "would you like to book",
    "book an appointment",
    "schedule an appointment",
)

_AFFIRMATIVE_FILLERS = frozenset(
    {
        "porfa",
        "por",
        "favor",
        "gracias",
        "muchas",
        "plis",
        "please",
        "thanks",
        "thank",
        "you",
    }
)
_AFFIRMATIVE_CORE = frozenset(
    {
        "si",
        "sí",
        "ok",
        "okay",
        "vale",
        "claro",
        "dale",
        "listo",
        "perfecto",
        "yes",
        "sure",
        "bueno",
        "bien",
    }
)
_AFFIRMATIVE_PHRASES = frozenset(
    {
        "de acuerdo",
        "esta bien",
        "está bien",
        "ok perfecto",
        "vale perfecto",
        "claro que si",
        "claro que sí",
    }
)
_SCHEDULING_INPUT_RE = re.compile(
    r"(?:"
    r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.?\s*m\.?|p\.?\s*m\.?)\b"
    r"|\b\d{1,2}\s*:\s*\d{2}\b"
    r"|\b(?:a\s+las|las)\s+\d{1,2}\b"
    r"|\b(?:lunes|martes|miercoles|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b"
    r"|\b(?:manana|ma[nñ]ana|pasado\s+manana|pasado\s+ma[nñ]ana)\b"
    r"|\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r")",
    re.IGNORECASE,
)
_SCHEDULING_ASK_MARKERS = (
    "que hora",
    "qué hora",
    "que dia",
    "qué día",
    "hora en punto",
    "elegir una hora",
    "elegir un horario",
    "what time",
    "which day",
    "pick a time",
)

# Tratamientos / términos dentales frecuentes (complementan catálogo y manual).
_CORE_SERVICIOS_KEYWORDS = [
    "limpieza",
    "extracción",
    "extraccion",
    "ortodoncia",
    "blanqueamiento",
    "corona",
    "endodoncia",
    "caries",
    "empaste",
    "relleno",
    "evaluacion",
    "evaluación",
    "radiografia",
    "radiografía",
    "panoramica",
    "panorámica",
    "implante",
    "invisalign",
    "carilla",
    "gingivectomia",
    "gingivectomía",
    "protesis",
    "prótesis",
    "brackets",
    "tratamiento",
    "tratamientos",
    "servicio",
    "servicios",
]


def _last_assistant_content(history: Sequence[Mapping[str, str]] | None) -> str:
    if not history:
        return ""
    for entry in reversed(list(history)):
        if (entry.get("role") or "").strip() == "assistant":
            return _normalize(entry.get("content") or "")
    return ""


def is_polite_decline_or_farewell(message: str) -> bool:
    """True si el mensaje es un rechazo cortés o despedida breve (small talk)."""
    msg = _normalize(message)
    if not msg:
        return False
    if msg in _POLITE_DECLINE_EXACT or msg in _FAREWELL_EXACT:
        return True
    if any(msg.startswith(prefix) for prefix in _GREETING_OR_THANKS_PREFIX):
        return True
    # Despedidas cortas: "ok bye", "bye gracias", etc. (≤4 palabras con bye/chao/adios).
    words = msg.split()
    if len(words) <= 4 and any(token in msg for token in ("bye", "chao", "adios", "adiós")):
        return True
    return False


def is_contextual_offer_decline(
    message: str,
    history: Sequence[Mapping[str, str]] | None,
) -> bool:
    """Rechazo breve tras una oferta de cita/evaluación del asistente."""
    msg = _normalize(message)
    if not msg or not history:
        return False
    if msg not in ("no", "nop", "nope", "nah", "no.", "no,"):
        return False
    last = _last_assistant_content(history)
    if not last:
        return False
    return assistant_offered_booking_or_action(last)


def assistant_offered_booking_or_action(assistant_text: str) -> bool:
    """True si el mensaje del asistente invitó a agendar, confirmar o elegir horario."""
    text = _fold_for_match(assistant_text)
    if not text:
        return False
    return any(marker in text for marker in _OFFER_CONTEXT_MARKERS)


def message_signals_scheduling_input(message: str) -> bool:
    """True si el mensaje aporta hora, día u otro dato de agendamiento."""
    folded = _fold_for_match(message)
    if not folded:
        return False
    return bool(_SCHEDULING_INPUT_RE.search(folded))


def assistant_asked_scheduling_detail(assistant_text: str) -> bool:
    """True si el asistente pidió elegir hora o día."""
    text = _fold_for_match(assistant_text)
    if not text:
        return False
    return any(marker in text for marker in _SCHEDULING_ASK_MARKERS)


def _strip_affirmative_fillers(text: str) -> str:
    tokens = text.split()
    while tokens and tokens[0] in _AFFIRMATIVE_FILLERS:
        tokens.pop(0)
    while tokens and tokens[-1] in _AFFIRMATIVE_FILLERS:
        tokens.pop()
    return " ".join(tokens)


def message_signals_affirmative_continuation(message: str) -> bool:
    """
    Mensaje corto cuyo núcleo es afirmación (p. ej. «si porfa», «dale», «sí gracias»).
    Enfoque estructural: quita muletillas y valida token afirmativo, no lista infinita.
    """
    msg = _normalize(message)
    if not msg:
        return False
    msg = re.sub(r"[!?.]+$", "", msg).strip()
    if not msg:
        return False
    words = msg.split()
    if len(words) > 6:
        return False

    core = _strip_affirmative_fillers(msg)
    if not core:
        return False
    if core in _AFFIRMATIVE_PHRASES:
        return True

    core_words = core.split()
    if len(core_words) > 4:
        return False
    if core_words[0] in _AFFIRMATIVE_CORE:
        return True
    if all(w in _AFFIRMATIVE_CORE for w in core_words):
        return True
    return False


def is_contextual_offer_acceptance(
    message: str,
    history: Sequence[Mapping[str, str]] | None,
) -> bool:
    """Afirmación breve tras una oferta de cita/evaluación del asistente."""
    if not history:
        return False
    last = _last_assistant_content(history)
    if not last or not assistant_offered_booking_or_action(last):
        return False

    msg = _normalize(message)
    if not msg:
        return False

    if message_signals_affirmative_continuation(message):
        return True

    # Seguimiento muy corto tras oferta («?», «cuándo», «qué día»).
    if len(msg.split()) <= 3:
        if msg in ("?", "??", "???") or msg.endswith("?"):
            return True
        followup_markers = ("cuando", "cuándo", "que dia", "qué día", "que hora", "qué hora", "when")
        msg_folded = _fold_for_match(msg)
        if any(marker in msg_folded for marker in followup_markers):
            return True
    return False


def assistant_message_is_offer_reconfirm(assistant_text: str) -> bool:
    """True si el último mensaje del asistente fue una reconfirmación de oferta de cita."""
    text = _fold_for_match(assistant_text)
    if not text:
        return False
    return "para confirmar" in text or "just to confirm" in text


def should_reconfirm_after_booking_offer(
    message: str,
    history: Sequence[Mapping[str, str]] | None,
) -> bool:
    """
    Respuesta ambigua tras oferta de cita: conviene reconfirmar en lugar del guardrail.

    Aplica cuando el asistente acaba de ofrecer agendar, el paciente respondió algo
    corto que no encaja en afirmación/rechazo claro, y aún no se reconfirmó en el turno anterior.
    """
    if not history:
        return False
    msg = _normalize(message)
    if not msg or len(msg.split()) > 10:
        return False

    last = _last_assistant_content(history)
    if not last or not assistant_offered_booking_or_action(last):
        return False
    if assistant_message_is_offer_reconfirm(last):
        return False

    if message_signals_affirmative_continuation(message):
        return False
    if is_polite_decline_or_farewell(msg) or is_contextual_offer_decline(msg, history):
        return False
    if message_signals_scheduling_input(message):
        return False
    return True


def should_fail_open_after_offer_reconfirm(
    message: str,
    history: Sequence[Mapping[str, str]] | None,
) -> bool:
    """Tras reconfirmar una oferta, segunda respuesta ambigua → dejar pasar a Gemini (CITA)."""
    if not history:
        return False
    msg = _normalize(message)
    if not msg or len(msg.split()) > 10:
        return False
    last = _last_assistant_content(history)
    if not assistant_message_is_offer_reconfirm(last):
        return False
    if is_polite_decline_or_farewell(msg) or is_contextual_offer_decline(msg, history):
        return False
    return True


def _inherit_intent_from_history(
    history: Sequence[Mapping[str, str]],
) -> Intent | None:
    """Herencia de intención a partir del historial reciente."""
    for prev in reversed(list(history)):
        content = _normalize(prev.get("content", ""))
        if not content:
            continue
        if "cita" in content or "agendar" in content or "reservar" in content or "reagendar" in content:
            return Intent.CITA
        content_folded = _fold_for_match(content)
        if (
            _msg_matches_any_keyword(content_folded, _CORE_SERVICIOS_KEYWORDS)
            or message_signals_price_inquiry(content)
            or "precio" in content_folded
        ):
            return Intent.SERVICIOS
    return None


def _is_servicios_inquiry(
    msg_folded: str,
    *,
    extra_service_keywords: Sequence[str] | None,
) -> bool:
    """
    True si el mensaje es una consulta de precio, información o menciona un servicio conocido.
    Usa matching acento-insensible y keywords del catálogo + manual + núcleo dental.
    """
    if message_signals_price_inquiry(msg_folded):
        return True

    all_keywords = list(_CORE_SERVICIOS_KEYWORDS)
    if extra_service_keywords:
        all_keywords.extend(k for k in extra_service_keywords if k and str(k).strip())

    if _msg_matches_any_keyword(msg_folded, all_keywords):
        return True

    if message_signals_service_info_inquiry(msg_folded) and _msg_matches_any_keyword(
        msg_folded, all_keywords
    ):
        return True

    return False


def classify_intent(
    message: str,
    language: str,
    history: Sequence[Mapping[str, str]] | None = None,
    *,
    extra_service_keywords: Sequence[str] | None = None,
) -> Intent:
    """
    Clasificación ligera de intención basada en reglas.

    Diseñada para ser fácilmente reemplazada o complementada por un clasificador LLM
    en el futuro sin cambiar la interfaz.

    ``extra_service_keywords`` permite ampliar los tratamientos reconocidos como
    dentro de dominio (p. ej. manual de la clínica + catálogo de servicios).
    """
    msg = _normalize(message)
    msg_folded = _fold_for_match(message)
    if not msg:
        return Intent.OUT_OF_DOMAIN

    # Rechazos corteses y despedidas (antes de otras reglas para no caer en OUT_OF_DOMAIN).
    if is_polite_decline_or_farewell(msg) or is_contextual_offer_decline(msg, history):
        return Intent.SMALL_TALK

    # Hora/día tras pedir horario o en flujo de cita → continuar agendamiento.
    if history and message_signals_scheduling_input(message):
        last = _last_assistant_content(history)
        if assistant_asked_scheduling_detail(last) or assistant_offered_booking_or_action(last):
            return Intent.CITA

    # Confirmaciones cortas: heredar intención previa usando el historial reciente.
    if history and message_signals_affirmative_continuation(message):
        inherited = _inherit_intent_from_history(history)
        if inherited is not None:
            return inherited

    # Aceptación contextual tras oferta explícita del asistente (espejo de is_contextual_offer_decline).
    if is_contextual_offer_acceptance(message, history):
        return Intent.CITA

    # Palabras clave para citas (agendar / cancelar / reagendar)
    cita_keywords = [
        "cita",
        "agendar",
        "agenda",
        "reservar",
        "reserva",
        "programar",
        "reagendar",
        "cambiar cita",
        "cambia mi cita",
        "reprogramar",
        "cancelar cita",
        "cancela mi cita",
    ]
    if any(k in msg_folded for k in cita_keywords):
        # Subtipo seguimiento: llegar tarde, confirmar, etc.
        seguimiento_keywords = [
            "llego tarde",
            "voy tarde",
            "retraso",
            "confirmo mi cita",
            "confirmar mi cita",
            "todavía sigue en pie",
            "sigue en pie",
            "confirmar asistencia",
        ]
        if any(k in msg_folded for k in seguimiento_keywords):
            return Intent.SEGUIMIENTO_CITA
        return Intent.CITA

    # Servicios / tratamientos / precios (catálogo + manual + patrones flexibles)
    if _is_servicios_inquiry(msg_folded, extra_service_keywords=extra_service_keywords):
        return Intent.SERVICIOS

    # Información de la clínica (dirección, horarios, contacto)
    clinica_info_keywords = [
        "dirección",
        "direccion",
        "ubicación",
        "ubicacion",
        "dónde están",
        "donde estan",
        "cómo llegar",
        "como llegar",
        "horario",
        "horarios",
        "abren",
        "cierran",
        "teléfono",
        "telefono",
        "número",
        "numero",
        "whatsapp",
        "parqueo",
        "parqueadero",
        "estacionamiento",
        "formas de pago",
        "pago",
    ]
    if any(k in msg_folded for k in clinica_info_keywords):
        return Intent.CLINICA_INFO

    # Última oportunidad: afirmación contextual antes de OUT_OF_DOMAIN.
    if is_contextual_offer_acceptance(message, history):
        return Intent.CITA

    return Intent.OUT_OF_DOMAIN


__all__ = [
    "Intent",
    "classify_intent",
    "extract_knowledge_base_topics",
    "knowledge_base_service_keywords",
    "is_polite_decline_or_farewell",
    "is_contextual_offer_decline",
    "is_contextual_offer_acceptance",
    "assistant_offered_booking_or_action",
    "assistant_asked_scheduling_detail",
    "assistant_message_is_offer_reconfirm",
    "message_signals_affirmative_continuation",
    "message_signals_scheduling_input",
    "should_reconfirm_after_booking_offer",
    "should_fail_open_after_offer_reconfirm",
    "message_signals_price_inquiry",
    "message_signals_service_info_inquiry",
]
