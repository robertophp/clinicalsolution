"""Tabla canónica de fechas en prompt: alinear 'viernes' con el YYYY-MM-DD correcto."""

from datetime import date

from backend.domain.prompt_clinic import format_canonical_calendar_dates_for_prompt


def test_may_2026_wednesday_ref_friday_is_15_not_16():
    """2026-05-13 es miércoles: el viernes siguiente es 2026-05-15; 2026-05-16 es sábado."""
    text = format_canonical_calendar_dates_for_prompt(date(2026, 5, 13), language="es", num_days=10)
    assert "2026-05-15 | viernes |" in text
    assert "2026-05-16 | sábado |" in text


def test_english_table_uses_weekday_names():
    text = format_canonical_calendar_dates_for_prompt(date(2026, 5, 13), language="en", num_days=5)
    assert "2026-05-15" in text
    assert "Friday" in text
