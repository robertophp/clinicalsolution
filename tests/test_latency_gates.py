"""Compuertas de ahorro de llamadas LLM: guardrail reglas-primero y salto de derivación."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.domain.escalation_signals import message_signals_complaint_or_fiscal
from backend.main import app
from backend.services.intent_classifier import Intent

INTERNAL_API_HEADERS = {"Authorization": "Bearer test-internal-api-key"}


@pytest.fixture
def mock_memory():
    m = MagicMock()
    m.get_recent_messages.return_value = []
    m.get_metadata.return_value = {}
    m.add_message.return_value = None
    return m


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# --- Heurística de queja/fiscal ---

def test_complaint_signals_detects_complaint():
    assert message_signals_complaint_or_fiscal("esto es un pésimo servicio, una queja formal") is True
    assert message_signals_complaint_or_fiscal("quiero un reembolso ya") is True
    assert message_signals_complaint_or_fiscal("this is a complaint, I want a refund") is True


def test_fiscal_signals_detected():
    assert message_signals_complaint_or_fiscal("necesito factura con crédito fiscal") is True
    assert message_signals_complaint_or_fiscal("do you give a tax invoice?") is True


def test_routine_question_has_no_signal():
    assert message_signals_complaint_or_fiscal("¿cuánto cuesta una limpieza?") is False
    assert message_signals_complaint_or_fiscal("quiero agendar una cita") is False


# --- Guardrail reglas-primero ---

@pytest.mark.asyncio
async def test_clear_service_message_skips_llm_classifier_and_transfer(client, mock_memory):
    """Mensaje claro de servicio: ni clasificador LLM ni detección de derivación se llaman."""
    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", mock_memory
    ), patch("backend.bootstrap.llm_classify_intent") as mock_llm_intent, patch(
        "backend.bootstrap.detect_human_transfer_need"
    ) as mock_detect:
        mock_gemini.generate_reply_with_tools.return_value = "La limpieza cuesta USD 25."
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+1234567890", "body": "¿cuánto cuesta una limpieza?"},
            headers=INTERNAL_API_HEADERS,
        )
    assert response.status_code == 200
    mock_llm_intent.assert_not_called()
    mock_detect.assert_not_called()
    mock_gemini.generate_reply_with_tools.assert_called_once()


@pytest.mark.asyncio
async def test_out_of_domain_message_calls_llm_classifier(client, mock_memory):
    """Mensaje no reconocido por reglas: se consulta al clasificador LLM."""
    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", mock_memory
    ), patch("backend.bootstrap.llm_classify_intent", return_value=Intent.OUT_OF_DOMAIN) as mock_llm_intent:
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+1234567890", "body": "¿quién ganó el partido de fútbol ayer?"},
            headers=INTERNAL_API_HEADERS,
        )
    assert response.status_code == 200
    mock_llm_intent.assert_called_once()
    data = response.json()
    assert "fuera de lo que puedo hacer" in data["reply"].lower()


# --- Salto de derivación con compuerta de queja ---

@pytest.mark.asyncio
async def test_complaint_within_service_runs_transfer_detection(client, mock_memory):
    """Queja sobre un servicio (in-domain por reglas) SÍ ejecuta la detección de derivación."""
    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", mock_memory
    ), patch("backend.bootstrap.detect_human_transfer_need", return_value=None) as mock_detect:
        mock_gemini.generate_reply_with_tools.return_value = "Lamento lo ocurrido."
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={
                "from_number": "+1234567890",
                "body": "me hicieron una limpieza pésima, es un mal servicio",
            },
            headers=INTERNAL_API_HEADERS,
        )
    assert response.status_code == 200
    mock_detect.assert_called_once()
