"""
Resolución de recursos GCP y WhatsApp según APP_ENV.

Si ``BIGQUERY_DATASET`` o ``FIRESTORE_DATABASE_ID`` están definidos en el entorno del
proceso, se respetan. Si no, se eligen valores por defecto según producción vs desarrollo/staging.
"""

from __future__ import annotations

import os

from ..schemas.clinic import ClinicConfig

DEV_BIGQUERY_DATASET = "clinica_datos"
PROD_BIGQUERY_DATASET = "clinica_datos_prod"
DEV_FIRESTORE_DATABASE_ID = "agentmemory"
PROD_FIRESTORE_DATABASE_ID = "agentmemory-prod"


def is_production_app_env(app_env: str | None) -> bool:
    return (app_env or "").strip().lower() in ("production", "prod")


def effective_bigquery_dataset(*, app_env: str | None, configured: str | None = None) -> str:
    """Dataset BigQuery para ``citas`` (tabla siempre ``citas``)."""
    if "BIGQUERY_DATASET" in os.environ:
        return (os.environ.get("BIGQUERY_DATASET") or "").strip() or _default_bigquery_dataset(app_env)
    if configured and str(configured).strip():
        return str(configured).strip()
    return _default_bigquery_dataset(app_env)


def effective_firestore_database_id(*, app_env: str | None, configured: str | None = None) -> str:
    """ID de base Firestore (modo nativo)."""
    if "FIRESTORE_DATABASE_ID" in os.environ:
        raw = (os.environ.get("FIRESTORE_DATABASE_ID") or "").strip()
        return raw or _default_firestore_database_id(app_env)
    if configured and str(configured).strip():
        return str(configured).strip()
    return _default_firestore_database_id(app_env)


def _default_bigquery_dataset(app_env: str | None) -> str:
    return PROD_BIGQUERY_DATASET if is_production_app_env(app_env) else DEV_BIGQUERY_DATASET


def _default_firestore_database_id(app_env: str | None) -> str:
    return PROD_FIRESTORE_DATABASE_ID if is_production_app_env(app_env) else DEV_FIRESTORE_DATABASE_ID


def clinic_whatsapp_phone_number_ids(cfg: ClinicConfig) -> tuple[str, ...]:
    """Todos los Phone Number ID de Meta registrados para la clínica (prod + dev)."""
    ids: list[str] = []
    for attr in ("whatsapp_phone_number_id", "whatsapp_phone_number_id_dev"):
        pid = getattr(cfg, attr, None)
        if pid and str(pid).strip():
            s = str(pid).strip()
            if s not in ids:
                ids.append(s)
    return tuple(ids)


def resolve_whatsapp_phone_number_id_for_outbound(cfg: ClinicConfig | None, *, app_env: str | None) -> str:
    """
    Phone Number ID para enviar por Graph API cuando no viene en el evento entrante.
    En no-producción prioriza el ID de prueba (conversación con pacientes en demo/STG).

    No usar para derivación al especialista; ver ``resolve_whatsapp_phone_number_id_for_specialist``.
    """
    if not cfg:
        return ""
    prod = (getattr(cfg, "whatsapp_phone_number_id", None) or "").strip()
    dev = (getattr(cfg, "whatsapp_phone_number_id_dev", None) or "").strip()
    if is_production_app_env(app_env):
        return prod
    return dev or prod


def resolve_whatsapp_phone_number_id_for_specialist(cfg: ClinicConfig | None) -> str:
    """
    Phone Number ID de la línea clínica (prod) para notificar al ``specialist_whatsapp``.

    Siempre ``whatsapp_phone_number_id``; nunca ``whatsapp_phone_number_id_dev`` (solo demo paciente).
    """
    if not cfg:
        return ""
    return (getattr(cfg, "whatsapp_phone_number_id", None) or "").strip()
