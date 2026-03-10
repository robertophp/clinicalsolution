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
from .repositories import create_cita
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

try:
    CLINICS_BY_ID = _load_clinics_config(CLINICS_FILE)
except Exception as exc:  # noqa: BLE001
    # En un contexto real se podría loggear y dejar que la app falle en el healthcheck.
    raise RuntimeError("Error cargando la configuración de clínicas.") from exc


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
    if not all([nombre, fecha, hora]):
        if language == "en":
            msg = "I couldn't schedule the appointment: name, date or time are missing. Please confirm all details."
        else:
            msg = "No pude agendar la cita: faltan nombre, fecha u hora. Por favor confirma todos los datos."
        return {"error": "Faltan datos", "mensaje": msg}
    db = SessionLocal()
    try:
        create_cita(
            db,
            clinic_id=clinic_id,
            paciente_nombre=nombre,
            telefono=from_number or "Sin teléfono",
            fecha=fecha,
            hora=hora,
        )
        # #region agent log
        try:
            with open("debug-84132f.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps({"sessionId":"84132f","runId":"post-fix","hypothesisId":"B","location":"main.py:_handle_agendar_cita","message":"create_cita ok","data":{"fecha":fecha,"hora":hora},"timestamp":round(time.time()*1000)}) + "\n")
        except Exception:
            pass
        # #endregion
        if language == "en":
            mensaje = f"Done! I've scheduled your appointment for {fecha} at {hora}."
        else:
            mensaje = f"¡Listo! He agendado tu cita para el {fecha} a las {hora}."
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

    # Idioma de la conversación: si hay historial reciente, reutilizamos el que haya en Firestore.
    # Si no hay historial (nueva sesión) o falta el dato, usamos langdetect y lo persistimos.
    language: str
    if not is_first_message:
        metadata = conversation_memory.get_metadata(clinic_id, from_number) or {}
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

    # Siempre incluir nombre y clínica en el contexto para que el asistente responda correctamente en cualquier turno
    identity_line = (
        f"\n\n[Datos del asistente: Tu nombre es {assistant_name}. Trabajas para la clínica {clinic_name}. "
        f"El ID de la clínica en este chat es: {clinic_id}. "
        f"Cuando te pregunten cómo te llamas, quién eres o con quién hablan, responde siempre con el nombre {assistant_name}. "
        "NUNCA preguntes al usuario a qué clínica quiere ir ni pidas que indique la clínica: el paciente ya está hablando con la clínica actual; usa siempre la clínica del contexto.]\n"
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

    # Instrucción para la herramienta agendar_cita (function calling)
    tool_instruction = (
        "\n\n[Tienes la herramienta agendar_cita(nombre, fecha, hora). La clínica se toma del contexto (no la pidas al usuario). "
        "Acepta fechas y horas en lenguaje natural. OBLIGATORIO: usa SIEMPRE la 'FECHA Y HORA DE REFERENCIA' indicada arriba como hoy/ahora para calcular fechas relativas: "
        "'próximo jueves' = el jueves de la semana actual o la siguiente según corresponda; 'mañana' = fecha de referencia + 1 día; 'el lunes' = el lunes próximo; etc. "
        "Cuando el usuario confirme, calcula la fecha correcta a partir de esa referencia y pasa a la herramienta fecha en YYYY-MM-DD y hora en HH:MM. "
        "Nunca pidas al usuario que escriba la fecha en formato aaaa-mm-dd ni que indique la clínica. "
        "Úsala solo cuando el usuario confirme explícitamente nombre, fecha y hora para agendar. "
        "Después de ejecutarla con éxito, responde al usuario usando exactamente el texto del campo 'mensaje' que te devuelva la herramienta.]"
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

