"""
Tests for the FastAPI app (health, /chat, /whatsapp).
Uses mocked GeminiService and ConversationMemory so no GCP/Firestore required.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend import config
from backend.main import app
from backend.services.intent_classifier import Intent


@pytest.fixture
def mock_memory():
    """Mock conversation_memory so tests don't need Firestore."""
    m = MagicMock()
    m.get_recent_messages.return_value = []
    m.get_metadata.return_value = {}
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
    ), patch("backend.main.llm_classify_intent", return_value=Intent.SMALL_TALK):
        mock_gemini.generate_reply_with_tools.return_value = (
            "Gracias por escribir. ¿En qué podemos ayudarte?"
        )
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+1234567890", "body": "Hola"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["reply"] == "Gracias por escribir. ¿En qué podemos ayudarte?"
    mock_gemini.generate_reply_with_tools.assert_called_once()


@pytest.mark.asyncio
async def test_chat_gemini_error_returns_fallback(client: AsyncClient, mock_memory):
    """When Gemini raises GeminiServiceError, /chat returns a friendly fallback message."""
    from backend.services.gemini_service import GeminiServiceError

    with patch("backend.main.gemini_service") as mock_gemini, patch(
        "backend.main.conversation_memory", mock_memory
    ), patch("backend.main.llm_classify_intent", return_value=Intent.SMALL_TALK):
        mock_gemini.generate_reply_with_tools.side_effect = GeminiServiceError("API error")
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
    ), patch("backend.main.llm_classify_intent", return_value=Intent.CITA):
        mock_gemini.generate_reply_with_tools.return_value = "Gracias. Te esperamos en la clínica."
        response = await client.post(
            "/whatsapp?clinic_id=demo_clinic_1",
            data={"From": "+1234567890", "Body": "Quiero una cita"},
        )
    assert response.status_code == 200
    assert "application/xml" in response.headers.get("content-type", "")
    assert "Gracias. Te esperamos" in response.text


@pytest.mark.asyncio
async def test_meta_webhook_verify_returns_challenge(client: AsyncClient, monkeypatch):
    """GET /webhooks/whatsapp con verify token correcto devuelve hub.challenge."""
    monkeypatch.setattr(config.settings, "META_WEBHOOK_VERIFY_TOKEN", "mi_token_secreto")
    response = await client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "mi_token_secreto",
            "hub.challenge": "challenge_ok_123",
        },
    )
    assert response.status_code == 200
    assert response.text == "challenge_ok_123"


@pytest.mark.asyncio
async def test_job_sync_calendar_rejects_without_secret(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(config.settings, "SCHEDULER_SYNC_SECRET", None)
    response = await client.post("/jobs/sync-calendar-to-bigquery?token=anything")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_job_sync_calendar_rejects_wrong_token(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(config.settings, "SCHEDULER_SYNC_SECRET", "good")
    response = await client.post("/jobs/sync-calendar-to-bigquery?token=bad")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_job_sync_calendar_ok_with_token(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(config.settings, "SCHEDULER_SYNC_SECRET", "good")
    fake = {"ok": True, "totals": {"clinics_processed": 0}, "by_clinic": []}
    with patch("backend.main.run_calendar_to_bigquery_sync", return_value=fake):
        response = await client.post("/jobs/sync-calendar-to-bigquery?token=good")
    assert response.status_code == 200
    assert response.json() == fake


@pytest.mark.asyncio
async def test_meta_webhook_image_with_caption_sends_template_not_gemini(
    client: AsyncClient, monkeypatch, mock_memory
):
    """Meta: imagen + caption sigue siendo plantilla; no llama a Gemini ni usa el caption."""
    monkeypatch.setattr(config.settings, "META_WEBHOOK_SKIP_SIGNATURE_VERIFY", True)
    monkeypatch.setattr(config.settings, "META_WHATSAPP_ACCESS_TOKEN", "test_token")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "1098116840045683"},
                            "messages": [
                                {
                                    "from": "50312345678",
                                    "type": "image",
                                    "image": {"caption": "hola mundo"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    with patch("backend.main.send_text_message", new_callable=AsyncMock) as mock_send, patch(
        "backend.main._generate_and_persist_reply"
    ) as mock_gen, patch("backend.main.conversation_memory", mock_memory):
        mock_send.return_value = None
        response = await client.post(
            "/webhooks/whatsapp",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 200
    mock_gen.assert_not_called()
    mock_send.assert_called_once()
    body_sent = mock_send.call_args.kwargs["body"]
    assert "foto" in body_sent.lower() or "photo" in body_sent.lower()


@pytest.mark.asyncio
async def test_meta_webhook_text_calls_gemini(client: AsyncClient, monkeypatch, mock_memory):
    monkeypatch.setattr(config.settings, "META_WEBHOOK_SKIP_SIGNATURE_VERIFY", True)
    monkeypatch.setattr(config.settings, "META_WHATSAPP_ACCESS_TOKEN", "test_token")
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "1098116840045683"},
                            "messages": [
                                {
                                    "from": "50312345678",
                                    "type": "text",
                                    "text": {"body": "Hola"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    with patch("backend.main.send_text_message", new_callable=AsyncMock) as mock_send, patch(
        "backend.main.conversation_memory", mock_memory
    ), patch("backend.main.llm_classify_intent", return_value=Intent.SMALL_TALK), patch(
        "backend.main.gemini_service"
    ) as mock_gemini:
        mock_send.return_value = None
        mock_gemini.generate_reply_with_tools.return_value = "Respuesta de prueba"
        response = await client.post(
            "/webhooks/whatsapp",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 200
    mock_gemini.generate_reply_with_tools.assert_called_once()
    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["body"] == "Respuesta de prueba"


@pytest.mark.asyncio
async def test_whatsapp_twilio_media_only_template_no_gemini(client: AsyncClient, mock_memory):
    """Twilio: adjunto sin Body → plantilla; no persiste ni llama a Gemini."""
    with patch("backend.main.gemini_service") as mock_gemini, patch(
        "backend.main.conversation_memory", mock_memory
    ):
        response = await client.post(
            "/whatsapp?clinic_id=demo_clinic_1",
            data={
                "From": "+1234567890",
                "Body": "",
                "NumMedia": "1",
                "MediaContentType0": "image/jpeg",
            },
        )
    assert response.status_code == 200
    assert "application/xml" in response.headers.get("content-type", "")
    mock_gemini.generate_reply_with_tools.assert_not_called()
    mock_memory.add_message.assert_not_called()
    assert "foto" in response.text.lower() or "photo" in response.text.lower()


@pytest.mark.asyncio
async def test_whatsapp_twilio_text_plus_media_still_uses_gemini(client: AsyncClient, mock_memory):
    with patch("backend.main.gemini_service") as mock_gemini, patch(
        "backend.main.conversation_memory", mock_memory
    ), patch("backend.main.llm_classify_intent", return_value=Intent.SMALL_TALK):
        mock_gemini.generate_reply_with_tools.return_value = "Ok"
        response = await client.post(
            "/whatsapp?clinic_id=demo_clinic_1",
            data={
                "From": "+1234567890",
                "Body": "Hola",
                "NumMedia": "1",
                "MediaContentType0": "image/jpeg",
            },
        )
    assert response.status_code == 200
    mock_gemini.generate_reply_with_tools.assert_called_once()
