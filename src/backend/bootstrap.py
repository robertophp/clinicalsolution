from __future__ import annotations

import json
import logging
import sys
import time
import traceback
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
from .services.intent_classifier import (
    Intent,
    classify_intent,
    extract_knowledge_base_topics,
    knowledge_base_service_keywords,
)
from .services.intent_llm_service import llm_classify_intent
from .services.calendar_sync_service import run_calendar_to_bigquery_sync
from .services.human_transfer_service import (
    HumanTransferDetection,
    build_specialist_derivation_message,
    classify_patient_summary_response,
    detect_human_transfer_need,
    format_patient_phone_display,
    generate_transfer_summary,
    parse_specialist_whatsapp_recipient,
    patient_prompt_after_transfer,
    patient_prompt_confirm_summary,
    patient_prompt_decline_transfer,
    patient_prompt_transfer_send_failed,
    patient_prompt_transfer_not_configured,
    patient_prompt_unclear_confirmation,
)
from .services.human_transfer_topics import resolve_transfer_topics_for_clinic
from .services.meta_whatsapp_service import (
    extract_incoming_whatsapp_events,
    send_text_message,
    send_text_message_sync,
    verify_webhook_signature,
)
from .services.whatsapp_media_replies import reply_for_meta_media_type, reply_for_twilio_media

from .domain.conversation_prompt import build_conversation_system_prompt
from .domain.booking_confirmation import (
    assistant_asks_booking_confirm,
    classify_booking_confirm_response,
    extract_pending_booking_from_conversation,
    normalize_pending_booking_args,
    patient_prompt_booking_declined,
    patient_prompt_booking_unclear,
)
from .domain.citas_handlers import (
    _handle_agendar_cita,
    _handle_cancelar_cita,
    _handle_listar_mis_citas_proximas,
    _handle_reagendar_cita,
    set_conversation_memory_for_cita_handlers,
)
from .domain.clinic_loader import CLINIC_POLICIES_BY_ID, load_clinic_tree
from .domain.clinics_config import build_whatsapp_phone_number_id_map
from .domain.runtime_env import resolve_whatsapp_phone_number_id_for_specialist
from .domain.clinics_state import init_clinics_by_id
from .domain.disponibilidad import (
    _handle_consultar_disponibilidad,
    _handle_consultar_primer_dia_disponible,
)
from .domain.language import _detect_language
from .domain.reply_booking_guard import claims_booking_saved_without_backend, fallback_ask_explicit_confirm
from .domain.prompt_clinic import (
    _build_transfer_resolution_context,
)
from .domain.human_contact_signals import message_signals_human_contact_request
from .domain.escalation_signals import message_signals_complaint_or_fiscal
from .domain.urgency_signals import message_signals_urgency
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
CLINICS_ROOT = BASE_DIR / "data" / "clinics"

try:
    CLINICS_BY_ID = load_clinic_tree(CLINICS_ROOT)
except Exception as exc:  # noqa: BLE001
    raise RuntimeError("Error cargando la configuración de clínicas desde data/clinics/.") from exc

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
    skip_transfer_detection = False
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
            phone_nid = resolve_whatsapp_phone_number_id_for_specialist(clinic_cfg)

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
                    logging.info(
                        "Derivación enviada al especialista clinic=%s to=%s phone_number_id=%s",
                        clinic_id,
                        specialist_digits,
                        phone_nid,
                    )
                except Exception:
                    logging.exception(
                        "Error enviando derivación al especialista por WhatsApp "
                        "(clinic=%s to=%s phone_number_id=%s)",
                        clinic_id,
                        specialist_digits,
                        phone_nid,
                    )
            else:
                logging.warning(
                    "Derivación no enviada: specialist=%s token=%s phone_nid=%s",
                    bool(specialist_digits),
                    bool(token),
                    bool(phone_nid),
                )

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

        if decision == "decline":
            try:
                conversation_memory.clear_human_transfer(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar estado human_transfer tras rechazo", exc_info=True)
            skip_transfer_detection = True
            reply_text = patient_prompt_decline_transfer(language)
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

        reply_text = patient_prompt_unclear_confirmation(language)
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    # --- Confirmación de agendado: sí/confirmo ejecuta agendar_cita en backend ---
    booking_phase = (metadata.get("booking_phase") or "none") if isinstance(metadata, dict) else "none"
    booking_pending_raw = metadata.get("booking_pending") if isinstance(metadata, dict) else None
    booking_pending: dict[str, str] | None = None
    if isinstance(booking_pending_raw, dict):
        booking_pending = normalize_pending_booking_args(booking_pending_raw)

    if booking_phase == "awaiting_confirm" and booking_pending:
        booking_decision = classify_booking_confirm_response(body, language)
        if booking_decision == "approve":
            out = _handle_agendar_cita(
                from_number=from_number,
                clinic_id=clinic_id,
                language=language,
                assistant_name=assistant_name,
                args=booking_pending,
            )
            try:
                conversation_memory.clear_booking_pending(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar booking_pending tras agendar", exc_info=True)
            reply_text = (
                str(out.get("mensaje")).strip()
                if isinstance(out, dict) and out.get("mensaje")
                else patient_prompt_booking_unclear(language)
            )
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

        if booking_decision == "decline":
            try:
                conversation_memory.clear_booking_pending(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar booking_pending tras rechazo", exc_info=True)
            reply_text = patient_prompt_booking_declined(language)
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

        if booking_decision == "revise":
            try:
                conversation_memory.clear_booking_pending(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar booking_pending para revisión", exc_info=True)
        else:
            reply_text = patient_prompt_booking_unclear(language)
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

    # Guardrails de dominio por clínica: clasificar intención y, solo si es fuera de dominio, responder sin llamar a Gemini.
    # Los temas del manual de la clínica (knowledge_base) cuentan como dentro de dominio.
    clinic_knowledge_base = getattr(clinic_cfg, "knowledge_base", None) if clinic_cfg else None
    clinic_topics = extract_knowledge_base_topics(clinic_knowledge_base)
    clinic_service_keywords = knowledge_base_service_keywords(clinic_knowledge_base)
    # Reglas-primero: las reglas reconocen citas, servicios del catálogo y temas del manual.
    # Solo si las reglas NO reconocen el mensaje (OUT_OF_DOMAIN) consultamos al LLM, para no gastar
    # una llamada en los mensajes claros y, a la vez, no bloquear por error lo que las reglas no cubren.
    try:
        intent = classify_intent(
            body,
            language,
            history,
            extra_service_keywords=clinic_service_keywords,
        )
    except Exception:
        intent = Intent.OUT_OF_DOMAIN

    if intent is Intent.OUT_OF_DOMAIN:
        try:
            intent = llm_classify_intent(
                gemini=gemini_service,
                message=body,
                language=language,
                history=history,
                clinic_topics=clinic_topics,
            )
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
    # Contacto humano explícito → derivación (prioridad sobre urgencia/dolor; no agendar citas).
    # Urgencia/dolor sin pedido de humano → flujo de citas, no derivación en este turno.
    force_human_contact = message_signals_human_contact_request(body)
    skip_transfer_for_urgency = message_signals_urgency(body) and not force_human_contact
    # Compuerta de ahorro: si la intención es de rutina (servicios/cita/info/small talk) y no hay
    # señales de contacto humano, queja o tema fiscal, omitimos la llamada LLM de detección de
    # derivación. Las escalaciones por contacto humano (heurística) y por queja/fiscal (heurística)
    # se preservan; solo se omite el chequeo cuando claramente no aporta.
    routine_intents = (
        Intent.SERVICIOS,
        Intent.CITA,
        Intent.SEGUIMIENTO_CITA,
        Intent.CLINICA_INFO,
        Intent.SMALL_TALK,
    )
    skip_transfer_for_clear_intent = (
        intent in routine_intents
        and not force_human_contact
        and not message_signals_complaint_or_fiscal(body)
    )
    if force_human_contact:
        logging.info(
            "Derivación forzada por solicitud de contacto humano (clinic_id=%s)",
            clinic_id,
        )
    elif skip_transfer_for_urgency:
        logging.info(
            "Derivación omitida por señal de urgencia/dolor (clinic_id=%s)",
            clinic_id,
        )
    elif skip_transfer_for_clear_intent:
        logging.info(
            "Derivación omitida por intención clara sin señales de queja/fiscal/humano (clinic_id=%s, intent=%s)",
            clinic_id,
            intent.value,
        )
    if (
        clinic_cfg
        and not skip_transfer_detection
        and not skip_transfer_for_urgency
        and not skip_transfer_for_clear_intent
    ):
        spec_digits = parse_specialist_whatsapp_recipient(getattr(clinic_cfg, "specialist_whatsapp", None))
        if spec_digits:
            topics = resolve_transfer_topics_for_clinic(
                clinic_id,
                getattr(clinic_cfg, "human_transfer_topic_keys", None),
            )
            detection: HumanTransferDetection | None
            if force_human_contact:
                detection = HumanTransferDetection(
                    matched_topics=("contacto_humano",),
                    brief_reason=(
                        "Explicit request to speak with a doctor or staff member"
                        if language == "en"
                        else "Solicitud explícita de hablar con doctor/a o encargado/a"
                    ),
                )
            else:
                detection = detect_human_transfer_need(
                    gemini_service,
                    message=body,
                    history=history,
                    language=language,
                    topics=topics,
                    resolution_context=_build_transfer_resolution_context(clinic_cfg, language),
                )
            if detection:
                try:
                    conversation_memory.clear_booking_pending(clinic_id, from_number)
                except Exception:
                    pass
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

    stored_full_name_for_prompt: str | None = None
    if isinstance(metadata, dict):
        stored_full_name_for_prompt = (metadata.get("patient_name") or "").strip() or None

    system_prompt_effective = build_conversation_system_prompt(
        language=language,
        clinic_id=clinic_id,
        clinic_name=clinic_name,
        assistant_name=assistant_name,
        system_prompt=system_prompt,
        system_prompt_en=system_prompt_en,
        is_first_message=is_first_message,
        stored_first_name=stored_first_name,
        stored_full_name=stored_full_name_for_prompt,
        clinics_by_id=CLINICS_BY_ID,
        policies=CLINIC_POLICIES_BY_ID.get(clinic_id),
    )

    chat_history = _build_chat_history_with_memory(clinic_id, from_number, body)

    mutation_tool_saved_ok = {"value": False}

    def tool_handler(name: str, args: dict) -> dict:
        if name == "consultar_disponibilidad":
            return _handle_consultar_disponibilidad(clinic_id=clinic_id, language=language, args=args)
        if name == "consultar_primer_dia_disponible":
            return _handle_consultar_primer_dia_disponible(clinic_id=clinic_id, language=language, args=args)
        if name == "listar_mis_citas_proximas":
            return _handle_listar_mis_citas_proximas(
                from_number=from_number,
                clinic_id=clinic_id,
                language=language,
            )
        if name == "agendar_cita":
            out = _handle_agendar_cita(
                from_number=from_number,
                clinic_id=clinic_id,
                language=language,
                assistant_name=assistant_name,
                args=args,
            )
            if isinstance(out, dict) and out.get("mensaje") and not out.get("error"):
                mutation_tool_saved_ok["value"] = True
                try:
                    conversation_memory.clear_booking_pending(clinic_id, from_number)
                except Exception:
                    pass
            return out
        if name == "cancelar_cita":
            out = _handle_cancelar_cita(from_number=from_number, clinic_id=clinic_id, language=language)
            if isinstance(out, dict) and out.get("mensaje") and not out.get("error"):
                mutation_tool_saved_ok["value"] = True
            return out
        if name == "reagendar_cita":
            out = _handle_reagendar_cita(
                from_number=from_number,
                clinic_id=clinic_id,
                language=language,
                assistant_name=assistant_name,
                args=args,
            )
            if isinstance(out, dict) and out.get("mensaje") and not out.get("error"):
                mutation_tool_saved_ok["value"] = True
            return out
        return {"error": "Herramienta desconocida", "mensaje": "No pude completar la acción."}

    reply_text = gemini_service.generate_reply_with_tools(
        system_prompt=system_prompt_effective,
        chat_history=chat_history,
        tool_handler=tool_handler,
        reply_language=language,
    )
    if not mutation_tool_saved_ok["value"] and claims_booking_saved_without_backend(
        reply_text, language=language
    ):
        logging.warning(
            "Respuesta afirma cita guardada sin herramienta exitosa (clinic_id=%s); se pide confirmación explícita.",
            clinic_id,
        )
        reply_text = fallback_ask_explicit_confirm(language)

    stored_full_name_for_pending = (metadata.get("patient_name") or "").strip() if isinstance(metadata, dict) else ""
    default_patient_name = stored_full_name_for_pending or (stored_first_name or "").strip() or "Paciente"
    if (
        not mutation_tool_saved_ok["value"]
        and assistant_asks_booking_confirm(reply_text)
    ):
        pending_extracted = extract_pending_booking_from_conversation(
            gemini_service,
            history=history,
            assistant_confirmation_message=reply_text,
            language=language,
            clinic_id=clinic_id,
            default_patient_name=default_patient_name,
        )
        if pending_extracted:
            try:
                conversation_memory.set_booking_awaiting_confirm(
                    clinic_id,
                    from_number,
                    pending=pending_extracted,
                )
            except Exception:
                logging.warning("No se pudo guardar booking_pending en Firestore", exc_info=True)

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


