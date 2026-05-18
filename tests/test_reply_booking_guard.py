"""Tests para reply_booking_guard (afirmaciones de cita sin persistencia)."""

import pytest

from backend.domain.reply_booking_guard import (
    claims_booking_saved_without_backend,
    fallback_ask_explicit_confirm,
)


@pytest.mark.parametrize(
    "text,lang,expected",
    [
        (
            "¡Con gusto, Roberto! Ya agendé tu limpieza dental para el lunes 18 de mayo a las 15:00. 😊",
            "es",
            True,
        ),
        ("¡Listo! He agendado tu cita para el 2026-05-18 a las 15:00 (servicio: Limpieza).", "es", True),
        ("Te la dejé agendada para mañana.", "es", True),
        ("¡Con gusto, Roberto! Ya está reagendada tu cita para el lunes a las 09:00.", "es", True),
        ("He reagendado tu limpieza para mañana.", "es", True),
        ("¿Con gusto te ayudo a reagendar tu cita?", "es", False),
        ("Tenemos horas a las 09:00 y 10:00 para ese día.", "es", False),
        ("¿Te parece bien el lunes a las 15:00?", "es", False),
        ("No pude agendar la cita. Por favor intenta de nuevo.", "es", False),
        ("I've scheduled your appointment for Monday.", "en", True),
        ("I've rescheduled your visit to Tuesday at 10.", "en", True),
        ("Your appointment has been rescheduled.", "en", True),
        ("Here are the available slots: 09:00, 10:00.", "en", False),
    ],
)
def test_claims_booking_saved_without_backend(text, lang, expected):
    assert claims_booking_saved_without_backend(text, language=lang) is expected


def test_fallback_mentions_sí():
    es = fallback_ask_explicit_confirm("es")
    assert "sí" in es.lower() or "confirmo" in es.lower()
    assert "gracias" in es.lower()
    en = fallback_ask_explicit_confirm("en")
    assert "yes" in en.lower() or "confirm" in en.lower()
