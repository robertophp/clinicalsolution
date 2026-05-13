"""Detección de fugas tipo print(default_api.agendar_cita(...)) en respuestas al paciente."""

from backend.services.gemini_service import reply_looks_like_tool_code_leak


def test_reply_looks_like_tool_code_leak_detects_print_default_api():
    bad = (
        'print(default_api.agendar_cita(nombre="Roberto Menjivar", '
        'fecha="2026-05-04", hora="08:00", servicio="evaluacion", suffix_urgencia="dolor_intenso"))'
    )
    assert reply_looks_like_tool_code_leak(bad) is True


def test_reply_looks_like_tool_code_leak_detects_agendar_cita_call():
    assert reply_looks_like_tool_code_leak("agendar_cita(nombre='x', fecha='2026-01-01')") is True


def test_reply_looks_like_tool_code_leak_normal_confirmation_false():
    ok = "¡Listo! He agendado tu cita para el 2026-05-04 a las 08:00 (servicio: Evaluacion)."
    assert reply_looks_like_tool_code_leak(ok) is False


def test_reply_looks_like_tool_code_leak_empty_false():
    assert reply_looks_like_tool_code_leak("") is False
