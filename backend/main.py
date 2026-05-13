from __future__ import annotations

import json
import logging
import sys
import time
import traceback
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from langdetect import LangDetectException, detect
from pydantic import BaseModel, ValidationError
from twilio.twiml.messaging_response import MessagingResponse

from .config import settings
from .database import SessionLocal
from .repositories import (
    CITA_STATUS_CANCELADA,
    CITA_STATUS_REAGENDADA,
    create_cita,
    get_latest_activa_cita_for_phone,
    get_latest_cita_for_phone,
    update_cita_status,
)
from .services.gemini_service import GeminiService, GeminiServiceError
from .services.conversation_memory import ConversationMemoryService
from .services.intent_classifier import Intent, classify_intent
from .services.intent_llm_service import llm_classify_intent
from .services.calendar_service import calendar_service, CalendarServiceError
from .services.calendar_sync_service import run_calendar_to_bigquery_sync
from .services.meta_whatsapp_service import (
    extract_incoming_whatsapp_events,
    send_text_message,
    verify_webhook_signature,
)
from .services.whatsapp_media_replies import reply_for_meta_media_type, reply_for_twilio_media

# Asegurar que los logs (y tracebacks) se vean en la consola de uvicorn
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
    stream=sys.stderr,
    force=True,
)

app = FastAPI(title="Clinica Assistant Agent", version="0.1.0")


class ClinicConfig(BaseModel):
    id: str
    name: str
    system_prompt: str  # Prompt base en español
    system_prompt_en: str | None = None  # Prompt equivalente en inglés (opcional)
    assistant_name: str = "Asistente Virtual"  # Nombre con el que se presenta el bot
    opening_hours: Dict[str, Any] | None = None  # Horarios de atención por bloque (ej. mon_fri, sat)
    # Guardrails por clínica: lista de intents permitidos; si está vacía/None se permiten todos excepto OUT_OF_DOMAIN.
    allowed_intents: list[str] | None = None
    # Integración con Google Calendar: ID del calendario y flag para habilitar sync.
    calendar_id: str | None = None
    calendar_sync_enabled: bool = False
    # Ubicación y cómo llegar (opcional por clínica).
    google_maps_link: str | None = None
    indicaciones_parqueo: str | None = None
    rutas_transporte_publico: str | None = None
    # WhatsApp Cloud API (Meta): ID del número en Graph API (no es el WABA ni el teléfono legible).
    whatsapp_phone_number_id: str | None = None


class ChatRequest(BaseModel):
    """Request body for the JSON /chat endpoint (testing without Twilio)."""

    from_number: str = ""
    body: str = ""


class ChatResponse(BaseModel):
    """JSON response with the assistant reply."""

    reply: str


def _detect_language(text: str) -> str:
    """
    Detecta el idioma del texto usando langdetect, normalizado a 'es' o 'en'.

    Solo se usa para el primer mensaje de una sesión; después se reutiliza
    el idioma almacenado en Firestore.
    """
    t = (text or "").strip()
    if not t:
        return "es"

    try:
        code = detect(t)
    except LangDetectException:
        return "es"

    code = (code or "").lower()
    if code.startswith("en"):
        return "en"
    if code.startswith("es"):
        return "es"

    # Fallback: asumimos español si el detector devuelve otro idioma
    return "es"


def _load_clinics_config(path: Path) -> Dict[str, ClinicConfig]:
    """Load clinic configuration from a JSON file into a dict keyed by clinic_id."""
    if not path.exists():
        raise FileNotFoundError(f"No se encontró el archivo de configuración de clínicas en: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("No se pudo leer o parsear 'clinics_mock.json'.") from exc

    clinics_raw: List[Dict[str, Any]] = data.get("clinics", [])
    clinics: Dict[str, ClinicConfig] = {}
    for clinic in clinics_raw:
        try:
            cfg = ClinicConfig(**clinic)
        except ValidationError as exc:  # noqa: BLE001
            raise RuntimeError(f"Configuración de clínica inválida: {clinic!r}") from exc
        clinics[cfg.id] = cfg

    if not clinics:
        raise RuntimeError("No se encontraron clínicas configuradas en 'clinics_mock.json'.")

    return clinics


def _retry_delete_event_async(
    calendar_id: str,
    event_id: str,
    retries: int = 2,
    delay_seconds: float = 5.0,
) -> None:
    """
    Reintenta de forma asíncrona y limitada borrar un evento de Calendar cuando
    la llamada inicial falló por un error de red/transitorio.

    - No bloquea la respuesta al paciente.
    - No corre de forma periódica: solo se dispara cuando hay un error.
    """

    if not calendar_id or not event_id:
        return

    def _worker() -> None:
        for _ in range(max(retries, 0)):
            try:
                calendar_service.delete_event(calendar_id=calendar_id, event_id=event_id)
                break
            except CalendarServiceError:
                time.sleep(max(delay_seconds, 0.1))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


BASE_DIR = Path(__file__).resolve().parent
CLINICS_FILE = BASE_DIR / "data" / "clinics_mock.json"
SERVICES_CATALOG_FILE = BASE_DIR / "data" / "services_catalog.json"

try:
    CLINICS_BY_ID = _load_clinics_config(CLINICS_FILE)
except Exception as exc:  # noqa: BLE001
    # En un contexto real se podría loggear y dejar que la app falle en el healthcheck.
    raise RuntimeError("Error cargando la configuración de clínicas.") from exc


def _build_whatsapp_phone_number_id_map(clinics: Dict[str, ClinicConfig]) -> Dict[str, str]:
    """Mapea Meta `phone_number_id` → `clinic_id` (solo clínicas con whatsapp_phone_number_id en JSON)."""
    m: Dict[str, str] = {}
    for cid, cfg in clinics.items():
        pid = getattr(cfg, "whatsapp_phone_number_id", None)
        if pid and str(pid).strip():
            m[str(pid).strip()] = cid
    return m


WHATSAPP_PHONE_NUMBER_ID_TO_CLINIC = _build_whatsapp_phone_number_id_map(CLINICS_BY_ID)


def _normalize_wa_id_for_storage(wa_id: str) -> str:
    """
    Meta envía el remitente como dígitos (ej. 50370211900).
    Unificamos con el formato usado con Twilio: whatsapp:+<código país><número>.
    """
    digits = "".join(c for c in (wa_id or "") if c.isdigit())
    if not digits:
        return (wa_id or "").strip() or "unknown"
    return f"whatsapp:+{digits}"


def _format_opening_hours_for_prompt(clinic: ClinicConfig, language: str) -> str:
    """Formatea los horarios de atención de la clínica para el prompt (ES/EN)."""
    opening_hours = getattr(clinic, "opening_hours", None) or {}
    if not opening_hours:
        return ""

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
        lines: list[str] = ["\n\n[OPENING HOURS of the clinic:]"]
    else:
        lines = ["\n\n[HORARIO DE ATENCIÓN de la clínica:]"]

    # opening_hours es un dict de bloques (mon_fri, sat, etc.)
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

    return "\n".join(lines)


def _format_clinic_location_for_prompt(clinic: ClinicConfig, language: str) -> str:
    """
    Enlace a Maps, parqueo y transporte: el modelo solo debe usar cada dato según la pregunta.
    - Ubicación / dirección / dónde están: solo el enlace de Google Maps (nada más de este bloque).
    - Parqueo: solo si el usuario pregunta explícitamente por parqueo/estacionamiento/aparcamiento.
    - Transporte público: solo si pregunta por autobuses/rutas/transporte público/cómo llegar en bus.
    """
    maps = (getattr(clinic, "google_maps_link", None) or "").strip()
    parking = (getattr(clinic, "indicaciones_parqueo", None) or "").strip()
    transit = (getattr(clinic, "rutas_transporte_publico", None) or "").strip()
    if not maps and not parking and not transit:
        return ""

    if language == "en":
        lines: list[str] = ["\n\n[CLINIC LOCATION – follow these rules strictly:]"]
        if maps:
            lines.append(f"- Google Maps link (use ONLY for location/address/where the clinic is): {maps}")
            lines.append(
                "  If the patient asks where the clinic is, the address, location, or how to find you on the map, "
                "reply with ONLY this link and a very short line (e.g. 'Here is the location:'). "
                "Do NOT add parking or public transport details in that same reply unless they also asked for them."
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
        if maps:
            lines.append(f"- Enlace a Google Maps (solo para ubicación/dirección/dónde queda la clínica): {maps}")
            lines.append(
                "  Si el paciente pregunta dónde está la clínica, la dirección, ubicación o cómo encontrarlos en el mapa, "
                "responde ÚNICAMENTE con este enlace y una frase muy breve (ej. 'Aquí está la ubicación:'). "
                "No añadas en esa misma respuesta información de parqueo ni de transporte público, salvo que también lo pregunte."
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


def _is_within_opening_hours(clinic: ClinicConfig, fecha: str, hora: str) -> bool:
    """
    Valida si la fecha (YYYY-MM-DD) y hora (HH:MM) caen dentro del horario de atención
    configurado para la clínica.
    """
    opening_hours = getattr(clinic, "opening_hours", None) or {}
    if not opening_hours:
        # Si no hay horarios configurados, no restringimos.
        return True

    fecha = (fecha or "").strip()
    hora = (hora or "").strip()
    if not fecha or not hora:
        return False

    try:
        d = datetime.strptime(fecha, "%Y-%m-%d").date()
        t = datetime.strptime(hora, "%H:%M").time()
    except ValueError:
        # Formato inválido, dejamos que otras validaciones se encarguen.
        return True

    # Mapear weekday (0=lun..6=dom) a códigos mon/tue/... usados en clinics_mock.json
    weekday_codes = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_code = weekday_codes[d.weekday()]

    for block in opening_hours.values():
        days = block.get("days", [])
        start = (block.get("from") or "").strip()
        end = (block.get("to") or "").strip()
        if not days or not start or not end:
            continue
        if day_code not in days:
            continue
        try:
            start_t = datetime.strptime(start, "%H:%M").time()
            end_t = datetime.strptime(end, "%H:%M").time()
        except ValueError:
            continue
        if start_t <= t <= end_t:
            return True

    return False


def _load_services_catalog(path: Path) -> List[Dict[str, Any]]:
    """Carga el catálogo de servicios desde JSON (id, name, price, status, aliases)."""
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data.get("services", [])
    except (OSError, json.JSONDecodeError):
        return []


def _format_services_catalog_for_prompt(services: List[Dict[str, Any]], language: str) -> str:
    """Formatea el catálogo de servicios para inyectarlo en el system prompt (ES/EN)."""
    if not services:
        return ""
    lines = [
        "\n\n[CATÁLOGO DE SERVICIOS – Usa el 'id' cuando agendes una cita o cuando el usuario pregunte por precios.]",
        "Servicios disponibles (id | nombre | precio | estado):",
    ]
    if language == "en":
        lines[0] = "\n\n[SERVICES CATALOG – Use the 'id' when booking an appointment or when the user asks for prices.]"
        lines[1] = "Available services (id | name | price | status):"
    for s in services:
        sid = s.get("id", "")
        name = s.get("name_en", s.get("name", "")) if language == "en" else s.get("name", s.get("name_en", ""))
        price = s.get("price", "")
        currency = s.get("currency", "USD")
        status = s.get("status", "available")
        status_label = "available" if status == "available" else status
        lines.append(f"  - id: {sid} | {name} | {currency} {price} | {status_label}")
    lines.append("Si el usuario pregunta cuánto cuesta algo o por precios, responde con estos datos. Si no indica el tipo de cita al agendar, pregúntale antes de usar la herramienta.")
    if language == "en":
        lines[-1] = "If the user asks how much something costs or for prices, answer using this list. If they don't specify the type of appointment when booking, ask before calling the tool."
    return "\n".join(lines)


try:
    _SERVICES_RAW = _load_services_catalog(SERVICES_CATALOG_FILE)
except Exception:  # noqa: BLE001
    _SERVICES_RAW = []


def _services_for_clinic(clinic_id: str) -> List[Dict[str, Any]]:
    """
    Filtra servicios por `clinic_id`.

    Regla:
    - si el servicio tiene `clinic_id == clinic_id` => se incluye
    - si el servicio tiene `clinic_id == "*"` => se considera compartido
    - si el servicio no tiene `clinic_id` (compatibilidad) => se incluye
    """
    out: List[Dict[str, Any]] = []
    for s in _SERVICES_RAW:
        sc = s.get("clinic_id")
        if sc is None or sc == "*" or sc == clinic_id:
            out.append(s)
    return out


gemini_service = GeminiService(
    project_id=settings.PROJECT_ID,
    location=settings.LOCATION,
)
conversation_memory = ConversationMemoryService(project_id=settings.PROJECT_ID)


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

    # Validar que la fecha y hora estén dentro del horario de atención de la clínica.
    clinic_cfg = CLINICS_BY_ID.get(clinic_id)
    if clinic_cfg is not None and not _is_within_opening_hours(clinic_cfg, fecha, hora):
        if language == "en":
            msg = (
                "I can't schedule an appointment at that day/time because it is outside this clinic's opening hours. "
                "Please choose a time within the clinic schedule shown above (weekdays and Saturday only)."
            )
        else:
            msg = (
                "No puedo agendar una cita en ese día u hora porque está fuera del horario de atención de esta clínica. "
                "Por favor elige un horario dentro del horario de la clínica que ves arriba (solo de lunes a sábado)."
            )
        return {"error": "Fuera de horario", "mensaje": msg}

    # Si el nombre recibido es solo el primer nombre del mismo paciente, usar nombre completo: primero de metadata (Firestore), y si no hay nombre completo ahí, de la última cita en BigQuery.
    first_word = nombre.split()[0] if nombre.split() else nombre
    first_word_norm = first_word[:1].upper() + first_word[1:].lower() if first_word else ""
    use_full_name: str | None = None

    metadata = conversation_memory.get_metadata(clinic_id, from_number) or {}
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

    # Persistir nombre del paciente para saludos futuros
    try:
        conversation_memory.set_patient_name(clinic_id, from_number, nombre)
    except Exception:
        # No interrumpir el flujo de cita si falla la escritura de metadatos
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

        # Sincronizar con Google Calendar si está habilitado para la clínica.
        if clinic_cfg and clinic_cfg.calendar_sync_enabled and clinic_cfg.calendar_id:
            try:
                event_id = calendar_service.create_event_for_cita(
                    calendar_id=clinic_cfg.calendar_id,
                    cita=cita,
                    clinic_name=clinic_cfg.name,
                    assistant_name=assistant_name,
                )
                cita.calendar_event_id = event_id
                cita.calendar_id = clinic_cfg.calendar_id
                cita.sync_status = "ok"
                cita.sync_error_message = None
                db.add(cita)
                db.commit()
            except CalendarServiceError as exc:
                # Marcamos el error de sync, pero no rompemos el flujo de la cita.
                cita.sync_status = "error"
                cita.sync_error_message = str(exc)
                db.add(cita)
                db.commit()
        # #region agent log
        try:
            with open("debug-84132f.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps({"sessionId":"84132f","runId":"post-fix","hypothesisId":"B","location":"main.py:_handle_agendar_cita","message":"create_cita ok","data":{"fecha":fecha,"hora":hora},"timestamp":round(time.time()*1000)}) + "\n")
        except Exception:
            pass
        # #endregion
        if language == "en":
            mensaje = f"Done! I've scheduled your appointment for {fecha} at {hora} (service: {servicio})."
        else:
            mensaje = f"¡Listo! He agendado tu cita para el {fecha} a las {hora} (servicio: {servicio})."
        return {"mensaje": mensaje}
    except Exception as e:
        # #region agent log
        try:
            with open("debug-84132f.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps({"sessionId":"84132f","runId":"post-fix","hypothesisId":"B","location":"main.py:_handle_agendar_cita","message":"create_cita error","data":{"error":str(e)},"timestamp":round(time.time()*1000)}) + "\n")
        except Exception:
            pass
        # #endregion
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
        # Antes de actualizar el estado en BQ, intentamos cancelar en Calendar (si aplica).
        clinic_cfg = CLINICS_BY_ID.get(clinic_id)
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
                # Lanzar un job asíncrono de reintento puntual (no un cron).
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
    fecha = (args.get("fecha") or "").strip()
    hora = (args.get("hora") or "").strip()
    servicio = (args.get("servicio") or "").strip()
    if not fecha or not hora:
        if language == "en":
            msg = "I need the new date and time to reschedule (e.g. 2025-03-15 and 10:00)."
        else:
            msg = "Necesito la nueva fecha y hora para reagendar (ej. 2025-03-15 y 10:00)."
        return {"error": "Faltan fecha u hora", "mensaje": msg}

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

        # Marcar la cita actual como reagendada.
        update_cita_status(db, cita_activa, CITA_STATUS_REAGENDADA)

        # Crear la nueva cita en BigQuery.
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

        # Sincronización con Calendar: eliminamos el evento anterior (si existe) y
        # creamos uno nuevo para la cita reagendada.
        clinic_cfg = CLINICS_BY_ID.get(clinic_id)
        if clinic_cfg and clinic_cfg.calendar_sync_enabled and clinic_cfg.calendar_id:
            # Borrar evento anterior si tenía integración.
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

            # Crear evento para la nueva cita.
            try:
                event_id = calendar_service.create_event_for_cita(
                    calendar_id=clinic_cfg.calendar_id,
                    cita=cita_nueva,
                    clinic_name=clinic_cfg.name,
                    assistant_name=assistant_name,
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
        "Cuando el usuario diga 'próximo jueves', 'mañana', 'el lunes', etc., calcula la fecha correcta a partir de esta fecha de referencia y pasa a la herramienta en YYYY-MM-DD y HH:MM.]\n"
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
                    "\n\n[CRITICAL - APPOINTMENT TIMES: The clinic ONLY accepts appointments during the opening hours listed above. "
                    "NEVER suggest or confirm a specific time (e.g. 'How about 11:00 AM?') if that day or time is outside those hours. "
                    "If the patient asks for a day we are closed (e.g. Sunday) or a time outside the range, do NOT say you can book it. "
                    "Say clearly that that day/time is not available and ask them to choose another within the published schedule. "
                    "Only call agendar_cita or reagendar_cita with date and time that fall strictly within the opening hours.]"
                )
            else:
                schedule_rule = (
                    "\n\n[CRÍTICO - HORARIOS DE CITA: La clínica SOLO acepta citas dentro del horario de atención indicado arriba. "
                    "NUNCA sugieras ni confirmes una hora concreta (ej. '¿Te parece a las 11:00?) si ese día u hora está fuera de ese horario. "
                    "Si el paciente pide un día en que no abrimos (ej. domingo) o una hora fuera del rango, NO digas que puedes agendarla. "
                    "Di claramente que ese día/hora no está disponible y pídele que elija otro dentro del horario publicado. "
                    "Solo llama agendar_cita o reagendar_cita con fecha y hora que caigan estrictamente dentro del horario de atención.]"
                )
            system_prompt_effective = system_prompt_effective + schedule_rule

        location_text = _format_clinic_location_for_prompt(clinic_cfg, language)
        if location_text:
            system_prompt_effective = system_prompt_effective + location_text

    # Inyectar catálogo de servicios para que el modelo sepa precios, disponibilidad y pueda pedir el tipo de cita
    catalog_text = _format_services_catalog_for_prompt(_services_for_clinic(clinic_id), language)
    if catalog_text:
        system_prompt_effective = system_prompt_effective + catalog_text

    # Instrucción para herramientas de citas (agendar, cancelar, reagendar)
    if language == "en":
        tool_instruction = (
            "\n\n[You have three appointment tools. The clinic is from context (do not ask the user). "
            "(1) agendar_cita(nombre, fecha, hora, servicio): for new appointments. "
            "Only pass date and time that fall WITHIN the clinic's opening hours (shown above). "
            "If the patient asks for an impossible time (e.g. Sunday or outside 08:00-17:00 on weekdays), do NOT call the tool: say that time is not available and ask them to choose within the published schedule. "
            "'servicio' must be the exact 'id' string from the SERVICES CATALOG above (do not invent ids). "
            "If you already know the patient, use their full name and do not ask. Only ask for name if the appointment is for someone else. "
            "(2) cancelar_cita(): no parameters. Use when the user asks to cancel their appointment. "
            "(3) reagendar_cita(fecha, hora, servicio optional): when they want to change date/time. Only with date/time within opening hours. "
            "Date as YYYY-MM-DD and time as HH:MM; use the REFERENCE DATE AND TIME above for 'tomorrow', 'next Friday', etc. "
            "After running any tool successfully, reply with the 'mensaje' field it returns.]"
        )
    else:
        tool_instruction = (
            "\n\n[Tienes tres herramientas de citas. La clínica se toma del contexto (no la pidas al usuario). "
            "(1) agendar_cita(nombre, fecha, hora, servicio): para citas nuevas. "
            "Solo pases fecha y hora que estén DENTRO del horario de atención de la clínica (el indicado arriba). "
            "Si el paciente pide un horario imposible (ej. domingo o fuera de 08:00-17:00 entre semana, etc.), NO llames la herramienta: responde que ese horario no está disponible y pídele uno dentro del horario. "
            "El parámetro 'servicio' debe ser el 'id' exacto de uno de los servicios del catálogo de arriba (no inventes ids). "
            "Si ya conoces al paciente, usa su nombre completo y no preguntes. Solo pregunta el nombre si la cita es para otra persona. "
            "(2) cancelar_cita(): sin parámetros. Úsala cuando el usuario pida cancelar su cita (ej. 'quiero cancelar mi cita', 'cancela mi reserva'). "
            "(3) reagendar_cita(fecha, hora, servicio opcional): cuando pida cambiar la fecha/hora de su cita. Solo con fecha/hora dentro del horario de atención. "
            "La fecha en YYYY-MM-DD y hora en HH:MM; usa la FECHA Y HORA DE REFERENCIA de arriba para calcular 'mañana', 'próximo viernes', etc. "
            "Para fechas relativas (mañana, próximo lunes, etc.) usa SIEMPRE la referencia indicada arriba y pasa a la herramienta en YYYY-MM-DD y HH:MM. "
            "Después de ejecutar cualquier herramienta con éxito, responde al usuario con el texto del campo 'mensaje' que te devuelva.]"
        )
    system_prompt_effective = system_prompt_effective.strip() + tool_instruction

    chat_history = _build_chat_history_with_memory(clinic_id, from_number, body)

    def tool_handler(name: str, args: dict) -> dict:
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


@app.get("/webhooks/whatsapp", response_class=PlainTextResponse)
async def meta_whatsapp_verify(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> PlainTextResponse:
    """
    Verificación del webhook que Meta hace al configurar la URL (GET).
    Debes usar el mismo META_WEBHOOK_VERIFY_TOKEN en Meta Developer Console y en .env.
    """
    if hub_mode != "subscribe":
        return PlainTextResponse("Forbidden", status_code=403)
    expected = (settings.META_WEBHOOK_VERIFY_TOKEN or "").strip()
    if not expected or hub_verify_token != expected:
        logging.warning("Meta webhook verify: token no coincide o no configurado")
        return PlainTextResponse("Forbidden", status_code=403)
    return PlainTextResponse(content=hub_challenge or "", status_code=200)


@app.post("/webhooks/whatsapp")
async def meta_whatsapp_webhook(request: Request) -> Response:
    """
    Webhook WhatsApp Cloud API (Meta). JSON entrante; respuesta al usuario vía Graph API.

    - Identifica la clínica por metadata.phone_number_id → whatsapp_phone_number_id en clinics_mock.json.
    - demo_clinic_2 sin phone_number_id sigue usando solo Twilio hasta que la agregues.
    """
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    skip_sig = settings.META_WEBHOOK_SKIP_SIGNATURE_VERIFY
    secret = (settings.META_APP_SECRET or "").strip()
    if not skip_sig:
        if not secret or not verify_webhook_signature(raw, sig, secret):
            logging.warning("Meta webhook POST: firma inválida o META_APP_SECRET ausente")
            return Response(status_code=403)

    try:
        data = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Response(status_code=400)

    events = extract_incoming_whatsapp_events(data)
    if not events:
        return Response(status_code=200)

    token = (settings.META_WHATSAPP_ACCESS_TOKEN or "").strip()
    if not token:
        logging.error("META_WHATSAPP_ACCESS_TOKEN no configurado; no se puede responder por WhatsApp")
        return Response(status_code=200)

    graph_ver = settings.META_GRAPH_API_VERSION

    for ev in events:
        clinic_id = WHATSAPP_PHONE_NUMBER_ID_TO_CLINIC.get(ev.phone_number_id)
        if not clinic_id:
            logging.warning(
                "Meta webhook: phone_number_id no asignado a ninguna clínica: %s. "
                "Añade whatsapp_phone_number_id en clinics_mock.json",
                ev.phone_number_id,
            )
            continue

        clinic = CLINICS_BY_ID.get(clinic_id)
        if not clinic:
            continue

        from_number = _normalize_wa_id_for_storage(ev.wa_from)
        if ev.is_text:
            try:
                reply_text = _generate_and_persist_reply(
                    clinic_id=clinic_id,
                    from_number=from_number,
                    body=ev.text_body,
                    system_prompt=clinic.system_prompt,
                    clinic_name=clinic.name,
                    assistant_name=clinic.assistant_name,
                    system_prompt_en=getattr(clinic, "system_prompt_en", None),
                )
            except GeminiServiceError as e:
                logging.warning("GeminiServiceError in Meta webhook: %s", e)
                reply_text = (
                    "Ha ocurrido un problema temporal al procesar tu mensaje. "
                    "Por favor, inténtalo de nuevo más tarde."
                )
            except Exception:
                logging.exception("Error inesperado en Meta webhook")
                reply_text = (
                    "Ha ocurrido un error inesperado al procesar tu mensaje. "
                    "Si el problema persiste, contacta con la clínica por teléfono."
                )
        else:
            lang = _resolve_whatsapp_reply_language(clinic_id, from_number)
            reply_text = reply_for_meta_media_type(meta_type=ev.media_type, lang=lang)

        try:
            await send_text_message(
                graph_version=graph_ver,
                phone_number_id=ev.phone_number_id,
                to_wa_id=ev.wa_from,
                body=reply_text,
                access_token=token,
            )
        except Exception:
            logging.exception("Error enviando respuesta por Graph API (WhatsApp)")

    return Response(status_code=200)


@app.post("/whatsapp", response_class=Response)
async def whatsapp_webhook(
    clinic_id: str = Query(..., description="Identificador de la clínica (?clinic_id=xxx)"),
    from_number: str = Form(..., alias="From", description="Número del paciente enviado por Twilio."),
    body: str = Form(default="", alias="Body", description="Mensaje de texto enviado por el paciente."),
    num_media: str = Form(default="0", alias="NumMedia"),
    media_content_type_0: str | None = Form(default=None, alias="MediaContentType0"),
) -> Response:
    """
    Webhook principal de WhatsApp (Twilio).

    - Identifica la clínica mediante ?clinic_id=xxx.
    - Lee la configuración de la clínica desde data/clinics_mock.json.
    - Orquesta la llamada a Gemini y devuelve TwiML.
    - Solo adjuntos sin texto: plantilla fija (sin Gemini ni Firestore).
    """
    clinic = CLINICS_BY_ID.get(clinic_id)
    if clinic is None:
        # Twilio espera una respuesta 200 con TwiML; aquí devolvemos mensaje de error controlado.
        resp = MessagingResponse()
        resp.message("Lo sentimos, no se encontró la clínica asociada. Verifica el enlace de WhatsApp.")
        return Response(content=str(resp), media_type="application/xml")

    try:
        n_media = int((num_media or "0").strip() or "0")
    except ValueError:
        n_media = 0
    body_stripped = (body or "").strip()

    if n_media > 0 and not body_stripped:
        lang = _resolve_whatsapp_reply_language(clinic_id, from_number)
        reply_text = reply_for_twilio_media(mime_type=media_content_type_0, lang=lang)
        twiml_response = MessagingResponse()
        twiml_response.message(reply_text)
        return Response(content=str(twiml_response), media_type="application/xml")

    if not body_stripped and n_media == 0:
        empty = MessagingResponse()
        return Response(content=str(empty), media_type="application/xml")

    try:
        reply_text = _generate_and_persist_reply(
            clinic_id=clinic_id,
            from_number=from_number,
            body=body_stripped,
            system_prompt=clinic.system_prompt,
            clinic_name=clinic.name,
            assistant_name=clinic.assistant_name,
            system_prompt_en=getattr(clinic, "system_prompt_en", None),
        )
    except GeminiServiceError as e:
        logging.warning("GeminiServiceError in /whatsapp: %s", e)
        resp = MessagingResponse()
        resp.message(
            "Ha ocurrido un problema temporal al procesar tu mensaje. "
            "Por favor, inténtalo de nuevo más tarde."
        )
        return Response(content=str(resp), media_type="application/xml")
    except Exception:
        logging.exception("Error inesperado en webhook /whatsapp")
        traceback.print_exc(file=sys.stderr)
        resp = MessagingResponse()
        resp.message(
            "Ha ocurrido un error inesperado al procesar tu mensaje. "
            "Si el problema persiste, contacta con la clínica por teléfono."
        )
        return Response(content=str(resp), media_type="application/xml")

    twiml_response = MessagingResponse()
    twiml_response.message(reply_text)

    return Response(content=str(twiml_response), media_type="application/xml")


@app.get("/health", response_class=Response)
async def healthcheck() -> Response:
    """Sencillo healthcheck para verificar que la app está viva."""
    return Response(content="OK", media_type="text/plain")


@app.get("/health/gcp")
async def healthcheck_gcp() -> dict:
    """
    Diagnóstico de configuración GCP: credenciales, Firestore y Vertex AI (Gemini).
    Útil para ver qué falla antes de probar por WhatsApp.
    """
    result: dict = {
        "config": {"project_id": settings.PROJECT_ID, "location": settings.LOCATION},
        "firestore": None,
        "gemini": None,
    }

    # Probar Firestore (solo lectura de un doc de prueba)
    try:
        conversation_memory.get_recent_messages("_health_check", "+0000000000")
        result["firestore"] = "ok"
    except Exception as e:  # noqa: BLE001
        result["firestore"] = f"error: {type(e).__name__}: {e}"

    # Probar Gemini (una llamada mínima)
    try:
        reply = gemini_service.generate_reply(
            system_prompt="Eres un asistente. Responde solo: OK.",
            chat_history=[{"role": "user", "content": "Di hola"}],
            max_output_tokens=10,
        )
        result["gemini"] = "ok" if reply else "empty_response"
    except Exception as e:  # noqa: BLE001
        result["gemini"] = f"error: {type(e).__name__}: {e}"

    return result


@app.get("/health/meta")
async def health_meta() -> dict:
    """
    Comprueba que las variables Meta estén cargadas (sin exponer secretos)
    y qué phone_number_id están mapeados a clínicas.
    """
    return {
        "meta_waba_id_configured": bool((settings.META_WABA_ID or "").strip()),
        "meta_access_token_configured": bool((settings.META_WHATSAPP_ACCESS_TOKEN or "").strip()),
        "meta_verify_token_configured": bool((settings.META_WEBHOOK_VERIFY_TOKEN or "").strip()),
        "meta_app_secret_configured": bool((settings.META_APP_SECRET or "").strip()),
        "meta_webhook_skip_signature": settings.META_WEBHOOK_SKIP_SIGNATURE_VERIFY,
        "graph_api_version": settings.META_GRAPH_API_VERSION,
        "whatsapp_phone_number_ids_mapped": WHATSAPP_PHONE_NUMBER_ID_TO_CLINIC,
    }


@app.post("/jobs/sync-calendar-to-bigquery", response_model=None)
async def job_sync_calendar_to_bigquery(
    token: str = Query("", description="Debe coincidir con SCHEDULER_SYNC_SECRET en .env"),
):
    """
    Job HTTP para Cloud Scheduler: reconcilia Google Calendar → BigQuery
    (citas activas con ``calendar_event_id`` del bot).

    Configura ``SCHEDULER_SYNC_SECRET`` y llama con ``?token=...``.
    """
    expected = (settings.SCHEDULER_SYNC_SECRET or "").strip()
    if not expected or token != expected:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    pairs = [
        (cid, (getattr(cfg, "calendar_id", None) or "").strip())
        for cid, cfg in CLINICS_BY_ID.items()
        if getattr(cfg, "calendar_sync_enabled", False) and (getattr(cfg, "calendar_id", None) or "").strip()
    ]

    db = SessionLocal()
    try:
        result = run_calendar_to_bigquery_sync(db, clinic_calendar_pairs=pairs)
    finally:
        db.close()

    return result


@app.post("/chat", response_model=ChatResponse)
async def chat_json(
    clinic_id: str = Query(..., description="Identificador de la clínica (?clinic_id=xxx)"),
    payload: ChatRequest | None = None,
) -> ChatResponse:
    """
    JSON endpoint to simulate the WhatsApp flow for local testing.
    Same logic as /whatsapp but accepts JSON and returns JSON (no TwiML).
    """
    if payload is None:
        payload = ChatRequest(from_number="", body="")
    clinic = CLINICS_BY_ID.get(clinic_id)
    if clinic is None:
        return ChatResponse(
            reply="Lo sentimos, no se encontró la clínica asociada. Verifica el enlace."
        )
    try:
        reply_text = _generate_and_persist_reply(
            clinic_id=clinic_id,
            from_number=payload.from_number,
            body=payload.body,
            system_prompt=clinic.system_prompt,
            clinic_name=clinic.name,
            assistant_name=clinic.assistant_name,
            system_prompt_en=getattr(clinic, "system_prompt_en", None),
        )
    except GeminiServiceError as e:
        logging.warning("GeminiServiceError in /chat: %s", e)
        return ChatResponse(
            reply="Ha ocurrido un problema temporal al procesar tu mensaje. Inténtalo de nuevo más tarde."
        )
    except Exception:
        logging.exception("Error inesperado en endpoint /chat")
        traceback.print_exc(file=sys.stderr)
        return ChatResponse(
            reply="Ha ocurrido un error inesperado. Si persiste, contacta con la clínica por teléfono."
        )
    return ChatResponse(reply=reply_text)

