"""
Detección y persistencia del último servicio consultado en la conversación.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Any

from .catalog import _service_display_label, _services_for_clinic

SERVICE_CONTEXT_MAX_AGE_HOURS = 24


def _normalize_text(text: str) -> str:
    s = (text or "").lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def _service_match_terms(service: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("name", "name_en"):
        val = (service.get(key) or "").strip()
        if val and len(val) >= 3:
            terms.append(val)
    for alias in service.get("aliases") or []:
        a = (alias or "").strip()
        if a and len(a) >= 3:
            terms.append(a)
    return terms


def detect_discussed_service(message: str, clinic_id: str) -> str | None:
    """
    Detecta el id de catálogo del servicio mencionado en el mensaje del paciente.
    Retorna None si no hay match claro.
    """
    msg_norm = _normalize_text(message)
    if not msg_norm:
        return None

    best_id: str | None = None
    best_len = 0

    for svc in _services_for_clinic(clinic_id):
        sid = (svc.get("id") or "").strip()
        if not sid:
            continue
        for term in _service_match_terms(svc):
            term_norm = _normalize_text(term)
            if len(term_norm) < 3:
                continue
            if term_norm in msg_norm and len(term_norm) > best_len:
                best_id = sid
                best_len = len(term_norm)

    return best_id


def service_context_is_fresh(updated_at: datetime | None) -> bool:
    if updated_at is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SERVICE_CONTEXT_MAX_AGE_HOURS)
    ts = updated_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


def resolve_last_discussed_service_for_prompt(
    clinic_id: str,
    language: str,
    service_id: str | None,
    updated_at: datetime | None,
) -> str | None:
    """Nombre legible del último servicio si el contexto sigue vigente."""
    sid = (service_id or "").strip()
    if not sid or not service_context_is_fresh(updated_at):
        return None
    label = _service_display_label(clinic_id, sid, language)
    return label if label and label != sid else None


__all__ = [
    "SERVICE_CONTEXT_MAX_AGE_HOURS",
    "detect_discussed_service",
    "resolve_last_discussed_service_for_prompt",
    "service_context_is_fresh",
]
