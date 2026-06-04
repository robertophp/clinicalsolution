"""Tests para extracción segura de texto de respuestas Vertex (Gemini 2.5 / MAX_TOKENS)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock

from backend.services.gemini_service import extract_response_text


def test_extract_response_text_from_text_property():
    response = MagicMock()
    type(response).text = PropertyMock(return_value="approve")
    assert extract_response_text(response) == "approve"


def test_extract_response_text_when_text_raises_uses_parts():
    response = MagicMock()
    type(response).text = PropertyMock(side_effect=ValueError("Cannot get the response text."))
    response.candidates = [
        SimpleNamespace(
            finish_reason=SimpleNamespace(name="MAX_TOKENS"),
            content=SimpleNamespace(parts=[SimpleNamespace(text="revise")]),
        )
    ]
    assert extract_response_text(response) == "revise"


def test_extract_response_text_max_tokens_no_parts_returns_none():
    response = MagicMock()
    type(response).text = PropertyMock(side_effect=ValueError("Cannot get the response text."))
    response.candidates = [
        SimpleNamespace(
            finish_reason=SimpleNamespace(name="MAX_TOKENS"),
            content=SimpleNamespace(parts=[]),
        )
    ]
    assert extract_response_text(response) is None
