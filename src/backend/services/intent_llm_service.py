from __future__ import annotations

from typing import Mapping, Sequence

from .gemini_service import CLASSIFIER_MAX_OUTPUT_TOKENS, GeminiService, GeminiServiceError
from .intent_classifier import Intent


def llm_classify_intent(
    gemini: GeminiService,
    *,
    message: str,
    language: str,
    history: Sequence[Mapping[str, str]] | None = None,
    clinic_topics: Sequence[str] | None = None,
) -> Intent:
    """
    Clasifica la intención del mensaje usando Gemini como clasificador ligero.

    Devuelve una etiqueta de Intent (cita, servicios, clinica_info, seguimiento_cita,
    small_talk u out_of_domain). Si algo falla, lanza GeminiServiceError y el
    llamador debería hacer fallback al clasificador por reglas.

    ``clinic_topics`` son temas adicionales (p. ej. tratamientos del manual de la clínica)
    que deben tratarse como DENTRO DE DOMINIO y clasificarse como ``servicios``.
    """
    msg = (message or "").strip()
    if not msg:
        return Intent.OUT_OF_DOMAIN

    topics = [t.strip() for t in (clinic_topics or []) if t and t.strip()]
    topics_block_es = ""
    topics_block_en = ""
    if topics:
        topics_list = "; ".join(topics)
        topics_block_es = (
            "Esta clínica ofrece además estos tratamientos/temas, que SIEMPRE están DENTRO "
            "DE DOMINIO (clasifícalos como 'servicios' aunque uses otra palabra el paciente): "
            f"{topics_list}.\n"
        )
        topics_block_en = (
            "This clinic also offers these treatments/topics, which are ALWAYS IN DOMAIN "
            "(classify them as 'servicios' even if the patient uses different wording): "
            f"{topics_list}.\n"
        )

    # Tomamos solo las últimas pocas entradas del historial para dar contexto.
    history = list(history or [])[-4:]
    history_text_parts: list[str] = []
    for m in history:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        prefix = "Usuario" if role == "user" else "Asistente"
        history_text_parts.append(f"{prefix}: {content}")
    history_text = "\n".join(history_text_parts)

    # Prompt de clasificación.
    if language == "en":
        instructions = (
            "Classify the patient's message into exactly one of these labels:\n"
            "- cita\n"
            "- servicios\n"
            "- clinica_info\n"
            "- seguimiento_cita\n"
            "- small_talk\n"
            "- unintelligible\n"
            "- out_of_domain\n\n"
            "Consider as IN DOMAIN anything related to this dental clinic: appointments (book, change, cancel), "
            "dental treatments or services, dental symptoms and doubts, prices, payment, clinic address, opening hours "
            "and basic small talk connected to the conversation.\n"
            "Polite declines (no thanks, not for now) and farewells (bye, goodbye, see you) are 'small_talk', "
            "NOT 'out_of_domain', even when the patient does not want to book.\n"
            "Use 'unintelligible' when the message is too garbled, abbreviated, slang-heavy, or ambiguous to determine "
            "what they want (e.g. random typing, 'k onda vo', 'asdf', 'nc', unclear abbreviations). "
            "Greetings and casual slang hellos are still 'small_talk', NOT 'unintelligible'.\n"
            f"{topics_block_en}"
            "Consider as OUT_OF_DOMAIN only when the message is coherent but clearly NOT about dental health or this clinic "
            "(pizza, car repair, politics, religion, homework, etc.).\n"
            "Answer with ONLY the label (for example: cita). No explanation."
        )
    else:
        instructions = (
            "Clasifica el mensaje del paciente en exactamente una de estas etiquetas:\n"
            "- cita\n"
            "- servicios\n"
            "- clinica_info\n"
            "- seguimiento_cita\n"
            "- small_talk\n"
            "- unintelligible\n"
            "- out_of_domain\n\n"
            "Considera DENTRO DE DOMINIO todo lo que sea sobre esta clínica dental: citas (agendar, cambiar, cancelar), "
            "tratamientos o servicios dentales, síntomas y dudas dentales, precios, formas de pago, dirección y horarios "
            "de la clínica, y small talk corto relacionado con la conversación.\n"
            "Los rechazos corteses (no gracias, no por ahora, mejor no) y las despedidas (adios, bye, chao, hasta luego) "
            "son 'small_talk', NO 'out_of_domain', aunque no pidan una cita.\n"
            "Usa 'unintelligible' cuando el mensaje esté tan mal escrito, abreviado, con jerga o ambiguo que NO se pueda "
            "determinar qué quiere (tecleo, 'k onda vo', 'asdf', 'nc', abreviaturas confusas). "
            "Saludos y jerga casual de saludo siguen siendo 'small_talk', NO 'unintelligible'.\n"
            f"{topics_block_es}"
            "Considera FUERA DE DOMINIO solo cuando el mensaje sea coherente pero claramente NO sea sobre salud dental "
            "o esta clínica (pizza, mecánica, política, religión, tareas escolares, etc.).\n"
            "Responde SOLO con la etiqueta (por ejemplo: cita). Sin explicación."
        )

    prompt_parts = ["=== Instrucciones de clasificación ===", instructions, ""]
    if history_text:
        prompt_parts.extend(
            [
                "=== Historial reciente ===",
                history_text,
                "",
            ]
        )
    prompt_parts.extend(
        [
            "=== Mensaje del paciente a clasificar ===",
            f"Paciente: {msg}",
            "",
            "Etiqueta:",
        ]
    )
    prompt = "\n".join(prompt_parts)

    # Usamos el wrapper de Gemini con pocos tokens y baja temperatura.
    text = gemini.generate_reply(
        system_prompt=prompt,
        chat_history=None,
        temperature=0.0,
        max_output_tokens=CLASSIFIER_MAX_OUTPUT_TOKENS,
        low_thinking=True,
    ).strip()

    # Normalizamos la etiqueta devuelta.
    label = (text or "").strip().lower()
    # Aceptar tanto sólo la etiqueta como frases cortas tipo "Intent: cita".
    for intent in Intent:
        if intent.value in label:
            return intent

    return Intent.OUT_OF_DOMAIN


__all__ = ["llm_classify_intent"]

