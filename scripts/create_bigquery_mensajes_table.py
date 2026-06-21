"""
Crea la tabla BigQuery ``mensajes`` (idempotente) para el registro liviano de mensajes.

Esquema (solo metadatos, sin contenido):
    clinica_id STRING, telefono STRING, rol STRING, canal STRING, creado_en TIMESTAMP

Particionada por día de ``creado_en`` (reduce costo de escaneo en las consultas del dashboard).

Uso:
    python scripts/create_bigquery_mensajes_table.py
    # Respeta BIGQUERY_DATASET / APP_ENV (dev -> clinica_datos, prod -> clinica_datos_prod).
"""
from __future__ import annotations

from google.cloud import bigquery

from backend.repositories.bigquery_client import MENSAJES_TABLE, effective_dataset, table_ref


def main() -> int:
    client = bigquery.Client()
    table_id = table_ref(MENSAJES_TABLE)

    schema = [
        bigquery.SchemaField("clinica_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("telefono", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("rol", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("canal", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("creado_en", "TIMESTAMP", mode="REQUIRED"),
    ]
    table = bigquery.Table(table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="creado_en",
    )
    table.clustering_fields = ["clinica_id"]

    created = client.create_table(table, exists_ok=True)
    print(f"OK: tabla lista en dataset '{effective_dataset()}': {created.full_table_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
