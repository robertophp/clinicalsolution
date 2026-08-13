"""Endpoints del dashboard: login, sesión, aislamiento por clínica y errores."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend import config
from backend.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_dashboard_page_served(client: AsyncClient):
    res = await client.get("/dashboard")
    assert res.status_code == 200
    assert "Panel de Métricas" in res.text
    assert "Personas escribiendo vs. Citas agendadas" in res.text
    assert "Volumen de mensajes" in res.text
    assert "monthSelect" in res.text


@pytest.mark.asyncio
async def test_api_requires_session(client: AsyncClient):
    res = await client.get("/dashboard/api/summary")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_login_invalid_credentials(client: AsyncClient):
    with patch("backend.api.routers.dashboard._user_repo") as repo:
        repo.authenticate.return_value = None
        res = await client.post(
            "/dashboard/login", json={"username": "x", "password": "y"}
        )
    assert res.status_code == 401
    assert res.json()["ok"] is False


@pytest.mark.asyncio
async def test_login_then_summary_uses_session_clinic(client: AsyncClient):
    fake_repo = MagicMock()
    fake_repo.authenticate.return_value = {"clinic_id": "demo_clinic_1", "username": "doctora"}

    fake_metrics = MagicMock()
    fake_metrics.get_summary.return_value = {
        "agendadas": 4, "canceladas": 1, "reagendadas": 0, "derivaciones": 2,
        "mensajes_usuario": 30, "personas_unicas": 12, "ratio_mensajes_por_cita": 7.5,
    }

    with patch("backend.api.routers.dashboard._user_repo", fake_repo), patch(
        "backend.api.routers.dashboard.MetricsRepository", return_value=fake_metrics
    ):
        login = await client.post(
            "/dashboard/login", json={"username": "doctora", "password": "ok"}
        )
        assert login.status_code == 200

        # Aunque el cliente intente forzar otra clínica por query, se ignora.
        res = await client.get("/dashboard/api/summary?clinic_id=otra_clinica")

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"]["agendadas"] == 4
    # El clinic_id usado proviene de la sesión (demo_clinic_1), no del query param.
    assert fake_metrics.get_summary.call_args.kwargs["clinic_id"] == "demo_clinic_1"


@pytest.mark.asyncio
async def test_logout_clears_session(client: AsyncClient):
    fake_repo = MagicMock()
    fake_repo.authenticate.return_value = {"clinic_id": "demo_clinic_1", "username": "doctora"}
    with patch("backend.api.routers.dashboard._user_repo", fake_repo):
        await client.post("/dashboard/login", json={"username": "d", "password": "ok"})
        await client.post("/dashboard/logout")
    res = await client.get("/dashboard/api/me")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_login_503_when_secret_missing(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(config.settings, "DASHBOARD_SESSION_SECRET", "")
    res = await client.post("/dashboard/login", json={"username": "d", "password": "x"})
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_timeseries_query_failure_returns_502(client: AsyncClient):
    fake_repo = MagicMock()
    fake_repo.authenticate.return_value = {"clinic_id": "demo_clinic_1", "username": "doctora"}
    fake_metrics = MagicMock()
    fake_metrics.get_timeseries.side_effect = RuntimeError("bq down")
    with patch("backend.api.routers.dashboard._user_repo", fake_repo), patch(
        "backend.api.routers.dashboard.MetricsRepository", return_value=fake_metrics
    ):
        await client.post("/dashboard/login", json={"username": "d", "password": "ok"})
        res = await client.get("/dashboard/api/timeseries?granularity=week")
    assert res.status_code == 502
    assert res.json()["ok"] is False
