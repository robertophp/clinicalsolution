from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .clinic import PaymentMethodLine


class ClinicBrandFile(BaseModel):
    """Contenido esperado de `data/clinics/<id>/brand.json`."""

    clinic_id: str
    name: str
    assistant_name: str = "Asistente Virtual"
    system_prompt: str
    system_prompt_en: str | None = None


class ClinicSiteFile(BaseModel):
    """Contenido esperado de `data/clinics/<id>/site.json` (operativo / integraciones)."""

    clinic_id: str
    opening_hours: dict[str, Any] | None = None
    allowed_intents: list[str] | None = None
    calendar_id: str | None = None
    calendar_sync_enabled: bool = False
    google_maps_link: str | None = None
    indicaciones_parqueo: str | None = None
    rutas_transporte_publico: str | None = None
    whatsapp_phone_number_id: str | None = None
    specialist_whatsapp: str | None = None
    payment_methods: list[PaymentMethodLine] | None = None
