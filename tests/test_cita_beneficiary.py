"""Titular vs. beneficiario en citas."""

from types import SimpleNamespace

from backend.domain.cita_beneficiary import (
    cita_attendee_display_name,
    cita_contact_display_name,
    cita_es_para_tercero,
    resolve_booking_fields_from_args,
)


def test_self_booking_fields():
    fields = resolve_booking_fields_from_args({"nombre": "Roberto Menjivar", "es_para_tercero": False})
    assert fields["es_para_tercero"] is False
    assert fields["paciente_nombre"] == "Roberto Menjivar"
    assert fields["nombre_secundario"] is None


def test_third_party_booking_fields():
    fields = resolve_booking_fields_from_args(
        {
            "nombre": "María López",
            "es_para_tercero": True,
            "nombre_titular": "Roberto Menjivar",
        }
    )
    assert fields["es_para_tercero"] is True
    assert fields["paciente_nombre"] == "Roberto Menjivar"
    assert fields["nombre_secundario"] == "María López"


def test_attendee_name_third_party():
    cita = SimpleNamespace(
        es_para_tercero=True,
        paciente_nombre="Roberto Menjivar",
        nombre_secundario="María López",
    )
    assert cita_attendee_display_name(cita) == "María López"
    assert cita_contact_display_name(cita) == "Roberto Menjivar"


def test_attendee_name_legacy_self():
    cita = SimpleNamespace(
        es_para_tercero=False,
        paciente_nombre="Roberto Menjivar",
        nombre_secundario=None,
    )
    assert cita_attendee_display_name(cita) == "Roberto Menjivar"
    assert cita_contact_display_name(cita) is None


def test_es_para_tercero_false_by_default():
    cita = SimpleNamespace(es_para_tercero=None, paciente_nombre="Ana")
    assert cita_es_para_tercero(cita) is False
