from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..database import SessionLocal
from ..repositories import (
    CITA_STATUS_CANCELADA,
    CITA_STATUS_REAGENDADA,
    create_cita,
    get_latest_activa_cita_for_phone,
    get_latest_cita_for_phone,
    list_upcoming_activa_citas_for_phone,
    update_cita_status,
)
from ..services.calendar_service import calendar_service, CalendarServiceError
from . import availability
from .calendar_retry import _retry_delete_event_async
from .catalog import _service_display_label
from .clinics_state import get_clinics_by_id
from .urgency_calendar import _calendar_suffix_label_for_cita

if TYPE_CHECKING:
    from ..services.conversation_memory import ConversationMemoryService

_conversation_memory: ConversationMemoryService | None = None


def set_conversation_memory_for_cita_handlers(svc: ConversationMemoryService) -> None:
    global _conversation_memory
    _conversation_memory = svc


def _cm() -> ConversationMemoryService:
    assert _conversation_memory is not None
    return _conversation_memory


def _handle_agendar_cita(
    from_number: str,
    clinic_id: str,
    language: str,
    assistant_name: str,
    args: dict,
) -> dict:
    """
    Ejecuta el insert en BigQuery para agendar una cita.
    clinic_id viene del contexto (webhook), no del usuario.
    language: 'es' o 'en' para devolver el mensaje de confirmación en el mismo idioma de la conversación.
    Devuelve un dict con 'mensaje' para que Gemini lo use en la respuesta al usuario.
    """
    from datetime import datetime

    nombre = (args.get("nombre") or "").strip()
    fecha = (args.get("fecha") or "").strip()
    hora = (args.get("hora") or "").strip()
    servicio = (args.get("servicio") or "").strip()
    if not all([nombre, fecha, hora, servicio]):
        if language == "en":
            msg = "I couldn't schedule the appointment: name, date, time or service type are missing. Please confirm all details, including the type of appointment (e.g. cleaning, check-up)."
        else:
            msg = "No pude agendar la cita: faltan nombre, fecha, hora o tipo de servicio. Por favor confirma todos los datos, incluyendo el tipo de cita (ej. limpieza, revisión)."
        return {"error": "Faltan datos", "mensaje": msg}

    try:
        d_cita = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        if language == "en":
            msg = "I need the appointment date in YYYY-MM-DD (e.g. 2026-04-25)."
        else:
            msg = "Necesito la fecha de la cita en formato AAAA-MM-DD (ej. 2026-04-25)."
        return {"error": "Fecha inválida", "mensaje": msg}

    today_sv = availability._today_in_el_salvador()
    if d_cita < today_sv:
        if language == "en":
            msg = "That date is in the past. Appointments are scheduled from the next calendar day onward (El Salvador time, UTC-6). Please pick a future date."
        else:
            msg = "Esa fecha ya pasó. Las citas se agendan a partir del día siguiente en hora de El Salvador (UTC-6). Por favor elige una fecha futura."
        return {"error": "Fecha pasada", "mensaje": msg}
    if d_cita == today_sv:
        if language == "en":
            msg = (
                "Hello! Thank you so much for your interest. ✨ For the best care, we schedule appointments with at least one "
                "full day's notice in El Salvador time (UTC-6). Would you like to look at available times starting tomorrow?"
            )
        else:
            msg = (
                "¡Hola! Muchas gracias por tu interés. ✨ Te comento que, para brindarte la mejor atención, nuestras citas se "
                "agendan con al menos un día de anticipación. ¿Te gustaría que revisemos los horarios disponibles a partir de mañana?"
            )
        return {"error": "Cita mismo día", "mensaje": msg}

    clinic_cfg = get_clinics_by_id().get(clinic_id)
    if clinic_cfg is not None and not availability._is_within_opening_hours(clinic_cfg, fecha, hora):
        if language == "en":
            msg = (
                "I can't schedule an appointment at that day/time. "
                "Appointments are 60 minutes and must finish by closing, so the last valid start is on the hour and at least "
                "one hour before the clinic's closing time for that day. "
                "Please pick another on-the-hour start time (for example, if we close at 17:00 on a weekday, the last start is 16:00)."
            )
        else:
            msg = (
                "No puedo agendar una cita en ese día u hora. "
                "Las citas duran 60 minutos y deben terminar a más tardar al cierre, así que la última hora de inicio válida "
                "es en punto y al menos una hora antes del cierre de ese día. "
                "Por favor elige otra hora en punto (por ejemplo: si entre semana cerramos a las 17:00, la última cita inicia a las 16:00)."
            )
        return {"error": "Fuera de horario", "mensaje": msg}
    if clinic_cfg is not None and clinic_cfg.calendar_sync_enabled and clinic_cfg.calendar_id:
        try:
            available_slots = availability._available_hourly_slots_for_clinic(clinic_cfg, fecha)
        except CalendarServiceError:
            available_slots = []
        if available_slots and hora not in available_slots:
            if language == "en":
                msg = (
                    f"That time is not available. Available times for {fecha}: {', '.join(available_slots)}. "
                    "Please choose one of those times on the hour."
                )
            else:
                msg = (
                    f"Ese horario ya no está disponible. Horas libres para {fecha}: {', '.join(available_slots)}. "
                    "Por favor elige una de esas horas en punto."
                )
            return {"error": "Horario no disponible", "mensaje": msg}

    first_word = nombre.split()[0] if nombre.split() else nombre
    first_word_norm = first_word[:1].upper() + first_word[1:].lower() if first_word else ""
    use_full_name: str | None = None

    metadata = _cm().get_metadata(clinic_id, from_number) or {}
    if isinstance(metadata, dict):
        stored_full_name = (metadata.get("patient_name") or "").strip()
        stored_first = (metadata.get("patient_first_name") or "").strip()
        if stored_full_name and stored_first and first_word_norm == stored_first and len(stored_full_name.split()) > 1:
            use_full_name = stored_full_name

    if use_full_name is None and first_word_norm:
        try:
            db_bq = SessionLocal()
            try:
                cita_prev = get_latest_cita_for_phone(db_bq, clinic_id=clinic_id, telefono=from_number)
                if cita_prev and (cita_prev.paciente_nombre or "").strip():
                    full_bq = cita_prev.paciente_nombre.strip()
                    parts_bq = full_bq.split()
                    first_bq = parts_bq[0][:1].upper() + parts_bq[0][1:].lower() if parts_bq else ""
                    if first_bq == first_word_norm and len(parts_bq) > 1:
                        use_full_name = full_bq
            finally:
                db_bq.close()
        except Exception:
            pass

    if use_full_name:
        nombre = use_full_name

    try:
        _cm().set_patient_name(clinic_id, from_number, nombre)
    except Exception:
        pass

    db = SessionLocal()
    try:
        cita = create_cita(
            db,
            clinic_id=clinic_id,
            paciente_nombre=nombre,
            telefono=from_number or "Sin teléfono",
            fecha=fecha,
            hora=hora,
            razon_cita=servicio,
            origen_reserva="whatsapp_assistant",
            agendado_por=assistant_name,
        )

        if clinic_cfg and clinic_cfg.calendar_sync_enabled and clinic_cfg.calendar_id:
            try:
                suffix_label = _calendar_suffix_label_for_cita(servicio, args)
                servicio_label_es = _service_display_label(clinic_id, servicio, "es")
                event_id = calendar_service.create_event_for_cita(
                    calendar_id=clinic_cfg.calendar_id,
                    cita=cita,
                    clinic_name=clinic_cfg.name,
                    assistant_name=assistant_name,
                    servicio_display=servicio_label_es,
                    calendar_suffix=suffix_label,
                )
                cita.calendar_event_id = event_id
                cita.calendar_id = clinic_cfg.calendar_id
                cita.sync_status = "ok"
                cita.sync_error_message = None
                db.add(cita)
                db.commit()
            except CalendarServiceError as exc:
                cita.sync_status = "error"
                cita.sync_error_message = str(exc)
                db.add(cita)
                db.commit()
        servicio_label = _service_display_label(clinic_id, servicio, language)
        if language == "en":
            mensaje = f"Done! I've scheduled your appointment for {fecha} at {hora} (service: {servicio_label})."
        else:
            mensaje = f"¡Listo! He agendado tu cita para el {fecha} a las {hora} (servicio: {servicio_label})."
        return {"mensaje": mensaje}
    except Exception as e:
        logging.warning("Error agendando cita: %s", e)
        if language == "en":
            msg = "I couldn't schedule the appointment. Please try again or contact the clinic."
        else:
            msg = "No pude agendar la cita. Por favor intenta de nuevo o contacta a la clínica."
        return {"error": str(e), "mensaje": msg}
    finally:
        db.close()


def _handle_cancelar_cita(from_number: str, clinic_id: str, language: str) -> dict:
    """
    Cancela la cita activa del paciente (teléfono + clínica del contexto).
    Devuelve mensaje de éxito o error para que Gemini lo use en la respuesta.
    """
    db = SessionLocal()
    try:
        cita = get_latest_activa_cita_for_phone(db, clinic_id=clinic_id, telefono=from_number)
        if not cita:
            if language == "en":
                msg = "You don't have an active appointment to cancel. If you had one, it may already be cancelled or rescheduled."
            else:
                msg = "No tienes una cita activa que cancelar. Si tenías una, puede que ya esté cancelada o reagendada."
            return {"error": "Sin cita activa", "mensaje": msg}
        clinic_cfg = get_clinics_by_id().get(clinic_id)
        if (
            clinic_cfg
            and clinic_cfg.calendar_sync_enabled
            and cita.calendar_id
            and cita.calendar_event_id
        ):
            try:
                calendar_service.delete_event(
                    calendar_id=cita.calendar_id,
                    event_id=cita.calendar_event_id,
                )
                cita.sync_status = "ok"
                cita.sync_error_message = None
            except CalendarServiceError as exc:
                cita.sync_status = "error"
                cita.sync_error_message = str(exc)
                _retry_delete_event_async(
                    calendar_id=cita.calendar_id,
                    event_id=cita.calendar_event_id,
                )
        update_cita_status(db, cita, CITA_STATUS_CANCELADA)
        if language == "en":
            mensaje = "Your appointment has been cancelled. If you need a new one, just ask to schedule it."
        else:
            mensaje = "Tu cita ha sido cancelada. Si necesitas una nueva, solo pide agendar una."
        return {"mensaje": mensaje}
    except Exception as e:
        logging.warning("Error cancelando cita: %s", e)
        if language == "en":
            msg = "I couldn't cancel the appointment. Please try again or contact the clinic."
        else:
            msg = "No pude cancelar la cita. Por favor intenta de nuevo o contacta a la clínica."
        return {"error": str(e), "mensaje": msg}
    finally:
        db.close()


def _handle_reagendar_cita(from_number: str, clinic_id: str, language: str, assistant_name: str, args: dict) -> dict:
    """
    Marca la cita activa actual como reagendada y crea una nueva con fecha/hora/servicio indicados.
    Si no se pasa servicio, se usa el de la cita actual.
    """
    from datetime import datetime

    fecha = (args.get("fecha") or "").strip()
    hora = (args.get("hora") or "").strip()
    servicio = (args.get("servicio") or "").strip()
    if not fecha or not hora:
        if language == "en":
            msg = "I need the new date and time to reschedule (e.g. 2025-03-15 and 10:00)."
        else:
            msg = "Necesito la nueva fecha y hora para reagendar (ej. 2025-03-15 y 10:00)."
        return {"error": "Faltan fecha u hora", "mensaje": msg}

    try:
        d_nueva = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        if language == "en":
            msg = "I need the new date in YYYY-MM-DD (e.g. 2026-04-25)."
        else:
            msg = "Necesito la nueva fecha en formato AAAA-MM-DD (ej. 2026-04-25)."
        return {"error": "Fecha inválida", "mensaje": msg}

    today_sv = availability._today_in_el_salvador()
    if d_nueva < today_sv:
        if language == "en":
            msg = "That date is in the past. Rescheduling is only to future days from the next calendar day onward (El Salvador time, UTC-6)."
        else:
            msg = "Esa fecha ya pasó. Solo se puede reagendar a días futuros a partir de mañana en hora de El Salvador (UTC-6)."
        return {"error": "Fecha pasada", "mensaje": msg}
    if d_nueva == today_sv:
        if language == "en":
            msg = (
                "We can't move your appointment to today. Appointments require at least one full day's notice in El "
                "Salvador time (UTC-6). Would you like to see times starting tomorrow?"
            )
        else:
            msg = (
                "No podemos reagendar tu cita para el mismo día. Se requiere al menos un día de anticipación en hora de "
                "El Salvador (UTC-6). ¿Te parece que revisemos horarios a partir de mañana?"
            )
        return {"error": "Cita mismo día", "mensaje": msg}

    clinic_cfg = get_clinics_by_id().get(clinic_id)
    if clinic_cfg is not None and not availability._is_within_opening_hours(clinic_cfg, fecha, hora):
        if language == "en":
            msg = (
                "I can't reschedule to that day/time. "
                "Appointments are 60 minutes and must finish by closing, so the last valid start is on the hour and at least "
                "one hour before closing (e.g. if we close at 17:00, 17:00 is not a valid start; 16:00 is the last start)."
            )
        else:
            msg = (
                "No puedo reagendar en ese día u hora. "
                "Las citas duran 60 minutos y deben terminar a más tardar al cierre: la última hora de inicio válida es en punto "
                "y al menos una hora antes del cierre (ej. si cerramos a las 17:00, no se puede iniciar a las 17:00; la última es 16:00)."
            )
        return {"error": "Fuera de horario", "mensaje": msg}
    if clinic_cfg is not None and clinic_cfg.calendar_sync_enabled and clinic_cfg.calendar_id:
        try:
            available_slots = availability._available_hourly_slots_for_clinic(clinic_cfg, fecha)
        except CalendarServiceError:
            available_slots = []
        if available_slots and hora not in available_slots:
            if language == "en":
                msg = (
                    f"That time is not available. Available times for {fecha}: {', '.join(available_slots)}. "
                    "Please choose one of those times on the hour."
                )
            else:
                msg = (
                    f"Ese horario ya no está disponible. Horas libres para {fecha}: {', '.join(available_slots)}. "
                    "Por favor elige una de esas horas en punto."
                )
            return {"error": "Horario no disponible", "mensaje": msg}

    db = SessionLocal()
    try:
        cita_activa = get_latest_activa_cita_for_phone(db, clinic_id=clinic_id, telefono=from_number)
        if not cita_activa:
            if language == "en":
                msg = "You don't have an active appointment to reschedule."
            else:
                msg = "No tienes una cita activa para reagendar."
            return {"error": "Sin cita activa", "mensaje": msg}

        nombre = (cita_activa.paciente_nombre or "").strip() or "Sin nombre"
        razon = (servicio or (cita_activa.razon_cita or "").strip()) or None

        update_cita_status(db, cita_activa, CITA_STATUS_REAGENDADA)

        cita_nueva = create_cita(
            db,
            clinic_id=clinic_id,
            paciente_nombre=nombre,
            telefono=from_number or "Sin teléfono",
            fecha=fecha,
            hora=hora,
            razon_cita=razon or "revision",
            origen_reserva="whatsapp_assistant",
            agendado_por=assistant_name,
        )

        if clinic_cfg and clinic_cfg.calendar_sync_enabled and clinic_cfg.calendar_id:
            if cita_activa.calendar_id and cita_activa.calendar_event_id:
                try:
                    calendar_service.delete_event(
                        calendar_id=cita_activa.calendar_id,
                        event_id=cita_activa.calendar_event_id,
                    )
                except CalendarServiceError as exc:
                    cita_activa.sync_status = "error"
                    cita_activa.sync_error_message = str(exc)
                    db.add(cita_activa)
                    db.commit()

            try:
                suffix_label = _calendar_suffix_label_for_cita(razon or "", args)
                servicio_label_es = _service_display_label(clinic_id, razon or "evaluacion", "es")
                event_id = calendar_service.create_event_for_cita(
                    calendar_id=clinic_cfg.calendar_id,
                    cita=cita_nueva,
                    clinic_name=clinic_cfg.name,
                    assistant_name=assistant_name,
                    servicio_display=servicio_label_es,
                    calendar_suffix=suffix_label,
                )
                cita_nueva.calendar_event_id = event_id
                cita_nueva.calendar_id = clinic_cfg.calendar_id
                cita_nueva.sync_status = "ok"
                cita_nueva.sync_error_message = None
                db.add(cita_nueva)
                db.commit()
            except CalendarServiceError as exc:
                cita_nueva.sync_status = "error"
                cita_nueva.sync_error_message = str(exc)
                db.add(cita_nueva)
                db.commit()
        if language == "en":
            mensaje = f"Done! I've rescheduled your appointment to {fecha} at {hora}."
        else:
            mensaje = f"¡Listo! He reagendado tu cita para el {fecha} a las {hora}."
        return {"mensaje": mensaje}
    except Exception as e:
        logging.warning("Error reagendando cita: %s", e)
        if language == "en":
            msg = "I couldn't reschedule the appointment. Please try again or contact the clinic."
        else:
            msg = "No pude reagendar la cita. Por favor intenta de nuevo o contacta a la clínica."
        return {"error": str(e), "mensaje": msg}
    finally:
        db.close()


def _handle_listar_mis_citas_proximas(from_number: str, clinic_id: str, language: str) -> dict:
    """
    Lista citas activas del paciente (teléfono + clínica) desde ahora en hora de El Salvador.
    Sin 'mensaje': el modelo enumera al paciente usando el arreglo citas y la nota.
    """
    db = SessionLocal()
    try:
        citas = list_upcoming_activa_citas_for_phone(db, clinic_id=clinic_id, telefono=from_number)
        items: list[dict] = []
        for c in citas:
            fe = c.fecha_cita.isoformat() if c.fecha_cita else ""
            hora_t = c.hora_cita
            hora_s = hora_t.strftime("%H:%M") if hora_t else ""
            sid = (c.razon_cita or "").strip()
            label = _service_display_label(clinic_id, sid, language) if sid else ""
            if not label:
                label = sid or ("Not specified" if language == "en" else "Sin especificar")
            items.append(
                {
                    "fecha": fe,
                    "hora": hora_s,
                    "servicio_id": sid or None,
                    "servicio": label,
                }
            )
        if language == "en":
            nota = (
                "Active upcoming appointments for this patient's WhatsApp number in this clinic, "
                "from the current moment in El Salvador (UTC-6). Each entry has fecha (YYYY-MM-DD), hora (HH:MM), "
                "and servicio (human-readable). List them clearly for the patient. If citas is empty, say they have "
                "no upcoming active appointments on file."
            )
        else:
            nota = (
                "Citas **activas** registradas para el teléfono de esta conversación en esta clínica, "
                "desde el momento actual en hora de El Salvador (UTC-6). Cada elemento trae fecha (AAAA-MM-DD), "
                "hora (HH:MM) y servicio (nombre legible). Enuméralas al paciente con fecha, hora y tipo de servicio. "
                "Si citas está vacío, indica que no tiene citas próximas registradas en el sistema."
            )
        return {"ok": True, "citas": items, "nota": nota}
    except Exception as e:
        logging.warning("Error listando citas próximas: %s", e)
        if language == "en":
            nota = "Could not load appointments from the system."
        else:
            nota = "No se pudieron consultar las citas en el sistema."
        return {"ok": False, "error": str(e), "citas": [], "nota": nota}
    finally:
        db.close()
