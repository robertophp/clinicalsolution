"""Detección del último servicio consultado."""

from backend.domain.service_context import detect_discussed_service


def test_detect_discussed_service_by_name() -> None:
    sid = detect_discussed_service(
        "¿Cuánto cuesta una limpieza dental?",
        "demo_clinic_1",
    )
    assert sid == "limpieza_dental"


def test_detect_discussed_service_no_match() -> None:
    assert detect_discussed_service("Hola, buenos días", "demo_clinic_1") is None
