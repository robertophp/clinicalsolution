from __future__ import annotations

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


def classify_intent(
    message: str,
    language: str,
    history: Sequence[Mapping[str, str]] | None = None,
) -> Intent:
    """
    Clasificación ligera de intención basada en reglas.

    Diseñada para ser fácilmente reemplazada o complementada por un clasificador LLM
    en el futuro sin cambiar la interfaz.
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


__all__ = ["Intent", "classify_intent"]

