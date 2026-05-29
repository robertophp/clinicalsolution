"""Confirmación de agendado en backend (sí / confirmo → agendar_cita)."""

import pytest

from backend.domain.booking_confirmation import (
    assistant_asks_booking_confirm,
    classify_booking_confirm_response,
    normalize_pending_booking_args,
)


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("si", "approve"),
        ("sí", "approve"),
        ("confirmo", "approve"),
        ("ok", "approve"),
        ("vale", "approve"),
        ("SI", "approve"),
        ("no gracias", "decline"),
        ("cambia la hora", "revise"),
        ("tal vez", "unclear"),
    ],
)
def test_classify_booking_confirm_response_es(msg, expected):
    assert classify_booking_confirm_response(msg, "es") == expected


def test_classify_booking_confirm_response_en_yes():
    assert classify_booking_confirm_response("yes", "en") == "approve"
    assert classify_booking_confirm_response("confirm", "en") == "approve"


def test_assistant_asks_booking_confirm_single_asterisk():
    text = "¿Me confirmas con un *sí* o *confirmo* para guardarla en el sistema?"
    assert assistant_asks_booking_confirm(text) is True


def test_assistant_asks_booking_confirm_detects_prompt():
    text = (
        "Para agendar un blanqueamiento dental para mañana, viernes 29 de mayo a las 10:00, "
        "necesito que me confirmes con un **sí** o **confirmo**. ¿Te parece bien?"
    )
    assert assistant_asks_booking_confirm(text) is True


def test_assistant_asks_booking_confirm_false_for_slots_list():
    assert assistant_asks_booking_confirm("Horas libres: 09:00, 10:00.") is False


def test_normalize_pending_booking_args():
    data = {
        "nombre": "Roberto Menjivar",
        "fecha": "2026-05-29",
        "hora": "10:00",
        "servicio": "blanqueamiento_dental",
    }
    out = normalize_pending_booking_args(data)
    assert out == data


def test_normalize_pending_booking_args_rejects_bad_date():
    assert normalize_pending_booking_args(
        {"nombre": "A", "fecha": "29-05-2026", "hora": "10:00", "servicio": "x"}
    ) is None
