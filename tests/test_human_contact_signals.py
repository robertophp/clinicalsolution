"""Petición explícita de contacto humano (prioridad sobre urgencia)."""

from backend.domain.human_contact_signals import message_signals_human_contact_request
from backend.domain.urgency_signals import message_signals_urgency


def test_human_contact_hablar_con_doctora():
    assert message_signals_human_contact_request("Quiero hablar con la doctora por favor") is True


def test_human_contact_encargado():
    assert message_signals_human_contact_request("¿Me comunica con un encargado?") is True


def test_human_contact_speak_with_doctor_en():
    assert message_signals_human_contact_request("I need to speak with the doctor") is True


def test_human_contact_generic_booking_false():
    assert message_signals_human_contact_request("quiero agendar limpieza el viernes") is False


def test_human_contact_wins_over_urgency_when_both():
    msg = "Es urgente, necesito hablar con el doctor"
    assert message_signals_human_contact_request(msg) is True
    assert message_signals_urgency(msg) is True
    # bootstrap: skip_transfer_for_urgency = urgency and not human_contact
    assert message_signals_urgency(msg) and not message_signals_human_contact_request(msg) is False
