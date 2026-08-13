"""Respuesta directa al paciente para agendar_cita / reagendar_cita / cancelar_cita (éxito o error), sin segunda vuelta al modelo."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.gemini_service import GeminiService


@pytest.fixture
def gemini_with_mock_model() -> tuple[GeminiService, MagicMock]:
    mock_model = MagicMock()
    with patch("backend.services.gemini_service.vertexai.init"), patch(
        "backend.services.gemini_service.GenerativeModel", return_value=mock_model
    ):
        service = GeminiService(project_id="test-project-123", location="us-central1")
    assert service._model is mock_model
    return service, mock_model


def _one_function_call_response(name: str, args: dict) -> MagicMock:
    fc = MagicMock()
    fc.name = name
    fc.args = args
    cand = MagicMock()
    cand.function_calls = [fc]
    cand.content = MagicMock()
    cand.content.parts = [MagicMock()]
    resp = MagicMock()
    resp.candidates = [cand]
    return resp


def test_agendar_cita_error_returns_mensaje_without_second_model_call(gemini_with_mock_model):
    service, mock_model = gemini_with_mock_model
    mock_model.generate_content.return_value = _one_function_call_response(
        "agendar_cita",
        {"nombre": "Ana", "fecha": "2026-05-20", "hora": "10:00", "servicio": "limpieza_dental"},
    )

    got = service.generate_reply_with_tools(
        system_prompt="Clínica test",
        chat_history=[{"role": "user", "content": "confirmo"}],
        tool_handler=lambda n, a: {
            "error": "BigQuery simulated",
            "mensaje": "No pude agendar la cita. Prueba otra vez.",
        },
        reply_language="es",
    )

    assert got == "No pude agendar la cita. Prueba otra vez."
    assert mock_model.generate_content.call_count == 1


def test_agendar_cita_success_returns_mensaje_without_second_model_call(gemini_with_mock_model):
    service, mock_model = gemini_with_mock_model
    mock_model.generate_content.return_value = _one_function_call_response(
        "agendar_cita",
        {"nombre": "Ana", "fecha": "2026-05-20", "hora": "10:00", "servicio": "limpieza_dental"},
    )
    ok_msg = "¡Listo! He agendado tu cita para el 2026-05-20 a las 10:00 (servicio: Limpieza)."

    got = service.generate_reply_with_tools(
        system_prompt="Clínica test",
        chat_history=[{"role": "user", "content": "sí"}],
        tool_handler=lambda n, a: {"mensaje": ok_msg},
        reply_language="es",
    )

    assert got == ok_msg
    assert mock_model.generate_content.call_count == 1


def test_cancelar_cita_with_error_returns_mensaje_direct(gemini_with_mock_model):
    service, mock_model = gemini_with_mock_model
    mock_model.generate_content.return_value = _one_function_call_response("cancelar_cita", {})

    got = service.generate_reply_with_tools(
        system_prompt="Clínica test",
        chat_history=[{"role": "user", "content": "cancela mi cita"}],
        tool_handler=lambda n, a: {"error": "Sin cita activa", "mensaje": "No tienes una cita activa que cancelar."},
        reply_language="es",
    )

    assert got == "No tienes una cita activa que cancelar."
    assert mock_model.generate_content.call_count == 1
