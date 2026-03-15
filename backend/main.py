from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, Form, Query
from fastapi.responses import Response
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


BASE_DIR = Path(__file__).resolve().parent
CLINICS_FILE = BASE_DIR / "data" / "clinics_mock.json"
SERVICES_CATALOG_FILE = BASE_DIR / "data" / "services_catalog.json"

try:
    CLINICS_BY_ID = _load_clinics_config(CLINICS_FILE)
except Exception as exc:  # noqa: BLE001
    # En un contexto real se podría loggear y dejar que la app falle en el healthcheck.
    raise RuntimeError("Error cargando la configuración de clínicas.") from exc


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


def _handle_agendar_cita(from_number: str, clinic_id: str, language: str, args: dict) -> dict:
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
        create_cita(
            db,
            clinic_id=clinic_id,
            paciente_nombre=nombre,
            telefono=from_number or "Sin teléfono",
            fecha=fecha,
            hora=hora,
            razon_cita=servicio,
        )
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


def _handle_reagendar_cita(from_number: str, clinic_id: str, language: str, args: dict) -> dict:
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

        update_cita_status(db, cita_activa, CITA_STATUS_REAGENDADA)
        create_cita(
            db,
            clinic_id=clinic_id,
            paciente_nombre=nombre,
            telefono=from_number or "Sin teléfono",
            fecha=fecha,
            hora=hora,
            razon_cita=razon or "revision",
        )
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

    # Inyectar horarios de la clínica
    clinic_cfg = CLINICS_BY_ID.get(clinic_id)
    if clinic_cfg is not None:
        schedule_text = _format_opening_hours_for_prompt(clinic_cfg, language)
        if schedule_text:
            system_prompt_effective = system_prompt_effective + schedule_text

    # Inyectar catálogo de servicios para que el modelo sepa precios, disponibilidad y pueda pedir el tipo de cita
    catalog_text = _format_services_catalog_for_prompt(_SERVICES_RAW, language)
    if catalog_text:
        system_prompt_effective = system_prompt_effective + catalog_text

    # Instrucción para herramientas de citas (agendar, cancelar, reagendar)
    tool_instruction = (
        "\n\n[Tienes tres herramientas de citas. La clínica se toma del contexto (no la pidas al usuario). "
        "(1) agendar_cita(nombre, fecha, hora, servicio): para citas nuevas. "
        "El parámetro 'servicio' debe ser el id de uno de los servicios del catálogo (ej. limpieza, revision, extraccion). "
        "Si ya conoces al paciente, usa su nombre completo y no preguntes. Solo pregunta el nombre si la cita es para otra persona. "
        "(2) cancelar_cita(): sin parámetros. Úsala cuando el usuario pida cancelar su cita (ej. 'quiero cancelar mi cita', 'cancela mi reserva'). "
        "(3) reagendar_cita(fecha, hora, servicio opcional): cuando pida cambiar la fecha/hora de su cita (ej. 'reagendar para el viernes', 'cambiar mi cita a mañana a las 10'). "
        "La fecha en YYYY-MM-DD y hora en HH:MM; usa la FECHA Y HORA DE REFERENCIA de arriba para calcular 'mañana', 'próximo viernes', etc. "
        "Si no indica tipo de servicio al reagendar, no hace falta pasarlo. "
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
                args=args,
            )
        if name == "cancelar_cita":
            return _handle_cancelar_cita(from_number=from_number, clinic_id=clinic_id, language=language)
        if name == "reagendar_cita":
            return _handle_reagendar_cita(
                from_number=from_number,
                clinic_id=clinic_id,
                language=language,
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


@app.post("/whatsapp", response_class=Response)
async def whatsapp_webhook(
    clinic_id: str = Query(..., description="Identificador de la clínica (?clinic_id=xxx)"),
    from_number: str = Form(..., alias="From", description="Número del paciente enviado por Twilio."),
    body: str = Form(..., alias="Body", description="Mensaje de texto enviado por el paciente."),
) -> Response:
    """
    Webhook principal de WhatsApp (Twilio).

    - Identifica la clínica mediante ?clinic_id=xxx.
    - Lee la configuración de la clínica desde data/clinics_mock.json.
    - Orquesta la llamada a Gemini y devuelve TwiML.
    """
    clinic = CLINICS_BY_ID.get(clinic_id)
    if clinic is None:
        # Twilio espera una respuesta 200 con TwiML; aquí devolvemos mensaje de error controlado.
        resp = MessagingResponse()
        resp.message("Lo sentimos, no se encontró la clínica asociada. Verifica el enlace de WhatsApp.")
        return Response(content=str(resp), media_type="application/xml")

    try:
        reply_text = _generate_and_persist_reply(
            clinic_id=clinic_id,
            from_number=from_number,
            body=body,
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

