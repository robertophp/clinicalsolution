"""Aceptaciones contextuales tras oferta de cita: deben clasificarse como CITA."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.domain.booking_confirmation import patient_prompt_offer_response_unclear
from backend.domain.conversation_prompt import build_conversation_system_prompt
from backend.domain.patient_name_extraction import assistant_asked_for_name
from backend.main import app
from backend.services.intent_classifier import (
    Intent,
    classify_intent,
    is_contextual_offer_acceptance,
    message_signals_affirmative_continuation,
    should_fail_open_after_offer_reconfirm,
    should_reconfirm_after_booking_offer,
)

INTERNAL_API_HEADERS = {"Authorization": "Bearer test-internal-api-key"}

_OFFER_HISTORY = [
    {"role": "user", "content": "¿Hacen limpieza dental?"},
    {
        "role": "assistant",
        "content": "¿Te gustaría agendar una cita para tu limpieza dental?",
    },
]


@pytest.mark.parametrize(
    "message",
    [
        "Si porfa",
        "si porfa",
        "sí gracias",
        "dale",
        "ok dale",
        "claro",
        "vale perfecto",
        "de acuerdo",
    ],
)
def test_affirmative_continuation_signals(message: str):
    assert message_signals_affirmative_continuation(message)


@pytest.mark.parametrize(
    "message",
    [
        "Si porfa",
        "dale",
        "sí gracias",
        "?",
    ],
)
def test_contextual_acceptance_after_booking_offer(message: str):
    assert is_contextual_offer_acceptance(message, _OFFER_HISTORY)
    assert classify_intent(message, "es", history=_OFFER_HISTORY) is Intent.CITA


def test_si_porfa_without_offer_context_stays_out_of_domain():
    assert not is_contextual_offer_acceptance("Si porfa", [])
    assert classify_intent("Si porfa", "es", history=[]) is Intent.OUT_OF_DOMAIN


def test_no_gracias_regression_stays_small_talk():
    assert classify_intent("no gracias", "es", history=_OFFER_HISTORY) is Intent.SMALL_TALK


def test_enthusiastic_reply_triggers_reconfirm_not_cita():
    msg = "Excelente me encantaría"
    assert classify_intent(msg, "es", history=_OFFER_HISTORY) is Intent.OUT_OF_DOMAIN
    assert should_reconfirm_after_booking_offer(msg, _OFFER_HISTORY)
    assert not should_reconfirm_after_booking_offer("Si porfa", _OFFER_HISTORY)


def test_offer_reconfirm_prompt_text_includes_service():
    text = patient_prompt_offer_response_unclear("es", service_name="Limpieza dental")
    assert "Para confirmar" in text
    assert "Limpieza dental" in text
    assert "**sí**" in text


def test_fail_open_after_offer_reconfirm_prompt():
    history = [
        *_OFFER_HISTORY,
        {"role": "user", "content": "Excelente me encantaría"},
        {
            "role": "assistant",
            "content": patient_prompt_offer_response_unclear("es", service_name="Limpieza dental"),
        },
    ]
    assert should_fail_open_after_offer_reconfirm("tal vez mañana", history)
    assert not should_reconfirm_after_booking_offer("tal vez mañana", history)


def test_scheduling_input_after_hour_prompt_is_cita_not_reconfirm():
    history = [
        {"role": "user", "content": "Quiero limpieza"},
        {
            "role": "assistant",
            "content": (
                "Las citas solo pueden agendarse en horas en punto. "
                "¿Te gustaría elegir una hora en punto para tu limpieza dental?"
            ),
        },
    ]
    msg = "8 am mejor"
    assert classify_intent(msg, "es", history=history) is Intent.CITA
    assert not should_reconfirm_after_booking_offer(msg, history)


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("¿Con quién tengo el gusto?", True),
        ("¿Me compartes tu nombre para atenderte mejor?", True),
        ("El precio de la limpieza es $25. ¿Te gustaría agendar?", False),
    ],
)
def test_assistant_asked_for_name(reply: str, expected: bool):
    assert assistant_asked_for_name(reply) is expected


def test_prompt_requires_name_before_booking_when_flag_set():
    from pathlib import Path

    from backend.domain.clinic_loader import CLINIC_POLICIES_BY_ID, load_clinic_tree

    root = Path(__file__).resolve().parents[1] / "src" / "backend" / "data" / "clinics"
    clinics = load_clinic_tree(root)
    cfg = clinics["demo_clinic_1"]
    text = build_conversation_system_prompt(
        language="es",
        clinic_id=cfg.id,
        clinic_name=cfg.name,
        assistant_name=cfg.assistant_name,
        system_prompt=cfg.system_prompt,
        system_prompt_en=cfg.system_prompt_en,
        is_first_message=False,
        stored_first_name=None,
        stored_full_name=None,
        clinics_by_id=clinics,
        policies=CLINIC_POLICIES_BY_ID.get(cfg.id),
        name_collection_phase="asked",
        require_name_before_booking=True,
    )
    assert "OBLIGATORIO" in text
    assert "nombre completo" in text
    assert "NO llames herramientas de citas hasta tener el nombre del beneficiario" in text


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_si_porfa_reaches_gemini_not_guardrail(client):
    memory = MagicMock()
    memory.get_metadata.return_value = {}
    memory.get_recent_messages.return_value = _OFFER_HISTORY
    memory.add_message.return_value = None

    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", memory
    ), patch("backend.bootstrap.llm_classify_intent") as mock_llm_intent, patch(
        "backend.bootstrap.get_latest_self_cita_for_phone", return_value=None
    ):
        mock_gemini.generate_reply_with_tools.return_value = (
            "¡Perfecto! ¿Me compartes tu nombre completo para agendar tu limpieza?"
        )
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+50370000002", "body": "Si porfa"},
            headers=INTERNAL_API_HEADERS,
        )

    assert response.status_code == 200
    mock_llm_intent.assert_not_called()
    data = response.json()
    assert "fuera de lo que puedo hacer" not in data["reply"].lower()
    mock_gemini.generate_reply_with_tools.assert_called_once()


@pytest.mark.asyncio
async def test_enthusiastic_ambiguous_reply_gets_reconfirm_not_guardrail(client):
    history = [
        {"role": "user", "content": "Hacen limpieza dental?"},
        {
            "role": "assistant",
            "content": (
                "¡Hola! Sí, hacemos limpieza dental. USD 30. "
                "¿Te gustaría agendar una cita para tu limpieza o prefieres una evaluación primero?"
            ),
        },
    ]
    memory = MagicMock()
    memory.get_metadata.return_value = {"last_discussed_service_id": "limpieza_dental"}
    memory.get_recent_messages.return_value = history
    memory.add_message.return_value = None
    memory.bump_confusion_count.return_value = 1

    with patch("backend.bootstrap.gemini_service") as mock_gemini, patch(
        "backend.bootstrap.conversation_memory", memory
    ), patch("backend.bootstrap.llm_classify_intent") as mock_llm_intent, patch(
        "backend.bootstrap.get_latest_self_cita_for_phone", return_value=None
    ), patch(
        "backend.bootstrap._service_display_label", return_value="Limpieza dental"
    ):
        response = await client.post(
            "/chat?clinic_id=demo_clinic_1",
            json={"from_number": "+50370000003", "body": "Excelente me encantaría"},
            headers=INTERNAL_API_HEADERS,
        )

    assert response.status_code == 200
    mock_llm_intent.assert_not_called()
    mock_gemini.generate_reply_with_tools.assert_not_called()
    data = response.json()
    assert "para confirmar" in data["reply"].lower()
    assert "agendar" in data["reply"].lower()
    assert "fuera de lo que puedo hacer" not in data["reply"].lower()
