from __future__ import annotations

import logging
import sys
import traceback

from fastapi import APIRouter, Form, Query, Response
from twilio.twiml.messaging_response import MessagingResponse

from ...bootstrap import CLINICS_BY_ID
from ...services.gemini_service import GeminiServiceError
from ...services.whatsapp_media_replies import reply_for_twilio_media

router = APIRouter(tags=["whatsapp-twilio"])


@router.post("/whatsapp", response_class=Response)
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
        resp = MessagingResponse()
        resp.message("Lo sentimos, no se encontró la clínica asociada. Verifica el enlace de WhatsApp.")
        return Response(content=str(resp), media_type="application/xml")

    try:
        n_media = int((num_media or "0").strip() or "0")
    except ValueError:
        n_media = 0
    body_stripped = (body or "").strip()

    if n_media > 0 and not body_stripped:
        from ...bootstrap import _resolve_whatsapp_reply_language

        lang = _resolve_whatsapp_reply_language(clinic_id, from_number)
        reply_text = reply_for_twilio_media(mime_type=media_content_type_0, lang=lang)
        twiml_response = MessagingResponse()
        twiml_response.message(reply_text)
        return Response(content=str(twiml_response), media_type="application/xml")

    if not body_stripped and n_media == 0:
        empty = MessagingResponse()
        return Response(content=str(empty), media_type="application/xml")

    try:
        from ...bootstrap import _generate_and_persist_reply

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
