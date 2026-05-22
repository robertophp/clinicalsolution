"""Catálogo: servicios de evaluación."""

from backend.domain.catalog import _format_services_catalog_for_prompt, evaluation_services_for_clinic


def test_evaluation_services_for_demo_clinic():
    ev = evaluation_services_for_clinic("demo_clinic_1")
    ids = {s["id"] for s in ev}
    assert "evaluacion" in ids
    assert all(s.get("is_evaluation") is True for s in ev)


def test_catalog_prompt_mentions_evaluation_guidance_es():
    services = evaluation_services_for_clinic("demo_clinic_1")
    text = _format_services_catalog_for_prompt(services, "es")
    assert "evaluación=sí" in text
    assert "cita de evaluación" in text
    assert "NO derives a humano" in text
