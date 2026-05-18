"""Tests unitarios para reintentos/timeout de Vertex (sin llamar a la API)."""

import concurrent.futures

import pytest

from backend.services.gemini_vertex_call import _is_retryable_vertex_error, call_with_timeout_and_retries


def test_retryable_recognizes_timeout():
    assert _is_retryable_vertex_error(concurrent.futures.TimeoutError())


def test_call_with_timeout_and_retries_succeeds_first_try():
    n = {"c": 0}

    def fn():
        n["c"] += 1
        return "ok"

    out = call_with_timeout_and_retries(
        fn,
        timeout_seconds=5.0,
        max_attempts=3,
        operation_label="test",
    )
    assert out == "ok"
    assert n["c"] == 1


def test_call_with_timeout_and_retries_eventually_succeeds():
    n = {"c": 0}

    class Flaky(Exception):
        pass

    def fn():
        n["c"] += 1
        if n["c"] < 2:
            raise Flaky("503 simulated")
        return "done"

    # "503" in message makes it retryable per string heuristic
    out = call_with_timeout_and_retries(
        fn,
        timeout_seconds=5.0,
        max_attempts=3,
        operation_label="test_flaky",
    )
    assert out == "done"
    assert n["c"] == 2


def test_call_with_timeout_and_retries_non_retryable_raises():
    def fn():
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        call_with_timeout_and_retries(
            fn,
            timeout_seconds=5.0,
            max_attempts=3,
            operation_label="test_perm",
        )
