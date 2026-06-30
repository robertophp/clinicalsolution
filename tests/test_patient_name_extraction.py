"""Extracción heurística del nombre del paciente."""

from backend.domain.patient_name_extraction import extract_patient_name_from_message


def test_extract_name_me_llamo() -> None:
    assert extract_patient_name_from_message("Me llamo Roberto García") == "Roberto García"


def test_extract_name_soy() -> None:
    assert extract_patient_name_from_message("Soy Ana") == "Ana"


def test_extract_name_short_reply() -> None:
    assert extract_patient_name_from_message("María López") == "María López"


def test_extract_name_skips_greeting() -> None:
    assert extract_patient_name_from_message("Hola") is None


def test_extract_name_skips_question() -> None:
    assert extract_patient_name_from_message("¿Cuánto cuesta?") is None
