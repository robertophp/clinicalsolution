"""
Timeout y reintentos con backoff para llamadas síncronas a ``GenerativeModel.generate_content``.

Vertex no expone timeout en la firma pública; acotamos la espera con un executor y ``future.result(timeout=…)``.
"""
from __future__ import annotations

import concurrent.futures
import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


def _is_retryable_vertex_error(exc: BaseException) -> bool:
    """429 / 5xx / unavailable / deadline / resource exhausted del cliente Google."""
    try:
        from google.api_core import exceptions as gexc

        if isinstance(
            exc,
            (
                gexc.ServiceUnavailable,
                gexc.DeadlineExceeded,
                gexc.ResourceExhausted,
                gexc.InternalServerError,
                gexc.TooManyRequests,
            ),
        ):
            return True
    except ImportError:
        pass

    if isinstance(exc, (TimeoutError, concurrent.futures.TimeoutError)):
        return True

    name = type(exc).__name__
    if name in ("ServiceUnavailable", "DeadlineExceeded", "ResourceExhausted", "InternalServerError", "TooManyRequests"):
        return True

    msg = str(exc).lower()
    for token in ("429", "503", "504", "deadline", "unavailable", "resource exhausted", "try again"):
        if token in msg:
            return True
    return False


def _backoff_before_retry(attempt_index: int) -> None:
    """attempt_index 0 tras primer fallo, 1 tras el segundo."""
    base = 0.5 if attempt_index == 0 else 1.5
    jitter = random.uniform(0.0, 0.35)
    time.sleep(base + jitter)


def call_with_timeout_and_retries(
    fn: Callable[[], T],
    *,
    timeout_seconds: float,
    max_attempts: int,
    operation_label: str = "vertex_generate",
) -> T:
    """
    Ejecuta ``fn`` (sin argumentos) hasta ``max_attempts`` veces con timeout por intento.

    Reintenta solo si el error es considerado transitorio (red/cuota/5xx/timeout de espera).
    """
    attempts = max(1, int(max_attempts))
    timeout_s = float(timeout_seconds)

    for attempt in range(attempts):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(fn)
                return fut.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError as exc:
            logger.warning(
                "%s: timeout tras %.1fs (intento %s/%s)",
                operation_label,
                timeout_s,
                attempt + 1,
                attempts,
            )
            if attempt < attempts - 1 and _is_retryable_vertex_error(exc):
                _backoff_before_retry(attempt)
                continue
            raise exc
        except Exception as exc:
            if attempt < attempts - 1 and _is_retryable_vertex_error(exc):
                logger.warning(
                    "%s: error reintentable %s: %s (intento %s/%s)",
                    operation_label,
                    type(exc).__name__,
                    exc,
                    attempt + 1,
                    attempts,
                )
                _backoff_before_retry(attempt)
                continue
            raise
