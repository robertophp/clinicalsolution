"""
Temas configurables que disparan derivación a un especialista humano vía WhatsApp.

Para ampliar el alcance, añade entradas a ``DEFAULT_TRANSFER_TOPICS`` o filtra por clínica
con ``human_transfer_topic_keys`` en ``clinics_mock.json`` (solo se aplican las claves listadas).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TransferTopicDefinition:
    """Definición estable de un tema sensible (clave + descripción para el clasificador LLM)."""

    key: str
    description_es: str
    description_en: str


DEFAULT_TRANSFER_TOPICS: tuple[TransferTopicDefinition, ...] = (
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
            "o síntomas muy severos que el bot no debe resolver solo."
        ),
        description_en=(
            "Complex or severe dental/medical situations: serious systemic disease, "
            "comorbidities, medications affecting treatment, high-risk contexts, "
            "or very severe symptoms that should not be handled by the bot alone."
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


def topics_for_clinic_keys(keys_filter: list[str] | None) -> tuple[TransferTopicDefinition, ...]:
    """Si ``keys_filter`` tiene valores, solo devuelve temas cuya ``key`` esté incluida."""
    if not keys_filter:
        return DEFAULT_TRANSFER_TOPICS
    allowed = {k.strip() for k in keys_filter if (k or "").strip()}
    if not allowed:
        return DEFAULT_TRANSFER_TOPICS
    return tuple(t for t in DEFAULT_TRANSFER_TOPICS if t.key in allowed)


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
    "topics_for_clinic_keys",
]
