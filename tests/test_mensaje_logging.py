"""Registro liviano de mensajes en BigQuery (fail-open) y enganche en el flujo del agente."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.repositories import mensaje_repository as mrepo
from backend.services.intent_classifier import Intent


def test_log_mensaje_requires_clinic_and_phone():
    assert mrepo.log_mensaje(clinic_id="", telefono="123") is False
    assert mrepo.log_mensaje(clinic_id="c1", telefono="") is False


def test_log_mensaje_inserts_metadata_only():
    fake_client = MagicMock()
    fake_client.insert_rows_json.return_value = []
    with patch.object(mrepo, "get_bigquery_client", return_value=fake_client), patch.object(
        mrepo, "table_ref", return_value="proj.ds.mensajes"
    ):
        ok = mrepo.log_mensaje(clinic_id="c1", telefono="+50312345678", rol="user", canal="meta")
    assert ok is True
    args = fake_client.insert_rows_json.call_args
    rows = args.args[1]
    row = rows[0]
    assert row["clinica_id"] == "c1"
    assert row["telefono"] == "+50312345678"
    assert row["rol"] == "user"
    assert row["canal"] == "meta"
    assert "creado_en" in row
    # Sin contenido del mensaje (privacidad)
    assert "content" not in row and "texto" not in row


def test_log_mensaje_is_fail_open_on_exception():
    with patch.object(mrepo, "get_bigquery_client", side_effect=RuntimeError("no creds")):
        assert mrepo.log_mensaje(clinic_id="c1", telefono="123") is False


def test_log_mensaje_returns_false_on_bq_errors():
    fake_client = MagicMock()
    fake_client.insert_rows_json.return_value = [{"index": 0, "errors": ["bad"]}]
    with patch.object(mrepo, "get_bigquery_client", return_value=fake_client), patch.object(
        mrepo, "table_ref", return_value="proj.ds.mensajes"
    ):
        assert mrepo.log_mensaje(clinic_id="c1", telefono="123") is False


@pytest.mark.asyncio
async def test_chat_flow_logs_user_message():
    transport = ASGITransport(app=__import__("backend.main", fromlist=["app"]).app)
    client = AsyncClient(transport=transport, base_url="http://test")

    memory = MagicMock()
    memory.get_recent_messages.return_value = []
    memory.get_metadata.return_value = {}

    with patch("backend.bootstrap.gemini_service") as gem, patch(
        "backend.bootstrap.conversation_memory", memory
    ), patch("backend.bootstrap.llm_classify_intent", return_value=Intent.SMALL_TALK), patch(
        "backend.bootstrap.log_mensaje"
    ) as mock_log:
        gem.generate_reply_with_tools.return_value = "Hola, ¿en qué te ayudo?"
        res = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+1234567890", "body": "Hola"},
            headers={"Authorization": "Bearer test-internal-api-key"},
        )
    assert res.status_code == 200
    mock_log.assert_called_once()
    kwargs = mock_log.call_args.kwargs
    assert kwargs["clinic_id"] == "demo_clinic_1"
    assert kwargs["telefono"] == "+1234567890"
    assert kwargs["canal"] == "chat"
