"""
Armado del system prompt efectivo para conversación con Gemini (clínica + horarios + catálogo + tools).

Mantiene la lógica fuera de ``bootstrap`` para que el orquestador sea más legible y las reglas
de prompt evolucionen en un solo módulo.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from ..schemas.clinic import ClinicConfig
from .catalog import _format_services_catalog_for_prompt, _services_for_clinic
from .prompt_clinic import (
    _format_clinic_location_for_prompt,
    _format_opening_hours_for_prompt,
    _format_payment_methods_for_prompt,
    _format_urgency_dolor_prompt_block,
    format_canonical_calendar_dates_for_prompt,
)


def build_citas_tool_instruction(language: str) -> str:
    """Bloque de instrucciones para las seis herramientas de citas (EN / ES)."""
    if (language or "").strip().lower() == "en":
        return (
            "\n\n[You have six appointment-related tools. The clinic is from context (do not ask the user). "
            "BOOKING uses El Salvador local date (UTC-6). Same-day appointments are NOT allowed; earliest day is tomorrow. "
            "(0) consultar_disponibilidad(fecha): REQUIRED before you list or suggest specific appointment start times for a **known** day. "
            "fecha in YYYY-MM-DD (use REFERENCE DATE above for 'Monday', 'tomorrow', etc.). "
            "The response includes horas_disponibles (HH:00 strings from Google Calendar when sync is on). "
            "You MUST only mention times that appear in horas_disponibles; never invent or guess slots. "
            "If the patient changes the day, call consultar_disponibilidad again for the new date. "
            "(1) consultar_primer_dia_disponible(max_dias optional): finds the **first calendar day from tomorrow** with at least one free slot, "
            "scanning up to max_dias days (default 14, max 30). Returns fecha, horas_disponibles, and primeras_tres_horas. "
            "Use for pain/urgency flows (see PAIN / URGENCY block above). "
            "(2) listar_mis_citas_proximas(): no parameters. Use when the patient asks about their appointments, reservations, "
            "'when is my appointment', or similar. Returns active appointments for this WhatsApp number from now (El Salvador UTC-6) onward: "
            "each item has fecha (YYYY-MM-DD), hora (HH:MM), and servicio (human-readable). List every row clearly for the patient; if the list is empty, say they have no upcoming active appointments on file. "
            "(3) agendar_cita(nombre, fecha, hora, servicio, suffix_urgencia optional): for new appointments. "
            "Only pass date and time that follow the BOOKING START TIMES rules above (60-minute visits; last start is 1 hour before closing in each block). "
            "Time must be on the hour only (HH:00). "
            "NEW BOOKING / ANOTHER SERVICE: For a new appointment (especially a different service than another one mentioned in the thread), "
            "the 'hora' must be one the patient chose for THIS booking—never assume or copy the time from a previous appointment unless they explicitly ask for the same time. "
            "If you only asked whether a DAY works (e.g. \"Does that day work?\") and they say yes, do NOT call agendar_cita yet: first call consultar_disponibilidad(fecha) and offer horas_disponibles, or ask \"What time would you like?\", then call agendar_cita only after a concrete hour is chosen or confirmed for this booking. "
            "If the patient asks for an impossible time (e.g. Sunday, or a start at the closing hour such as 17:00 when the block ends at 17:00), do NOT call the tool: explain that the last bookable start is one hour before closing and ask them to pick a valid on-the-hour time. "
            "'servicio' must be the exact 'id' string from the SERVICES CATALOG above (do not invent ids). "
            "SERVICIO / evaluacion: Do NOT pass servicio=evaluacion when the patient only gave a day and time or never chose a service—ask which catalog id they want, "
            "and if you suggest an evaluation ask them to confirm they want evaluacion before booking. "
            "Use evaluacion only when the PAIN / URGENCY block applies (they described pain or urgency) or they clearly asked for a check-up/evaluation and accepted it. "
            "Optional suffix_urgencia: only with servicio=evaluacion for pain flows: dolor_post_cita or dolor_intenso (see PAIN / URGENCY block). "
            "If you already know the patient, use their full name and do not ask. Only ask for name if the appointment is for someone else. "
            "(4) cancelar_cita(): no parameters. Use when the user asks to cancel their appointment. "
            "(5) reagendar_cita(fecha, hora, servicio optional, suffix_urgencia optional): when they want to change date/time. Only with date/time within opening hours and with time in HH:00. "
            "Date as YYYY-MM-DD and time as HH:00; use the REFERENCE DATE AND TIME above for 'tomorrow', 'next Friday', etc. "
            "CRITICAL — Call agendar_cita only when you already have a concrete start time (HH:00) for THIS booking that the patient chose or confirmed from available slots. "
            "If you only confirmed the DAY (e.g. \"Does that day work for your cleaning?\" + \"yes\") with no hour yet for this booking, do NOT call agendar_cita in that turn: first call consultar_disponibilidad(fecha), offer horas_disponibles, or ask for a time, then book after they pick a time (and confirm date+time+service if needed). "
            "CONFIRMATION before agendar_cita / reagendar_cita: Ask out loud for a clear answer, repeating service (name or catalog id), weekday date, and time, "
            "and state that you need *yes* or *confirm* to save it in the system—for example: "
            "\"To save it: [service] on [date] at [HH:00]. Reply *yes* or *confirm* (a thank-you alone is not enough).\" "
            "If they reply only with thanks, politeness (\"very kind\"), or small talk without *yes* / *confirm*, do NOT call the tool—ask again for *yes* or *confirm*. "
            "Only after that explicit reply, call agendar_cita or reagendar_cita in the same turn with the agreed fields. "
            "NEVER tell the patient the appointment is saved, booked, or rescheduled without a successful tool call in that flow. "
            "For agendar_cita, cancelar_cita, and reagendar_cita: the system shows the patient the exact text returned by the tool (success or error); "
            "do not claim success before calling the tool. "
            "For listar_mis_citas_proximas, consultar_disponibilidad and consultar_primer_dia_disponible there is usually no 'mensaje': summarize slots or appointments in natural language. "
            "Never show the patient source code, print(, default_api, or function names with parentheses—use tools or plain language only.]"
        )
    return (
        "\n\n[Tienes seis herramientas relacionadas con citas. La clínica se toma del contexto (no la pidas al usuario). "
        "El agendado usa la fecha local de El Salvador (UTC-6). No hay citas para el mismo día; el primer día posible es mañana. "
        "(0) consultar_disponibilidad(fecha): OBLIGATORIA antes de listar u ofrecer horas concretas de inicio de cita para un día **ya elegido**. "
        "fecha en YYYY-MM-DD (usa la FECHA DE REFERENCIA de arriba para 'el lunes', 'mañana', etc.). "
        "La respuesta trae horas_disponibles (cadenas HH:00; con sincronización activa vienen de Google Calendar). "
        "SOLO puedes mencionar horas que aparezcan en horas_disponibles; nunca inventes ni completes la lista por tu cuenta. "
        "Si el paciente cambia de día, vuelve a llamar consultar_disponibilidad con la nueva fecha. "
        "(1) consultar_primer_dia_disponible(max_dias opcional): encuentra el **primer día calendario desde mañana** con al menos un hueco, "
        "revisando hasta max_días días (por defecto 14, máximo 30). Devuelve fecha, horas_disponibles y primeras_tres_horas. "
        "Úsala en flujos de dolor/urgencia (ver bloque DOLOR / URGENCIA arriba). "
        "(2) listar_mis_citas_proximas(): sin parámetros. Úsala cuando el paciente pregunte por sus citas, reservas, "
        "«¿cuándo tengo cita?», «¿a qué hora es mi cita?» o similar. Devuelve citas **activas** de este número desde ahora (El Salvador UTC-6): "
        "cada elemento trae fecha (AAAA-MM-DD), hora (HH:MM) y servicio (nombre legible). Enuméralas con fecha, hora y tipo de servicio; si citas está vacío, di que no hay citas próximas registradas. "
        "(3) agendar_cita(nombre, fecha, hora, servicio, suffix_urgencia opcional): para citas nuevas. "
        "Solo pases fecha y hora que cumplan las reglas de 'HORARIOS PARA INICIAR UNA CITA' de arriba (visitas de 60 min; la última hora de inicio es 1 hora antes del cierre de cada bloque). "
        "La hora debe ir solo en punto (HH:00). "
        "CITA NUEVA / OTRO SERVICIO: la hora debe ser la que el paciente eligió para ESTA reserva; nunca asumas ni copies la hora de otra cita del historial "
        "(ej. una revisión a las 08:00) para una limpieza u otro servicio salvo que diga explícitamente que quiere la misma hora. "
        "Si solo preguntaste si le viene bien el DÍA (ej. «¿Te parece bien ese día para tu limpieza?») y responde sí, aún NO llames agendar_cita en ese turno: "
        "primero consultar_disponibilidad(fecha), ofrece horas_disponibles o pregunta «¿a qué hora te viene bien?», y solo después de que elija una hora concreta (y confirmes fecha+hora+servicio si aplica) llama agendar_cita. "
        "Si el paciente pide un horario imposible (ej. domingo, o iniciar a la hora de cierre como 17:00 si el bloque cierra a las 17:00), NO llames la herramienta: explica que la última cita del día inicia una hora antes del cierre y pídele una hora válida en punto. "
        "El parámetro 'servicio' debe ser el 'id' exacto de uno de los servicios del catálogo de arriba (no inventes ids). "
        "SERVICIO / evaluacion: NO uses servicio=evaluacion si el paciente solo dio día y hora o nunca eligió tipo de cita—pregunta qué id del catálogo quiere; "
        "si sugieres evaluación, pide confirmación explícita de que acepta evaluacion antes de agendar. "
        "Usa evaluacion solo cuando aplique el bloque DOLOR / URGENCIA (ya describió dolor o urgencia) o pidió claramente evaluación/revisión y lo aceptó. "
        "Opcional suffix_urgencia: solo con servicio=evaluacion en flujos de dolor: dolor_post_cita o dolor_intenso (ver bloque DOLOR / URGENCIA). "
        "Si ya conoces al paciente, usa su nombre completo y no preguntes. Solo pregunta el nombre si la cita es para otra persona. "
        "(4) cancelar_cita(): sin parámetros. Úsala cuando el usuario pida cancelar su cita (ej. 'quiero cancelar mi cita', 'cancela mi reserva'). "
        "(5) reagendar_cita(fecha, hora, servicio opcional, suffix_urgencia opcional): cuando pida cambiar la fecha/hora de su cita. Solo con fecha/hora dentro del horario de atención y hora en HH:00. "
        "La fecha en YYYY-MM-DD y hora en HH:00; usa la FECHA Y HORA DE REFERENCIA de arriba para calcular 'mañana', 'próximo viernes', etc. "
        "Para fechas relativas (mañana, próximo lunes, etc.) usa SIEMPRE la referencia indicada arriba y pasa a la herramienta en YYYY-MM-DD y HH:00. "
        "CRÍTICO — Llama agendar_cita solo cuando ya tengas una hora de inicio concreta (HH:00) para ESTA reserva, elegida o confirmada por el paciente entre las opciones válidas. "
        "Si solo confirmaste el DÍA (ej. «¿Te parece bien ese día para tu limpieza?» + «sí») y aún no hay hora para esta cita, NO llames agendar_cita en ese turno: "
        "primero consultar_disponibilidad(fecha), ofrece horas_disponibles o pregunta «¿a qué hora?», y agendar_cita solo cuando haya hora concreta (y confirmación de fecha+hora+servicio si corresponde). "
        "CONFIRMACIÓN antes de agendar_cita / reagendar_cita: Pregunta en voz alta de forma explícita, repitiendo servicio (nombre o id del catálogo), fecha con día de la semana y hora, "
        "y di que hace falta **sí** o **confirmo** para guardarla en el sistema, por ejemplo: "
        "\"Para registrarla: [servicio] el [fecha] a las [HH:00]. Responde **sí** o **confirmo** (solo gracias o «muy amable» no alcanza).\" "
        "Si responde solo con gracias, cortesía o charla sin **sí** / **confirmo**, NO llames la herramienta: vuelve a pedir **sí** o **confirmo**. "
        "Solo con esa respuesta explícita, llama agendar_cita o reagendar_cita en el mismo turno con los datos acordados. "
        "NUNCA digas que la cita quedó guardada, agendada o reagendada sin una llamada exitosa a la herramienta en ese flujo. "
        "Para agendar_cita, cancelar_cita y reagendar_cita: el sistema muestra al paciente el texto exacto que devuelve la herramienta (éxito o error); "
        "no afirmes éxito antes de llamar a la herramienta. "
        "Para listar_mis_citas_proximas, consultar_disponibilidad y consultar_primer_dia_disponible normalmente no hay 'mensaje': resume las horas o las citas en lenguaje natural. "
        "Nunca muestres al paciente código, print(, default_api ni nombres de funciones con paréntesis: usa las herramientas o lenguaje natural.]"
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

    identity_line = (
        f"\n\n[Datos del asistente: Tu nombre es {assistant_name}. Trabajas para la clínica {clinic_name}. "
        f"El ID de la clínica en este chat es: {clinic_id}. "
        f"Cuando te pregunten cómo te llamas, quién eres o con quién hablan, responde siempre con el nombre {assistant_name}. "
        "NUNCA preguntes al usuario a qué clínica quiere ir ni pidas que indique la clínica: el paciente ya está hablando con la clínica actual; usa siempre la clínica del contexto."
        f"{identidad_paciente}]\n"
        "\n[Idioma: Responde siempre en el mismo idioma en que el usuario te escribe. "
        "Si escribe en español, responde en español; si escribe en inglés, responde en inglés; y así con cualquier otro idioma.]\n"
    )

    if is_first_message:
        extra_instruction = (
            "\n\n[Instrucción para esta respuesta: Es el primer mensaje del usuario. "
            f"Preséntate diciendo que te llamas {assistant_name} y que eres el asistente de {clinic_name}. "
            "Nunca uses placeholders como [Tu nombre]; usa siempre el nombre del asistente indicado.]"
        )
    else:
        extra_instruction = (
            "\n\n[Instrucción para esta respuesta: Ya hay historial de conversación. "
            "Sé directa y conversacional.]"
        )

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

    system_prompt_effective = base_prompt.strip() + referencia_fecha + identity_line + extra_instruction

    clinic_cfg = clinics_by_id.get(clinic_id)
    if clinic_cfg is not None:
        schedule_text = _format_opening_hours_for_prompt(clinic_cfg, language)
        if schedule_text:
            system_prompt_effective = system_prompt_effective + schedule_text
            if language == "en":
                schedule_rule = (
                    "\n\n[CRITICAL - APPOINTMENT TIMES: Use the BOOKING START TIMES rules above, not only the reception open/close line. "
                    "LEAD TIME: The clinic uses El Salvador time (UTC-6, no DST). Never book or offer same-day appointments; "
                    "the earliest bookable day is TOMORROW relative to that local date. If they ask for today, explain one-day notice and offer from tomorrow. "
                    "Each appointment is 60 minutes and must end by closing; the last valid start is 1 hour before the closing time in each block. "
                    "NEVER tell the patient that starting at the closing hour (e.g. 5:00 PM / 17:00 when the block ends at 17:00) is inside hours or OK to book. "
                    "NEVER suggest or confirm a specific start time if that day or start time is invalid under those rules. "
                    "Only suggest times on the hour (e.g. 08:00, 09:00, 10:00). Never suggest fractional times such as 08:30 or 09:15. "
                    "If the patient asks for a day we are closed or a start time at or after closing, do NOT say you can book it. "
                    "Say clearly that that start time is not available and offer the last valid on-the-hour starts from the list above. "
                    "Before listing concrete start times for a day, call consultar_disponibilidad(fecha) and only offer times from horas_disponibles. "
                    "Only call agendar_cita or reagendar_cita with a valid on-the-hour start time under those rules.]"
                )
            else:
                schedule_rule = (
                    "\n\n[CRÍTICO - HORARIOS DE CITA: Usa las reglas de 'HORARIOS PARA INICIAR UNA CITA' de arriba, no solo la línea de apertura/cierre. "
                    "ANTICIPACIÓN: Se usa hora de El Salvador (UTC-6, sin horario de verano). No agendes ni ofrezcas citas para el mismo día; "
                    "el primer día reservable es a partir de mañana respecto a esa fecha local. Si pide cita hoy, explica la anticipación mínima y ofrece desde mañana. "
                    "Cada cita dura 60 minutos y debe terminar a más tardar al cierre; la última hora de inicio válida es 1 hora antes del cierre de cada bloque. "
                    "NUNCA digas al paciente que iniciar a la hora de cierre (ej. 17:00 si el bloque es hasta las 17:00) está dentro del horario o se puede agendar. "
                    "NUNCA sugieras ni confirmes una hora de inicio concreta si ese día u hora no es válida según esas reglas. "
                    "Solo sugiere horas en punto (ej. 08:00, 09:00, 10:00). Nunca ofrezcas horarios fraccionados como 08:30 o 09:15. "
                    "Si el paciente pide un día en que no abrimos o una hora de inicio a la hora de cierre o después, NO digas que puedes agendarla. "
                    "Di claramente que esa hora de inicio no está disponible y ofrece las últimas horas de inicio válidas indicadas arriba. "
                    "Antes de listar horas concretas de inicio para un día, llama consultar_disponibilidad(fecha) y solo ofrece las que vengan en horas_disponibles. "
                    "Solo llama agendar_cita o reagendar_cita con una hora de inicio en punto válida según esas reglas.]"
                )
            system_prompt_effective = system_prompt_effective + schedule_rule

        location_text = _format_clinic_location_for_prompt(clinic_cfg, language)
        if location_text:
            system_prompt_effective = system_prompt_effective + location_text

        payment_text = _format_payment_methods_for_prompt(clinic_cfg, language)
        if payment_text:
            system_prompt_effective = system_prompt_effective + payment_text

    catalog_text = _format_services_catalog_for_prompt(_services_for_clinic(clinic_id), language)
    if catalog_text:
        system_prompt_effective = system_prompt_effective + catalog_text

    system_prompt_effective = system_prompt_effective + _format_urgency_dolor_prompt_block(language)
    system_prompt_effective = system_prompt_effective.strip() + build_citas_tool_instruction(language)
    return system_prompt_effective
