"""Tests unitarios para derivación a especialista (sin Vertex AI)."""

from backend.services.human_transfer_service import (
    _reference_context_block_for_transfer_summary,
    build_specialist_derivation_message,
    classify_patient_summary_response,
    format_patient_phone_display,
    parse_specialist_whatsapp_recipient,
    patient_prompt_confirm_summary,
    sanitize_specialist_summary_body,
)
from backend.services.human_transfer_topics import topics_for_clinic_keys


def test_parse_specialist_whatsapp_recipient():
    assert parse_specialist_whatsapp_recipient("+503 7123-4567") == "50371234567"
    assert parse_specialist_whatsapp_recipient("") is None


def test_format_patient_phone_display():
    assert format_patient_phone_display("whatsapp:+50370123456") == "+50370123456"


def test_build_specialist_derivation_message_format():
    msg = build_specialist_derivation_message(
        patient_name="María López",
        patient_phone_display="+50370001122",
        summary="Quiere información de ortodoncia.",
    )
    assert "NUEVA DERIVACIÓN" in msg
    assert "María López" in msg
    assert "+50370001122" in msg
    assert "ortodoncia" in msg


def test_topics_for_clinic_keys_filter():
    all_keys = {t.key for t in topics_for_clinic_keys(None)}
    filtered = topics_for_clinic_keys(["quejas", "creditos_fiscales"])
    assert len(filtered) == 2
    assert {t.key for t in filtered} <= all_keys
    assert "contacto_humano" in all_keys


def test_classify_patient_summary_approve_heuristic_es():
    gemini = object()
    assert (
        classify_patient_summary_response(
            gemini,
            patient_message="sí",
            language="es",
            current_summary="x",
        )
        == "approve"
    )


def test_classify_patient_summary_approve_heuristic_confirmo():
    gemini = object()
    assert (
        classify_patient_summary_response(
            gemini,
            patient_message="confirmo",
            language="es",
            current_summary="Resumen de prueba.",
        )
        == "approve"
    )


def test_sanitize_specialist_summary_strips_echoed_prompt_and_question():
    raw = """Asistente: He actualizado el resumen para nuestro especialista:
Resumen ejecutivo:
Paciente comenta molestia tras la cita de ayer.
¿Es correcto o deseas agregar algo más, Roberto?"""
    out = sanitize_specialist_summary_body(raw)
    assert "¿Es correcto" not in out
    assert "Asistente" not in out
    assert "cita de ayer" in out


def test_patient_prompt_confirm_summary_single_closing_question_es():
    msg = patient_prompt_confirm_summary("Paciente comenta X.", "es")
    assert msg.count("¿Te parece bien") == 1
    assert msg.count("👉") >= 1


def test_classify_patient_summary_approve_heuristic_en():
    gemini = object()
    assert (
        classify_patient_summary_response(
            gemini,
            patient_message="yes",
            language="en",
            current_summary="x",
        )
        == "approve"
    )


def test_classify_patient_summary_decline_heuristic_es():
    gemini = object()
    assert (
        classify_patient_summary_response(
            gemini,
            patient_message="no",
            language="es",
            current_summary="Resumen de prueba.",
        )
        == "decline"
    )
    assert (
        classify_patient_summary_response(
            gemini,
            patient_message="no quiero",
            language="es",
            current_summary="Resumen de prueba.",
        )
        == "decline"
    )


def test_transfer_summary_reference_block_es_has_anchor_and_rules():
    b = _reference_context_block_for_transfer_summary(language="es")
    assert "REFERENCIA" in b
    assert "Fecha de hoy en YYYY-MM-DD:" in b
    assert "FUTURO" in b
    assert "posterior" in b.lower()


def test_transfer_summary_reference_block_en_has_anchor_and_rules():
    b = _reference_context_block_for_transfer_summary(language="en")
    assert "REFERENCE" in b
    assert "Today's date (YYYY-MM-DD):" in b
    assert "FUTURE" in b
