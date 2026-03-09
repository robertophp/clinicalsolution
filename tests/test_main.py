"""
Tests for the FastAPI app (health, /chat, /whatsapp).
Uses mocked GeminiService and ConversationMemory so no GCP/Firestore required.
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


@pytest.fixture
def mock_memory():
    """Mock conversation_memory so tests don't need Firestore."""
    m = MagicMock()
    m.get_recent_messages.return_value = []
    m.add_message.return_value = None
    return m


@pytest.fixture
def client():
    """Async HTTP client for the FastAPI app."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health_returns_ok(client: AsyncClient):
    """GET /health returns 200 and 'OK'."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.text == "OK"


@pytest.mark.asyncio
async def test_chat_unknown_clinic_returns_error_message(client: AsyncClient):
    """POST /chat with unknown clinic_id returns JSON with error message."""
    response = await client.post(
        "/chat?clinic_id=unknown_clinic",
        json={"from_number": "+123", "body": "Hola"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    assert "no se encontró la clínica" in data["reply"].lower()


@pytest.mark.asyncio
async def test_chat_with_mocked_gemini_returns_reply(client: AsyncClient, mock_memory):
    """POST /chat with valid clinic_id and mocked Gemini returns the mocked reply."""
    with patch("backend.main.gemini_service") as mock_gemini, patch(
        "backend.main.conversation_memory", mock_memory
    ):
        mock_gemini.generate_reply.return_value = "Gracias por escribir. ¿En qué podemos ayudarte?"
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+1234567890", "body": "Hola"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Gracias por escribir. ¿En qué podemos ayudarte?"
    mock_gemini.generate_reply.assert_called_once()


@pytest.mark.asyncio
async def test_chat_gemini_error_returns_fallback(client: AsyncClient, mock_memory):
    """When Gemini raises GeminiServiceError, /chat returns a friendly fallback message."""
    from backend.services.gemini_service import GeminiServiceError

    with patch("backend.main.gemini_service") as mock_gemini, patch(
        "backend.main.conversation_memory", mock_memory
    ):
        mock_gemini.generate_reply.side_effect = GeminiServiceError("API error")
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+123", "body": "Hola"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    assert "problema temporal" in data["reply"].lower() or "inténtalo" in data["reply"].lower()


@pytest.mark.asyncio
async def test_whatsapp_unknown_clinic_returns_twiml(client: AsyncClient):
    """POST /whatsapp with unknown clinic_id returns 200 and TwiML error message."""
    response = await client.post(
        "/whatsapp?clinic_id=unknown",
        data={"From": "+123", "Body": "Hola"},
    )
    assert response.status_code == 200
    assert "application/xml" in response.headers.get("content-type", "")
    assert "no se encontró la clínica" in response.text.lower() or "Message" in response.text


@pytest.mark.asyncio
async def test_whatsapp_with_mocked_gemini_returns_twiml(client: AsyncClient, mock_memory):
    """POST /whatsapp with valid clinic and mocked Gemini returns TwiML with reply."""
    with patch("backend.main.gemini_service") as mock_gemini, patch(
        "backend.main.conversation_memory", mock_memory
    ):
        mock_gemini.generate_reply.return_value = "Gracias. Te esperamos en la clínica."
        response = await client.post(
            "/whatsapp?clinic_id=demo_clinic_1",
            data={"From": "+1234567890", "Body": "Quiero una cita"},
        )
    assert response.status_code == 200
    assert "application/xml" in response.headers.get("content-type", "")
    assert "Gracias. Te esperamos" in response.text
