from __future__ import annotations

from datetime import date, datetime, timedelta

from ..schemas.clinic import ClinicConfig
from ..services.intent_classifier import extract_knowledge_base_topics
from .catalog import _services_for_clinic

_WEEKDAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS_ES = (
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
_MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def format_canonical_calendar_dates_for_prompt(
    today_sv: date,
    *,
    language: str,
    num_days: int = 21,
) -> str:
    """
    Tabla YYYY-MM-DD ↔ día de la semana (El Salvador) para el prompt.
    Evita errores del modelo (ej. llamar 'viernes' a una fecha que en calendario es sábado).
    """
    use_en = (language or "").strip().lower() == "en"
    weekdays = _WEEKDAYS_EN if use_en else _WEEKDAYS_ES
    months = _MONTHS_EN if use_en else _MONTHS_ES
    lines: list[str] = []
    for offset in range(max(1, num_days)):
        d = today_sv + timedelta(days=offset)
        wd = weekdays[d.weekday()]
        month_name = months[d.month - 1]
        lines.append(f"  {d.isoformat()} | {wd} | {d.day} {month_name} {d.year}")
    table = "\n".join(lines)

    if use_en:
        return (
            "\n\n[CANONICAL DATE TABLE (local clinic date, same as 'today' above). "
            "Each row is the ONLY correct pairing of YYYY-MM-DD with the weekday. "
            "If the user says 'Friday' or 'next Friday', pick the row whose weekday column is exactly Friday and use that YYYY-MM-DD in consultar_disponibilidad and agendar_cita. "
            "NEVER invent dates or mismatch weekday and calendar date (e.g. saying 'Friday May 16' when that row is Saturday is WRONG—fix using this table). "
            "When you speak naturally to the patient, the weekday and date must match the chosen row.]\n"
            f"{table}\n"
        )

    return (
        "\n\n[TABLA CANÓNICA DE FECHAS (misma fecha local de El Salvador que 'hoy' arriba). "
        "Cada fila es la ÚNICA forma correcta de emparejar YYYY-MM-DD con el día de la semana. "
        "Si el usuario dice 'viernes', 'el viernes' o 'próximo viernes', elige la fila cuya columna central sea exactamente viernes y usa ese YYYY-MM-DD en consultar_disponibilidad y agendar_cita. "
        "NUNCA inventes fechas ni mezcles el número del día con un día de la semana que no corresponda (ej. decir 'viernes 16 de mayo' cuando la fila del 2026-05-16 es sábado es INCORRECTO—corrige usando esta tabla). "
        "Al hablar con el paciente en español, el día de la semana y la fecha deben coincidir con la fila elegida.]\n"
        f"{table}\n"
    )


def _format_opening_hours_for_prompt(clinic: ClinicConfig, language: str) -> str:
    """Formatea los horarios de atención de la clínica para el prompt (ES/EN)."""
    opening_hours = getattr(clinic, "opening_hours", None) or {}
    if not opening_hours:
        return ""

    def _last_booking_start_hhmm(from_h: str, to_h: str) -> str | None:
        """Última hora de INICIO válida para una cita de 60 min que termine al cierre."""
        try:
            start_block = datetime.strptime((from_h or "").strip(), "%H:%M")
            end_block = datetime.strptime((to_h or "").strip(), "%H:%M")
        except ValueError:
            return None
        last_start = end_block - timedelta(hours=1)
        if last_start < start_block:
            return None
        return last_start.strftime("%H:%M")

    def _days_label(days: list[str]) -> str:
        mapping_es = {
            "mon": "lunes",
            "tue": "martes",
            "wed": "miércoles",
            "thu": "jueves",
            "fri": "viernes",
            "sat": "sábado",
            "sun": "domingo",
        }
        mapping_en = {
            "mon": "Monday",
            "tue": "Tuesday",
            "wed": "Wednesday",
            "thu": "Thursday",
            "fri": "Friday",
            "sat": "Saturday",
            "sun": "Sunday",
        }
        mapping = mapping_en if language == "en" else mapping_es
        return ", ".join(mapping.get(d, d) for d in days)

    if language == "en":
        lines: list[str] = ["\n\n[OPENING HOURS of the clinic (reception / when the clinic is open):]"]
    else:
        lines = ["\n\n[HORARIO DE ATENCIÓN de la clínica (recepción / cuando la clínica está abierta):]"]

    for block in opening_hours.values():
        days = block.get("days", [])
        start = block.get("from")
        end = block.get("to")
        if not days or not start or not end:
            continue
        days_txt = _days_label(days)
        if language == "en":
            lines.append(f"- {days_txt}: from {start} to {end}")
        else:
            lines.append(f"- {days_txt}: de {start} a {end}")

    if language == "en":
        lines.append("\n[BOOKING START TIMES – each visit is 60 minutes:]")
        lines.append(
            "The closing time in each line (the 'to' time) is when the clinic stops for that block. "
            "A 60-minute appointment must FINISH by that closing time, so the LAST valid appointment START "
            "is exactly 1 hour BEFORE that closing time. "
            "NEVER tell the patient that a start at the same clock time as closing (e.g. 17:00 when the block ends at 17:00) "
            "is 'within hours' or bookable; it is NOT a valid start time."
        )
        lines.append("Last valid on-the-hour start for each block above:")
    else:
        lines.append("\n[HORARIOS PARA INICIAR UNA CITA – cada visita dura 60 minutos:]")
        lines.append(
            "La hora de cierre en cada línea (la hora 'a' / 'to') es cuando termina la atención en ese bloque. "
            "Cada cita dura 60 minutos y debe terminar a más tardar a esa hora de cierre, "
            "por lo que la ÚLTIMA hora de INICIO de cita permitida es exactamente 1 hora ANTES de ese cierre. "
            "NUNCA digas al paciente que una cita puede INICIAR a la misma hora en que cierra el bloque "
            "(ej. 17:00 si el bloque es hasta las 17:00): esa hora NO es un inicio válido de cita."
        )
        lines.append("Última hora de inicio en punto permitida por bloque (según el horario de arriba):")

    for block in opening_hours.values():
        days = block.get("days", [])
        start = block.get("from")
        end = block.get("to")
        if not days or not start or not end:
            continue
        days_txt = _days_label(days)
        last_start = _last_booking_start_hhmm(str(start), str(end))
        if not last_start:
            continue
        if language == "en":
            lines.append(f"- {days_txt}: last bookable start at {last_start} (do not offer {end} as a start time).")
        else:
            lines.append(
                f"- {days_txt}: última cita que puedes ofrecer iniciando a las {last_start} "
                f"(no ofrezcas inicio a las {end}; a esa hora ya cierra la clínica para ese bloque)."
            )

    return "\n".join(lines)


def _format_clinic_phone_for_prompt(clinic: ClinicConfig, language: str) -> str:
    """Teléfono de contacto de la clínica (no confundir con el WhatsApp del bot ni el del especialista)."""
    phone = (getattr(clinic, "clinic_phone", None) or "").strip()
    if not phone:
        return ""
    if language == "en":
        return (
            f"\n\n[CLINIC PHONE – use ONLY if they ask for the clinic phone, call us, landline, or how to reach reception: "
            f"{phone}. Do not invent other numbers.]"
        )
    return (
        f"\n\n[TELÉFONO DE LA CLÍNICA – úsalo SOLO si preguntan el teléfono de la clínica, llamar, fijo o recepción: "
        f"{phone}. No inventes otros números.]"
    )


def _format_clinic_location_for_prompt(clinic: ClinicConfig, language: str) -> str:
    """
    Dirección física, enlace a Maps, parqueo y transporte: el modelo solo debe usar cada dato según la pregunta.
    - Ubicación / dirección: texto de dirección + enlace Maps en el mismo mensaje.
    - Parqueo: solo si el usuario pregunta explícitamente por parqueo/estacionamiento/aparcamiento.
    - Transporte público: solo si pregunta por autobuses/rutas/transporte público/cómo llegar en bus.
    """
    address = (getattr(clinic, "clinic_address", None) or "").strip()
    maps = (getattr(clinic, "google_maps_link", None) or "").strip()
    parking = (getattr(clinic, "indicaciones_parqueo", None) or "").strip()
    transit = (getattr(clinic, "rutas_transporte_publico", None) or "").strip()
    if not address and not maps and not parking and not transit:
        return ""

    if language == "en":
        lines: list[str] = ["\n\n[CLINIC LOCATION – follow these rules strictly:]"]
        if address or maps:
            lines.append("- Physical address + map (when they ask location/address/where you are):")
            if address:
                lines.append(f"  Address text (include in your reply): {address}")
            if maps:
                lines.append(f"  Google Maps link (same reply, after the address): {maps}")
            lines.append(
                "  Reply in ONE message, e.g. 'Here is the location:' then the address text, then the link. "
                "Do NOT add parking or public transport unless they also asked."
            )
        if parking:
            lines.append(f"- Parking (ONLY if they explicitly ask about parking): {parking}")
        if transit:
            lines.append(
                f"- Public transport routes (ONLY if they explicitly ask about buses, routes, or public transport): {transit}"
            )
        lines.append(
            "Never volunteer parking or public transport information in greetings or general replies. "
            "Do not repeat the Maps link when answering only about parking or buses unless they also asked for the link."
        )
    else:
        lines = ["\n\n[UBICACIÓN – sigue estas reglas al pie de la letra:]"]
        if address or maps:
            lines.append("- Dirección física + mapa (si preguntan dónde están / dirección / ubicación):")
            if address:
                lines.append(f"  Texto de dirección (inclúyelo en la respuesta): {address}")
            if maps:
                lines.append(f"  Enlace Google Maps (en el mismo mensaje, después de la dirección): {maps}")
            lines.append(
                "  Responde en UN solo mensaje, por ejemplo: 'Te comparto la ubicación:' luego el texto de la dirección, "
                "luego el enlace. No añadas parqueo ni transporte en esa misma respuesta salvo que también lo pregunte."
            )
        if parking:
            lines.append(f"- Parqueo (SOLO si pregunta explícitamente por parqueo, estacionamiento o aparcamiento): {parking}")
        if transit:
            lines.append(
                f"- Transporte público (SOLO si pregunta explícitamente por autobuses, rutas o transporte público / cómo llegar en bus): {transit}"
            )
        lines.append(
            "No ofrezcas por tu cuenta datos de parqueo ni de transporte en saludos o respuestas generales. "
            "No repitas el enlace de Maps al responder solo sobre parqueo o buses, salvo que también pidan el enlace."
        )
    return "\n".join(lines)


def _format_payment_methods_for_prompt(clinic: ClinicConfig, language: str) -> str:
    """
    Lista de métodos de pago por clínica. El modelo debe limitarse a estos textos
    cuando pregunten por pagos, tarifas o comisiones.
    """
    raw = getattr(clinic, "payment_methods", None) or []
    bullets: list[str] = []
    for row in raw:
        if language == "en":
            line = (row.en or "").strip() or (row.es or "").strip()
        else:
            line = (row.es or "").strip() or (row.en or "").strip()
        if line:
            bullets.append(f"- {line}")
    if not bullets:
        return ""

    if language == "en":
        header = "\n\n[ACCEPTED PAYMENT METHODS – authoritative list for this clinic:]"
        rules = (
            "\nTone: WhatsApp-friendly and concise. "
            "For a general question about how to pay (\"what payment methods do you accept?\"): "
            "reply in one or two short sentences summarizing the types (e.g. cash, bank transfer, cards)—do NOT read out or paste every bullet. "
            "If they specifically ask about 0 % fee, installments, \"meses sin intereses\", or paying in installments: "
            "answer in plain language (yes/no per the list), then briefly explain only what the list allows "
            "(e.g. 0 % installments only with the banks named in the list; not available with other banks if the list says so). "
            "You may only mention options, banks, and rates that appear in the bullets; if unsure, say they can confirm at the clinic. "
            "Do not dump the full bullet list unless they explicitly ask for every detail. "
            "Do not volunteer this block in greetings; use it when payment comes up.]"
        )
    else:
        header = "\n\n[MÉTODOS DE PAGO ACEPTADOS – lista oficial de esta clínica:]"
        rules = (
            "\nTono: WhatsApp, breve y cercano. "
            "Si preguntan en general qué métodos de pago aceptan: responde en una o dos frases cortas resumiendo "
            "(ej. efectivo, transferencia bancaria y tarjetas); NO enumeres ni copies todas las viñetas. "
            "Si preguntan solo por tasa 0, meses sin intereses, cuotas o pagar a plazos: responde primero con un sí o no claro según la lista, "
            "en lenguaje sencillo, y explica en una frase extra que la tasa 0 / plazos sin esa comisión solo aplica con los bancos que indica la lista "
            "(Banco Agrícola y Banco Davivienda si así figura), y que con otros bancos esa opción no está disponible si la lista lo dice. "
            "Solo puedes mencionar formas de pago, bancos y tasas que aparezcan en las viñetas; si no está, que confirmen en la clínica. "
            "No pegues la lista completa salvo que pidan explícitamente todos los detalles. "
            "No ofrezcas este bloque en saludos; úsalo cuando el tema sea el pago.]"
        )
    return header + "\n" + "\n".join(bullets) + rules


def _build_transfer_resolution_context(clinic_cfg: ClinicConfig | None, language: str) -> str:
    """
    Contexto para el clasificador de derivación: qué puede resolver el bot antes de escalar a humano.
    Evita derivar consultas informativas sobre pagos/precios que ya están en catálogo + métodos de pago.
    """
    use_en = (language or "").strip().lower().startswith("en")
    chunks: list[str] = []

    if clinic_cfg is not None:
        # Servicios del catálogo (solo nombre legible, deduplicado) para que el clasificador
        # verifique si la clínica ofrece el tratamiento ANTES de derivar.
        service_names: list[str] = []
        seen_names: set[str] = set()
        for s in _services_for_clinic(getattr(clinic_cfg, "id", "") or ""):
            name = (s.get("name_en") if use_en else s.get("name")) or s.get("name") or s.get("name_en") or ""
            name = str(name).strip()
            if not name:
                continue
            key = name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)
            service_names.append(name)

        kb_topics = extract_knowledge_base_topics(getattr(clinic_cfg, "knowledge_base", None))

        if service_names or kb_topics:
            offered_lines: list[str] = []
            if service_names:
                offered_lines.append("Servicios del catálogo: " + "; ".join(service_names[:120]))
            if kb_topics:
                offered_lines.append("Temas del manual de la clínica: " + "; ".join(kb_topics))
            offered_block = "\n".join(offered_lines)
            if use_en:
                chunks.append(
                    "TREATMENTS/SERVICES THIS CLINIC OFFERS (catalog + knowledge base). Use this to verify scope "
                    "BEFORE escalating:\n"
                    f"{offered_block}\n"
                    "If the patient is only ASKING (info, price, options) about any treatment that appears here "
                    "(or an obvious synonym/variant), set requires_human_transfer=false: the assistant must answer "
                    "with empathy, a reference price from the catalog, and offer to book an evaluation. "
                    "Do NOT escalate just because the case sounds complex or specialized if the treatment is covered here."
                )
            else:
                chunks.append(
                    "TRATAMIENTOS/SERVICIOS QUE ESTA CLÍNICA SÍ OFRECE (catálogo + base de conocimiento). Úsalo para "
                    "verificar el alcance ANTES de derivar:\n"
                    f"{offered_block}\n"
                    "Si el paciente solo PREGUNTA (información, precio, opciones) por un tratamiento que aparece aquí "
                    "(o un sinónimo/variante evidente), usa requires_human_transfer=false: el asistente debe responder "
                    "con empatía, un precio referencial del catálogo y ofrecer agendar una evaluación. "
                    "NO derives solo porque el caso suene complejo o especializado si el tratamiento está cubierto aquí."
                )

    if clinic_cfg is not None:
        raw_pm = getattr(clinic_cfg, "payment_methods", None) or []
        lines: list[str] = []
        for row in raw_pm:
            if use_en:
                line = (row.en or "").strip() or (row.es or "").strip()
            else:
                line = (row.es or "").strip() or (row.en or "").strip()
            if line:
                lines.append(line)
        if lines:
            listed = "\n".join(f"- {t}" for t in lines[:24])
            if use_en:
                chunks.append(
                    "Official payment options the assistant may use (do NOT require human transfer if the patient "
                    "only asks about cards, installments, 0% fee, which banks, or says it feels expensive but is "
                    "clearly asking what payment options exist):\n"
                    f"{listed}"
                )
            else:
                chunks.append(
                    "Opciones de pago oficiales que el asistente puede usar (NO requieras derivación si solo preguntan "
                    "por tarjeta, cuotas, tasa 0, qué bancos, o dicen que les parece caro pero en realidad buscan "
                    "alternativas de pago según esta lista):\n"
                    f"{listed}"
                )
    if use_en:
        chunks.append(
            "Service prices are in the SERVICES CATALOG embedded in the assistant instructions; "
            "routine price questions are not escalation unless there is a billing dispute or harsh complaint."
        )
        chunks.append(
            "requires_human_transfer=false when: price ask for a catalog service; dental pain plus review/evaluation "
            "price — respond with catalog price, empathy, recommend evaluation booking, do NOT escalate."
        )
        chunks.append(
            "requires_human_transfer=false when: urgent/emergency/same-day request or dental pain — the assistant "
            "must use consultar_primer_dia_disponible, explain same-day booking is not available, offer the first three "
            "slots from tomorrow (primeras_tres_horas), and book evaluacion when appropriate; do NOT escalate."
        )
        chunks.append(
            "requires_human_transfer=true (immediate): patient explicitly asks to speak with a doctor, manager, or human — "
            "escalate via specialist transfer; do NOT offer appointment booking or availability tools."
        )
        chunks.append(
            "requires_human_transfer=true only if: the treatment is NOT in the offered list above (catalog/knowledge base); "
            "patient insists on special quote after evaluation was offered; serious complaint; fiscal topics; subspecialty "
            "beyond catalog. The 'especialidades' and 'casos_medicos_complejos' topics apply ONLY when the treatment is "
            "outside the offered list — never escalate a simple info/price question about a treatment we offer."
        )
        chunks.append(
            "Maxillofacial / maxilo: informational or price questions → requires_human_transfer=false (catalog). "
            "Maxillofacial appointment or availability → dedicated direct-transfer flow; do NOT use especialidades topic."
        )
    else:
        chunks.append(
            "Los precios de servicios están en el CATÁLOGO del prompt del asistente; "
            "preguntas rutinarias de precio no son derivación salvo conflicto de cobro o reclamo fuerte."
        )
        chunks.append(
            "requires_human_transfer=false cuando: preguntan precio de un servicio que está en catálogo; "
            "mencionan dolor dental y preguntan por revisión/evaluación/consulta o por un servicio con is_evaluation; "
            "combinan dolor + precio de evaluación/revisión — el asistente debe dar precio referencial, empatía, "
            "recomendar cita de evaluación y ofrecer agendar, NO derivar."
        )
        chunks.append(
            "requires_human_transfer=false cuando: piden urgente, emergencia, cita hoy o reportan dolor — el asistente "
            "debe usar consultar_primer_dia_disponible, explicar que no hay citas el mismo día, ofrecer las tres primeras "
            "horas desde mañana (primeras_tres_horas) y agendar evaluacion si corresponde; NO derivar."
        )
        chunks.append(
            "requires_human_transfer=true (inmediato): el paciente pide explícitamente hablar con doctor/a, encargado/a o "
            "humano — derivar al especialista; NO ofrecer agendar cita ni consultar disponibilidad."
        )
        chunks.append(
            "requires_human_transfer=true solo si: el tratamiento NO está en la lista ofrecida arriba (catálogo/manual); "
            "el paciente insiste en cotización especial o escalamiento tras ya haber recibido precio y oferta de evaluación; "
            "queja grave; temas fiscales; especialidad fuera de alcance del catálogo. Los temas 'especialidades' y "
            "'casos_medicos_complejos' aplican SOLO cuando el tratamiento está fuera de la lista ofrecida — nunca derives "
            "una simple pregunta de información/precio por un tratamiento que sí ofrecemos."
        )
        chunks.append(
            "Maxilofacial / maxilo: preguntas informativas o de precio → requires_human_transfer=false (catálogo). "
            "Cita u horarios maxilofaciales → flujo dedicado del sistema (derivación directa), NO usar tema especialidades."
        )
    return "\n\n".join(chunks) if chunks else ""


def _format_urgency_dolor_prompt_block(language: str) -> str:
    """Instrucciones para dolor/urgencia y uso de herramientas de primer día disponible."""
    if language == "en":
        return (
            "\n\n[PAIN / URGENCY – Use when the patient mentions dental pain, swelling, **urgent/urgency**, "
            "**emergency**, needs to be seen **today**, or similar. Do NOT use for generic booking (day/time only) "
            "without pain/urgency cues, greetings, or small talk.]\n"
            "- Respond with brief empathy and reassurance; the clinic team will help.\n"
            "- **Same-day policy:** appointments cannot start today (El Salvador UTC-6); earliest is **tomorrow**. "
            "If they ask for today, say so clearly with empathy before offering slots.\n"
            "- Prefer an evaluation appointment: use catalog service id `evaluacion` unless they clearly want another service; "
            "confirm they accept an evaluation before booking.\n"
            "- Call `consultar_primer_dia_disponible` (max_dias 1–30, default 14) for the **first day from tomorrow** with a free slot.\n"
            "- Offer only `primeras_tres_horas` first (up to three HH:00 starts) unless they ask for more.\n"
            "- Do NOT escalate to a human specialist for routine urgent booking when evaluacion is in the catalog.\n"
            "- `suffix_urgencia` with `evaluacion`: `dolor_post_cita` if linked to a recent procedure/visit; "
            "`dolor_intenso` for severe pain/urgency without a recent procedure tie-in.\n"
            "- In `agendar_cita` pass `servicio`=`evaluacion` and `suffix_urgencia` as above; omit `suffix_urgencia` for ordinary bookings.\n"
        )
    return (
        "\n\n[DOLOR / URGENCIA – Úsalo cuando el paciente mencione dolor dental, molestia, inflamación, "
        "**urgente/urgencia**, **emergencia**, quiera ser atendido **hoy** o similar. "
        "NO lo uses en citas genéricas (solo día/hora sin urgencia), saludos ni charla.]\n"
        "- Responde con empatía breve y tranquiliza: el equipo de la clínica le ayudará.\n"
        "- **Política mismo día:** no hay citas que inicien hoy (hora El Salvador UTC-6); lo más pronto es **desde mañana**. "
        "Si piden hoy, explícalo con empatía antes de ofrecer horarios.\n"
        "- Prioriza cita de **evaluación**: usa el id de catálogo `evaluacion` (o el servicio evaluación=sí acordado) "
        "salvo que elija otro servicio con claridad; confirma que acepta evaluación antes de agendar.\n"
        "- No derives a humano por este escenario si la evaluación está en catálogo.\n"
        "- Llama `consultar_primer_dia_disponible` (max_dias 1–30, por defecto 14) para el **primer día con huecos desde mañana**.\n"
        "- Ofrece solo las horas de `primeras_tres_horas` (máximo tres HH:00) salvo que pida más.\n"
        "- `suffix_urgencia` con `evaluacion`: `dolor_post_cita` si el dolor va ligado a procedimiento/cita reciente; "
        "`dolor_intenso` si es dolor fuerte o urgencia sin atarlo a un procedimiento reciente.\n"
        "- En `agendar_cita` incluye `servicio`=`evaluacion` y `suffix_urgencia` según el caso; en citas normales no envíes `suffix_urgencia`.\n"
    )
