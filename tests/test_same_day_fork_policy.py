"""Flujo cita mismo día: señales, elección y prompts."""

from backend.domain.same_day_fork_policy import (
    classify_same_day_choice_response,
    format_same_day_appointment_prompt_block,
    patient_prompt_same_day_choice,
    patient_prompt_same_day_transfer_sent,
    should_trigger_same_day_fork,
)
from backend.domain.urgency_signals import message_signals_same_day_request, message_signals_urgency
from backend.schemas.clinic_policies import SameDayForkPolicies
from tests.emoji_utils import count_emojis as _emoji_count


_POLICY = SameDayForkPolicies(enabled=True)


def test_same_day_request_detects_cita_hoy():
    assert message_signals_same_day_request("quiero una cita hoy")


def test_same_day_request_detects_pueden_atenderme_hoy():
    assert message_signals_same_day_request("pueden atenderme hoy?")


def test_same_day_request_detects_appointment_today_en():
    assert message_signals_same_day_request("I need an appointment today")


def test_mild_pain_without_hoy_does_not_trigger_same_day():
    assert message_signals_urgency("tengo dolor en una muela") is True
    assert message_signals_same_day_request("tengo dolor en una muela") is False


def test_classify_same_day_choice_appointment():
    assert classify_same_day_choice_response("prefiero mañana a primera hora") == "appointment"


def test_classify_same_day_choice_team_contact():
    assert classify_same_day_choice_response("que me contacte el equipo") == "team_contact"


def test_classify_same_day_choice_numeric_one():
    assert classify_same_day_choice_response("1") == "appointment"


def test_classify_same_day_choice_numeric_two():
    assert classify_same_day_choice_response("2") == "team_contact"


def test_same_day_choice_prompt_has_user_wording():
    text = patient_prompt_same_day_choice("es", _POLICY)
    assert "por este canal" in text
    assert "hoy, sin embargo" in text
    assert "cupos disponibles hoy mismo" in text
    assert "🙏" in text
    assert _emoji_count(text) <= 2


def test_same_day_transfer_sent_includes_phone_and_confirmation():
    text = patient_prompt_same_day_transfer_sent(
        "es",
        _POLICY,
        clinic_phone="+503 2235 3513",
    )
    assert "direccionado a nuestro equipo" in text
    assert "+503 2235 3513" in text
    assert "✅" in text
    assert "Cualquier otra consulta" in text


def test_same_day_appointment_block_mentions_tools():
    block = format_same_day_appointment_prompt_block("es")
    assert "consultar_primer_dia_disponible" in block
    assert "DEBES llamar" in block


def test_should_trigger_same_day_fork_when_none():
    assert should_trigger_same_day_fork(
        policy_enabled=True,
        same_day_phase="none",
        emergency_phase="none",
        booking_phase="none",
        message="quiero cita hoy",
    )


def test_should_not_trigger_when_appointment_chosen_sticky():
    assert not should_trigger_same_day_fork(
        policy_enabled=True,
        same_day_phase="appointment_chosen",
        emergency_phase="none",
        booking_phase="none",
        message="quiero cita hoy",
    )


def test_should_not_trigger_when_emergency_phase_active():
    assert not should_trigger_same_day_fork(
        policy_enabled=True,
        same_day_phase="none",
        emergency_phase="awaiting_choice",
        booking_phase="none",
        message="quiero cita hoy",
    )
