"""
Registro liviano de mensajes en BigQuery (tabla ``mensajes``).

Solo metadatos, SIN contenido del mensaje (privacidad y costo):
``clinica_id``, ``telefono``, ``rol`` (user/assistant), ``canal`` (meta/twilio/chat),
``creado_en`` (TIMESTAMP, instante UTC).

Sirve para métricas del dashboard: cuántas veces escriben los pacientes, personas
únicas y ratio mensajes/cita. El logging es *fail-open*: si BigQuery falla, NO rompe
la respuesta del agente.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .bigquery_client import MENSAJES_TABLE, get_bigquery_client, table_ref

logger = logging.getLogger(__name__)

ROL_USER = "user"
ROL_ASSISTANT = "assistant"

# Canales soportados (origen del mensaje entrante).
CANAL_META = "meta"
CANAL_TWILIO = "twilio"
CANAL_CHAT = "chat"


def log_mensaje(
    *,
    clinic_id: str,
    telefono: str,
    rol: str = ROL_USER,
    canal: str = CANAL_META,
    creado_en: datetime | None = None,
) -> bool:
    """
    Inserta una fila de metadatos del mensaje en BigQuery vía streaming insert.

    Devuelve ``True`` si se insertó sin errores, ``False`` en cualquier fallo
    (no lanza excepción: el flujo de conversación nunca debe romperse por esto).
    """
    cid = (clinic_id or "").strip()
    tel = (telefono or "").strip()
    if not cid or not tel:
        return False

    ts = creado_en or datetime.now(timezone.utc)
    row = {
        "clinica_id": cid,
        "telefono": tel,
        "rol": (rol or ROL_USER).strip() or ROL_USER,
        "canal": (canal or CANAL_META).strip() or CANAL_META,
        "creado_en": ts.isoformat(),
    }

    try:
        client = get_bigquery_client()
        errors = client.insert_rows_json(table_ref(MENSAJES_TABLE), [row])
        if errors:
            logger.warning("Logging de mensaje con errores BigQuery: %s", errors)
            return False
        return True
    except Exception:  # noqa: BLE001 - fail-open: nunca rompe la respuesta
        logger.warning("No se pudo registrar el mensaje en BigQuery (fail-open)", exc_info=True)
        return False


__all__ = [
    "log_mensaje",
    "ROL_USER",
    "ROL_ASSISTANT",
    "CANAL_META",
    "CANAL_TWILIO",
    "CANAL_CHAT",
]
