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
    CANAL_META,
    CITA_STATUS_CANCELADA,
    CITA_STATUS_REAGENDADA,
    ROL_USER,
    TRANSFERENCIA_ESTADO_PENDIENTE_RESUMEN,
    TRANSFERENCIA_ESTADO_TRANSFERIDO,
    create_cita,
    get_latest_activa_cita_for_phone,
    get_latest_self_cita_for_phone,
    log_mensaje,
    update_cita_status,
    update_latest_cita_transferencia_estado,
)
from .services.gemini_service import GeminiService, GeminiServiceError
from .services.conversation_memory import ConversationMemoryService
from .services.intent_classifier import (
    Intent,
    classify_intent,
    extract_knowledge_base_topics,
    is_contextual_offer_acceptance,
    knowledge_base_service_keywords,
    should_fail_open_after_offer_reconfirm,
    should_reconfirm_after_booking_offer,
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
from .domain.booking_beneficiary import build_booking_beneficiary_hint
from .domain.booking_confirmation import (
    assistant_asks_booking_confirm,
    classify_booking_confirm_response,
    extract_pending_booking_from_conversation,
    normalize_pending_booking_args,
    patient_prompt_booking_declined,
    patient_prompt_booking_unclear,
    patient_prompt_offer_response_unclear,
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
from .domain.catalog import _service_display_label, catalog_intent_keywords, catalog_intent_topics
from .domain.cordales_requirement import (
    classify_patient_xray_response,
    message_signals_cordales_inquiry,
)
from .domain.pediatric_policy import (
    PediatricAgeResult,
    classify_pediatric_context,
    patient_prompt_pediatric_decline,
    pediatric_ineligibility_result,
)
from .domain.maxillofacial_policy import (
    MaxillofacialContextResult,
    classify_maxillofacial_context,
    patient_prompt_maxillofacial_followup,
    patient_prompt_maxillofacial_transfer_sent,
)
from .domain.emergency_fork_policy import (
    classify_emergency_choice_response,
    patient_prompt_emergency_choice,
    patient_prompt_emergency_choice_unclear,
    patient_prompt_emergency_followup,
    patient_prompt_emergency_transfer_sent,
    should_trigger_emergency_fork,
)
from .domain.same_day_fork_policy import (
    classify_same_day_choice_response,
    patient_prompt_same_day_choice,
    patient_prompt_same_day_choice_unclear,
    patient_prompt_same_day_followup,
    patient_prompt_same_day_transfer_sent,
    should_trigger_same_day_fork,
)
from .domain.patient_name_extraction import (
    assistant_asked_for_name,
    try_extract_name_correction,
    try_extract_patient_name,
)
from .domain.service_context import (
    detect_discussed_service,
    service_context_is_fresh,
)

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
    channel: str = CANAL_META,
) -> str:
    """
    Recupera historial, construye system instruction (clínica + primer mensaje vs conversacional),
    llama a Gemini con system primero e historial después, persiste y devuelve la respuesta.

    Si aplica la derivación a especialista humano, puede llamar a Gemini solo para clasificación/resumen
    y enviar WhatsApp al especialista de forma síncrona vía Graph API antes de responder al paciente.
    """
    # Métrica de dashboard: registrar el mensaje entrante del paciente (solo metadatos, fail-open).
    # Una fila por mensaje entrante => permite contar mensajes, personas únicas y ratio mensajes/cita.
    log_mensaje(
        clinic_id=clinic_id,
        telefono=from_number,
        rol=ROL_USER,
        canal=channel,
    )

    history = conversation_memory.get_recent_messages(clinic_id, from_number)
    is_first_message = len(history) == 0

    # Metadata ligera: idioma de conversación y nombre del paciente (si ya se conoce)
    metadata = conversation_memory.get_metadata(clinic_id, from_number) or {}
    stored_first_name: str | None = None
    name_collection_phase = "none"
    # True cuando el nombre viene de Firestore (actualizado por conversación);
    # False cuando se toma de BigQuery como fallback. Evita que BQ sobreescriba correcciones del usuario.
    name_from_firestore = False
    if isinstance(metadata, dict):
        stored_first_name = (metadata.get("patient_first_name") or None)  # Firestore
        name_collection_phase = (metadata.get("name_collection_phase") or "none").strip()
        if stored_first_name:
            name_collection_phase = "known"
            name_from_firestore = True

    # Si no tenemos nombre en memoria pero ya existen citas previas en BigQuery,
    # intentamos recuperar el nombre del paciente a partir del teléfono y la clínica.
    if not stored_first_name:
        try:
            db = SessionLocal()
            try:
                cita = get_latest_self_cita_for_phone(db, clinic_id=clinic_id, telefono=from_number)
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

    # Si tenemos primer nombre procedente de BQ (no corregido por el usuario en conversación)
    # y no hay nombre completo, intentar obtenerlo de BigQuery para el prompt.
    # Si el nombre vino de Firestore se respeta: el usuario puede haberlo corregido.
    if stored_first_name and not name_from_firestore and isinstance(metadata, dict):
        stored_full = (metadata.get("patient_name") or "").strip()
        if not stored_full or len(stored_full.split()) < 2:
            try:
                db = SessionLocal()
                try:
                    cita = get_latest_self_cita_for_phone(db, clinic_id=clinic_id, telefono=from_number)
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

    maxillo_followup_phase = (
        (metadata.get("maxillofacial_transfer_phase") or "none").strip()
        if isinstance(metadata, dict)
        else "none"
    )
    if maxillo_followup_phase == "awaiting_followup":
        reply_text = patient_prompt_maxillofacial_followup(language)
        try:
            conversation_memory.clear_maxillofacial_transfer(clinic_id, from_number)
        except Exception:
            logging.warning("No se pudo limpiar maxillofacial_transfer_phase", exc_info=True)
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    emergency_phase = (
        (metadata.get("emergency_phase") or "none").strip()
        if isinstance(metadata, dict)
        else "none"
    )
    if emergency_phase == "awaiting_followup":
        reply_text = patient_prompt_emergency_followup(language)
        try:
            conversation_memory.clear_emergency_fork(clinic_id, from_number)
        except Exception:
            logging.warning("No se pudo limpiar emergency_phase", exc_info=True)
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    same_day_phase = (
        (metadata.get("same_day_phase") or "none").strip()
        if isinstance(metadata, dict)
        else "none"
    )
    if same_day_phase == "awaiting_followup":
        reply_text = patient_prompt_same_day_followup(language)
        try:
            conversation_memory.clear_same_day_fork(clinic_id, from_number)
        except Exception:
            logging.warning("No se pudo limpiar same_day_phase", exc_info=True)
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    emergency_appointment_chosen = emergency_phase == "appointment_chosen"
    same_day_appointment_chosen = same_day_phase == "appointment_chosen"

    name_just_corrected = False

    # Corrección de nombre cuando ya hay uno guardado (typo, nombre completo, etc.)
    if stored_first_name:
        stored_full_for_correction = (
            (metadata.get("patient_name") or "").strip() if isinstance(metadata, dict) else ""
        )
        corrected_name = try_extract_name_correction(
            gemini_service,
            body,
            language,
            stored_name=stored_full_for_correction or stored_first_name,
            stored_first_name=stored_first_name,
        )
        if corrected_name:
            try:
                conversation_memory.set_patient_name(clinic_id, from_number, corrected_name)
                parts = corrected_name.split()
                fn = parts[0] if parts else corrected_name
                stored_first_name = fn[:1].upper() + fn[1:].lower()
                name_collection_phase = "known"
                name_just_corrected = True
                if isinstance(metadata, dict):
                    metadata = dict(metadata)
                    metadata["patient_name"] = corrected_name
                    metadata["patient_first_name"] = stored_first_name
            except Exception:
                pass

    # Recolección de nombre del mensaje actual (fase asked o nombre en primer mensaje)
    if not stored_first_name and name_collection_phase in {"asked", "none"}:
        extracted_name = try_extract_patient_name(gemini_service, body, language)
        if extracted_name:
            try:
                conversation_memory.set_patient_name(clinic_id, from_number, extracted_name)
                stored_first_name = extracted_name.split()[0]
                stored_first_name = stored_first_name[:1].upper() + stored_first_name[1:].lower()
                name_collection_phase = "known"
                if isinstance(metadata, dict):
                    metadata = dict(metadata)
                    metadata["patient_name"] = extracted_name
                    metadata["patient_first_name"] = stored_first_name
            except Exception:
                pass
        elif name_collection_phase == "asked":
            try:
                conversation_memory.set_name_collection_phase(clinic_id, from_number, "skipped")
                name_collection_phase = "skipped"
            except Exception:
                pass

    # Detectar servicio mencionado y persistir contexto
    last_discussed_service_id: str | None = None
    last_discussed_service_at = None
    if isinstance(metadata, dict):
        last_discussed_service_id = (metadata.get("last_discussed_service_id") or "").strip() or None
        last_discussed_service_at = metadata.get("last_discussed_service_at")

    detected_service = detect_discussed_service(body, clinic_id)
    if detected_service:
        try:
            conversation_memory.set_last_discussed_service(clinic_id, from_number, detected_service)
            last_discussed_service_id = detected_service
            last_discussed_service_at = None  # recién actualizado
        except Exception:
            pass

    last_discussed_service_name: str | None = None
    if last_discussed_service_id and (detected_service or service_context_is_fresh(last_discussed_service_at)):
        label = _service_display_label(clinic_id, last_discussed_service_id, language)
        if label and label != last_discussed_service_id:
            last_discussed_service_name = label

    clinic_policies = CLINIC_POLICIES_BY_ID.get(clinic_id)
    cordales_policy = (
        clinic_policies.cordales_panoramic_requirement if clinic_policies else None
    )
    cordales_xray_phase = "none"
    if isinstance(metadata, dict):
        cordales_xray_phase = (metadata.get("cordales_xray_phase") or "none").strip()

    cordales_flow_active = False
    if cordales_policy and cordales_policy.enabled:
        cordales_flow_active = message_signals_cordales_inquiry(
            body,
            cordales_policy,
            last_discussed_service_id=last_discussed_service_id,
        )
        if cordales_flow_active and cordales_xray_phase == "asked":
            xray_response = classify_patient_xray_response(body)
            if xray_response == "has_panoramic":
                try:
                    conversation_memory.set_cordales_xray_phase(
                        clinic_id, from_number, "has_panoramic"
                    )
                    cordales_xray_phase = "has_panoramic"
                except Exception:
                    pass
            elif xray_response == "needs_at_clinic":
                try:
                    conversation_memory.set_cordales_xray_phase(
                        clinic_id, from_number, "needs_at_clinic"
                    )
                    cordales_xray_phase = "needs_at_clinic"
                except Exception:
                    pass

    pediatric_policy = (
        clinic_policies.pediatric_age_policy if clinic_policies else None
    )
    pediatric_result: PediatricAgeResult | None = None
    stored_beneficiario_edad: int | None = None
    if isinstance(metadata, dict) and metadata.get("beneficiario_edad") is not None:
        try:
            stored_beneficiario_edad = int(metadata.get("beneficiario_edad"))
        except (TypeError, ValueError):
            stored_beneficiario_edad = None

    if pediatric_policy and pediatric_policy.enabled:
        pediatric_result = classify_pediatric_context(
            body, pediatric_policy, history=history
        )
        if (
            pediatric_result.is_pediatric
            and pediatric_result.mentioned_age is not None
            and pediatric_result.age_eligible is not False
        ):
            try:
                conversation_memory.set_beneficiario_edad(
                    clinic_id, from_number, pediatric_result.mentioned_age
                )
            except Exception:
                logging.warning(
                    "No se pudo guardar beneficiario_edad en Firestore",
                    exc_info=True,
                )

    pediatric_ineligible = pediatric_ineligibility_result(
        body,
        pediatric_policy,
        history=history,
        stored_beneficiario_edad=stored_beneficiario_edad,
    )
    if pediatric_ineligible and pediatric_policy:
        try:
            conversation_memory.clear_booking_pending(clinic_id, from_number)
        except Exception:
            logging.warning("No se pudo limpiar booking_pending por edad pediátrica", exc_info=True)
        reply_text = patient_prompt_pediatric_decline(language, pediatric_policy)
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    maxillofacial_result: MaxillofacialContextResult | None = None
    maxillo_policy = clinic_policies.maxillofacial_policy if clinic_policies else None
    if maxillo_policy and maxillo_policy.enabled:
        maxillofacial_result = classify_maxillofacial_context(
            body,
            history,
            maxillo_policy,
            last_discussed_service_id=last_discussed_service_id,
        )

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
            patient_name_tr = stored_full_name_tr or (stored_first_name or "").strip() or "No indicado"

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

    fork_awaiting_choice = (
        same_day_phase == "awaiting_choice" or emergency_phase == "awaiting_choice"
    )

    if booking_phase == "awaiting_confirm" and booking_pending and not fork_awaiting_choice:
        if pediatric_ineligible and pediatric_policy:
            try:
                conversation_memory.clear_booking_pending(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar booking_pending por edad pediátrica", exc_info=True)
            reply_text = patient_prompt_pediatric_decline(language, pediatric_policy)
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

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
            if isinstance(out, dict) and out.get("mensaje") and not out.get("error"):
                try:
                    conversation_memory.clear_emergency_fork(clinic_id, from_number)
                except Exception:
                    logging.warning("No se pudo limpiar emergency_phase tras agendar", exc_info=True)
                try:
                    conversation_memory.clear_same_day_fork(clinic_id, from_number)
                except Exception:
                    logging.warning("No se pudo limpiar same_day_phase tras agendar", exc_info=True)
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
            try:
                conversation_memory.clear_emergency_fork(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar emergency_phase tras rechazo", exc_info=True)
            try:
                conversation_memory.clear_same_day_fork(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar same_day_phase tras rechazo", exc_info=True)
            reply_text = patient_prompt_booking_declined(language)
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

        if booking_decision == "revise":
            try:
                conversation_memory.clear_booking_pending(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar booking_pending para revisión", exc_info=True)
            try:
                conversation_memory.clear_emergency_fork(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar emergency_phase para revisión", exc_info=True)
            try:
                conversation_memory.clear_same_day_fork(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar same_day_phase para revisión", exc_info=True)
        else:
            reply_text = patient_prompt_booking_unclear(language)
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

    # --- Emergencia / dolor grave: elección cita vs contacto del equipo ---
    emergency_policy = (
        clinic_policies.emergency_fork_policy if clinic_policies else None
    )
    same_day_policy = (
        clinic_policies.same_day_fork_policy if clinic_policies else None
    )
    if emergency_policy and emergency_policy.enabled and emergency_phase == "awaiting_choice":
        choice = classify_emergency_choice_response(body)
        if choice == "team_contact" and clinic_cfg:
            spec_digits = parse_specialist_whatsapp_recipient(
                getattr(clinic_cfg, "specialist_whatsapp", None)
            )
            if not spec_digits:
                reply_text = patient_prompt_transfer_not_configured(language)
                conversation_memory.add_message(clinic_id, from_number, "user", body)
                conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
                return reply_text
            try:
                conversation_memory.clear_booking_pending(clinic_id, from_number)
            except Exception:
                pass
            detection = HumanTransferDetection(
                matched_topics=("emergencia_dolor",),
                brief_reason=(
                    "Emergency / severe dental pain — patient chose medical team contact"
                    if language == "en"
                    else "Emergencia / dolor dental grave — paciente eligió contacto del equipo médico"
                ),
            )
            summary = generate_transfer_summary(
                gemini_service,
                history=history,
                current_message=body,
                language=language,
                detection=detection,
            )
            token = (settings.META_WHATSAPP_ACCESS_TOKEN or "").strip()
            phone_nid = resolve_whatsapp_phone_number_id_for_specialist(clinic_cfg)
            stored_full_name_tr = (metadata.get("patient_name") or "").strip() if isinstance(metadata, dict) else ""
            patient_name_tr = stored_full_name_tr or (stored_first_name or "").strip() or "No indicado"
            specialist_body = build_specialist_derivation_message(
                patient_name=patient_name_tr,
                patient_phone_display=format_patient_phone_display(from_number),
                summary=summary,
            )
            sent_ok = False
            if token and phone_nid:
                try:
                    send_text_message_sync(
                        graph_version=settings.META_GRAPH_API_VERSION,
                        phone_number_id=phone_nid,
                        to_wa_id=spec_digits,
                        body=specialist_body,
                        access_token=token,
                    )
                    sent_ok = True
                    logging.info(
                        "Derivación emergencia enviada clinic=%s to=%s phone_number_id=%s",
                        clinic_id,
                        spec_digits,
                        phone_nid,
                    )
                except Exception:
                    logging.exception(
                        "Error enviando derivación emergencia (clinic=%s to=%s)",
                        clinic_id,
                        spec_digits,
                    )
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            clinic_phone = getattr(clinic_cfg, "clinic_phone", None)
            if sent_ok:
                try:
                    conversation_memory.set_emergency_awaiting_followup(clinic_id, from_number)
                except Exception:
                    logging.warning("No se pudo marcar emergency awaiting_followup", exc_info=True)
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
                    logging.warning("BigQuery transferencia_estado emergencia no actualizado", exc_info=True)
                reply_text = patient_prompt_emergency_transfer_sent(
                    language,
                    emergency_policy,
                    clinic_phone=clinic_phone,
                )
            else:
                reply_text = patient_prompt_transfer_send_failed(language)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

        if choice == "appointment":
            try:
                conversation_memory.clear_booking_pending(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar booking_pending por elección emergencia", exc_info=True)
            try:
                conversation_memory.set_emergency_appointment_chosen(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo marcar emergency appointment_chosen", exc_info=True)
            emergency_appointment_chosen = True
        elif choice == "unclear":
            reply_text = patient_prompt_emergency_choice_unclear(language, emergency_policy)
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

    if same_day_policy and same_day_policy.enabled and same_day_phase == "awaiting_choice":
        choice = classify_same_day_choice_response(body)
        if choice == "team_contact" and clinic_cfg:
            spec_digits = parse_specialist_whatsapp_recipient(
                getattr(clinic_cfg, "specialist_whatsapp", None)
            )
            if not spec_digits:
                reply_text = patient_prompt_transfer_not_configured(language)
                conversation_memory.add_message(clinic_id, from_number, "user", body)
                conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
                return reply_text
            try:
                conversation_memory.clear_booking_pending(clinic_id, from_number)
            except Exception:
                pass
            detection = HumanTransferDetection(
                matched_topics=("cita_mismo_dia",),
                brief_reason=(
                    "Same-day appointment request — patient chose team contact"
                    if language == "en"
                    else "Solicitud de cita para hoy — paciente eligió contacto del equipo"
                ),
            )
            summary = generate_transfer_summary(
                gemini_service,
                history=history,
                current_message=body,
                language=language,
                detection=detection,
            )
            token = (settings.META_WHATSAPP_ACCESS_TOKEN or "").strip()
            phone_nid = resolve_whatsapp_phone_number_id_for_specialist(clinic_cfg)
            stored_full_name_tr = (metadata.get("patient_name") or "").strip() if isinstance(metadata, dict) else ""
            patient_name_tr = stored_full_name_tr or (stored_first_name or "").strip() or "No indicado"
            specialist_body = build_specialist_derivation_message(
                patient_name=patient_name_tr,
                patient_phone_display=format_patient_phone_display(from_number),
                summary=summary,
            )
            sent_ok = False
            if token and phone_nid:
                try:
                    send_text_message_sync(
                        graph_version=settings.META_GRAPH_API_VERSION,
                        phone_number_id=phone_nid,
                        to_wa_id=spec_digits,
                        body=specialist_body,
                        access_token=token,
                    )
                    sent_ok = True
                    logging.info(
                        "Derivación cita mismo día enviada clinic=%s to=%s phone_number_id=%s",
                        clinic_id,
                        spec_digits,
                        phone_nid,
                    )
                except Exception:
                    logging.exception(
                        "Error enviando derivación cita mismo día (clinic=%s to=%s)",
                        clinic_id,
                        spec_digits,
                    )
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            clinic_phone = getattr(clinic_cfg, "clinic_phone", None)
            if sent_ok:
                try:
                    conversation_memory.set_same_day_awaiting_followup(clinic_id, from_number)
                except Exception:
                    logging.warning("No se pudo marcar same_day awaiting_followup", exc_info=True)
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
                    logging.warning("BigQuery transferencia_estado cita mismo día no actualizado", exc_info=True)
                reply_text = patient_prompt_same_day_transfer_sent(
                    language,
                    same_day_policy,
                    clinic_phone=clinic_phone,
                )
            else:
                reply_text = patient_prompt_transfer_send_failed(language)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

        if choice == "appointment":
            try:
                conversation_memory.clear_booking_pending(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo limpiar booking_pending por elección cita hoy", exc_info=True)
            try:
                conversation_memory.set_same_day_appointment_chosen(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo marcar same_day appointment_chosen", exc_info=True)
            same_day_appointment_chosen = True
        elif choice == "unclear":
            reply_text = patient_prompt_same_day_choice_unclear(language, same_day_policy)
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

    # Guardrails de dominio por clínica: clasificar intención y, solo si es fuera de dominio, responder sin llamar a Gemini.
    # Los temas del manual de la clínica (knowledge_base) cuentan como dentro de dominio.
    clinic_knowledge_base = getattr(clinic_cfg, "knowledge_base", None) if clinic_cfg else None
    clinic_topics = extract_knowledge_base_topics(clinic_knowledge_base)
    clinic_service_keywords = knowledge_base_service_keywords(clinic_knowledge_base)
    catalog_keywords = catalog_intent_keywords(clinic_id)
    merged_service_keywords = list(
        dict.fromkeys([*clinic_service_keywords, *catalog_keywords])
    )
    catalog_topics = catalog_intent_topics(clinic_id)
    merged_clinic_topics = list(dict.fromkeys([*clinic_topics, *catalog_topics]))
    # Reglas-primero: las reglas reconocen citas, servicios del catálogo y temas del manual.
    # Solo si las reglas NO reconocen el mensaje (OUT_OF_DOMAIN) consultamos al LLM, para no gastar
    # una llamada en los mensajes claros y, a la vez, no bloquear por error lo que las reglas no cubren.
    try:
        intent = classify_intent(
            body,
            language,
            history,
            extra_service_keywords=merged_service_keywords,
        )
    except Exception:
        intent = Intent.OUT_OF_DOMAIN

    if intent is Intent.OUT_OF_DOMAIN and is_contextual_offer_acceptance(body, history):
        intent = Intent.CITA

    if intent is Intent.OUT_OF_DOMAIN and should_reconfirm_after_booking_offer(body, history):
        reply_text = patient_prompt_offer_response_unclear(
            language,
            service_name=last_discussed_service_name,
        )
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    if intent is Intent.OUT_OF_DOMAIN:
        try:
            intent = llm_classify_intent(
                gemini=gemini_service,
                message=body,
                language=language,
                history=history,
                clinic_topics=merged_clinic_topics,
            )
        except Exception:
            intent = Intent.OUT_OF_DOMAIN

    if intent is Intent.OUT_OF_DOMAIN and should_fail_open_after_offer_reconfirm(body, history):
        intent = Intent.CITA

    if name_just_corrected and intent is Intent.OUT_OF_DOMAIN:
        intent = Intent.SMALL_TALK

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

    # --- Maxilofacial: derivación directa sin confirmación de resumen (cita / horarios) ---
    if (
        maxillofacial_result
        and maxillofacial_result.is_active
        and maxillofacial_result.intent == "booking"
        and clinic_cfg
    ):
        spec_digits = parse_specialist_whatsapp_recipient(
            getattr(clinic_cfg, "specialist_whatsapp", None)
        )
        if not spec_digits:
            reply_text = patient_prompt_transfer_not_configured(language)
            conversation_memory.add_message(clinic_id, from_number, "user", body)
            conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
            return reply_text

        try:
            conversation_memory.clear_booking_pending(clinic_id, from_number)
        except Exception:
            pass
        detection = HumanTransferDetection(
            matched_topics=("maxilofacial",),
            brief_reason=(
                "Maxillofacial appointment or availability request"
                if language == "en"
                else "Solicitud de cita u horarios de cirugía maxilofacial"
            ),
        )
        summary = generate_transfer_summary(
            gemini_service,
            history=history,
            current_message=body,
            language=language,
            detection=detection,
        )
        token = (settings.META_WHATSAPP_ACCESS_TOKEN or "").strip()
        phone_nid = resolve_whatsapp_phone_number_id_for_specialist(clinic_cfg)
        stored_full_name_tr = (metadata.get("patient_name") or "").strip() if isinstance(metadata, dict) else ""
        patient_name_tr = stored_full_name_tr or (stored_first_name or "").strip() or "No indicado"
        specialist_body = build_specialist_derivation_message(
            patient_name=patient_name_tr,
            patient_phone_display=format_patient_phone_display(from_number),
            summary=summary,
        )
        sent_ok = False
        if token and phone_nid:
            try:
                send_text_message_sync(
                    graph_version=settings.META_GRAPH_API_VERSION,
                    phone_number_id=phone_nid,
                    to_wa_id=spec_digits,
                    body=specialist_body,
                    access_token=token,
                )
                sent_ok = True
                logging.info(
                    "Derivación maxilofacial enviada clinic=%s to=%s phone_number_id=%s",
                    clinic_id,
                    spec_digits,
                    phone_nid,
                )
            except Exception:
                logging.exception(
                    "Error enviando derivación maxilofacial (clinic=%s to=%s)",
                    clinic_id,
                    spec_digits,
                )
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        if sent_ok:
            try:
                conversation_memory.set_maxillofacial_awaiting_followup(clinic_id, from_number)
            except Exception:
                logging.warning("No se pudo marcar maxillofacial awaiting_followup", exc_info=True)
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
                logging.warning("BigQuery transferencia_estado maxilofacial no actualizado", exc_info=True)
            reply_text = patient_prompt_maxillofacial_transfer_sent(language)
        else:
            reply_text = patient_prompt_transfer_send_failed(language)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    # --- Emergencia / dolor grave: primer turno → preguntar cita vs contacto del equipo ---
    if should_trigger_emergency_fork(
        policy_enabled=bool(emergency_policy and emergency_policy.enabled),
        emergency_phase=emergency_phase,
        booking_phase=booking_phase,
        message=body,
        force_human_contact=message_signals_human_contact_request(body),
        maxillofacial_booking=bool(
            maxillofacial_result
            and maxillofacial_result.is_active
            and maxillofacial_result.intent == "booking"
        ),
    ):
        reply_text = patient_prompt_emergency_choice(
            language,
            emergency_policy,
            first_name=stored_first_name,
        )
        try:
            conversation_memory.set_emergency_awaiting_choice(clinic_id, from_number)
        except Exception:
            logging.warning("No se pudo marcar emergency awaiting_choice", exc_info=True)
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    # --- Cita mismo día: primer turno → preguntar mañana vs contacto del equipo ---
    if should_trigger_same_day_fork(
        policy_enabled=bool(same_day_policy and same_day_policy.enabled),
        same_day_phase=same_day_phase,
        emergency_phase=emergency_phase,
        booking_phase=booking_phase,
        message=body,
        force_human_contact=message_signals_human_contact_request(body),
        maxillofacial_booking=bool(
            maxillofacial_result
            and maxillofacial_result.is_active
            and maxillofacial_result.intent == "booking"
        ),
    ):
        reply_text = patient_prompt_same_day_choice(
            language,
            same_day_policy,
            first_name=stored_first_name,
        )
        try:
            conversation_memory.set_same_day_awaiting_choice(clinic_id, from_number)
        except Exception:
            logging.warning("No se pudo marcar same_day awaiting_choice", exc_info=True)
        conversation_memory.add_message(clinic_id, from_number, "user", body)
        conversation_memory.add_message(clinic_id, from_number, "assistant", reply_text)
        return reply_text

    maxillofacial_info_active = bool(
        maxillofacial_result
        and maxillofacial_result.is_active
        and maxillofacial_result.intent == "info"
    )
    skip_transfer_for_maxillo_info = maxillofacial_info_active

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
        and not skip_transfer_for_maxillo_info
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

    require_name_before_booking = (
        intent in (Intent.CITA, Intent.SEGUIMIENTO_CITA) and not stored_first_name
    )

    booking_beneficiary_hint = build_booking_beneficiary_hint(
        body,
        language=language,
        stored_first_name=stored_first_name,
    )

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
        name_collection_phase=name_collection_phase if not stored_first_name else "known",
        last_discussed_service_name=last_discussed_service_name,
        cordales_flow_active=cordales_flow_active,
        cordales_xray_phase=cordales_xray_phase,
        require_name_before_booking=require_name_before_booking,
        booking_beneficiary_hint=booking_beneficiary_hint,
        name_just_corrected=name_just_corrected,
        pediatric_result=pediatric_result,
        maxillofacial_info_active=maxillofacial_info_active,
        emergency_appointment_chosen=emergency_appointment_chosen,
        same_day_appointment_chosen=same_day_appointment_chosen,
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
                try:
                    conversation_memory.clear_emergency_fork(clinic_id, from_number)
                except Exception:
                    logging.warning("No se pudo limpiar emergency_phase tras agendar_cita", exc_info=True)
                try:
                    conversation_memory.clear_same_day_fork(clinic_id, from_number)
                except Exception:
                    logging.warning("No se pudo limpiar same_day_phase tras agendar_cita", exc_info=True)
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
    default_patient_name = stored_full_name_for_pending or (stored_first_name or "").strip()
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
            default_patient_name=default_patient_name or None,
            last_discussed_service_id=last_discussed_service_id,
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

    # Marcar fase asked solo si el asistente realmente preguntó el nombre en este turno.
    if not stored_first_name and name_collection_phase == "none" and assistant_asked_for_name(reply_text):
        try:
            conversation_memory.set_name_collection_phase(clinic_id, from_number, "asked")
        except Exception:
            pass

    # Flujo cordales: tras primera respuesta con pregunta obligatoria, marcar fase asked
    if cordales_flow_active and cordales_xray_phase == "none":
        try:
            conversation_memory.set_cordales_xray_phase(clinic_id, from_number, "asked")
        except Exception:
            pass

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


