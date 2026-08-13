"""Flujo cordales + radiografía panorámica obligatoria."""

from pathlib import Path

from backend.domain.clinic_loader import CLINIC_POLICIES_BY_ID, load_clinic_tree
from backend.domain.conversation_prompt import build_conversation_system_prompt
from backend.domain.cordales_requirement import (
    classify_patient_xray_response,
    format_cordales_panoramic_prompt_block,
    message_signals_cordales_inquiry,
)
from backend.domain.citas_handlers import _handle_agendar_cita
from backend.schemas.clinic_policies import CordalesPanoramicRequirementPolicies
from unittest.mock import MagicMock, patch

from backend.domain.citas_handlers import set_conversation_memory_for_cita_handlers
from backend.schemas import ClinicConfig


def _demo_policies():
    root = Path(__file__).resolve().parents[1] / "src" / "backend" / "data" / "clinics"
    load_clinic_tree(root)
    return CLINIC_POLICIES_BY_ID["demo_clinic_1"]


def test_message_signals_cordales_trigger():
    policy = _demo_policies().cordales_panoramic_requirement
    assert message_signals_cordales_inquiry("quiero extraer un cordal", policy) is True
    assert message_signals_cordales_inquiry("muela del juicio", policy) is True
    assert message_signals_cordales_inquiry("hola", policy) is False


def test_classify_xray_response():
    assert classify_patient_xray_response("sí la tengo") == "has_panoramic"
    assert classify_patient_xray_response("no la tengo") == "needs_at_clinic"
    assert classify_patient_xray_response("cuánto cuesta") == "unclear"


def test_prompt_block_contains_mandatory_question():
    policy = _demo_policies().cordales_panoramic_requirement
    block = format_cordales_panoramic_prompt_block(
        language="es",
        clinic_id="demo_clinic_1",
        policy=policy,
        cordales_xray_phase="none",
    )
    assert "CORDALES + RADIOGRAFÍA PANORÁMICA" in block
    assert "radiografía panorámica" in block
    assert "evaluacion_radiografia_panoramica" in block
    assert "NUNCA llames agendar_cita" in block


def test_system_prompt_includes_cordales_block_when_active():
    root = Path(__file__).resolve().parents[1] / "src" / "backend" / "data" / "clinics"
    clinics = load_clinic_tree(root)
    cfg = clinics["demo_clinic_1"]
    text = build_conversation_system_prompt(
        language="es",
        clinic_id=cfg.id,
        clinic_name=cfg.name,
        assistant_name=cfg.assistant_name,
        system_prompt=cfg.system_prompt,
        system_prompt_en=cfg.system_prompt_en,
        is_first_message=False,
        stored_first_name=None,
        stored_full_name=None,
        clinics_by_id=clinics,
        policies=CLINIC_POLICIES_BY_ID.get(cfg.id),
        cordales_flow_active=True,
        cordales_xray_phase="none",
    )
    assert "CORDALES + RADIOGRAFÍA PANORÁMICA" in text
    assert "equipo radiológico" in text


def test_agendar_cordal_blocked_by_policy():
    memory = MagicMock()
    memory.get_metadata.return_value = {}
    set_conversation_memory_for_cita_handlers(memory)

    demo_clinic = {
        "demo_clinic_1": ClinicConfig(
            id="demo_clinic_1",
            name="Clínica demo",
            system_prompt="x",
            opening_hours={
                "mon_fri": {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "08:00", "to": "17:00"}
            },
            calendar_sync_enabled=False,
            calendar_id=None,
        ),
    }

    with patch("backend.domain.citas_handlers.get_clinics_by_id", return_value=demo_clinic):
        out = _handle_agendar_cita(
            from_number="whatsapp:+50370000001",
            clinic_id="demo_clinic_1",
            language="es",
            assistant_name="Bernardo",
            args={
                "nombre": "Ana",
                "fecha": "2026-06-15",
                "hora": "10:00",
                "servicio": "exodoncia_de_cordal_piezas",
            },
        )

    assert out.get("error") == "Extracción cordal bloqueada"
    assert "evaluación" in (out.get("mensaje") or "").lower()
