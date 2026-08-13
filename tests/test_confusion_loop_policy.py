"""Tests del manejador anti-bucle de confusión (menú numérico según contexto)."""
from __future__ import annotations

from backend.domain.confusion_loop_policy import (
    classify_booking_menu_choice,
    classify_confusion_menu_choice,
    classify_general_menu_choice,
    classify_scheduling_menu_choice,
    extract_offered_hours,
    looks_like_bot_repetition,
    message_is_ambiguous,
    patient_prompt_confusion_menu,
    resolve_confusion_context,
    user_reply_is_non_answer,
)
from backend.schemas.clinic_policies import ConfusionLoopPolicies
from tests.emoji_utils import count_emojis


def test_message_is_ambiguous_detects_short_and_gibberish():
    assert message_is_ambiguous("") is True
    assert message_is_ambiguous("xq") is True
    assert message_is_ambiguous("jsjs") is True
    assert message_is_ambiguous("n c") is True
    assert message_is_ambiguous("tons") is True
    assert message_is_ambiguous("voooo") is True
    assert message_is_ambiguous("aaa") is True
    assert message_is_ambiguous("quiero agendar una cita") is False


def test_looks_like_bot_repetition_detects_loop():
    prev = "Para guardar la cita responde **sí** o **confirmo**."
    assert looks_like_bot_repetition(prev, prev) is True
    assert looks_like_bot_repetition(prev, "¿Te ayudo con otra cosa?") is False


def test_extract_offered_hours():
    text = "Tenemos disponibilidad a las 08:00, 09:00 o 10:00. ¿Te gustaría agendar?"
    assert extract_offered_hours(text) == ["08:00", "09:00", "10:00"]


def test_user_reply_is_non_answer_after_scheduling_offer():
    last = (
        "Para mañana viernes 17 de julio, tenemos disponibilidad para una evaluación "
        "a las 08:00, 09:00 o 10:00. ¿Te gustaría agendar en alguna de esas horas?"
    )
    assert user_reply_is_non_answer("N c", last) is True
    assert user_reply_is_non_answer("Tons?", last) is True
    assert user_reply_is_non_answer("si porfa", last) is False
    assert user_reply_is_non_answer("a las 8am", last) is False


def test_resolve_confusion_context_prioritizes_active_flow():
    assert (
        resolve_confusion_context(
            emergency_phase="awaiting_choice",
            same_day_phase="none",
            booking_phase="none",
        )
        == "emergency"
    )
    assert (
        resolve_confusion_context(
            emergency_phase="none",
            same_day_phase="none",
            booking_phase="awaiting_confirm",
        )
        == "booking_confirm"
    )
    assert (
        resolve_confusion_context(
            emergency_phase="none",
            same_day_phase="none",
            booking_phase="none",
        )
        == "general"
    )


def test_scheduling_menu_lists_hours():
    policy = ConfusionLoopPolicies(enabled=True, threshold=2)
    text = patient_prompt_confusion_menu(
        "es",
        policy,
        context="scheduling_offer",
        offered_hours=["08:00", "09:00", "10:00"],
    )
    assert "1. 08:00" in text
    assert "2. 09:00" in text
    assert "3. 10:00" in text
    assert "Ver otros horarios" in text


def test_classify_scheduling_menu_choice():
    hours = ["08:00", "09:00", "10:00"]
    choice, hour = classify_scheduling_menu_choice("2", offered_hours=hours)
    assert choice == "hour"
    assert hour == "09:00"
    choice2, _ = classify_scheduling_menu_choice("4", offered_hours=hours)
    assert choice2 == "other_times"
    choice3, _ = classify_scheduling_menu_choice("5", offered_hours=hours)
    assert choice3 == "human"


def test_emergency_menu_has_numbered_options():
    policy = ConfusionLoopPolicies(enabled=True, threshold=2)
    text = patient_prompt_confusion_menu("es", policy, context="emergency")
    assert "1." in text
    assert "2." in text
    assert "🙈" in text


def test_general_menu_has_four_options():
    policy = ConfusionLoopPolicies(enabled=True, threshold=2)
    text = patient_prompt_confusion_menu("es", policy, context="general")
    assert "1." in text
    assert "4." in text
    assert count_emojis(text) <= 2


def test_classify_emergency_menu_choice():
    assert classify_confusion_menu_choice("1", context="emergency") == "appointment"
    assert classify_confusion_menu_choice("2", context="emergency") == "team_contact"


def test_classify_general_menu_choice():
    assert classify_general_menu_choice("1") == "appointment"
    assert classify_general_menu_choice("4") == "human"


def test_classify_booking_menu_choice():
    assert classify_booking_menu_choice("1") == "approve"
    assert classify_booking_menu_choice("3") == "decline"
