from __future__ import annotations

from pydantic import BaseModel, Field


class BookingPromptPolicies(BaseModel):
    """Textos de agendado que pueden variar por clínica (inyectados en el prompt de herramientas)."""

    confirmation_example_es: str | None = None
    confirmation_example_en: str | None = None


class CordalesPanoramicRequirementPolicies(BaseModel):
    """
  Requisito de radiografía panorámica para extracción de cordales.
  Configurable por clínica en ``policies.json``.
  """

    enabled: bool = False
    trigger_terms: list[str] = Field(
        default_factory=lambda: [
            "cordal",
            "cordales",
            "muela del juicio",
            "muelas del juicio",
            "tercer molar",
            "terceros molares",
            "wisdom tooth",
            "wisdom teeth",
        ]
    )
    target_service_ids: list[str] = Field(default_factory=lambda: ["exodoncia_de_cordal_piezas"])
    evaluation_with_xray_service_id: str = "evaluacion_radiografia_panoramica"
    evaluation_only_service_id: str = "evaluacion"
    standalone_xray_service_id: str = "radiografia_panoramica"
    mandatory_question_es: str | None = (
        "¿Ya cuentas con tu radiografía panorámica, o te gustaría que agendemos una cita de "
        "evaluación inicial para tomártela aquí en la clínica con nuestro equipo radiológico?"
    )
    mandatory_question_en: str | None = (
        "Do you already have a panoramic X-ray, or would you like us to schedule an initial "
        "evaluation appointment to take it here at the clinic with our radiology team?"
    )
    reminder_if_has_xray_es: str | None = (
        "Recuerda traer tu radiografía panorámica el día de la cita."
    )
    reminder_if_has_xray_en: str | None = (
        "Please remember to bring your panoramic X-ray on the day of your appointment."
    )
    offer_if_no_xray_es: str | None = None
    offer_if_no_xray_en: str | None = None
    block_direct_cordal_booking: bool = True


class PediatricAgePolicies(BaseModel):
    """
    Restricción de edad mínima para pacientes pediátricos.
    Configurable por clínica en ``policies.json``.
    """

    enabled: bool = False
    min_age: int = 6
    trigger_terms: list[str] = Field(
        default_factory=lambda: [
            "niño",
            "niña",
            "hijo",
            "hija",
            "menor",
            "bebé",
            "bebe",
            "pequeño",
            "pequeña",
            "peque",
            "mi nene",
            "mi nena",
            "child",
            "kid",
            "son",
            "daughter",
            "baby",
            "toddler",
            "infant",
            "minor",
            "little one",
        ]
    )
    welcome_es: str = (
        "¡Claro que sí! 😊 Atendemos niños y niñas de {min_age} añitos en adelante 🦷✨"
    )
    welcome_en: str = (
        "Of course! 😊 We see children {min_age} years and older 🦷✨"
    )
    decline_es: str = (
        "Entendemos tu preocupación 💛 Lamentablemente solo atendemos pacientes de "
        "{min_age} añitos en adelante. ¿Hay algo más en lo que pueda ayudarte? 😊"
    )
    decline_en: str = (
        "We understand your concern 💛 Unfortunately we only see patients {min_age} years "
        "and older. Is there anything else I can help you with? 😊"
    )
    evaluation_service_id: str = "evaluacion"


class MaxillofacialPolicies(BaseModel):
    """
    Cirugía maxilofacial: info desde catálogo; citas/horarios → derivación directa al especialista.
    Configurable por clínica en ``policies.json``.
    """

    enabled: bool = False
    trigger_terms: list[str] = Field(
        default_factory=lambda: [
            "maxilo",
            "maxilofacial",
            "mucocele",
            "cirugia maxilofacial",
            "cirugía maxilofacial",
            "oral surgery",
            "maxillofacial",
        ]
    )
    target_service_ids: list[str] = Field(
        default_factory=lambda: [
            "evaluacion_con_especialista_maxilofacial",
            "cirugia_de_mucocele_maxilofacial",
        ]
    )
    block_direct_booking: bool = True


class ClinicPolicies(BaseModel):
    """Políticas y ajustes de prompt por clínica (archivo `policies.json`)."""

    clinic_id: str
    human_transfer_topic_keys: list[str] | None = None
    transfer_topics_file: str | None = None
    booking: BookingPromptPolicies = Field(default_factory=BookingPromptPolicies)
    cordales_panoramic_requirement: CordalesPanoramicRequirementPolicies = Field(
        default_factory=CordalesPanoramicRequirementPolicies
    )
    pediatric_age_policy: PediatricAgePolicies = Field(
        default_factory=PediatricAgePolicies
    )
    maxillofacial_policy: MaxillofacialPolicies = Field(
        default_factory=MaxillofacialPolicies
    )
