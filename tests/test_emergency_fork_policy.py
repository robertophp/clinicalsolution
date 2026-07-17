"""Flujo de emergencia / dolor grave: señales, elección y prompts."""

from backend.domain.emergency_fork_policy import (
    classify_emergency_choice_response,
    format_emergency_appointment_prompt_block,
    message_signals_severe_urgency,
    patient_prompt_emergency_choice,
    patient_prompt_emergency_transfer_sent,
    should_trigger_emergency_fork,
)
from backend.domain.urgency_signals import message_signals_urgency
from backend.schemas.clinic_policies import EmergencyForkPolicies
from tests.emoji_utils import count_emojis


_POLICY = EmergencyForkPolicies(enabled=True)


def test_severe_urgency_detects_intense_pain():
    assert message_signals_severe_urgency("me duele mucho la muela es demasiado intenso")


def test_severe_urgency_detects_emergency():
    assert message_signals_severe_urgency("necesito una emergencia dental")


def test_mild_pain_does_not_trigger_severe():
    assert message_signals_urgency("tengo dolor en una muela") is True
    assert message_signals_severe_urgency("tengo dolor en una muela") is False


def test_mild_discomfort_does_not_trigger_severe():
    assert message_signals_severe_urgency("tengo un poco de molestia") is False


def test_classify_emergency_choice_appointment():
    assert classify_emergency_choice_response("prefiero la cita mañana a primera hora") == "appointment"


def test_classify_emergency_choice_team_contact():
    assert classify_emergency_choice_response("que me contacte el equipo médico") == "team_contact"


def test_classify_emergency_choice_unclear():
    assert classify_emergency_choice_response("ok") == "unclear"


def test_emergency_choice_prompt_has_emojis():
    text = patient_prompt_emergency_choice("es", _POLICY)
    assert "🦷" in text
    assert "💛" in text
    assert count_emojis(text) <= 2


def test_emergency_transfer_sent_includes_phone():
    text = patient_prompt_emergency_transfer_sent(
        "es",
        _POLICY,
        clinic_phone="+503 2235 3513",
    )
    assert "+503 2235 3513" in text
    assert "✅" in text


def test_emergency_appointment_block_mentions_tools():
    block = format_emergency_appointment_prompt_block("es")
    assert "consultar_primer_dia_disponible" in block
    assert "dolor_intenso" in block
    assert "DEBES llamar" in block


def test_should_trigger_emergency_fork_when_none_and_severe():
    assert should_trigger_emergency_fork(
        policy_enabled=True,
        emergency_phase="none",
        booking_phase="none",
        message="me duele mucho la muela es demasiado intenso",
    )


def test_should_not_trigger_when_appointment_chosen_sticky():
    assert not should_trigger_emergency_fork(
        policy_enabled=True,
        emergency_phase="appointment_chosen",
        booking_phase="none",
        message="me duele mucho la muela es demasiado intenso",
    )


def test_should_trigger_again_after_phase_reset():
    assert should_trigger_emergency_fork(
        policy_enabled=True,
        emergency_phase="none",
        booking_phase="none",
        message="me duele mucho la muela es demasiado intenso",
    )


def test_should_not_trigger_mild_pain():
    assert not should_trigger_emergency_fork(
        policy_enabled=True,
        emergency_phase="none",
        booking_phase="none",
        message="tengo dolor en una muela",
    )
