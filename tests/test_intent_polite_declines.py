"""Rechazos corteses y despedidas: deben clasificarse como SMALL_TALK."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app
from backend.services.intent_classifier import (
    Intent,
    classify_intent,
    is_polite_decline_or_farewell,
)

INTERNAL_API_HEADERS = {"Authorization": "Bearer test-internal-api-key"}


@pytest.mark.parametrize(
    "message",
    [
        "no gracias",
        "no, gracias",
        "ok bye",
        "bye",
        "chao",
        "adios",
        "hasta luego",
        "gracias",
        "por ahora no",
    ],
)
def test_polite_decline_and_farewell_are_small_talk(message: str):
    assert is_polite_decline_or_farewell(message)
    assert classify_intent(message, "es") is Intent.SMALL_TALK


def test_contextual_no_after_offer_is_small_talk():
    history = [
        {"role": "user", "content": "¿precio biodentine?"},
        {
            "role": "assistant",
            "content": "¿Te gustaría agendar una evaluación para que la doctora valore tu caso?",
        },
    ]
    assert classify_intent("no", "es", history=history) is Intent.SMALL_TALK


def test_no_alone_without_offer_context_stays_out_of_domain():
    assert classify_intent("no", "es", history=[]) is Intent.OUT_OF_DOMAIN


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_no_gracias_reaches_gemini_not_guardrail(client):
    memory = MagicMock()
    memory.get_metadata.return_value = {"patient_first_name": "Roberto"}
    memory.get_recent_messages.return_value = [
        {"role": "user", "content": "¿precio biodentine?"},
        {
            "role": "assistant",
            "content": "¿Te gustaría agendar una evaluación para que la doctora valore tu caso?",
        },
    ]
    memory.add_message.return_value = None

    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", memory
    ), patch("backend.bootstrap.llm_classify_intent") as mock_llm_intent:
        mock_gemini.generate_reply_with_tools.return_value = (
            "Entendido, Roberto. Si más adelante quieres agendar, aquí estoy."
        )
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+50370000001", "body": "no gracias"},
            headers=INTERNAL_API_HEADERS,
        )

    assert response.status_code == 200
    mock_llm_intent.assert_not_called()
    data = response.json()
    assert "fuera de lo que puedo hacer" not in data["reply"].lower()
    mock_gemini.generate_reply_with_tools.assert_called_once()
