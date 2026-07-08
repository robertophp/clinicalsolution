"""Manual + catálogo: sinónimos (ej. Biodentine) y reglas anti-denegación."""

from pathlib import Path

from backend.domain.catalog import _format_services_catalog_for_prompt, _services_for_clinic
from backend.domain.clinic_loader import load_clinic_tree
from backend.domain.conversation_prompt import build_conversation_system_prompt
from backend.services.intent_classifier import (
    Intent,
    classify_intent,
    extract_knowledge_base_topics,
    knowledge_base_service_keywords,
)


def _clinics():
    root = Path(__file__).resolve().parents[1] / "src" / "backend" / "data" / "clinics"
    return load_clinic_tree(root)


def test_biodentine_keyword_from_manual():
    cfg = _clinics()["demo_clinic_1"]
    topics = extract_knowledge_base_topics(cfg.knowledge_base)
    assert any("BIODENTINE" in t.upper() for t in topics)
    kws = knowledge_base_service_keywords(cfg.knowledge_base)
    assert "biodentine" in kws


def test_biodentine_question_is_servicios_intent():
    cfg = _clinics()["demo_clinic_1"]
    kws = knowledge_base_service_keywords(cfg.knowledge_base)
    msg = (
        "Me dijeron que tengo caries muy profundas y será necesario endodoncia "
        "pero escuché del tratamiento de biodentine con ustedes que precio tiene. Y cómo funciona?"
    )
    assert classify_intent(msg, "es", extra_service_keywords=kws) is Intent.SERVICIOS


def test_catalog_recubrimiento_pulpar_has_biodentine_alias():
    services = _services_for_clinic("demo_clinic_1")
    rp = next(s for s in services if s.get("id") == "recubrimiento_pulpar_pieza")
    aliases = [a.lower() for a in (rp.get("aliases") or [])]
    assert "biodentine" in aliases


def test_system_prompt_includes_clinical_team_messaging_rule():
    clinics = _clinics()
    cfg = clinics["demo_clinic_1"]
    text = build_conversation_system_prompt(
        language="es",
        clinic_id=cfg.id,
        clinic_name=cfg.name,
        assistant_name=cfg.assistant_name,
        system_prompt=cfg.system_prompt,
        system_prompt_en=cfg.system_prompt_en,
        is_first_message=False,
        stored_first_name="Samuel",
        stored_full_name=None,
        clinics_by_id=clinics,
        policies=None,
    )
    assert "equipo de especialistas altamente calificados" in text
    assert "NUNCA" in text and "solo la Dra. Palacios" in text
    assert "nunca digas que solo una persona" in text.lower()


def test_system_prompt_forbids_denying_manual_treatments():
    clinics = _clinics()
    cfg = clinics["demo_clinic_1"]
    text = build_conversation_system_prompt(
        language="es",
        clinic_id=cfg.id,
        clinic_name=cfg.name,
        assistant_name=cfg.assistant_name,
        system_prompt=cfg.system_prompt,
        system_prompt_en=cfg.system_prompt_en,
        is_first_message=False,
        stored_first_name="Roberto",
        stored_full_name=None,
        clinics_by_id=clinics,
        policies=None,
    )
    assert "NUNCA digas que «no lo tenemos»" in text or "NUNCA niegues un tratamiento" in text
    assert "Biodentine" in text
    assert "RECUBRIMIENTO PULPAR" in text.upper() or "Recubrimiento pulpar" in text
    assert "USD 100" in text or "100.00" in text


def test_catalog_prompt_includes_manual_cross_reference():
    text = _format_services_catalog_for_prompt(_services_for_clinic("demo_clinic_1"), "es")
    assert "manual de la clínica" in text
    assert "Biodentine" in text
    assert "NUNCA niegues" in text
