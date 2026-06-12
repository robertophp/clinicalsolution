"""El clasificador de derivación debe verificar catálogo/manual antes de derivar."""

from pathlib import Path
from unittest.mock import MagicMock

from backend.domain.clinic_loader import load_clinic_tree
from backend.domain.prompt_clinic import _build_transfer_resolution_context
from backend.services.human_transfer_service import detect_human_transfer_need
from backend.services.human_transfer_topics import topics_for_clinic_keys


def _clinics() -> dict:
    root = Path(__file__).resolve().parents[1] / "src" / "backend" / "data" / "clinics"
    return load_clinic_tree(root)


def test_resolution_context_lists_catalog_and_kb_topics():
    cfg = _clinics()["demo_clinic_1"]
    ctx = _build_transfer_resolution_context(cfg, "es")
    # Servicios del catálogo presentes.
    assert "Blanqueamiento dental" in ctx
    # Temas del manual presentes.
    assert "BLANQUEAMIENTO INTERNO" in ctx
    # Regla de verificar antes de derivar.
    assert "ANTES de derivar" in ctx
    assert "NO derives solo porque el caso suene complejo" in ctx


def test_resolution_context_english_variant():
    cfg = _clinics()["demo_clinic_1"]
    ctx = _build_transfer_resolution_context(cfg, "en")
    assert "THIS CLINIC OFFERS" in ctx
    assert "Do NOT escalate just because the case sounds complex" in ctx


def test_resolution_context_without_clinic_omits_catalog_block():
    ctx = _build_transfer_resolution_context(None, "es")
    # Sin clínica no hay bloque de catálogo/manual, pero sí reglas genéricas.
    assert "SÍ OFRECE" not in ctx
    assert "Blanqueamiento dental" not in ctx


def test_detect_respects_false_for_offered_treatment():
    """Si Gemini decide no derivar (blanqueamiento sí ofrecido), detect devuelve None."""
    gemini = MagicMock()
    gemini.generate_reply.return_value = (
        '{"requires_human_transfer": false, "matched_topics": [], "brief_reason": ""}'
    )
    result = detect_human_transfer_need(
        gemini,
        message=(
            "Tengo un diente de adelante que se puso negro tras un golpe y endodoncia. "
            "¿Tienen blanqueamiento para eso y cuánto cuesta?"
        ),
        history=None,
        language="es",
        topics=topics_for_clinic_keys(None),
        resolution_context=_build_transfer_resolution_context(_clinics()["demo_clinic_1"], "es"),
    )
    assert result is None


def test_detect_still_escalates_when_true():
    """Caso de control: si Gemini marca derivación con tema válido, se respeta."""
    gemini = MagicMock()
    gemini.generate_reply.return_value = (
        '{"requires_human_transfer": true, "matched_topics": ["contacto_humano"], '
        '"brief_reason": "pide hablar con la doctora"}'
    )
    result = detect_human_transfer_need(
        gemini,
        message="Quiero hablar con la doctora",
        history=None,
        language="es",
        topics=topics_for_clinic_keys(None),
        resolution_context=_build_transfer_resolution_context(_clinics()["demo_clinic_1"], "es"),
    )
    assert result is not None
    assert "contacto_humano" in result.matched_topics
