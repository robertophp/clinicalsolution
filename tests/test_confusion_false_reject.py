"""Tests Caso A/B: falso rechazo OUT_OF_DOMAIN y bucle en oferta de cita."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app
from backend.services.intent_classifier import Intent

INTERNAL_API_HEADERS = {"Authorization": "Bearer test-internal-api-key"}


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_unintelligible_shows_general_menu_not_rejection(client):
    memory = MagicMock()
    memory.get_metadata.return_value = {}
    memory.get_recent_messages.return_value = []
    memory.add_message.return_value = None

    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", memory
    ), patch("backend.bootstrap.llm_classify_intent", return_value=Intent.UNINTELLIGIBLE), patch(
        "backend.bootstrap.get_latest_self_cita_for_phone", return_value=None
    ):
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+50370000010", "body": "k onda vo!"},
            headers=INTERNAL_API_HEADERS,
        )

    assert response.status_code == 200
    mock_gemini.generate_reply_with_tools.assert_not_called()
    data = response.json()
    assert "fuera de lo que puedo hacer" not in data["reply"].lower()
    assert "1." in data["reply"]
    assert "Agendar" in data["reply"] or "agendar" in data["reply"]


@pytest.mark.asyncio
async def test_coherent_off_topic_still_gets_rejection(client):
    memory = MagicMock()
    memory.get_metadata.return_value = {}
    memory.get_recent_messages.return_value = []
    memory.add_message.return_value = None

    with patch("backend.bootstrap.gemini_service"), patch(
        "backend.bootstrap.conversation_memory", memory
    ), patch("backend.bootstrap.llm_classify_intent", return_value=Intent.OUT_OF_DOMAIN), patch(
        "backend.bootstrap.get_latest_self_cita_for_phone", return_value=None
    ):
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+50370000011", "body": "¿venden pizza?"},
            headers=INTERNAL_API_HEADERS,
        )

    assert response.status_code == 200
    assert "fuera de lo que puedo hacer" in response.json()["reply"].lower()


@pytest.mark.asyncio
async def test_llm_failure_shows_menu_not_rejection(client):
    memory = MagicMock()
    memory.get_metadata.return_value = {}
    memory.get_recent_messages.return_value = []
    memory.add_message.return_value = None

    with patch("backend.bootstrap.gemini_service"), patch(
        "backend.bootstrap.conversation_memory", memory
    ), patch("backend.bootstrap.llm_classify_intent", side_effect=RuntimeError("fail")), patch(
        "backend.bootstrap.get_latest_self_cita_for_phone", return_value=None
    ):
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+50370000012", "body": "Hey voooo"},
            headers=INTERNAL_API_HEADERS,
        )

    assert response.status_code == 200
    assert "fuera de lo que puedo hacer" not in response.json()["reply"].lower()
    assert "1." in response.json()["reply"]


@pytest.mark.asyncio
async def test_scheduling_non_answer_escalates_to_numbered_menu(client):
    offer = (
        "Para mañana viernes 17 de julio, tenemos disponibilidad para una evaluación "
        "a las 08:00, 09:00 o 10:00. ¿Te gustaría agendar en alguna de esas horas?"
    )
    history = [{"role": "assistant", "content": offer}]
    memory = MagicMock()
    memory.get_metadata.return_value = {}
    memory.get_recent_messages.return_value = history
    memory.add_message.return_value = None
    memory.bump_confusion_count.return_value = 2

    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", memory
    ), patch("backend.bootstrap.get_latest_self_cita_for_phone", return_value=None):
        mock_gemini.generate_reply_with_tools.return_value = "should not be called"
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+50370000013", "body": "Tons?"},
            headers=INTERNAL_API_HEADERS,
        )

    assert response.status_code == 200
    mock_gemini.generate_reply_with_tools.assert_not_called()
    reply = response.json()["reply"]
    assert "08:00" in reply
    assert "1." in reply
    assert "¿Te gustaría agendar?" not in reply
