"""Validaciones al arrancar la aplicación (producción)."""

from __future__ import annotations

import logging

from ..config import settings


def validate_production_settings() -> None:
    """
    Si ``APP_ENV`` es producción, exige configuración mínima segura:

    - ``INTERNAL_API_KEY`` no vacío (protege /chat y diagnósticos).
    - Si hay token de Meta WhatsApp, exige ``META_APP_SECRET`` y firma activa.
    """
    env = (settings.APP_ENV or "").strip().lower()
    if env not in ("production", "prod"):
        return

    if not (settings.INTERNAL_API_KEY or "").strip():
        raise RuntimeError(
            "APP_ENV es producción pero INTERNAL_API_KEY está vacío. "
            "Define INTERNAL_API_KEY para proteger POST /chat y GET /health/gcp, /health/meta."
        )

    if (settings.META_WHATSAPP_ACCESS_TOKEN or "").strip():
        if not (settings.META_APP_SECRET or "").strip():
            raise RuntimeError(
                "APP_ENV es producción y META_WHATSAPP_ACCESS_TOKEN está definido, pero META_APP_SECRET está vacío. "
                "La verificación de firma del webhook de Meta no puede activarse."
            )
        if settings.META_WEBHOOK_SKIP_SIGNATURE_VERIFY:
            raise RuntimeError(
                "APP_ENV es producción con Meta WhatsApp: META_WEBHOOK_SKIP_SIGNATURE_VERIFY debe ser false."
            )

    if not (settings.DASHBOARD_SESSION_SECRET or "").strip():
        logging.warning(
            "DASHBOARD_SESSION_SECRET no está definido: el login del dashboard de métricas estará deshabilitado."
        )

    logging.info("Validación de arranque (APP_ENV=production): INTERNAL_API_KEY y Meta OK.")
