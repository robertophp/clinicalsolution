"""
Armado del system prompt efectivo para conversación con Gemini (clínica + horarios + catálogo + tools).

Mantiene la lógica fuera de ``bootstrap`` para que el orquestador sea más legible y las reglas
de prompt evolucionen en un solo módulo. Los bloques largos se renderizan con Jinja2 bajo
``templates/prompt/``.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..schemas.clinic import ClinicConfig
from ..schemas.clinic_policies import BookingPromptPolicies, ClinicPolicies
from .catalog import _format_services_catalog_for_prompt, _services_for_clinic
from .prompt_clinic import (
    _format_clinic_location_for_prompt,
    _format_clinic_phone_for_prompt,
    _format_opening_hours_for_prompt,
    _format_payment_methods_for_prompt,
    _format_urgency_dolor_prompt_block,
    format_canonical_calendar_dates_for_prompt,
)

_PROMPT_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "prompt"

_DEFAULT_CONFIRMATION_ES = (
    "Para registrarla: [servicio] el [fecha] a las [HH:00]. Responde **sí** o **confirmo** "
    "(solo gracias o «muy amable» no alcanza)."
)
_DEFAULT_CONFIRMATION_EN = (
    "To save it: [service] on [date] at [HH:00]. Reply *yes* or *confirm* (a thank-you alone is not enough)."
)

_jinja_env: Environment | None = None


def _prompt_env() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(str(_PROMPT_TEMPLATES_DIR)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
    return _jinja_env


def _booking_policies(policies: ClinicPolicies | None) -> BookingPromptPolicies:
    return policies.booking if policies else BookingPromptPolicies()


def build_citas_tool_instruction(language: str, policies: ClinicPolicies | None = None) -> str:
    """Bloque de instrucciones para las seis herramientas de citas (EN / ES), con textos de confirmación configurables."""
    b = _booking_policies(policies)
    confirmation_es = (b.confirmation_example_es or _DEFAULT_CONFIRMATION_ES).strip()
    confirmation_en = (b.confirmation_example_en or _DEFAULT_CONFIRMATION_EN).strip()
    return _prompt_env().get_template("citas_tools.j2").render(
        language=(language or "").strip(),
        confirmation_example_es=confirmation_es,
        confirmation_example_en=confirmation_en,
    )


def build_conversation_system_prompt(
    *,
    language: str,
    clinic_id: str,
    clinic_name: str,
    assistant_name: str,
    system_prompt: str,
    system_prompt_en: str | None,
    is_first_message: bool,
    stored_first_name: str | None,
    stored_full_name: str | None,
    clinics_by_id: Mapping[str, ClinicConfig],
    policies: ClinicPolicies | None = None,
) -> str:
    """
    Construye el system prompt completo para un turno de conversación con herramientas de citas.
    """
    tz_salvador = timezone(timedelta(hours=-6))
    now_local = datetime.now(tz_salvador)
    _dias = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
    _meses = (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    )
    dia_semana = _dias[now_local.weekday()]
    mes = _meses[now_local.month - 1]
    fecha_ref_iso = now_local.strftime("%Y-%m-%d")
    hora_ref_iso = now_local.strftime("%H:%M")
    referencia_fecha = (
        f"\n\n[FECHA Y HORA DE REFERENCIA (usa esto como 'hoy' y 'ahora', hora El Salvador UTC-6): "
        f"Hoy es {dia_semana} {now_local.day} de {mes} de {now_local.year}. "
        f"Fecha de referencia en YYYY-MM-DD: {fecha_ref_iso}. "
        f"Hora actual de referencia HH:MM: {hora_ref_iso}. "
        "Cuando el usuario diga 'próximo jueves', 'mañana', 'el viernes', etc., OBLIGATORIO: usa la TABLA CANÓNICA de fechas que viene justo debajo; "
        "cada fila empareja un YYYY-MM-DD con su día de la semana real (no calcules el calendario solo de memoria). "
        "Pasa a las herramientas siempre en YYYY-MM-DD y HH:00 (solo horas en punto).]\n"
    )
    referencia_fecha += format_canonical_calendar_dates_for_prompt(
        now_local.date(),
        language=language,
        num_days=21,
    )

    if stored_first_name:
        identidad_paciente = (
            f" También conoces al paciente: su primer nombre es {stored_first_name}. "
            f"Salúdalo solo por su primer nombre ({stored_first_name}) y NO vuelvas a pedirle su nombre."
        )
        if stored_full_name and len(stored_full_name.split()) > 1:
            identidad_paciente += (
                f" Cuando el usuario pida agendar una cita y NO diga que es para otra persona, la cita es para este paciente: "
                f"usa DIRECTAMENTE el nombre completo \"{stored_full_name}\" en la herramienta agendar_cita y NUNCA preguntes el nombre. "
                "Solo pregunta el nombre completo si el usuario indica explícitamente que la cita es para otra persona (ej. mi esposa, mi hijo, etc.)."
            )
        else:
            identidad_paciente += (
                f" Cuando agende una cita para este mismo paciente (sin decir que es para otro), usa \"{stored_first_name}\" en la herramienta y no preguntes el nombre."
            )
    else:
        identidad_paciente = (
            " Si todavía no conoces el nombre del paciente, puedes preguntarlo una sola vez de forma natural "
            "y luego recuerda ese nombre para el resto de la conversación."
        )

    env = _prompt_env()
    identity_line = "\n\n" + env.get_template("identity_extra.j2").render(
        assistant_name=assistant_name,
        clinic_name=clinic_name,
        clinic_id=clinic_id,
        identidad_paciente=identidad_paciente,
        is_first_message=is_first_message,
    ).strip() + "\n"

    if language == "en" and system_prompt_en:
        base_prompt = system_prompt_en
    elif language == "en":
        base_prompt = (
            system_prompt.strip()
            + "\n\n[IMPORTANTE: Aunque estas instrucciones estén en español, "
            "RESPONDE SIEMPRE AL PACIENTE EN INGLÉS. No respondas en español en esta conversación.]"
        )
    else:
        base_prompt = system_prompt

    system_prompt_effective = base_prompt.strip() + referencia_fecha + identity_line

    clinic_cfg = clinics_by_id.get(clinic_id)
    if clinic_cfg is not None:
        schedule_text = _format_opening_hours_for_prompt(clinic_cfg, language)
        if schedule_text:
            system_prompt_effective = system_prompt_effective + schedule_text
            schedule_rule = env.get_template("schedule_rule.j2").render(language=language)
            system_prompt_effective = system_prompt_effective + schedule_rule

        location_text = _format_clinic_location_for_prompt(clinic_cfg, language)
        if location_text:
            system_prompt_effective = system_prompt_effective + location_text

        phone_text = _format_clinic_phone_for_prompt(clinic_cfg, language)
        if phone_text:
            system_prompt_effective = system_prompt_effective + phone_text

        payment_text = _format_payment_methods_for_prompt(clinic_cfg, language)
        if payment_text:
            system_prompt_effective = system_prompt_effective + payment_text

    catalog_text = _format_services_catalog_for_prompt(_services_for_clinic(clinic_id), language)
    if catalog_text:
        system_prompt_effective = system_prompt_effective + catalog_text

    # Catálogo antes del bloque de dolor/urgencia (prioridad citas y precios).
    system_prompt_effective = system_prompt_effective.strip() + build_citas_tool_instruction(language, policies)
    system_prompt_effective = system_prompt_effective + _format_urgency_dolor_prompt_block(language)
    return system_prompt_effective
