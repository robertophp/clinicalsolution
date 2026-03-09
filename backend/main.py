from __future__ import annotations

import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, Form, Query
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError
from twilio.twiml.messaging_response import MessagingResponse

from .config import settings
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
    system_prompt: str
    assistant_name: str = "Asistente Virtual"  # Nombre con el que se presenta el bot


class ChatRequest(BaseModel):
    """Request body for the JSON /chat endpoint (testing without Twilio)."""

    from_number: str = ""
    body: str = ""


class ChatResponse(BaseModel):
    """JSON response with the assistant reply."""

    reply: str


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


def _generate_and_persist_reply(
    clinic_id: str,
    from_number: str,
    body: str,
    system_prompt: str,
    clinic_name: str,
    assistant_name: str = "Asistente Virtual",
) -> str:
    """
    Recupera historial, construye system instruction (clínica + primer mensaje vs conversacional),
    llama a Gemini con system primero e historial después, persiste y devuelve la respuesta.
    """
    history = conversation_memory.get_recent_messages(clinic_id, from_number)
    is_first_message = len(history) == 0

    # Siempre incluir nombre y clínica en el contexto para que el asistente responda correctamente en cualquier turno
    identity_line = (
        f"\n\n[Datos del asistente: Tu nombre es {assistant_name}. Trabajas para la clínica {clinic_name}. "
        f"Cuando te pregunten cómo te llamas, quién eres o con quién hablan, responde siempre con el nombre {assistant_name}.]\n"
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

    system_prompt_effective = system_prompt.strip() + identity_line + extra_instruction
    chat_history = _build_chat_history_with_memory(clinic_id, from_number, body)

    reply_text = gemini_service.generate_reply(
        system_prompt=system_prompt_effective,
        chat_history=chat_history,
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

