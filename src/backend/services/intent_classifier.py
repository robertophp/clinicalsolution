from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Mapping, Sequence


class Intent(str, Enum):
    """Intenciones de alto nivel que el asistente puede manejar."""

    CITA = "cita"  # agendar, cancelar, reagendar, cambiar horario
    SERVICIOS = "servicios"  # precios, tipos de tratamiento, qué incluye cada servicio
    CLINICA_INFO = "clinica_info"  # dirección, horarios, formas de pago, contacto
    SEGUIMIENTO_CITA = "seguimiento_cita"  # llegar tarde, confirmar asistencia, dudas sobre cita concreta
    SMALL_TALK = "small_talk"  # saludos, gracias, despedidas breves
    OUT_OF_DOMAIN = "out_of_domain"  # cualquier otro tema ajeno a la clínica


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


# Palabras genéricas que aparecen en los títulos del manual pero no sirven como
# keyword discriminante (evita falsos positivos triviales en el fallback de reglas).
_KB_TITLE_STOPWORDS = {
    "dental",
    "dentales",
    "alta",
    "estetica",
    "interno",
    "profunda",
    "tecnologia",
    "tecnologica",
}


def extract_knowledge_base_topics(knowledge_base: str | None) -> list[str]:
    """
    Extrae los títulos de sección (``## ...``) del manual de la clínica como temas limpios.

    Convierte encabezados tipo ``## **5\\. ENDODONCIA**`` en ``"ENDODONCIA"``. Sirve para
    que los clasificadores de intención traten esos temas como dentro de dominio.
    """
    if not knowledge_base:
        return []
    topics: list[str] = []
    for raw_line in knowledge_base.splitlines():
        line = raw_line.strip()
        if not line.startswith("## "):
            continue
        title = line.lstrip("#").strip()
        title = title.replace("**", "").strip()
        # Quitar numeración tipo "5\. " o "5. " al inicio.
        title = re.sub(r"^\d+\s*\\?\.\s*", "", title).strip()
        title = title.strip("*").strip()
        if title:
            topics.append(title)
    return topics


def knowledge_base_service_keywords(knowledge_base: str | None) -> list[str]:
    """
    Deriva keywords (con y sin acentos) a partir de los títulos del manual para el
    clasificador por reglas. Mantiene tokens significativos (>= 5 letras) que no sean
    palabras genéricas, de modo que tratamientos como endodoncia, carillas, laminados,
    gingivectomía o invisalign se reconozcan como dentro de dominio.
    """
    keywords: list[str] = []
    seen: set[str] = set()
    for topic in extract_knowledge_base_topics(knowledge_base):
        for token in re.split(r"[^0-9A-Za-zÁÉÍÓÚáéíóúÜüÑñ]+", topic):
            token = token.strip().lower()
            if len(token) < 5:
                continue
            base = _strip_accents(token)
            if base in _KB_TITLE_STOPWORDS:
                continue
            for variant in (token, base):
                if variant and variant not in seen:
                    seen.add(variant)
                    keywords.append(variant)
    return keywords


def classify_intent(
    message: str,
    language: str,
    history: Sequence[Mapping[str, str]] | None = None,
    *,
    extra_service_keywords: Sequence[str] | None = None,
) -> Intent:
    """
    Clasificación ligera de intención basada en reglas.

    Diseñada para ser fácilmente reemplazada o complementada por un clasificador LLM
    en el futuro sin cambiar la interfaz.

    ``extra_service_keywords`` permite ampliar los tratamientos reconocidos como
    dentro de dominio (p. ej. derivados del manual de la clínica).
    """
    msg = _normalize(message)
    if not msg:
        return Intent.OUT_OF_DOMAIN

    # Confirmaciones cortas: heredar intención previa usando el historial reciente.
    confirm_keywords = [
        "si",
        "sí",
        "claro",
        "ok",
        "vale",
        "está bien",
        "esta bien",
        "de acuerdo",
    ]
    if history and msg in confirm_keywords:
        for prev in reversed(list(history)):
            content = _normalize(prev.get("content", ""))
            if not content:
                continue
            if "cita" in content or "agendar" in content or "reservar" in content or "reagendar" in content:
                return Intent.CITA
            if (
                "limpieza" in content
                or "ortodoncia" in content
                or "precio" in content
                or "cuánto cuesta" in content
                or "cuanto cuesta" in content
            ):
                return Intent.SERVICIOS
        # Si no hay contexto claro, seguimos con las reglas normales.

    # Palabras clave para citas (agendar / cancelar / reagendar)
    cita_keywords = [
        "cita",
        "agendar",
        "agenda",
        "reservar",
        "reserva",
        "programar",
        "reagendar",
        "cambiar cita",
        "cambia mi cita",
        "reprogramar",
        "cancelar cita",
        "cancela mi cita",
    ]
    if any(k in msg for k in cita_keywords):
        # Subtipo seguimiento: llegar tarde, confirmar, etc.
        seguimiento_keywords = [
            "llego tarde",
            "voy tarde",
            "retraso",
            "confirmo mi cita",
            "confirmar mi cita",
            "todavía sigue en pie",
            "sigue en pie",
            "confirmar asistencia",
        ]
        if any(k in msg for k in seguimiento_keywords):
            return Intent.SEGUIMIENTO_CITA
        return Intent.CITA

    # Servicios / tratamientos / precios
    servicios_keywords = [
        "limpieza",
        "extracción",
        "extraccion",
        "ortodoncia",
        "blanqueamiento",
        "corona",
        "endodoncia",
        "caries",
        "empaste",
        "relleno",
        "precio",
        "cuánto cuesta",
        "cuanto cuesta",
        "cuánto vale",
        "cuanto vale",
        "tarifa",
        "cobran",
    ]
    if extra_service_keywords:
        servicios_keywords = servicios_keywords + [
            _normalize(k) for k in extra_service_keywords if k and k.strip()
        ]
    if any(k in msg for k in servicios_keywords):
        return Intent.SERVICIOS

    # Información de la clínica (dirección, horarios, contacto)
    clinica_info_keywords = [
        "dirección",
        "direccion",
        "ubicación",
        "ubicacion",
        "dónde están",
        "donde estan",
        "cómo llegar",
        "como llegar",
        "horario",
        "horarios",
        "abren",
        "cierran",
        "teléfono",
        "telefono",
        "número",
        "numero",
        "whatsapp",
        "parqueo",
        "parqueadero",
        "estacionamiento",
        "formas de pago",
        "pago",
    ]
    if any(k in msg for k in clinica_info_keywords):
        return Intent.CLINICA_INFO

    # Small talk básica
    small_talk_keywords = [
        "hola",
        "buenos días",
        "buenos dias",
        "buenas tardes",
        "buenas noches",
        "gracias",
        "muchas gracias",
        "ok gracias",
        "hasta luego",
        "adiós",
        "adios",
    ]
    if any(msg.startswith(k) or msg == k for k in small_talk_keywords):
        return Intent.SMALL_TALK

    return Intent.OUT_OF_DOMAIN


__all__ = [
    "Intent",
    "classify_intent",
    "extract_knowledge_base_topics",
    "knowledge_base_service_keywords",
]

