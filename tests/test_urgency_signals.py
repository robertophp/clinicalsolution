"""Señales de urgencia/dolor para prioridad sobre derivación a humano."""

from backend.domain.prompt_clinic import _format_urgency_dolor_prompt_block
from backend.domain.urgency_signals import message_signals_urgency


def test_message_signals_urgency_pueden_atenderme_hoy():
    assert message_signals_urgency("pueden atenderme hoy? es urgente") is True


def test_message_signals_urgency_dolor():
    assert message_signals_urgency("tengo mucho dolor en una muela") is True


def test_message_signals_urgency_emergencia_en():
    assert message_signals_urgency("I need an emergency appointment") is True


def test_message_signals_urgency_generic_booking_false():
    assert message_signals_urgency("quiero agendar limpieza el viernes a las 10") is False


def test_message_signals_urgency_empty_false():
    assert message_signals_urgency("") is False


def test_urgency_prompt_block_es_mentions_same_day_policy():
    block = _format_urgency_dolor_prompt_block("es")
    assert "urgente" in block.lower()
    assert "consultar_primer_dia_disponible" in block
    assert "mañana" in block
    assert "evaluacion" in block
    assert "primeras_tres_horas" in block
