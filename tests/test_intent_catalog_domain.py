"""Clasificador de intención: consultas de precio/servicio y catálogo."""

from backend.domain.catalog import catalog_intent_keywords
from backend.services.intent_classifier import (
    Intent,
    classify_intent,
    message_signals_price_inquiry,
)


def test_price_inquiry_que_cuesta_without_cuanto():
    assert message_signals_price_inquiry("Que cuesta una radiografía panorámica?") is True
    assert message_signals_price_inquiry("que cuesta la evaluacion?") is True
    assert message_signals_price_inquiry("a cuanto sale la limpieza") is True


def test_classify_radiografia_panoramica_price_question():
    kws = catalog_intent_keywords("demo_clinic_1")
    assert classify_intent(
        "Que cuesta una radiografía panorámica?",
        "es",
        extra_service_keywords=kws,
    ) is Intent.SERVICIOS


def test_classify_evaluacion_price_question():
    kws = catalog_intent_keywords("demo_clinic_1")
    assert classify_intent(
        "que cuesta la evaluacion?",
        "es",
        extra_service_keywords=kws,
    ) is Intent.SERVICIOS


def test_classify_service_info_with_catalog_term():
    kws = catalog_intent_keywords("demo_clinic_1")
    assert classify_intent(
        "hacen radiografia panoramica?",
        "es",
        extra_service_keywords=kws,
    ) is Intent.SERVICIOS


def test_classify_cuanto_cuesta_limpieza_still_works():
    kws = catalog_intent_keywords("demo_clinic_1")
    assert classify_intent(
        "¿cuánto cuesta una limpieza?",
        "es",
        extra_service_keywords=kws,
    ) is Intent.SERVICIOS


def test_out_of_domain_unrelated_topic():
    kws = catalog_intent_keywords("demo_clinic_1")
    assert classify_intent(
        "¿quién ganó el partido de fútbol ayer?",
        "es",
        extra_service_keywords=kws,
    ) is Intent.OUT_OF_DOMAIN
