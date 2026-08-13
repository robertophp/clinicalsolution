"""Construcción de SQL y agregaciones del repositorio de métricas."""

from datetime import date

import pytest

from backend.repositories import metrics_repository as mr


def test_normalize_granularity_defaults_to_day():
    assert mr.normalize_granularity(None) == "day"
    assert mr.normalize_granularity("DAY") == "day"
    assert mr.normalize_granularity("week") == "week"
    assert mr.normalize_granularity("month") == "month"
    assert mr.normalize_granularity("bogus") == "day"


def test_default_date_range_is_full_current_year():
    start, end = mr.default_date_range()
    assert start.month == 1 and start.day == 1
    assert end.month == 12 and end.day == 31
    assert start.year == end.year


def test_period_expr_uses_el_salvador_tz_and_truncation():
    assert "America/El_Salvador" in mr._period_expr("day")
    assert "WEEK(MONDAY)" in mr._period_expr("week")
    assert "MONTH" in mr._period_expr("month")


def test_summary_sql_filters_by_clinic_and_range():
    sql_citas, sql_mensajes = mr.build_summary_sql(
        citas_table="p.d.citas", mensajes_table="p.d.mensajes"
    )
    assert "clinica_id = @clinic_id" in sql_citas
    assert "BETWEEN @start AND @end" in sql_citas
    assert "COUNTIF(status = 'cancelada')" in sql_citas
    assert "COUNTIF(status = 'reagendada')" in sql_citas
    assert "transferencia_estado" in sql_citas
    assert "COUNT(DISTINCT" in sql_mensajes
    assert "rol = 'user'" in sql_mensajes


def test_timeseries_sql_groups_by_period():
    sql_citas, sql_mensajes = mr.build_timeseries_sql(
        citas_table="p.d.citas", mensajes_table="p.d.mensajes", granularity="month"
    )
    assert "GROUP BY period" in sql_citas
    assert "GROUP BY period" in sql_mensajes
    assert "clinica_id = @clinic_id" in sql_mensajes


def test_compute_ratio_handles_zero_citas():
    assert mr.compute_ratio(100, 5) == 20.0
    assert mr.compute_ratio(10, 0) is None
    assert mr.compute_ratio(0, 0) is None


def test_merge_timeseries_combines_and_fills_zeros():
    citas = [{"period": date(2026, 1, 1), "citas": 2}, {"period": date(2026, 1, 3), "citas": 1}]
    mensajes = [
        {"period": date(2026, 1, 1), "mensajes": 10, "personas": 4},
        {"period": date(2026, 1, 2), "mensajes": 5, "personas": 2},
    ]
    out = mr.merge_timeseries(citas, mensajes)
    periods = [r["period"] for r in out]
    assert periods == ["2026-01-01", "2026-01-02", "2026-01-03"]

    day1 = out[0]
    assert day1["mensajes"] == 10 and day1["citas"] == 2
    assert day1["ratio_mensajes_por_cita"] == 5.0

    day2 = out[1]  # solo mensajes, sin citas => ratio None
    assert day2["citas"] == 0 and day2["ratio_mensajes_por_cita"] is None

    day3 = out[2]  # solo citas, sin mensajes
    assert day3["mensajes"] == 0 and day3["citas"] == 1


class _FakeQueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class _FakeClient:
    """Devuelve filas predefinidas en orden de llamada a query()."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def query(self, sql, job_config=None):
        self.calls.append((sql, job_config))
        return _FakeQueryJob(self._results.pop(0))


def test_get_summary_maps_rows_and_ratio(monkeypatch):
    monkeypatch.setattr(mr, "table_ref", lambda t: f"proj.ds.{t}")
    client = _FakeClient(
        [
            [{"agendadas": 10, "canceladas": 2, "reagendadas": 1, "derivaciones": 3}],
            [{"mensajes_usuario": 50, "personas_unicas": 20}],
        ]
    )
    repo = mr.MetricsRepository(client=client)
    out = repo.get_summary(clinic_id="c1", start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert out["agendadas"] == 10
    assert out["derivaciones"] == 3
    assert out["mensajes_usuario"] == 50
    assert out["personas_unicas"] == 20
    assert out["ratio_mensajes_por_cita"] == 5.0
    # Verifica que el clinic_id viaja como parámetro (aislamiento)
    _, job_config = client.calls[0]
    params = {p.name: p.value for p in job_config.query_parameters}
    assert params["clinic_id"] == "c1"
