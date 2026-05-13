"""
Tests for the FastAPI app (health, /chat, /whatsapp).
Uses mocked GeminiService and ConversationMemory so no GCP/Firestore required.
"""

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend import config
from backend.domain.availability import _is_within_opening_hours
from backend.domain.catalog import _service_display_label
from backend.domain.disponibilidad import (
    _handle_consultar_disponibilidad,
    _handle_consultar_primer_dia_disponible,
)
from backend.domain.prompt_clinic import (
    _format_opening_hours_for_prompt,
    _format_payment_methods_for_prompt,
)
from backend.domain.urgency_calendar import _calendar_suffix_label_for_cita
from backend.main import app
from backend.schemas import ClinicConfig, PaymentMethodLine
from backend.services.calendar_service import CalendarServiceError
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


def test_is_within_opening_hours_rejects_fractional_minutes():
    clinic = ClinicConfig(
        id="c1",
        name="Clinic",
        system_prompt="x",
        opening_hours={
            "mon_fri": {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "08:00", "to": "17:00"}
        },
    )
    # 2026-04-20 es lunes.
    assert _is_within_opening_hours(clinic, "2026-04-20", "08:30") is False


def test_is_within_opening_hours_enforces_last_slot_before_close():
    clinic = ClinicConfig(
        id="c1",
        name="Clinic",
        system_prompt="x",
        opening_hours={
            "mon_fri": {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "08:00", "to": "17:00"}
        },
    )
    # Última hora válida es 16:00 porque la cita dura 60 minutos.
    assert _is_within_opening_hours(clinic, "2026-04-20", "16:00") is True
    assert _is_within_opening_hours(clinic, "2026-04-20", "17:00") is False


def test_format_opening_hours_prompt_explains_last_start_before_close():
    clinic = ClinicConfig(
        id="c1",
        name="Clinic",
        system_prompt="x",
        opening_hours={
            "mon_fri": {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "08:00", "to": "17:00"},
            "sat": {"days": ["sat"], "from": "08:00", "to": "14:00"},
        },
    )
    text_es = _format_opening_hours_for_prompt(clinic, "es")
    assert "16:00" in text_es
    assert "13:00" in text_es
    assert "no ofrezcas inicio a las 17:00" in text_es
    assert "no ofrezcas inicio a las 14:00" in text_es
    text_en = _format_opening_hours_for_prompt(clinic, "en")
    assert "last bookable start" in text_en.lower()
    assert "16:00" in text_en


def test_format_payment_methods_empty_when_not_configured():
    clinic = ClinicConfig(id="c1", name="Clinic", system_prompt="x")
    assert _format_payment_methods_for_prompt(clinic, "es") == ""
    assert _format_payment_methods_for_prompt(clinic, "en") == ""


def test_format_payment_methods_lists_bilingual_lines():
    clinic = ClinicConfig(
        id="c1",
        name="Clinic",
        system_prompt="x",
        payment_methods=[
            PaymentMethodLine(es="Solo efectivo.", en="Cash only."),
            PaymentMethodLine(es="Tarjetas.", en="Cards accepted."),
        ],
    )
    text_es = _format_payment_methods_for_prompt(clinic, "es")
    assert "MÉTODOS DE PAGO" in text_es
    assert "Solo efectivo." in text_es
    assert "Tarjetas." in text_es

    text_en = _format_payment_methods_for_prompt(clinic, "en")
    assert "ACCEPTED PAYMENT" in text_en
    assert "Cash only." in text_en
    assert "Cards accepted." in text_en


def test_calendar_suffix_only_for_evaluacion():
    assert _calendar_suffix_label_for_cita("evaluacion", {"suffix_urgencia": "dolor_intenso"}) == "dolor intenso"
    assert _calendar_suffix_label_for_cita("evaluacion", {"suffix_urgencia": "dolor_post_cita"}) == "dolor post cita"
    assert _calendar_suffix_label_for_cita("limpieza", {"suffix_urgencia": "dolor_intenso"}) is None
    assert _calendar_suffix_label_for_cita("evaluacion", {}) is None


def test_consultar_primer_dia_disponible_finds_next_day_demo_clinic_2():
    # 2026-05-03 es domingo; mañana lunes 2026-05-04 tiene horario mon_fri en demo_clinic_2.
    with patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 5, 3)):
        out = _handle_consultar_primer_dia_disponible("demo_clinic_2", "es", {})
    assert out["ok"] is True
    assert out["fecha"] == "2026-05-04"
    assert len(out["primeras_tres_horas"]) <= 3
    assert len(out["horas_disponibles"]) >= 1
    assert out["primeras_tres_horas"] == out["horas_disponibles"][:3]


def test_consultar_primer_dia_sin_disponibilidad_cuando_siempre_cerrado():
    with (
        patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 5, 3)),
        patch("backend.domain.availability._opening_ranges_for_day", return_value=[]),
    ):
        out = _handle_consultar_primer_dia_disponible("demo_clinic_2", "es", {"max_dias": 5})
    assert out["ok"] is False
    assert out["error"] == "sin_disponibilidad"


def test_consultar_disponibilidad_returns_calendar_slots_when_sync_on():
    # 2026-04-20 debe ser estrictamente posterior a "hoy" (El Salvador) para no activar regla de anticipación.
    with (
        patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 4, 19)),
        patch("backend.domain.availability._available_hourly_slots_for_clinic", return_value=["09:00", "10:00"]),
    ):
        out = _handle_consultar_disponibilidad("demo_clinic_1", "es", {"fecha": "2026-04-20"})
    assert out["ok"] is True
    assert out["fuente"] == "google_calendar"
    assert out["horas_disponibles"] == ["09:00", "10:00"]


def test_consultar_disponibilidad_calendar_error_returns_ok_false():
    with (
        patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 4, 19)),
        patch(
            "backend.domain.availability._available_hourly_slots_for_clinic",
            side_effect=CalendarServiceError("API down"),
        ),
    ):
        out = _handle_consultar_disponibilidad("demo_clinic_1", "es", {"fecha": "2026-04-20"})
    assert out["ok"] is False
    assert out["error"] == "calendar_read_failed"


def test_consultar_disponibilidad_sin_sync_usa_solo_horario():
    with patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 4, 19)):
        out = _handle_consultar_disponibilidad("demo_clinic_2", "es", {"fecha": "2026-04-20"})
    assert out["ok"] is True
    assert out["fuente"] == "solo_horario_sin_calendario"
    assert "08:00" in out["horas_disponibles"]
    assert "16:00" in out["horas_disponibles"]
    assert "17:00" not in out["horas_disponibles"]


def test_consultar_disponibilidad_mismo_dia_vacio_y_nota_anticipacion():
    with patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 4, 20)):
        out_es = _handle_consultar_disponibilidad("demo_clinic_1", "es", {"fecha": "2026-04-20"})
    assert out_es["ok"] is True
    assert out_es["horas_disponibles"] == []
    assert "anticipación" in (out_es.get("nota") or "").lower() or "mismo día" in (out_es.get("nota") or "").lower()

    with patch("backend.domain.availability._today_in_el_salvador", return_value=date(2026, 4, 20)):
        out_en = _handle_consultar_disponibilidad("demo_clinic_1", "en", {"fecha": "2026-04-20"})
    assert out_en["ok"] is True
    assert out_en["horas_disponibles"] == []
    assert "same-day" in (out_en.get("nota") or "").lower() or "tomorrow" in (out_en.get("nota") or "").lower()


def test_service_display_label_uses_name_or_name_en():
    assert _service_display_label("demo_clinic_1", "limpieza_dental", "es") == "Limpieza dental"
    assert _service_display_label("demo_clinic_1", "limpieza_dental", "en") == "Limpieza dental"
    assert _service_display_label("demo_clinic_1", "id_inexistente_xyz", "es") == "id_inexistente_xyz"


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
    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", mock_memory
    ), patch("backend.bootstrap.llm_classify_intent", return_value=Intent.SMALL_TALK):
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

    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", mock_memory
    ), patch("backend.bootstrap.llm_classify_intent", return_value=Intent.SMALL_TALK):
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
    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", mock_memory
    ), patch("backend.bootstrap.llm_classify_intent", return_value=Intent.CITA):
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
    with patch("backend.services.calendar_sync_service.run_calendar_to_bigquery_sync", return_value=fake):
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
    with patch("backend.api.routers.whatsapp_meta.send_text_message", new_callable=AsyncMock) as mock_send, patch(
        "backend.bootstrap._generate_and_persist_reply"
    ) as mock_gen, patch("backend.bootstrap.conversation_memory", mock_memory):
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
    with patch("backend.api.routers.whatsapp_meta.send_text_message", new_callable=AsyncMock) as mock_send, patch(
        "backend.bootstrap.conversation_memory", mock_memory
    ), patch("backend.bootstrap.llm_classify_intent", return_value=Intent.SMALL_TALK), patch(
        "backend.bootstrap.gemini_service"
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
    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", mock_memory
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
    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", mock_memory
    ), patch("backend.bootstrap.llm_classify_intent", return_value=Intent.SMALL_TALK):
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
