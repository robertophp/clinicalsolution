"""Flujo maxilofacial: detección info vs booking, mensajes y bloqueo de citas."""

from unittest.mock import MagicMock, patch

import pytest

from backend.domain.citas_handlers import _handle_agendar_cita, set_conversation_memory_for_cita_handlers
from backend.domain.maxillofacial_policy import (
    classify_maxillofacial_context,
    format_maxillofacial_info_prompt_block,
    message_signals_maxillofacial_booking,
    message_signals_maxillofacial_context,
    patient_prompt_maxillofacial_followup,
    patient_prompt_maxillofacial_transfer_sent,
)
from backend.schemas.clinic_policies import ClinicPolicies, MaxillofacialPolicies


_POLICY = MaxillofacialPolicies(
    enabled=True,
    trigger_terms=["maxilo", "maxilofacial"],
    target_service_ids=[
        "evaluacion_con_especialista_maxilofacial",
        "cirugia_de_mucocele_maxilofacial",
    ],
    block_direct_booking=True,
)


def test_signals_maxilo_in_message():
    assert message_signals_maxillofacial_context("info de maxilo", _POLICY)


def test_signals_maxilo_via_service_id():
    assert message_signals_maxillofacial_context(
        "hola",
        _POLICY,
        last_discussed_service_id="evaluacion_con_especialista_maxilofacial",
    )


def test_booking_intent_on_cita_request():
    result = classify_maxillofacial_context(
        "Quiero agendar cita con maxilofacial",
        None,
        _POLICY,
    )
    assert result.is_active is True
    assert result.intent == "booking"


def test_info_intent_on_price_question():
    result = classify_maxillofacial_context(
        "¿Cuánto cuesta la evaluación maxilofacial?",
        None,
        _POLICY,
    )
    assert result.is_active is True
    assert result.intent == "info"


def test_booking_signals_horarios():
    assert message_signals_maxillofacial_booking("tienen horarios disponibles?")


def test_info_prompt_block_mentions_catalog():
    block = format_maxillofacial_info_prompt_block(
        language="es",
        clinic_id="demo_clinic_1",
        policy=_POLICY,
    )
    assert "MAXILOFACIAL" in block
    assert "NO llames agendar_cita" in block


def test_patient_messages_es():
    assert "maxilofacial" in patient_prompt_maxillofacial_transfer_sent("es").lower()
    assert "quedo a la orden" in patient_prompt_maxillofacial_followup("es").lower()


def test_agendar_blocked_for_maxillo_service():
    policies = {
        "demo_clinic_1": ClinicPolicies(
            clinic_id="demo_clinic_1",
            maxillofacial_policy=MaxillofacialPolicies(
                enabled=True,
                target_service_ids=["evaluacion_con_especialista_maxilofacial"],
                block_direct_booking=True,
            ),
        ),
    }
    set_conversation_memory_for_cita_handlers(MagicMock())
    with (
        patch("backend.domain.citas_handlers.get_clinics_by_id") as mock_clinics,
        patch("backend.domain.citas_handlers.CLINIC_POLICIES_BY_ID", policies),
    ):
        mock_clinics.return_value = {"demo_clinic_1": MagicMock(calendar_sync_enabled=False)}
        out = _handle_agendar_cita(
            from_number="+50370000001",
            clinic_id="demo_clinic_1",
            language="es",
            assistant_name="Bernardo",
            args={
                "nombre": "Ana Pérez",
                "fecha": "2026-08-01",
                "hora": "10:00",
                "servicio": "evaluacion_con_especialista_maxilofacial",
            },
        )
    assert out.get("error") == "Cita maxilofacial bloqueada"
    assert "maxilofacial" in (out.get("mensaje") or "").lower()
