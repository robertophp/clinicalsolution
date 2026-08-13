"""Tests de reintento ante MAX_TOKENS y compatibilidad de GenerationConfig."""

from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock

from backend.services import gemini_service as gs
from backend.services.gemini_service import (
    GeminiService,
    _build_generation_config,
    extract_text_and_finish,
)


def test_extract_text_and_finish_returns_finish_reason():
    response = MagicMock()
    type(response).text = PropertyMock(side_effect=ValueError("Cannot get the response text."))
    response.candidates = [
        SimpleNamespace(
            finish_reason=SimpleNamespace(name="MAX_TOKENS"),
            content=SimpleNamespace(parts=[SimpleNamespace(text="Una endodoncia")]),
        )
    ]
    text, finish = extract_text_and_finish(response)
    assert text == "Una endodoncia"
    assert finish == "MAX_TOKENS"


def test_build_generation_config_without_thinking_config(monkeypatch):
    monkeypatch.setattr(gs, "ThinkingConfig", None)
    config = _build_generation_config(temperature=0.2, max_output_tokens=512, low_thinking=True)
    assert config.to_dict().get("max_output_tokens") == 512


def test_build_generation_config_thinking_config_type_error(monkeypatch):
    class FakeThinking:
        def __init__(self, thinking_budget: int):
            self.thinking_budget = thinking_budget

    class FakeGenerationConfig:
        def __init__(self, **kwargs):
            if "thinking_config" in kwargs:
                raise TypeError("unexpected keyword")
            self.temperature = kwargs["temperature"]
            self.max_output_tokens = kwargs["max_output_tokens"]

    monkeypatch.setattr(gs, "ThinkingConfig", FakeThinking)
    monkeypatch.setattr(gs, "GenerationConfig", FakeGenerationConfig)
    config = _build_generation_config(temperature=0.1, max_output_tokens=128, low_thinking=True)
    assert config.max_output_tokens == 128


def _make_service_with_mock_vertex(responses: list) -> GeminiService:
    service = GeminiService.__new__(GeminiService)
    service._model = MagicMock()
    call_count = {"n": 0}

    def fake_vertex_generate(*, contents, generation_config=None, tools=None):
        idx = call_count["n"]
        call_count["n"] += 1
        return responses[min(idx, len(responses) - 1)]

    service._vertex_generate = fake_vertex_generate
    return service


def test_generate_reply_retries_on_max_tokens():
    truncated = MagicMock()
    type(truncated).text = PropertyMock(side_effect=ValueError("truncated"))
    truncated.candidates = [
        SimpleNamespace(
            finish_reason=SimpleNamespace(name="MAX_TOKENS"),
            content=SimpleNamespace(parts=[SimpleNamespace(text="Una end")]),
        )
    ]
    complete = MagicMock()
    type(complete).text = PropertyMock(return_value="Una endodoncia es un tratamiento completo.")
    complete.candidates = [
        SimpleNamespace(
            finish_reason=SimpleNamespace(name="STOP"),
            content=SimpleNamespace(
                parts=[SimpleNamespace(text="Una endodoncia es un tratamiento completo.")]
            ),
        )
    ]

    service = _make_service_with_mock_vertex([truncated, complete])
    out = service.generate_reply(
        system_prompt="test",
        max_output_tokens=512,
        retry_max_output_tokens=2048,
    )
    assert out == "Una endodoncia es un tratamiento completo."


def test_generate_reply_with_tools_retries_on_max_tokens():
    truncated = MagicMock()
    type(truncated).text = PropertyMock(side_effect=ValueError("truncated"))
    truncated.candidates = [
        SimpleNamespace(
            finish_reason=SimpleNamespace(name="MAX_TOKENS"),
            content=SimpleNamespace(parts=[SimpleNamespace(text="solicit")]),
            function_calls=[],
        )
    ]
    complete = MagicMock()
    type(complete).text = PropertyMock(
        return_value="El paciente solicita información sobre endodoncia y precios."
    )
    complete.candidates = [
        SimpleNamespace(
            finish_reason=SimpleNamespace(name="STOP"),
            content=SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        text="El paciente solicita información sobre endodoncia y precios."
                    )
                ]
            ),
            function_calls=[],
        )
    ]

    service = _make_service_with_mock_vertex([truncated, complete])
    out = service.generate_reply_with_tools(
        system_prompt="test",
        tool_handler=lambda _n, _a: {},
        max_output_tokens=512,
    )
    assert "endodoncia" in out
