"""Tests: los temas del manual de la clínica cuentan como dentro de dominio."""

from unittest.mock import MagicMock

from backend.services.intent_classifier import (
    Intent,
    classify_intent,
    extract_knowledge_base_topics,
    knowledge_base_service_keywords,
)
from backend.services.intent_llm_service import llm_classify_intent

SAMPLE_KB = """# Manual

## **1\\. DIAGNÓSTICO DENTAL**
Texto.

### **Preguntas Frecuentes (FAQ):**
* algo

## **5\\. ENDODONCIA**
Texto.

## **10\\. LAMINADOS VS. CARILLAS DE ALTA ESTÉTICA**
Texto.

## **11\\. ORTODONCIA E INVISALIGN**
Texto.

## **12\\. GINGIVECTOMÍA ESTÉTICA LÁSER**
Texto.
"""


def test_extract_topics_strips_numbering_and_markup():
    topics = extract_knowledge_base_topics(SAMPLE_KB)
    assert "ENDODONCIA" in topics
    assert "DIAGNÓSTICO DENTAL" in topics
    assert "ORTODONCIA E INVISALIGN" in topics
    assert "GINGIVECTOMÍA ESTÉTICA LÁSER" in topics
    # No debe quedar numeración ni asteriscos ni "Preguntas Frecuentes" (es ###).
    assert all(not t.startswith("*") for t in topics)
    assert all("Preguntas Frecuentes" not in t for t in topics)


def test_extract_topics_empty():
    assert extract_knowledge_base_topics(None) == []
    assert extract_knowledge_base_topics("") == []


def test_keywords_include_treatment_terms_with_and_without_accents():
    kws = knowledge_base_service_keywords(SAMPLE_KB)
    assert "endodoncia" in kws
    assert "laminados" in kws
    assert "carillas" in kws
    assert "invisalign" in kws
    assert "gingivectomía" in kws
    assert "gingivectomia" in kws  # variante sin acento
    # Palabras genéricas no deben colarse como keyword.
    assert "dental" not in kws
    assert "estetica" not in kws


def test_rules_classifier_uses_extra_keywords_for_invisalign():
    kws = knowledge_base_service_keywords(SAMPLE_KB)
    # Sin keywords del manual: "invisalign" no se reconoce -> out_of_domain.
    assert classify_intent("¿hacen invisalign?", "es") is Intent.OUT_OF_DOMAIN
    # Con keywords del manual: pasa a servicios (dentro de dominio).
    assert (
        classify_intent("¿hacen invisalign?", "es", extra_service_keywords=kws)
        is Intent.SERVICIOS
    )


def test_rules_classifier_gingivectomia_without_accent():
    kws = knowledge_base_service_keywords(SAMPLE_KB)
    assert (
        classify_intent("me interesa la gingivectomia", "es", extra_service_keywords=kws)
        is Intent.SERVICIOS
    )


def test_llm_classifier_includes_topics_in_prompt():
    gemini = MagicMock()
    gemini.generate_reply.return_value = "servicios"
    result = llm_classify_intent(
        gemini=gemini,
        message="¿qué es la gingivectomía?",
        language="es",
        clinic_topics=["ENDODONCIA", "GINGIVECTOMÍA ESTÉTICA LÁSER"],
    )
    assert result is Intent.SERVICIOS
    sent_prompt = gemini.generate_reply.call_args.kwargs["system_prompt"]
    assert "GINGIVECTOMÍA ESTÉTICA LÁSER" in sent_prompt
    assert "DENTRO DE DOMINIO" in sent_prompt
