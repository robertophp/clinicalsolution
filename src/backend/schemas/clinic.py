from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel


class PaymentMethodLine(BaseModel):
    """Una línea de política de pagos por clínica, en español e inglés."""

    es: str
    en: str


class ClinicConfig(BaseModel):
    id: str
    name: str
    system_prompt: str  # Prompt base en español
    system_prompt_en: str | None = None  # Prompt equivalente en inglés (opcional)
    assistant_name: str = "Asistente Virtual"  # Nombre con el que se presenta el bot
    opening_hours: Dict[str, Any] | None = None  # Horarios de atención por bloque (ej. mon_fri, sat)
    # Guardrails por clínica: lista de intents permitidos; si está vacía/None se permiten todos excepto OUT_OF_DOMAIN.
    allowed_intents: list[str] | None = None
    # Integración con Google Calendar: ID del calendario y flag para habilitar sync.
    calendar_id: str | None = None
    calendar_sync_enabled: bool = False
    # Ubicación y cómo llegar (opcional por clínica).
    google_maps_link: str | None = None
    indicaciones_parqueo: str | None = None
    rutas_transporte_publico: str | None = None
    # WhatsApp Cloud API (Meta): ID del número en Graph API (no es el WABA ni el teléfono legible).
    whatsapp_phone_number_id: str | None = None
    # Número WhatsApp del especialista humano para derivaciones (E.164 o solo dígitos). Opcional por clínica.
    specialist_whatsapp: str | None = None
    # Si se define, solo esas claves de ``human_transfer_topics`` aplican; si es null se usan todas las por defecto.
    human_transfer_topic_keys: list[str] | None = None
    # Formas de pago aceptadas (solo lo listado aquí debe mencionarse al paciente).
    payment_methods: list[PaymentMethodLine] | None = None
