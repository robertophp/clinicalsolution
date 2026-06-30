"""Contratos mínimos del prompt de conversación (Jinja + políticas)."""

from pathlib import Path

from backend.domain.clinic_loader import CLINIC_POLICIES_BY_ID, load_clinic_tree
from backend.domain.conversation_prompt import build_citas_tool_instruction, build_conversation_system_prompt
from backend.schemas.clinic_policies import BookingPromptPolicies, ClinicPolicies


def _clinics() -> dict:
    root = Path(__file__).resolve().parents[1] / "src" / "backend" / "data" / "clinics"
    return load_clinic_tree(root)


def test_build_citas_tool_instruction_uses_booking_override() -> None:
    policies = ClinicPolicies(
        clinic_id="x",
        booking=BookingPromptPolicies(confirmation_example_es="TEXTO_CONFIRMACION_CUSTOM_ES"),
    )
    out = build_citas_tool_instruction("es", policies)
    assert "TEXTO_CONFIRMACION_CUSTOM_ES" in out
    assert "consultar_disponibilidad" in out


def test_system_prompt_contains_clinic_and_tools() -> None:
    clinics = _clinics()
    cfg = clinics["demo_clinic_1"]
    text = build_conversation_system_prompt(
        language="es",
        clinic_id=cfg.id,
        clinic_name=cfg.name,
        assistant_name=cfg.assistant_name,
        system_prompt=cfg.system_prompt,
        system_prompt_en=cfg.system_prompt_en,
        is_first_message=True,
        stored_first_name=None,
        stored_full_name=None,
        clinics_by_id=clinics,
        policies=CLINIC_POLICIES_BY_ID.get(cfg.id),
    )
    assert cfg.id in text
    assert cfg.name in text
    assert "consultar_disponibilidad" in text
    assert "HORARIOS PARA INICIAR" in text
    assert "NUNCA muestres al paciente IDs internos del catálogo" in text
    assert "nunca ids del catálogo" in text
    assert "DOLOR / URGENCIA" in text
    assert "consultar_primer_dia_disponible" in text
    assert "no hay citas que inicien hoy" in text
    assert "BASE DE CONOCIMIENTO DE LA CLÍNICA" in text
    assert "DIAGNÓSTICO DENTAL" in text
    assert "centro de imágenes propio" in text
    assert "ESTILO Y TONO" in text
    assert "NO cambies el flujo de agendamiento" in text
    assert "FORMATO DE RESPUESTA (WhatsApp)" in text
    assert "Máximo 3–4 líneas cortas" in text
    assert "beneficios resumidos en 1 frase natural" in text
    assert "NUNCA digas que «no lo tenemos»" in text


def test_first_message_prompt_requires_empathetic_greeting() -> None:
    clinics = _clinics()
    cfg = clinics["demo_clinic_1"]
    text = build_conversation_system_prompt(
        language="es",
        clinic_id=cfg.id,
        clinic_name=cfg.name,
        assistant_name=cfg.assistant_name,
        system_prompt=cfg.system_prompt,
        system_prompt_en=cfg.system_prompt_en,
        is_first_message=True,
        stored_first_name=None,
        stored_full_name=None,
        clinics_by_id=clinics,
        policies=CLINIC_POLICIES_BY_ID.get(cfg.id),
        name_collection_phase="none",
    )
    assert f"Hola, soy {cfg.assistant_name}" in text
    assert "con gusto te ayudo" in text
    assert "¿en qué puedo ayudarte?" in text
    assert "¿Con quién tengo el gusto?" in text


def test_prompt_includes_last_discussed_service_name_not_id() -> None:
    clinics = _clinics()
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
        last_discussed_service_name="Limpieza dental",
    )
    assert "Limpieza dental" in text
    assert "limpieza_dental" not in text.split("ÚLTIMO SERVICIO")[1][:200]


def test_system_prompt_without_knowledge_base_omits_block() -> None:
    clinics = _clinics()
    cfg = clinics["demo_clinic_2"]
    text = build_conversation_system_prompt(
        language="es",
        clinic_id=cfg.id,
        clinic_name=cfg.name,
        assistant_name=cfg.assistant_name,
        system_prompt=cfg.system_prompt,
        system_prompt_en=cfg.system_prompt_en,
        is_first_message=True,
        stored_first_name=None,
        stored_full_name=None,
        clinics_by_id=clinics,
        policies=CLINIC_POLICIES_BY_ID.get(cfg.id),
    )
    assert "BASE DE CONOCIMIENTO DE LA CLÍNICA" not in text
    assert "FORMATO DE RESPUESTA (WhatsApp)" in text
