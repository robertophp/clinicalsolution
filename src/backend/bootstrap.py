from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from .config import settings
from .database import SessionLocal
from .repositories import (
    CITA_STATUS_CANCELADA,
    CITA_STATUS_REAGENDADA,
    TRANSFERENCIA_ESTADO_PENDIENTE_RESUMEN,
    TRANSFERENCIA_ESTADO_TRANSFERIDO,
    create_cita,
    get_latest_activa_cita_for_phone,
    get_latest_cita_for_phone,
    update_cita_status,
    update_latest_cita_transferencia_estado,
)
from .services.gemini_service import GeminiService, GeminiServiceError
from .services.conversation_memory import ConversationMemoryService
from .services.intent_classifier import Intent, classify_intent
from .services.intent_llm_service import llm_classify_intent
from .services.calendar_sync_service import run_calendar_to_bigquery_sync
from .services.human_transfer_service import (
    build_specialist_derivation_message,
    classify_patient_summary_response,
    detect_human_transfer_need,
    format_patient_phone_display,
    generate_transfer_summary,
    parse_specialist_whatsapp_recipient,
    patient_prompt_after_transfer,
    patient_prompt_confirm_summary,
    patient_prompt_transfer_send_failed,
    patient_prompt_transfer_not_configured,
    patient_prompt_unclear_confirmation,
)
from .services.human_transfer_topics import topics_for_clinic_keys
from .services.meta_whatsapp_service import (
    extract_incoming_whatsapp_events,
    send_text_message,
    send_text_message_sync,
    verify_webhook_signature,
)
from .services.whatsapp_media_replies import reply_for_meta_media_type, reply_for_twilio_media
from .schemas import ClinicConfig

from .domain.catalog import (
    _format_services_catalog_for_prompt,
    _service_display_label,
    _services_for_clinic,
)
from .domain.citas_handlers import (
    _handle_agendar_cita,
    _handle_cancelar_cita,
    _handle_reagendar_cita,
    set_conversation_memory_for_cita_handlers,
)
from .domain.clinics_config import build_whatsapp_phone_number_id_map, load_clinics_config
from .domain.clinics_state import init_clinics_by_id
from .domain.disponibilidad import (
    _handle_consultar_disponibilidad,
    _handle_consultar_primer_dia_disponible,
)
from .domain.language import _detect_language
from .domain.prompt_clinic import (
    _build_transfer_resolution_context,
    _format_clinic_location_for_prompt,
    _format_opening_hours_for_prompt,
    _format_payment_methods_for_prompt,
    _format_urgency_dolor_prompt_block,
)
from .domain.urgency_calendar import _calendar_suffix_label_for_cita
from .domain.wa_normalization import _normalize_wa_id_for_storage

# Asegurar que los logs (y tracebacks) se vean en la consola de uvicorn
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
    stream=sys.stderr,
    force=True,
)


BASE_DIR = Path(__file__).resolve().parent
CLINICS_FILE = BASE_DIR / "data" / "clinics_mock.json"

try:
    CLINICS_BY_ID = load_clinics_config(CLINICS_FILE)
except Exception as exc:  # noqa: BLE001
    raise RuntimeError("Error cargando la configuración de clínicas.") from exc

init_clinics_by_id(CLINICS_BY_ID)

WHATSAPP_PHONE_NUMBER_ID_TO_CLINIC = build_whatsapp_phone_number_id_map(CLINICS_BY_ID)

gemini_service = GeminiService(
    project_id=settings.PROJECT_ID,
    location=settings.LOCATION,
)
conversation_memory = ConversationMemoryService(project_id=settings.PROJECT_ID)
set_conversation_memory_for_cita_handlers(conversation_memory)


def _build_chat_history_with_memory(
    clinic_id: str,
    from_number: str,
    body: str,
) -> List[Dict[str, str]]:
    """
    Builds chat_history: last N messages from Firestore (within TTL) + current user message.
    """
    history = conversation_memory.get_recent_messages(clinic_id, from_number)
    current = {"role": "user", "content": f"De: {from_number}. Mensaje: {body}"}
    return [*history, current]


def _generate_and_persist_reply(
    clinic_id: str,
    from_number: str,
    body: str,
    system_prompt: str,
    clinic_name: str,
    assistant_name: str = "Asistente Virtual",
    system_prompt_en: str | None = None,
) -> str:
    """
    Recupera historial, construye system instruction (clínica + primer mensaje vs conversacional),
    llama a Gemini con system primero e historial después, persiste y devuelve la respuesta.

    Si aplica la derivación a especialista humano, puede llamar a Gemini solo para clasificación/resumen
    y enviar WhatsApp al especialista de forma síncrona vía Graph API antes de responder al paciente.
    """
    history = conversation_memory.get_recent_messages(clinic_id, from_number)
    is_first_message = len(history) == 0

    # Metadata ligera: idioma de conversación y nombre del paciente (si ya se conoce)
    metadata = conversation_memory.get_metadata(clinic_id, from_number) or {}
    stored_first_name: str | None = None
    if isinstance(metadata, dict):
        stored_first_name = (metadata.get("patient_first_name") or None)  # Firestore

    # Si no tenemos nombre en memoria pero ya existen citas previas en BigQuery,
    # intentamos recuperar el nombre del paciente a partir del teléfono y la clínica.
    if not stored_first_name:
        try:
            db = SessionLocal()
            try:
                cita = get_latest_cita_for_phone(db, clinic_id=clinic_id, telefono=from_number)
            finally:
                db.close()
            if cita and (cita.paciente_nombre or "").strip():
                full_name = cita.paciente_nombre.strip()
                parts = full_name.split()
                fn = parts[0] if parts else full_name
                stored_first_name = fn[:1].upper() + fn[1:].lower()
                # Persistir en memoria para futuros turnos
                try:
                    conversation_memory.set_patient_name(clinic_id, from_number, full_name)
                except Exception:
                    pass
        except Exception:
            # Si BigQuery falla, no rompemos el flujo de conversación.
            stored_first_name = stored_first_name

    # Si tenemos primer nombre pero no nombre completo (o solo una palabra), intentar obtener nombre completo de BigQuery para el prompt.
    if stored_first_name and isinstance(metadata, dict):
        stored_full = (metadata.get("patient_name") or "").strip()
        if not stored_full or len(stored_full.split()) < 2:
            try:
                db = SessionLocal()
                try:
                    cita = get_latest_cita_for_phone(db, clinic_id=clinic_id, telefono=from_number)
                    if cita and (cita.paciente_nombre or "").strip():
                        full_bq = cita.paciente_nombre.strip()
                        if len(full_bq.split()) > 1:
                            metadata = dict(metadata) if metadata else {}
                            metadata["patient_name"] = full_bq
                            try:
                                conversation_memory.set_patient_name(clinic_id, from_number, full_bq)
                            except Exception:
                                pass
                finally:
                    db.close()
            except Exception:
                pass

    # Idioma de la conversación: si hay historial reciente, reutilizamos el que haya en Firestore.
    # Si no hay historial (nueva sesión) o falta el dato, usamos langdetect y lo persistimos.
    language: str
    if not is_first_message:
        if isinstance(metadata, dict):
            stored_lang = metadata.get("conversation_language")
        else:
            stored_lang = None
        if stored_lang in {"es", "en"}:
            language = stored_lang  # continuar sesión en el mismo idioma
        else:
            language = _detect_language(body)
            conversation_memory.set_conversation_language(clinic_id, from_number, language)
    else:
        language = _detect_language(body)
        conversation_memory.set_conversation_language(clinic_id, from_number, language)

    clinic_cfg = CLINICS_BY_ID.get(clinic_id)

    # --- Derivación a especialista: confirmación del resumen (antes de guardrails de dominio) ---
    human_phase = (metadata.get("human_transfer_phase") or "none") if isinstance(metadata, dict) else "none"
    stored_transfer_summary = (metadata.get("human_transfer_summary") or "").strip() if isinstance(metadata, dict) else ""
    if human_phase == "awaiting_summary" and stored_transfer_summary:
        decision = classify_patient_summary_response(
            gemini_service,
            patient_message=body,
            language=language,
            current_summary=stored_transfer_summary,
        )
        if decision == "approve":
            specialist_digits = parse_specialist_whatsapp_recipient(
                getattr(clinic_cfg, "specialist_whatsapp", None) if clinic_cfg else None
            )
            token = (settings.META_WHATSAPP_ACCESS_TOKEN or "").strip()
            phone_nid = (getattr(clinic_cfg, "whatsapp_phone_number_id", None) or "").strip() if clinic_cfg else ""

            stored_full_name_tr = (metadata.get("patient_name") or "").strip() if isinstance(metadata, dict) else ""
            patient_name_tr = stored_full_name_tr or (stored_first_name or "").strip() or "Paciente"

            specialist_body = build_specialist_derivation_message(
                patient_name=patient_name_tr,
                patient_phone_display=format_patient_phone_display(from_number),
                summary=stored_transfer_summary,
            )
            sent_ok = False
            if specialist_digits and token and phone_nid:
                try:
                    send_text_message_sync(
                        graph_version=settings.META_GRAPH_API_VERSION,
                        phone_number_id=phone_nid,
                        to_wa_id=specialist_digits,
                        body=specialist_body,
                        access_token=token,
                    )
                    sent_ok = True
                except Exception:
                    logging.exception("Error enviando derivación al especialista por WhatsApp")

            conversation_memory.add_message(clinic_id, from_number, "user", body)
            if sent_ok:
                try:
                    conversation_memory.clear_human_transfer(clinic_id, from_number)
                except Exception:
                    logging.warning("No se pudo limpiar estado human_transfer en Firestore", exc_info=True)
                try:
                    db = SessionLocal()
                    try:
                        update_latest_cita_transferencia_estado(
                            db,
                            clinic_id=clinic_id,
                            telefono=from_number,
                            estado=TRANSFERENCIA_ESTADO_TRANSFERIDO,
                        )
                    finally:
                        db.close()
                except Exception:
                    logging.warning("BigQuery transferencia_estado=transferido no actualizado", exc_info=True)
                reply_text = patient_prompt_after_transfer(language)
            elif not specialist_digits:
                try:
                    conversation_memory.clear_human_transfer(clinic_id, from_number)
                except Exception:
                    pass
                reply_text = patient_prompt_transfer_not_configured(language)
            else:
                reply_text = patient_prompt_transfer_send_failed(language)

            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

        if decision == "revise":
            new_summary = generate_transfer_summary(
                gemini_service,
                history=history,
                current_message=body,
                language=language,
                detection=None,
                patient_correction=body,
                previous_summary=stored_transfer_summary,
            )
            try:
                conversation_memory.update_human_transfer_summary(
                    clinic_id, from_number, summary=new_summary
                )
            except Exception:
                logging.warning("No se pudo actualizar el resumen human_transfer", exc_info=True)
            try:
                db = SessionLocal()
                try:
                    update_latest_cita_transferencia_estado(
                        db,
                        clinic_id=clinic_id,
                        telefono=from_number,
                        estado=TRANSFERENCIA_ESTADO_PENDIENTE_RESUMEN,
                    )
                finally:
                    db.close()
            except Exception:
                logging.warning("BigQuery transferencia_estado pendiente no actualizado", exc_info=True)

            reply_text = patient_prompt_confirm_summary(new_summary, language)
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

        reply_text = patient_prompt_unclear_confirmation(language)
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    # Guardrails de dominio por clínica: clasificar intención y, solo si es fuera de dominio, responder sin llamar a Gemini.
    try:
        # Primero intentamos clasificar con LLM; si falla, hacemos fallback a reglas.
        intent = llm_classify_intent(
            gemini=gemini_service,
            message=body,
            language=language,
            history=history,
        )
    except Exception:
        try:
            intent = classify_intent(body, language, history)
        except Exception:
            intent = Intent.OUT_OF_DOMAIN

    if intent is Intent.OUT_OF_DOMAIN:
        if language == "en":
            reply_text = (
                "I'm sorry 😔, that's outside what I can help with. "
                "I'm focused on this dental clinic: appointments, treatments, prices and opening hours. "
                "If you want, I can help you with a dental question or to book or manage an appointment here."
            )
        else:
            reply_text = (
                "Lo siento 😔, eso está fuera de lo que puedo hacer. "
                "Estoy enfocado en esta clínica dental: citas, tratamientos, precios y horarios. "
                "Si quieres, con gusto te ayudo con una duda dental o a agendar o gestionar tu cita aquí."
            )
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    # --- Derivación a especialista: detección de tema sensible (solo si hay número de especialista) ---
    if clinic_cfg:
        spec_digits = parse_specialist_whatsapp_recipient(getattr(clinic_cfg, "specialist_whatsapp", None))
        if spec_digits:
            topics = topics_for_clinic_keys(getattr(clinic_cfg, "human_transfer_topic_keys", None))
            detection = detect_human_transfer_need(
                gemini_service,
                message=body,
                history=history,
                language=language,
                topics=topics,
                resolution_context=_build_transfer_resolution_context(clinic_cfg, language),
            )
            if detection:
                summary = generate_transfer_summary(
                    gemini_service,
                    history=history,
                    current_message=body,
                    language=language,
                    detection=detection,
                )
                try:
                    conversation_memory.set_human_transfer_awaiting_summary(
                        clinic_id,
                        from_number,
                        summary=summary,
                        categories=list(detection.matched_topics),
                    )
                except Exception:
                    logging.warning("No se pudo guardar estado human_transfer", exc_info=True)
                try:
                    db = SessionLocal()
                    try:
                        update_latest_cita_transferencia_estado(
                            db,
                            clinic_id=clinic_id,
                            telefono=from_number,
                            estado=TRANSFERENCIA_ESTADO_PENDIENTE_RESUMEN,
                        )
                    finally:
                        db.close()
                except Exception:
                    logging.warning("BigQuery transferencia_estado pendiente no actualizado", exc_info=True)

                reply_text = patient_prompt_confirm_summary(summary, language)
                conversation_memory.add_message(clinic_id, from_number, "user", body)
                conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
                return reply_text

    # Fecha y hora actual como referencia (hora local El Salvador, UTC-6; sin dependencia de tzdata/zoneinfo)
    tz_salvador = timezone(timedelta(hours=-6))
    now_local = datetime.now(tz_salvador)
    _dias = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
    _meses = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
    dia_semana = _dias[now_local.weekday()]
    mes = _meses[now_local.month - 1]
    fecha_ref_iso = now_local.strftime("%Y-%m-%d")
    hora_ref_iso = now_local.strftime("%H:%M")
    referencia_fecha = (
        f"\n\n[FECHA Y HORA DE REFERENCIA (usa esto como 'hoy' y 'ahora', hora El Salvador UTC-6): "
        f"Hoy es {dia_semana} {now_local.day} de {mes} de {now_local.year}. "
        f"Fecha de referencia en YYYY-MM-DD: {fecha_ref_iso}. "
        f"Hora actual de referencia HH:MM: {hora_ref_iso}. "
        "Cuando el usuario diga 'próximo jueves', 'mañana', 'el lunes', etc., calcula la fecha correcta a partir de esta fecha de referencia y pasa a la herramienta en YYYY-MM-DD y HH:00 (solo horas en punto).]\n"
    )

    # Siempre incluir nombre y clínica en el contexto para que el asistente responda correctamente en cualquier turno.
    stored_full_name: str | None = None
    if isinstance(metadata, dict):
        stored_full_name = (metadata.get("patient_name") or "").strip() or None
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

    # Elegir prompt base según idioma detectado (ES/EN)
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

    # Inyectar horarios de la clínica y regla estricta para no crear falsas expectativas
    clinic_cfg = CLINICS_BY_ID.get(clinic_id)
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

    # Inyectar catálogo de servicios para que el modelo sepa precios, disponibilidad y pueda pedir el tipo de cita
    catalog_text = _format_services_catalog_for_prompt(_services_for_clinic(clinic_id), language)
    if catalog_text:
        system_prompt_effective = system_prompt_effective + catalog_text

    system_prompt_effective = system_prompt_effective + _format_urgency_dolor_prompt_block(language)

    # Instrucción para herramientas de citas (agendar, cancelar, reagendar)
    if language == "en":
        tool_instruction = (
            "\n\n[You have five appointment-related tools. The clinic is from context (do not ask the user). "
            "BOOKING uses El Salvador local date (UTC-6). Same-day appointments are NOT allowed; earliest day is tomorrow. "
            "(0) consultar_disponibilidad(fecha): REQUIRED before you list or suggest specific appointment start times for a **known** day. "
            "fecha in YYYY-MM-DD (use REFERENCE DATE above for 'Monday', 'tomorrow', etc.). "
            "The response includes horas_disponibles (HH:00 strings from Google Calendar when sync is on). "
            "You MUST only mention times that appear in horas_disponibles; never invent or guess slots. "
            "If the patient changes the day, call consultar_disponibilidad again for the new date. "
            "(1) consultar_primer_dia_disponible(max_dias optional): finds the **first calendar day from tomorrow** with at least one free slot, "
            "scanning up to max_dias days (default 14, max 30). Returns fecha, horas_disponibles, and primeras_tres_horas. "
            "Use for pain/urgency flows (see PAIN / URGENCY block above). "
            "(2) agendar_cita(nombre, fecha, hora, servicio, suffix_urgencia optional): for new appointments. "
            "Only pass date and time that follow the BOOKING START TIMES rules above (60-minute visits; last start is 1 hour before closing in each block). "
            "Time must be on the hour only (HH:00). "
            "If the patient asks for an impossible time (e.g. Sunday, or a start at the closing hour such as 17:00 when the block ends at 17:00), do NOT call the tool: explain that the last bookable start is one hour before closing and ask them to pick a valid on-the-hour time. "
            "'servicio' must be the exact 'id' string from the SERVICES CATALOG above (do not invent ids). "
            "Optional suffix_urgencia: only with servicio=evaluacion for pain flows: dolor_post_cita or dolor_intenso (see PAIN / URGENCY block). "
            "If you already know the patient, use their full name and do not ask. Only ask for name if the appointment is for someone else. "
            "(3) cancelar_cita(): no parameters. Use when the user asks to cancel their appointment. "
            "(4) reagendar_cita(fecha, hora, servicio optional, suffix_urgencia optional): when they want to change date/time. Only with date/time within opening hours and with time in HH:00. "
            "Date as YYYY-MM-DD and time as HH:00; use the REFERENCE DATE AND TIME above for 'tomorrow', 'next Friday', etc. "
            "For agendar_cita, cancelar_cita, and reagendar_cita: if the tool returns a 'mensaje' field, use that text for the user. "
            "For consultar_disponibilidad and consultar_primer_dia_disponible there is usually no 'mensaje': summarize slots in natural language. "
            "Never show the patient source code, print(, default_api, or function names with parentheses—use tools or plain language only.]"
        )
    else:
        tool_instruction = (
            "\n\n[Tienes cinco herramientas relacionadas con citas. La clínica se toma del contexto (no la pidas al usuario). "
            "El agendado usa la fecha local de El Salvador (UTC-6). No hay citas para el mismo día; el primer día posible es mañana. "
            "(0) consultar_disponibilidad(fecha): OBLIGATORIA antes de listar u ofrecer horas concretas de inicio de cita para un día **ya elegido**. "
            "fecha en YYYY-MM-DD (usa la FECHA DE REFERENCIA de arriba para 'el lunes', 'mañana', etc.). "
            "La respuesta trae horas_disponibles (cadenas HH:00; con sincronización activa vienen de Google Calendar). "
            "SOLO puedes mencionar horas que aparezcan en horas_disponibles; nunca inventes ni completes la lista por tu cuenta. "
            "Si el paciente cambia de día, vuelve a llamar consultar_disponibilidad con la nueva fecha. "
            "(1) consultar_primer_dia_disponible(max_dias opcional): encuentra el **primer día calendario desde mañana** con al menos un hueco, "
            "revisando hasta max_días días (por defecto 14, máximo 30). Devuelve fecha, horas_disponibles y primeras_tres_horas. "
            "Úsala en flujos de dolor/urgencia (ver bloque DOLOR / URGENCIA arriba). "
            "(2) agendar_cita(nombre, fecha, hora, servicio, suffix_urgencia opcional): para citas nuevas. "
            "Solo pases fecha y hora que cumplan las reglas de 'HORARIOS PARA INICIAR UNA CITA' de arriba (visitas de 60 min; la última hora de inicio es 1 hora antes del cierre de cada bloque). "
            "La hora debe ir solo en punto (HH:00). "
            "Si el paciente pide un horario imposible (ej. domingo, o iniciar a la hora de cierre como 17:00 si el bloque cierra a las 17:00), NO llames la herramienta: explica que la última cita del día inicia una hora antes del cierre y pídele una hora válida en punto. "
            "El parámetro 'servicio' debe ser el 'id' exacto de uno de los servicios del catálogo de arriba (no inventes ids). "
            "Opcional suffix_urgencia: solo con servicio=evaluacion en flujos de dolor: dolor_post_cita o dolor_intenso (ver bloque DOLOR / URGENCIA). "
            "Si ya conoces al paciente, usa su nombre completo y no preguntes. Solo pregunta el nombre si la cita es para otra persona. "
            "(3) cancelar_cita(): sin parámetros. Úsala cuando el usuario pida cancelar su cita (ej. 'quiero cancelar mi cita', 'cancela mi reserva'). "
            "(4) reagendar_cita(fecha, hora, servicio opcional, suffix_urgencia opcional): cuando pida cambiar la fecha/hora de su cita. Solo con fecha/hora dentro del horario de atención y hora en HH:00. "
            "La fecha en YYYY-MM-DD y hora en HH:00; usa la FECHA Y HORA DE REFERENCIA de arriba para calcular 'mañana', 'próximo viernes', etc. "
            "Para fechas relativas (mañana, próximo lunes, etc.) usa SIEMPRE la referencia indicada arriba y pasa a la herramienta en YYYY-MM-DD y HH:00. "
            "Para agendar_cita, cancelar_cita y reagendar_cita: si la herramienta devuelve el campo 'mensaje', responde con ese texto al usuario. "
            "Para consultar_disponibilidad y consultar_primer_dia_disponible normalmente no hay 'mensaje': resume las horas en lenguaje natural. "
            "Nunca muestres al paciente código, print(, default_api ni nombres de funciones con paréntesis: usa las herramientas o lenguaje natural.]"
        )
    system_prompt_effective = system_prompt_effective.strip() + tool_instruction

    chat_history = _build_chat_history_with_memory(clinic_id, from_number, body)

    def tool_handler(name: str, args: dict) -> dict:
        if name == "consultar_disponibilidad":
            return _handle_consultar_disponibilidad(clinic_id=clinic_id, language=language, args=args)
        if name == "consultar_primer_dia_disponible":
            return _handle_consultar_primer_dia_disponible(clinic_id=clinic_id, language=language, args=args)
        if name == "agendar_cita":
            return _handle_agendar_cita(
                from_number=from_number,
                clinic_id=clinic_id,
                language=language,
                assistant_name=assistant_name,
                args=args,
            )
        if name == "cancelar_cita":
            return _handle_cancelar_cita(from_number=from_number, clinic_id=clinic_id, language=language)
        if name == "reagendar_cita":
            return _handle_reagendar_cita(
                from_number=from_number,
                clinic_id=clinic_id,
                language=language,
                assistant_name=assistant_name,
                args=args,
            )
        return {"error": "Herramienta desconocida", "mensaje": "No pude completar la acción."}

    reply_text = gemini_service.generate_reply_with_tools(
        system_prompt=system_prompt_effective,
        chat_history=chat_history,
        tool_handler=tool_handler,
        reply_language=language,
    )
    conversation_memory.add_message(clinic_id, from_number, "user", body)
    conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
    return reply_text


def _resolve_whatsapp_reply_language(
    clinic_id: str,
    from_number: str,
    *,
    hint_text: str | None = None,
) -> str:
    """es | en para plantillas de medio; reutiliza idioma de conversación o detección."""
    metadata = conversation_memory.get_metadata(clinic_id, from_number) or {}
    stored = metadata.get("conversation_language") if isinstance(metadata, dict) else None
    if stored in ("es", "en"):
        return stored
    if hint_text and hint_text.strip():
        return _detect_language(hint_text)
    return "es"


