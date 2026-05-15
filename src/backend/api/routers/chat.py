from __future__ import annotations

import logging
import sys
import traceback

from fastapi import APIRouter, Depends, Query

from ..internal_auth import require_internal_api_key
from ...bootstrap import CLINICS_BY_ID
from ...schemas import ChatRequest, ChatResponse
from ...services.gemini_service import GeminiServiceError

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_internal_api_key)])
async def chat_json(
    clinic_id: str = Query(..., description="Identificador de la clínica (?clinic_id=xxx)"),
    payload: ChatRequest | None = None,
) -> ChatResponse:
    """
    JSON endpoint to simulate the WhatsApp flow for local testing.
    Same logic as /whatsapp but accepts JSON and returns JSON (no TwiML).

    Si ``INTERNAL_API_KEY`` está definido en entorno, envía
    ``Authorization: Bearer <token>`` o ``X-API-Key: <token>``.
    """
    if payload is None:
        payload = ChatRequest(from_number="", body="")
    clinic = CLINICS_BY_ID.get(clinic_id)
    if clinic is None:
        return ChatResponse(
            reply="Lo sentimos, no se encontró la clínica asociada. Verifica el enlace."
        )
    try:
        from ...bootstrap import _generate_and_persist_reply

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
