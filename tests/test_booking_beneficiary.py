"""Detección de cita para titular vs. otra persona."""

from backend.domain.booking_beneficiary import (
    build_booking_beneficiary_hint,
    message_signals_booking_for_other,
    message_signals_booking_for_self,
)


def test_booking_for_other_signals():
    assert message_signals_booking_for_other("No es para mí, es para mi madre")
    assert message_signals_booking_for_other("Quiero cita para mi hermano Manuel")


def test_booking_for_self_signals():
    assert message_signals_booking_for_self("Es para mí")
    assert not message_signals_booking_for_self("No es para mí, es para mi madre")


def test_hint_for_other():
    hint = build_booking_beneficiary_hint(
        "cita para mi madre",
        language="es",
        stored_first_name="Roberto",
    )
    assert hint is not None
    assert "es_para_tercero=true" in hint


def test_normalize_pending_with_tercero():
    from backend.domain.booking_confirmation import normalize_pending_booking_args

    out = normalize_pending_booking_args(
        {
            "nombre": "María López",
            "fecha": "2026-05-15",
            "hora": "10:00",
            "servicio": "limpieza_dental",
            "es_para_tercero": True,
            "nombre_titular": "Roberto Menjivar",
        }
    )
    assert out is not None
    assert out["es_para_tercero"] == "true"
    assert out["nombre_titular"] == "Roberto Menjivar"
