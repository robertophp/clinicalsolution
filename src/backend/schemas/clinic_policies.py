from __future__ import annotations

from pydantic import BaseModel, Field


class BookingPromptPolicies(BaseModel):
    """Textos de agendado que pueden variar por clínica (inyectados en el prompt de herramientas)."""

    confirmation_example_es: str | None = None
    confirmation_example_en: str | None = None


class ClinicPolicies(BaseModel):
    """Políticas y ajustes de prompt por clínica (archivo `policies.json`)."""

    clinic_id: str
    human_transfer_topic_keys: list[str] | None = None
    transfer_topics_file: str | None = None
    booking: BookingPromptPolicies = Field(default_factory=BookingPromptPolicies)
