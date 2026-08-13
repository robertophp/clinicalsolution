"""
Cliente BigQuery compartido (lectura de métricas y logging de mensajes).

Centraliza la resolución de proyecto/dataset y la construcción del cliente para no
duplicar esa lógica entre el repositorio de métricas y el de mensajes.
"""
from __future__ import annotations

from functools import lru_cache

from google.cloud import bigquery

from ..config import settings
from ..domain.runtime_env import effective_bigquery_dataset

MENSAJES_TABLE = "mensajes"
CITAS_TABLE = "citas"


def effective_dataset() -> str:
    """Dataset BigQuery efectivo según APP_ENV / variables de entorno."""
    return effective_bigquery_dataset(
        app_env=settings.APP_ENV,
        configured=getattr(settings, "BIGQUERY_DATASET", None),
    )


def table_ref(table: str, *, dataset: str | None = None) -> str:
    """Referencia totalmente cualificada ``project.dataset.table``."""
    ds = dataset or effective_dataset()
    return f"{settings.PROJECT_ID}.{ds}.{table}"


@lru_cache
def get_bigquery_client() -> bigquery.Client:
    """Cliente BigQuery cacheado (usa credenciales por defecto / IAM)."""
    return bigquery.Client(project=settings.PROJECT_ID)


__all__ = [
    "MENSAJES_TABLE",
    "CITAS_TABLE",
    "effective_dataset",
    "table_ref",
    "get_bigquery_client",
]
