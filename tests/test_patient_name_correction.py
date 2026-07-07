"""Corrección de nombre del titular WhatsApp (puerta amplia + regex + LLM)."""

from unittest.mock import MagicMock

from backend.domain.conversation_prompt import build_conversation_system_prompt
from backend.domain.patient_name_extraction import (
    extract_corrected_name_from_message,
    extract_corrected_name_with_gemini,
    message_may_be_name_correction,
    message_signals_name_correction,
    try_extract_name_correction,
)

ANTONIO_MSG = (
    "Veo q me llamas Roberto pero me gustaría corregir mi nombre es Antonio Menjivar"
)


def test_signals_name_correction():
    assert message_signals_name_correction("Me equivoqué, mi nombre es Alejandro")
    assert message_signals_name_correction("No es Alejandrro, es Alejandro")
    assert message_signals_name_correction("Correct my name to John Smith")
    assert message_signals_name_correction(ANTONIO_MSG)
    assert not message_signals_name_correction("Hola Alejandro, quiero una cita")


def test_broad_gate_antonio_case():
    assert message_may_be_name_correction(
        ANTONIO_MSG,
        stored_name="Roberto Menjivar",
        stored_first_name="Roberto",
    )


def test_broad_gate_skips_unrelated_message():
    assert not message_may_be_name_correction(
        "Hola Alejandro, quiero una cita",
        stored_first_name="Alejandro",
    )


def test_extract_antonio_case_without_llm():
    name = extract_corrected_name_from_message(
        ANTONIO_MSG,
        stored_name="Roberto Menjivar",
        stored_first_name="Roberto",
    )
    assert name == "Antonio Menjivar"


def test_extract_corrected_name_typo_fix():
    name = extract_corrected_name_from_message(
        "Me equivoqué con el nombre, es Alejandro",
        stored_name="Alejandrro",
    )
    assert name == "Alejandro"


def test_extract_corrected_name_not_x_is_y():
    name = extract_corrected_name_from_message(
        "No es Alejandrro, es Alejandro García",
        stored_name="Alejandrro",
    )
    assert name == "Alejandro García"


def test_extract_corrected_name_skips_same_name():
    assert (
        extract_corrected_name_from_message(
            "Me equivoqué, es Alejandro",
            stored_name="Alejandro",
            stored_first_name="Alejandro",
        )
        is None
    )


def test_extract_corrected_name_without_context_returns_none():
    assert extract_corrected_name_from_message("Alejandro Menjivar") is None


def test_try_extract_name_correction_without_gemini():
    name = try_extract_name_correction(
        None,
        "Corrige mi nombre a Roberto Menjivar",
        "es",
        stored_name="Roberto",
        stored_first_name="Roberto",
    )
    assert name == "Roberto Menjivar"


def test_llm_fallback_when_regex_insufficient():
    gemini = MagicMock()
    gemini.generate_reply.return_value = (
        '{"is_name_correction": true, "nombre": "Antonio Menjivar"}'
    )
    name = extract_corrected_name_with_gemini(
        gemini,
        "Oye no me digas Roberto porfa, soy Antonio Menjivar",
        "es",
        stored_name="Roberto Menjivar",
        stored_first_name="Roberto",
    )
    assert name == "Antonio Menjivar"
    gemini.generate_reply.assert_called_once()


def test_prompt_includes_name_correction_block():
    text = build_conversation_system_prompt(
        language="es",
        clinic_id="demo_clinic_1",
        clinic_name="Clínica",
        assistant_name="Bernardo",
        system_prompt="Base",
        system_prompt_en=None,
        is_first_message=False,
        stored_first_name="Alejandro",
        stored_full_name="Alejandro García",
        clinics_by_id={},
        name_just_corrected=True,
    )
    assert "CORRECCIÓN DE NOMBRE" in text
    assert "he actualizado tu nombre" in text
