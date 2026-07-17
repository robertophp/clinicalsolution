"""
Manejador anti-bucle: cuando el agente no comprende al usuario, escala a un menú numérico
según el contexto activo (emergencia, same-day, confirmación de cita, oferta de horario) o menú general.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Literal

from ..schemas.clinic_policies import ConfusionLoopPolicies
from ..services.intent_classifier import (
    assistant_message_is_offer_reconfirm,
    assistant_offered_booking_or_action,
    is_polite_decline_or_farewell,
    message_signals_affirmative_continuation,
    message_signals_scheduling_input,
)
from .booking_confirmation import (
    assistant_asks_booking_confirm,
    classify_booking_confirm_response,
)
from .emergency_fork_policy import classify_emergency_choice_response

ConfusionContext = Literal[
    "emergency", "same_day", "booking_confirm", "scheduling_offer", "general"
]
GeneralMenuChoice = Literal["appointment", "prices", "info", "human", "unclear"]
BookingMenuChoice = Literal["approve", "decline", "revise", "unclear"]
SchedulingMenuChoice = Literal["hour", "other_times", "human", "unclear"]

_NON_ANSWER_TOKENS = frozenset(
    {
        "nc",
        "ntp",
        "tons",
        "tonces",
        "tonz",
        "aja",
        "pss",
        "ps",
        "eh",
        "ah",
        "mm",
        "hmm",
        "xd",
        "lol",
        "n",
        "c",
    }
)

_HOUR_OFFER_RE = re.compile(r"\b(\d{1,2}):00\b")

_GENERAL_1_RE = re.compile(
    r"\b(opci[oó]n\s*1|opcion\s*1|\b1\b|cita|agendar|appointment|book)\b",
    re.IGNORECASE,
)
_GENERAL_2_RE = re.compile(
    r"\b(opci[oó]n\s*2|opcion\s*2|\b2\b|precio|precios|costo|costos|price|prices|tratamiento)\b",
    re.IGNORECASE,
)
_GENERAL_3_RE = re.compile(
    r"\b(opci[oó]n\s*3|opcion\s*3|\b3\b|horario|horarios|ubicaci[oó]n|d[oó]nde|location|hours|address)\b",
    re.IGNORECASE,
)
_GENERAL_4_RE = re.compile(
    r"\b(opci[oó]n\s*4|opcion\s*4|\b4\b|humano|equipo|persona|doctor|doctora|especialista|team|human)\b",
    re.IGNORECASE,
)

_BOOKING_MENU_1_RE = re.compile(
    r"\b(opci[oó]n\s*1|opcion\s*1|\b1\b|confirm|confirmo|s[ií]|yes)\b",
    re.IGNORECASE,
)
_BOOKING_MENU_2_RE = re.compile(
    r"\b(opci[oó]n\s*2|opcion\s*2|\b2\b|cambiar|change|revise|otra|different)\b",
    re.IGNORECASE,
)
_BOOKING_MENU_3_RE = re.compile(
    r"\b(opci[oó]n\s*3|opcion\s*3|\b3\b|cancel|cancelar|no)\b",
    re.IGNORECASE,
)

_SCHED_OTHER_RE = re.compile(
    r"\b(otros?\s+horarios?|otra\s+hora|other\s+times?|different\s+time)\b",
    re.IGNORECASE,
)
_SCHED_HUMAN_RE = re.compile(
    r"\b(equipo|humano|persona|doctor|doctora|especialista|team|human)\b",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"\*\*", "", s)
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def _policy_text(policy: ConfusionLoopPolicies, field_es: str, field_en: str, language: str) -> str:
    use_en = (language or "").strip().lower().startswith("en")
    raw = getattr(policy, field_en if use_en else field_es, None)
    return (raw or "").strip()


def looks_like_bot_repetition(prev: str, new: str) -> bool:
    """True si la respuesta nueva es casi igual a la anterior del asistente."""
    a, b = _normalize(prev), _normalize(new)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 30 and shorter in longer and len(shorter) / len(longer) > 0.65:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.88


def message_is_ambiguous(message: str) -> bool:
    """True si el mensaje es muy corto, vacío o parece ruido sin intención clara."""
    raw = (message or "").strip()
    if not raw:
        return True
    folded = _normalize(raw)
    core = re.sub(r"[?!.]+$", "", folded).strip()
    if not core:
        return True
    if len(core) <= 4:
        return True
    if re.fullmatch(r"[\W\d_]+", core):
        return True
    if re.fullmatch(r"(x+|a+|jaja+|jsjs+|zzz+|asd+|q+|xq|mm+|hmm+)", core):
        return True
    if re.search(r"(.)\1{2,}", core):
        return True
    tokens = core.split()
    if len(tokens) <= 3 and all(t in _NON_ANSWER_TOKENS or len(t) <= 2 for t in tokens):
        return True
    if core in _NON_ANSWER_TOKENS:
        return True
    if re.fullmatch(r"[a-z]{1,2}(\s+[a-z]{1,2})?", core):
        return True
    return False


def extract_offered_hours(assistant_text: str) -> list[str]:
    """Extrae horas HH:00 ofrecidas en el último mensaje del asistente."""
    seen: set[str] = set()
    hours: list[str] = []
    for match in _HOUR_OFFER_RE.finditer(assistant_text or ""):
        hour = int(match.group(1))
        if 0 <= hour <= 23:
            fmt = f"{hour:02d}:00"
            if fmt not in seen:
                seen.add(fmt)
                hours.append(fmt)
    return hours


def assistant_awaiting_user_answer(last_assistant: str) -> bool:
    """True si el bot acaba de ofrecer algo o hizo una pregunta que espera respuesta."""
    text = (last_assistant or "").strip()
    if not text:
        return False
    if assistant_offered_booking_or_action(text):
        return True
    if assistant_asks_booking_confirm(text):
        return True
    if assistant_message_is_offer_reconfirm(text):
        return True
    return text.rstrip().endswith("?")


def user_reply_is_non_answer(message: str, last_assistant: str) -> bool:
    """
    True si el bot esperaba respuesta y el usuario envió algo no usable (Caso B).
    """
    if not assistant_awaiting_user_answer(last_assistant):
        return False
    if message_signals_affirmative_continuation(message):
        return False
    if is_polite_decline_or_farewell(message):
        return False
    if message_signals_scheduling_input(message):
        return False
    if classify_booking_confirm_response(message, "es") != "unclear":
        return False
    return message_is_ambiguous(message)


def resolve_confusion_context(
    *,
    emergency_phase: str,
    same_day_phase: str,
    booking_phase: str,
) -> ConfusionContext:
    """Determina qué menú mostrar según el flujo activo."""
    if (emergency_phase or "none").strip() == "awaiting_choice":
        return "emergency"
    if (same_day_phase or "none").strip() == "awaiting_choice":
        return "same_day"
    if (booking_phase or "none").strip() == "awaiting_confirm":
        return "booking_confirm"
    return "general"


def find_scheduling_offer_from_history(
    history: list[dict[str, str]] | None,
) -> tuple[ConfusionContext, list[str]] | None:
    """Busca el último mensaje del asistente con horarios ofrecidos en el historial."""
    if not history:
        return None
    for entry in reversed(list(history)):
        if (entry.get("role") or "").strip() != "assistant":
            continue
        content = (entry.get("content") or "").strip()
        if not content:
            continue
        hours = extract_offered_hours(content)
        if hours and assistant_offered_booking_or_action(content):
            return ("scheduling_offer", hours)
    return None


def resolve_non_answer_menu_context(
    *,
    last_assistant: str,
    emergency_phase: str,
    same_day_phase: str,
    booking_phase: str,
    history: list[dict[str, str]] | None = None,
) -> ConfusionContext:
    """Contexto del menú cuando el usuario no respondió lo esperado."""
    hours = extract_offered_hours(last_assistant)
    if hours and assistant_offered_booking_or_action(last_assistant):
        return "scheduling_offer"
    sched_from_history = find_scheduling_offer_from_history(history)
    if sched_from_history and assistant_awaiting_user_answer(last_assistant):
        return "scheduling_offer"
    return resolve_confusion_context(
        emergency_phase=emergency_phase,
        same_day_phase=same_day_phase,
        booking_phase=booking_phase,
    )


def offered_hours_for_non_answer_context(
    *,
    context: ConfusionContext,
    last_assistant: str,
    history: list[dict[str, str]] | None = None,
) -> list[str] | None:
    if context != "scheduling_offer":
        return None
    hours = extract_offered_hours(last_assistant)
    if hours:
        return hours
    sched = find_scheduling_offer_from_history(history)
    return sched[1] if sched else None


def patient_prompt_confusion_menu(
    language: str,
    policy: ConfusionLoopPolicies,
    *,
    context: ConfusionContext,
    first_name: str | None = None,
    offered_hours: list[str] | None = None,
) -> str:
    """Menú numerado de rescate según contexto."""
    custom = _policy_text(policy, "menu_es", "menu_en", language)
    if context == "general" and custom:
        return custom

    use_en = (language or "").strip().lower().startswith("en")
    name_bit = f", {first_name}" if first_name else ""

    if context == "scheduling_offer":
        hours = offered_hours or []
        lines: list[str] = []
        if use_en:
            header = f"Sorry{name_bit}, I didn't quite understand 🙈 Please reply with a number:\n"
            for idx, hour in enumerate(hours, start=1):
                lines.append(f"{idx}. {hour}")
            next_idx = len(hours) + 1
            lines.append(f"{next_idx}. See other times")
            lines.append(f"{next_idx + 1}. Speak with our team")
            return header + "\n".join(lines)
        header = f"Disculpa{name_bit}, no logré entenderte 🙈 Elige un número:\n"
        for idx, hour in enumerate(hours, start=1):
            lines.append(f"{idx}. {hour}")
        next_idx = len(hours) + 1
        lines.append(f"{next_idx}. Ver otros horarios")
        lines.append(f"{next_idx + 1}. Hablar con el equipo")
        return header + "\n".join(lines)

    if context == "emergency":
        if use_en:
            return (
                f"Sorry{name_bit}, I didn't quite understand 🙈 To help you, please reply with a number:\n"
                "1. Emergency appointment tomorrow at the first available time\n"
                "2. Have our medical team contact you"
            )
        return (
            f"Disculpa{name_bit}, no logré entenderte 🙈 Para ayudarte, elige un número:\n"
            "1. Cita de emergencia mañana a primera hora\n"
            "2. Que nuestro equipo médico te contacte"
        )

    if context == "same_day":
        if use_en:
            return (
                f"Sorry{name_bit}, I didn't quite understand 🙈 Please reply with a number:\n"
                "1. Schedule you as soon as possible for tomorrow at the first available time\n"
                "2. Have our team contact you to check same-day availability"
            )
        return (
            f"Disculpa{name_bit}, no logré entenderte 🙈 Elige un número:\n"
            "1. Agendarte lo antes posible para mañana a primera hora\n"
            "2. Que nuestro equipo te contacte para revisar cupos de hoy"
        )

    if context == "booking_confirm":
        if use_en:
            return (
                "Sorry, I didn't quite understand 🙈 Please reply with a number:\n"
                "1. Confirm the appointment\n"
                "2. Change something\n"
                "3. Cancel"
            )
        return (
            "Disculpa, no logré entenderte 🙈 Dime con un número:\n"
            "1. Confirmar la cita\n"
            "2. Cambiar algo\n"
            "3. Cancelar"
        )

    if use_en:
        return (
            f"Sorry{name_bit}, I don't think I'm understanding you well 🙈 "
            "To help you better, please reply with a number:\n"
            "1. Book or manage an appointment\n"
            "2. Prices and treatments\n"
            "3. Hours and location\n"
            "4. Speak with our clinic team"
        )
    return (
        f"Disculpa{name_bit}, creo que no te estoy entendiendo bien 🙈 "
        "Para ayudarte mejor, dime con un número:\n"
        "1. Agendar o gestionar una cita\n"
        "2. Precios y tratamientos\n"
        "3. Horarios y ubicación\n"
        "4. Hablar con el equipo de la clínica"
    )


def patient_prompt_confusion_menu_unclear(
    language: str,
    policy: ConfusionLoopPolicies,
    *,
    context: ConfusionContext,
) -> str:
    custom = _policy_text(policy, "menu_unclear_es", "menu_unclear_en", language)
    if custom:
        return custom
    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        return (
            "I'm still having trouble understanding 🙈 Please reply with just the number "
            "of the option that fits best (for example: 1 or 2)."
        )
    return (
        "Sigo sin lograr entenderte 🙈 Por favor responde solo con el número de la opción "
        "que prefieras (por ejemplo: 1 o 2)."
    )


def patient_prompt_non_answer_reask(language: str, *, last_assistant: str) -> str:
    """Re-pregunta breve antes de escalar al menú (1er turno confuso en Caso B)."""
    use_en = (language or "").strip().lower().startswith("en")
    if assistant_message_is_offer_reconfirm(last_assistant):
        if use_en:
            return (
                "I want to make sure I help you correctly 🙂 "
                "Would you like to book the appointment? Reply **yes** or tell me what you prefer."
            )
        return (
            "Quiero ayudarte bien 🙂 ¿Quieres agendar la cita? "
            "Responde **sí** o cuéntame qué prefieres."
        )
    if extract_offered_hours(last_assistant):
        if use_en:
            return (
                "I didn't quite catch that 🙂 Which time works for you? "
                "You can reply with a time like 08:00 or tell me if you'd like other options."
            )
        return (
            "No te entendí del todo 🙂 ¿Qué hora te queda mejor? "
            "Puedes responder con una hora como 08:00 o decirme si prefieres otras opciones."
        )
    if use_en:
        return (
            "I'm not sure I understood 🙂 Could you tell me a bit more? "
            "I can help with appointments, treatments, prices, or opening hours."
        )
    return (
        "No estoy seguro de haberte entendido 🙂 ¿Me cuentas un poco más? "
        "Puedo ayudarte con citas, tratamientos, precios u horarios."
    )


def patient_prompt_general_menu_routed(language: str, choice: GeneralMenuChoice) -> str:
    use_en = (language or "").strip().lower().startswith("en")
    if choice == "appointment":
        if use_en:
            return "Happy to help! 📅 What type of appointment or evaluation would you like to book?"
        return "¡Con gusto! 📅 ¿Para qué tipo de cita o evaluación te gustaría agendar?"
    if choice == "prices":
        if use_en:
            return "Sure 💰 Which treatment would you like pricing for?"
        return "Claro 💰 ¿De qué tratamiento te gustaría conocer el precio?"
    if choice == "info":
        if use_en:
            return "Of course 📍 Would you like our opening hours, address, or both?"
        return "Con gusto 📍 ¿Te comparto horarios, ubicación o ambos?"
    if use_en:
        return "I'll connect you with our team 👩‍⚕️"
    return "Te conecto con nuestro equipo 👩‍⚕️"


def patient_prompt_scheduling_other_times(language: str) -> str:
    use_en = (language or "").strip().lower().startswith("en")
    if use_en:
        return "Sure 🕐 Tell me which day works better and I'll check other available times."
    return "Claro 🕐 Dime qué día te queda mejor y reviso otros horarios disponibles."


def classify_scheduling_menu_choice(
    message: str,
    *,
    offered_hours: list[str],
) -> tuple[SchedulingMenuChoice, str | None]:
    """
    Clasifica respuesta al menú de horarios.
    Retorna (choice, hour) donde hour solo aplica si choice == 'hour'.
    """
    hours = offered_hours or []
    folded = _normalize(message)
    if not folded:
        return ("unclear", None)

    num_match = re.search(r"\b(\d+)\b", folded)
    if num_match:
        n = int(num_match.group(1))
        if 1 <= n <= len(hours):
            return ("hour", hours[n - 1])
        if n == len(hours) + 1:
            return ("other_times", None)
        if n == len(hours) + 2:
            return ("human", None)

    if _SCHED_OTHER_RE.search(folded):
        return ("other_times", None)
    if _SCHED_HUMAN_RE.search(folded):
        return ("human", None)

    for hour in hours:
        if hour.replace(":00", "") in folded or hour in folded:
            return ("hour", hour)

    return ("unclear", None)


def classify_confusion_menu_choice(
    message: str,
    *,
    context: ConfusionContext,
    offered_hours: list[str] | None = None,
) -> str:
    """Clasifica la respuesta numérica según el contexto del menú activo."""
    if context == "scheduling_offer":
        choice, _hour = classify_scheduling_menu_choice(message, offered_hours=offered_hours or [])
        return choice
    if context in ("emergency", "same_day"):
        return classify_emergency_choice_response(message)
    if context == "booking_confirm":
        return classify_booking_menu_choice(message)
    return classify_general_menu_choice(message)


def classify_general_menu_choice(message: str) -> GeneralMenuChoice:
    folded = _normalize(message)
    if not folded:
        return "unclear"
    hits = [
        ("appointment", bool(_GENERAL_1_RE.search(folded))),
        ("prices", bool(_GENERAL_2_RE.search(folded))),
        ("info", bool(_GENERAL_3_RE.search(folded))),
        ("human", bool(_GENERAL_4_RE.search(folded))),
    ]
    matched = [k for k, ok in hits if ok]
    if len(matched) == 1:
        return matched[0]  # type: ignore[return-value]
    return "unclear"


def classify_booking_menu_choice(message: str) -> BookingMenuChoice:
    """Clasifica respuesta numérica en menú de confirmación de cita."""
    folded = _normalize(message)
    if not folded:
        return "unclear"
    c1 = bool(_BOOKING_MENU_1_RE.search(folded))
    c2 = bool(_BOOKING_MENU_2_RE.search(folded))
    c3 = bool(_BOOKING_MENU_3_RE.search(folded))
    if sum([c1, c2, c3]) != 1:
        fb = classify_booking_confirm_response(message, "es")
        if fb != "unclear":
            return fb
        return "unclear"
    if c1:
        return "approve"
    if c2:
        return "revise"
    return "decline"


__all__ = [
    "BookingMenuChoice",
    "ConfusionContext",
    "GeneralMenuChoice",
    "SchedulingMenuChoice",
    "assistant_awaiting_user_answer",
    "classify_booking_menu_choice",
    "classify_confusion_menu_choice",
    "classify_general_menu_choice",
    "classify_scheduling_menu_choice",
    "extract_offered_hours",
    "find_scheduling_offer_from_history",
    "looks_like_bot_repetition",
    "message_is_ambiguous",
    "offered_hours_for_non_answer_context",
    "patient_prompt_confusion_menu",
    "patient_prompt_confusion_menu_unclear",
    "patient_prompt_general_menu_routed",
    "patient_prompt_non_answer_reask",
    "patient_prompt_scheduling_other_times",
    "resolve_confusion_context",
    "resolve_non_answer_menu_context",
    "user_reply_is_non_answer",
]
