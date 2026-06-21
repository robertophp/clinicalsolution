"""
Repositorio de métricas del dashboard (solo lectura, agregado por clínica).

Todas las consultas:
- Filtran SIEMPRE por ``clinica_id`` (aislamiento estricto por clínica).
- Usan ``creado_en`` (instante del evento) como dimensión de fecha, convertido a la
  zona horaria de El Salvador para agrupar por día/semana/mes.
- Aceptan un rango ``[start, end]`` (DATE, inclusivo). Por defecto: año actual.

Diseño: la construcción de SQL es pura y testeable; la ejecución es una capa fina
sobre el cliente BigQuery.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from google.cloud import bigquery

from .bigquery_client import CITAS_TABLE, MENSAJES_TABLE, get_bigquery_client, table_ref

EL_SALVADOR_TZ = "America/El_Salvador"

ORIGEN_WHATSAPP_ASSISTANT = "whatsapp_assistant"

GRANULARITIES = ("day", "week", "month")


def _now_el_salvador() -> datetime:
    return datetime.now(timezone(timedelta(hours=-6)))


def default_date_range() -> tuple[date, date]:
    """Rango por defecto del dashboard: del 1 de enero al 31 de diciembre del año actual."""
    today = _now_el_salvador().date()
    return date(today.year, 1, 1), date(today.year, 12, 31)


def normalize_granularity(value: str | None) -> str:
    """Devuelve una granularidad válida (day/week/month); por defecto 'day'."""
    v = (value or "").strip().lower()
    return v if v in GRANULARITIES else "day"


def _period_expr(granularity: str) -> str:
    """
    Expresión SQL que calcula el inicio del período (DATE) a partir de ``creado_en``,
    en hora local de El Salvador. Lanza ValueError si la granularidad no es válida.
    """
    g = normalize_granularity(granularity)
    local_date = f"DATE(creado_en, '{EL_SALVADOR_TZ}')"
    if g == "day":
        return local_date
    if g == "week":
        return f"DATE_TRUNC({local_date}, WEEK(MONDAY))"
    return f"DATE_TRUNC({local_date}, MONTH)"


def _date_filter() -> str:
    """Cláusula de filtro por clínica y rango de fechas (parámetros @clinic_id/@start/@end)."""
    return (
        "clinica_id = @clinic_id "
        f"AND DATE(creado_en, '{EL_SALVADOR_TZ}') BETWEEN @start AND @end"
    )


def build_summary_sql(*, citas_table: str, mensajes_table: str) -> tuple[str, str]:
    """SQL de totales: (sql_citas, sql_mensajes). Separadas para no acoplar las dos tablas."""
    where = _date_filter()
    sql_citas = (
        "SELECT "
        "COUNT(*) AS agendadas, "
        "COUNTIF(status = 'cancelada') AS canceladas, "
        "COUNTIF(status = 'reagendada') AS reagendadas, "
        "COUNTIF(transferencia_estado IS NOT NULL AND transferencia_estado != '') AS derivaciones "
        f"FROM `{citas_table}` WHERE {where}"
    )
    sql_mensajes = (
        "SELECT "
        "COUNTIF(rol = 'user') AS mensajes_usuario, "
        "COUNT(DISTINCT IF(rol = 'user', telefono, NULL)) AS personas_unicas "
        f"FROM `{mensajes_table}` WHERE {where}"
    )
    return sql_citas, sql_mensajes


def build_timeseries_sql(*, citas_table: str, mensajes_table: str, granularity: str) -> tuple[str, str]:
    """SQL de series por período: (sql_citas, sql_mensajes)."""
    where = _date_filter()
    period = _period_expr(granularity)
    sql_citas = (
        f"SELECT {period} AS period, COUNT(*) AS citas "
        f"FROM `{citas_table}` WHERE {where} GROUP BY period ORDER BY period"
    )
    sql_mensajes = (
        f"SELECT {period} AS period, "
        "COUNTIF(rol = 'user') AS mensajes, "
        "COUNT(DISTINCT IF(rol = 'user', telefono, NULL)) AS personas "
        f"FROM `{mensajes_table}` WHERE {where} GROUP BY period ORDER BY period"
    )
    return sql_citas, sql_mensajes


def build_by_service_sql(*, citas_table: str) -> str:
    """SQL de citas agrupadas por servicio (razon_cita)."""
    where = _date_filter()
    return (
        "SELECT COALESCE(NULLIF(TRIM(razon_cita), ''), '(sin especificar)') AS servicio, "
        "COUNT(*) AS total "
        f"FROM `{citas_table}` WHERE {where} "
        "GROUP BY servicio ORDER BY total DESC"
    )


def _query_params(*, clinic_id: str, start: date, end: date) -> list[bigquery.ScalarQueryParameter]:
    return [
        bigquery.ScalarQueryParameter("clinic_id", "STRING", clinic_id),
        bigquery.ScalarQueryParameter("start", "DATE", start),
        bigquery.ScalarQueryParameter("end", "DATE", end),
    ]


def merge_timeseries(citas_rows: list[dict], mensajes_rows: list[dict]) -> list[dict]:
    """
    Combina filas de citas y mensajes por período en una sola serie ordenada.

    Cada elemento: {period, mensajes, personas, citas, ratio_mensajes_por_cita}.
    ``ratio`` = mensajes / citas (None si no hubo citas en el período).
    """
    by_period: dict[str, dict] = {}

    def _key(row: dict) -> str:
        p = row.get("period")
        return p.isoformat() if hasattr(p, "isoformat") else str(p)

    for row in mensajes_rows:
        k = _key(row)
        entry = by_period.setdefault(
            k, {"period": k, "mensajes": 0, "personas": 0, "citas": 0}
        )
        entry["mensajes"] = int(row.get("mensajes") or 0)
        entry["personas"] = int(row.get("personas") or 0)

    for row in citas_rows:
        k = _key(row)
        entry = by_period.setdefault(
            k, {"period": k, "mensajes": 0, "personas": 0, "citas": 0}
        )
        entry["citas"] = int(row.get("citas") or 0)

    out = []
    for k in sorted(by_period.keys()):
        entry = by_period[k]
        citas = entry["citas"]
        entry["ratio_mensajes_por_cita"] = (
            round(entry["mensajes"] / citas, 2) if citas > 0 else None
        )
        out.append(entry)
    return out


def compute_ratio(mensajes_usuario: int, agendadas: int) -> float | None:
    """Mensajes de usuario por cita agendada (None si no hubo citas)."""
    if agendadas and agendadas > 0:
        return round(mensajes_usuario / agendadas, 2)
    return None


@dataclass
class MetricsRepository:
    """Ejecuta las consultas de métricas contra BigQuery para una clínica y rango."""

    client: bigquery.Client | None = None

    def _client(self) -> bigquery.Client:
        return self.client or get_bigquery_client()

    def _run(self, sql: str, params: list[bigquery.ScalarQueryParameter]) -> list[dict]:
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = self._client().query(sql, job_config=job_config).result()
        return [dict(r) for r in rows]

    def get_summary(self, *, clinic_id: str, start: date, end: date) -> dict:
        citas_sql, mensajes_sql = build_summary_sql(
            citas_table=table_ref(CITAS_TABLE),
            mensajes_table=table_ref(MENSAJES_TABLE),
        )
        params = _query_params(clinic_id=clinic_id, start=start, end=end)
        citas_rows = self._run(citas_sql, params)
        mensajes_rows = self._run(mensajes_sql, params)
        citas = citas_rows[0] if citas_rows else {}
        mensajes = mensajes_rows[0] if mensajes_rows else {}

        agendadas = int(citas.get("agendadas") or 0)
        mensajes_usuario = int(mensajes.get("mensajes_usuario") or 0)
        return {
            "agendadas": agendadas,
            "canceladas": int(citas.get("canceladas") or 0),
            "reagendadas": int(citas.get("reagendadas") or 0),
            "derivaciones": int(citas.get("derivaciones") or 0),
            "mensajes_usuario": mensajes_usuario,
            "personas_unicas": int(mensajes.get("personas_unicas") or 0),
            "ratio_mensajes_por_cita": compute_ratio(mensajes_usuario, agendadas),
        }

    def get_timeseries(self, *, clinic_id: str, start: date, end: date, granularity: str) -> list[dict]:
        citas_sql, mensajes_sql = build_timeseries_sql(
            citas_table=table_ref(CITAS_TABLE),
            mensajes_table=table_ref(MENSAJES_TABLE),
            granularity=granularity,
        )
        params = _query_params(clinic_id=clinic_id, start=start, end=end)
        citas_rows = self._run(citas_sql, params)
        mensajes_rows = self._run(mensajes_sql, params)
        return merge_timeseries(citas_rows, mensajes_rows)

    def get_by_service(self, *, clinic_id: str, start: date, end: date) -> list[dict]:
        sql = build_by_service_sql(citas_table=table_ref(CITAS_TABLE))
        params = _query_params(clinic_id=clinic_id, start=start, end=end)
        rows = self._run(sql, params)
        return [
            {"servicio": r.get("servicio") or "(sin especificar)", "total": int(r.get("total") or 0)}
            for r in rows
        ]


__all__ = [
    "MetricsRepository",
    "EL_SALVADOR_TZ",
    "GRANULARITIES",
    "default_date_range",
    "normalize_granularity",
    "build_summary_sql",
    "build_timeseries_sql",
    "build_by_service_sql",
    "merge_timeseries",
    "compute_ratio",
]
