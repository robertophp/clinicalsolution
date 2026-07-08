"""Política pediátrica de edad mínima: detección, extracción de edad, prompt block y calendario."""
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.domain.pediatric_policy import (
    PediatricAgeResult,
    classify_pediatric_context,
    extract_mentioned_age,
    format_pediatric_prompt_block,
    message_signals_pediatric_context,
    patient_prompt_pediatric_decline,
    pediatric_ineligibility_result,
)
from backend.schemas.clinic_policies import PediatricAgePolicies

_POLICY = PediatricAgePolicies(enabled=True, min_age=6)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_signals_pediatric_hijo():
    assert message_signals_pediatric_context("Quiero una cita para mi hijo", _POLICY)


def test_signals_pediatric_hija():
    assert message_signals_pediatric_context("Tengo una consulta para mi hija", _POLICY)


def test_signals_pediatric_nino():
    assert message_signals_pediatric_context("Es para un niño de 8 años", _POLICY)


def test_signals_pediatric_nina():
    assert message_signals_pediatric_context("Mi niña necesita evaluación", _POLICY)


def test_signals_pediatric_menor():
    assert message_signals_pediatric_context("Es para un menor de edad", _POLICY)


def test_signals_pediatric_child_en():
    assert message_signals_pediatric_context("I need an appointment for my child", _POLICY)


def test_signals_pediatric_son_en():
    assert message_signals_pediatric_context("Booking for my son", _POLICY)


def test_signals_pediatric_daughter_en():
    assert message_signals_pediatric_context("My daughter needs a cleaning", _POLICY)


def test_no_signal_for_adult_messages():
    assert not message_signals_pediatric_context("Quiero una cita para mí", _POLICY)
    assert not message_signals_pediatric_context("Para mi madre", _POLICY)
    assert not message_signals_pediatric_context("I want to book an appointment", _POLICY)


def test_no_signal_when_disabled():
    disabled = PediatricAgePolicies(enabled=False, min_age=6)
    assert not message_signals_pediatric_context("cita para mi hijo", disabled)


# ---------------------------------------------------------------------------
# Age extraction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg,expected", [
    ("tiene 5 años", 5),
    ("tiene 5 añitos", 5),
    ("de 8 años", 8),
    ("de 8 anitos", 8),
    ("mi hijo de 3 años", 3),
    ("niña de 10 años de edad", 10),
    ("tiene cuatro años", 4),
    ("tiene cinco años", 5),
    ("Tiene 4 y se llama Santiago", 4),
])
def test_extract_age_spanish(msg, expected):
    assert extract_mentioned_age(msg) == expected


@pytest.mark.parametrize("msg,expected", [
    ("she's 4 years old", 4),
    ("he is 7 years old", 7),
    ("child is 5 years", 5),
    ("my son is 9 y.o.", 9),
    ("she is three years old", 3),
])
def test_extract_age_english(msg, expected):
    assert extract_mentioned_age(msg) == expected


def test_extract_age_none_when_not_mentioned():
    assert extract_mentioned_age("quiero una cita para mi hijo") is None
    assert extract_mentioned_age("my child needs an appointment") is None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def test_classify_under_age():
    result = classify_pediatric_context("Cita para mi hijo de 5 años", _POLICY)
    assert result.is_pediatric is True
    assert result.mentioned_age == 5
    assert result.age_eligible is False


def test_classify_eligible():
    result = classify_pediatric_context("Mi niña de 8 años", _POLICY)
    assert result.is_pediatric is True
    assert result.mentioned_age == 8
    assert result.age_eligible is True


def test_classify_exactly_min_age():
    result = classify_pediatric_context("Mi hijo tiene 6 añitos", _POLICY)
    assert result.age_eligible is True


def test_classify_no_age():
    result = classify_pediatric_context("Quiero cita para mi hijo", _POLICY)
    assert result.is_pediatric is True
    assert result.mentioned_age is None
    assert result.age_eligible is None


def test_classify_not_pediatric():
    result = classify_pediatric_context("Quiero una evaluación dental", _POLICY)
    assert result.is_pediatric is False
    assert result.mentioned_age is None


# ---------------------------------------------------------------------------
# Prompt block
# ---------------------------------------------------------------------------

def _result(eligible, age=None):
    return PediatricAgeResult(is_pediatric=True, mentioned_age=age, age_eligible=eligible)


def test_prompt_block_decline_es():
    block = format_pediatric_prompt_block(
        language="es", policy=_POLICY, result=_result(False, age=4)
    )
    assert "POLÍTICA PEDIÁTRICA" in block
    assert "NO ofrezcas agendar" in block
    assert "NO llames herramientas" in block
    assert "NO ofrezcas evaluación" in block
    assert "ÚNICAMENTE" in block
    assert "4" in block
    assert "6" in block


def test_decline_message_es_short_no_booking_offer():
    msg = patient_prompt_pediatric_decline("es", _POLICY)
    lower = msg.lower()
    assert "6" in msg
    assert "evaluación" not in lower
    assert "agendar" not in lower
    assert "quedo" in lower or "ayudo" in lower


def test_prompt_block_decline_en():
    block = format_pediatric_prompt_block(
        language="en", policy=_POLICY, result=_result(False, age=3)
    )
    assert "PEDIATRIC POLICY" in block
    assert "Do NOT offer to book" in block


def test_prompt_block_welcome_with_age_es():
    block = format_pediatric_prompt_block(
        language="es", policy=_POLICY, result=_result(True, age=8)
    )
    assert "es_para_tercero=true" in block
    assert "nombre_secundario" in block
    assert "menor_8_anios" in block
    assert "suffix_urgencia" in block


def test_prompt_block_welcome_with_age_en():
    block = format_pediatric_prompt_block(
        language="en", policy=_POLICY, result=_result(True, age=7)
    )
    assert "es_para_tercero=true" in block
    assert "menor_7_anios" in block


def test_prompt_block_unknown_age_es():
    block = format_pediatric_prompt_block(
        language="es", policy=_POLICY, result=_result(None)
    )
    assert "es_para_tercero=true" in block
    assert "añitos" in block


def test_prompt_block_empty_when_not_pediatric():
    result = PediatricAgeResult(is_pediatric=False, mentioned_age=None, age_eligible=None)
    block = format_pediatric_prompt_block(language="es", policy=_POLICY, result=result)
    assert block == ""


# ---------------------------------------------------------------------------
# Real WhatsApp phrases
# ---------------------------------------------------------------------------

def test_real_phrase_hijo_de_5():
    result = classify_pediatric_context(
        "Quiero cita para mi hijo de 5 añitos", _POLICY
    )
    assert result.is_pediatric is True
    assert result.age_eligible is False


def test_real_phrase_hija_de_8():
    result = classify_pediatric_context(
        "Necesito evaluación para mi hija de 8 años", _POLICY
    )
    assert result.is_pediatric is True
    assert result.age_eligible is True


def test_real_phrase_general_question():
    result = classify_pediatric_context("Ustedes atienden niños?", _POLICY)
    assert result.is_pediatric is True
    assert result.mentioned_age is None


def test_classify_under_age_with_tiene_4_only():
    history = [{"role": "user", "content": "Quiero limpieza dental para niños"}]
    result = classify_pediatric_context(
        "Tiene 4 y se llama Santiago", _POLICY, history=history
    )
    assert result.is_pediatric is True
    assert result.mentioned_age == 4
    assert result.age_eligible is False


def test_ineligibility_from_history_when_followup_no_age():
    history = [
        {"role": "user", "content": "Quiero limpieza dental para niños"},
        {"role": "assistant", "content": "¿Cuántos añitos tiene?"},
        {"role": "user", "content": "Tiene 4 y se llama Santiago"},
    ]
    blocked = pediatric_ineligibility_result(
        "Solo quiero limpieza",
        _POLICY,
        history=history,
    )
    assert blocked is not None
    assert blocked.age_eligible is False
    assert blocked.mentioned_age == 4


# ---------------------------------------------------------------------------
# Calendar: minor note in description
# ---------------------------------------------------------------------------

def test_calendar_minor_note_in_description():
    from backend.services.calendar_service import CalendarService

    cita = SimpleNamespace(
        fecha_cita=date(2026, 7, 15),
        hora_cita=time(10, 0),
        es_para_tercero=True,
        paciente_nombre="María López",
        nombre_secundario="Sofía López",
        beneficiario_edad=8,
        razon_cita="evaluacion",
        telefono="+50370000001",
        clinic_id="demo_clinic_1",
    )
    event = CalendarService._build_event_payload(
        cita=cita,
        clinic_name="Clínica demo",
        assistant_name="Bernardo",
        servicio_display="Evaluación",
        calendar_suffix=None,
    )
    desc = event["description"]
    assert "menor de edad (8 años)" in desc
    # Title should NOT contain "menor_8_anios"
    assert "menor_8_anios" not in event["summary"]


def test_calendar_no_minor_note_when_no_suffix():
    from backend.services.calendar_service import CalendarService

    cita = SimpleNamespace(
        fecha_cita=date(2026, 7, 15),
        hora_cita=time(10, 0),
        es_para_tercero=False,
        paciente_nombre="Roberto Menjivar",
        nombre_secundario=None,
        razon_cita="limpieza_dental",
        telefono="+50370000002",
        clinic_id="demo_clinic_1",
    )
    event = CalendarService._build_event_payload(
        cita=cita,
        clinic_name="Clínica demo",
        assistant_name="Bernardo",
        servicio_display="Limpieza dental",
    )
    assert "menor de edad" not in event["description"]
