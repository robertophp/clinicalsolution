"""Resolución de edad del beneficiario para citas pediátricas."""

from backend.domain.beneficiary_age import (
    extract_beneficiary_age_from_messages,
    parse_beneficiario_edad_arg,
    parse_edad_from_suffix_urgencia,
    resolve_beneficiario_edad,
)
from backend.services.calendar_service import CalendarService
from types import SimpleNamespace
from datetime import date, time


def test_parse_edad_from_suffix():
    assert parse_edad_from_suffix_urgencia("menor_6_anios") == 6
    assert parse_edad_from_suffix_urgencia("menor_8_anios") == 8
    assert parse_edad_from_suffix_urgencia("dolor_intenso") is None


def test_resolve_priority_explicit_arg():
    age = resolve_beneficiario_edad(
        args={"beneficiario_edad": 7, "suffix_urgencia": "menor_5_anios"},
        es_para_tercero=True,
        metadata={"beneficiario_edad": 6},
        chat_history=[{"role": "user", "content": "tiene 4 años"}],
    )
    assert age == 7


def test_resolve_from_metadata_when_args_missing():
    age = resolve_beneficiario_edad(
        args={},
        es_para_tercero=True,
        metadata={"beneficiario_edad": 6},
        chat_history=None,
    )
    assert age == 6


def test_resolve_from_history_when_only_chat():
    age = resolve_beneficiario_edad(
        args={},
        es_para_tercero=True,
        metadata={},
        chat_history=[
            {"role": "assistant", "content": "¿cuántos años tiene?"},
            {"role": "user", "content": "Tiene 6 años y se llama Flavio González"},
        ],
    )
    assert age == 6


def test_resolve_none_for_self_booking():
    assert (
        resolve_beneficiario_edad(
            args={"beneficiario_edad": 6},
            es_para_tercero=False,
            metadata={"beneficiario_edad": 6},
        )
        is None
    )


def test_calendar_uses_beneficiario_edad_without_suffix():
    cita = SimpleNamespace(
        fecha_cita=date(2026, 7, 7),
        hora_cita=time(14, 0),
        es_para_tercero=True,
        paciente_nombre="Andres Menjivar",
        nombre_secundario="Flavio González",
        beneficiario_edad=6,
        razon_cita="limpieza_dental",
        telefono="whatsapp:+50374351282",
        clinic_id="demo_clinic_1",
    )
    event = CalendarService._build_event_payload(
        cita=cita,
        clinic_name="Clínica Dental Tu Sonrisa",
        assistant_name="Bernardo",
        servicio_display="Limpieza dental",
        calendar_suffix=None,
    )
    assert "menor de edad (6 años)" in event["description"]
    assert "menor_6_anios" not in event["summary"]


def test_extract_beneficiary_age_from_messages_latest_user():
    msgs = [
        {"role": "user", "content": "tiene 5 años"},
        {"role": "user", "content": "ahora tiene 7 años"},
    ]
    assert extract_beneficiary_age_from_messages(msgs) == 7
