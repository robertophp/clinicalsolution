"""
Temas configurables que disparan derivación a un especialista humano vía WhatsApp.

Para ampliar el alcance, añade entradas a ``DEFAULT_TRANSFER_TOPICS``, filtra por clínica con
``human_transfer_topic_keys`` en ``policies.json``, o define ``transfer_topics_file`` en esa misma
carpeta para sustituir el catálogo por clínica.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TransferTopicDefinition:
    """Definición estable de un tema sensible (clave + descripción para el clasificador LLM)."""

    key: str
    description_es: str
    description_en: str


_CLINIC_TRANSFER_TOPIC_OVERRIDES: dict[str, tuple[TransferTopicDefinition, ...]] = {}


DEFAULT_TRANSFER_TOPICS: tuple[TransferTopicDefinition, ...] = (
    TransferTopicDefinition(
        key="contacto_humano",
        description_es=(
            "El paciente pide EXPLÍCITAMENTE hablar con un humano: doctor/a, encargado/a, responsable, "
            "persona real, atención humana, etc. Derivar de inmediato; NO intentar agendar cita ni ofrecer horarios. "
            "Incluye «quiero hablar con la doctora», «comuníquenme con un encargado»."
        ),
        description_en=(
            "Patient EXPLICITLY asks to speak with a human: doctor, manager, supervisor, real person, etc. "
            "Escalate immediately; do NOT try to book appointments or offer time slots. "
            "Includes \"I want to speak with the doctor\", \"connect me with a manager\"."
        ),
    ),
    TransferTopicDefinition(
        key="quejas",
        description_es=(
            "Quejas o reclamos fuertes (no preguntas informativas): mal servicio vivido, cobros que consideran "
            "incorrectos o injustos tras explicación, negativa de ayuda, insistencia en hablar con responsable, "
            "conflictos sobre cargos ya aplicados, tiempos de espera vividos como grave falta de atención. "
            "NO incluyas aquí solo preguntas del tipo «¿tienen opción con banco?», «¿aceptan cuotas?», "
            "«me parece caro» buscando alternativas de pago si eso puede aclararse con la información estándar de la clínica."
        ),
        description_en=(
            "Strong complaints (not informational questions): bad service experienced, billing disputes after explanation, "
            "refusal of help, demands for a manager, conflicts over charges already applied, serious wait-time grievances. "
            "Do NOT include mere questions like \"do you have bank financing?\", \"installments?\", "
            "\"it sounds expensive\" seeking payment options if those can be answered from standard clinic payment info."
        ),
    ),
    TransferTopicDefinition(
        key="especialidades",
        description_es=(
            "Consultas que requieren especialista distinto al flujo general: ortodoncia, "
            "cirugía maxilofacial, implantes complejos u otras especialidades avanzadas."
        ),
        description_en=(
            "Topics that need a subspecialist beyond routine flow: orthodontics, "
            "oral/maxillofacial surgery, complex implants, or other advanced specialties."
        ),
    ),
    TransferTopicDefinition(
        key="casos_medicos_complejos",
        description_es=(
            "Casos médicos dentales complejos o graves: enfermedades sistemicas relevantes, "
            "comorbilidades, medicación que condiciona tratamiento, situaciones de alto riesgo "
            "que el bot no debe resolver solo. NO incluyas aquí pedidos rutinarios de cita urgente, "
            "dolor agudo o «¿me atienden hoy?» — esos van al flujo de evaluación/urgencia del asistente."
        ),
        description_en=(
            "Complex or severe dental/medical situations: serious systemic disease, "
            "comorbidities, medications affecting treatment, high-risk contexts "
            "the bot should not handle alone. Do NOT include routine urgent booking, "
            "acute pain, or 'can you see me today' — those use the assistant urgency flow."
        ),
    ),
    TransferTopicDefinition(
        key="creditos_fiscales",
        description_es=(
            "Facturación fiscal, CFDI, RFC, deducciones, comprobantes fiscales, "
            "créditos fiscales dentales o requisitos tributarios para la clínica."
        ),
        description_en=(
            "Tax invoicing, receipts/deductions, taxpayer IDs, or dental expense "
            "tax credits and clinic fiscal requirements."
        ),
    ),
)


def set_transfer_topic_overrides(mapping: dict[str, tuple[TransferTopicDefinition, ...]]) -> None:
    """Reemplaza el mapa de temas personalizados por clínica (lo invoca ``clinic_loader`` al arrancar)."""
    _CLINIC_TRANSFER_TOPIC_OVERRIDES.clear()
    _CLINIC_TRANSFER_TOPIC_OVERRIDES.update(mapping)


def _filter_topics_by_keys(
    topics: tuple[TransferTopicDefinition, ...],
    keys_filter: list[str] | None,
) -> tuple[TransferTopicDefinition, ...]:
    if not keys_filter:
        return topics
    allowed = {k.strip() for k in keys_filter if (k or "").strip()}
    if not allowed:
        return topics
    return tuple(t for t in topics if t.key in allowed)


def topics_for_clinic_keys(keys_filter: list[str] | None) -> tuple[TransferTopicDefinition, ...]:
    """Si ``keys_filter`` tiene valores, solo devuelve temas cuya ``key`` esté incluida."""
    return _filter_topics_by_keys(DEFAULT_TRANSFER_TOPICS, keys_filter)


def resolve_transfer_topics_for_clinic(
    clinic_id: str,
    keys_filter: list[str] | None,
) -> tuple[TransferTopicDefinition, ...]:
    """Temas base por clínica (override opcional desde JSON) y filtro por ``human_transfer_topic_keys``."""
    base = _CLINIC_TRANSFER_TOPIC_OVERRIDES.get(clinic_id, DEFAULT_TRANSFER_TOPICS)
    return _filter_topics_by_keys(base, keys_filter)


def format_topics_for_prompt(topics: tuple[TransferTopicDefinition, ...], language: str) -> str:
    """Bloque de texto para prompts Gemini (ES/EN)."""
    lines: list[str] = []
    use_en = (language or "").strip().lower().startswith("en")
    for t in topics:
        desc = t.description_en if use_en else t.description_es
        lines.append(f'- "{t.key}": {desc}')
    return "\n".join(lines)


__all__ = [
    "DEFAULT_TRANSFER_TOPICS",
    "TransferTopicDefinition",
    "format_topics_for_prompt",
    "resolve_transfer_topics_for_clinic",
    "set_transfer_topic_overrides",
    "topics_for_clinic_keys",
]
