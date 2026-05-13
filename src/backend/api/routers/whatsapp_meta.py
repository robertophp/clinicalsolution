from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, Response

from ...bootstrap import (
    CLINICS_BY_ID,
    WHATSAPP_PHONE_NUMBER_ID_TO_CLINIC,
    settings,
)
from ...domain.wa_normalization import _normalize_wa_id_for_storage
from ...services.gemini_service import GeminiServiceError
from ...services.meta_whatsapp_service import (
    extract_incoming_whatsapp_events,
    send_text_message,
    verify_webhook_signature,
)
from ...services.whatsapp_media_replies import reply_for_meta_media_type

router = APIRouter(tags=["whatsapp-meta"])


@router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
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


@router.post("/webhooks/whatsapp")
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
                from ...bootstrap import _generate_and_persist_reply

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
            from ...bootstrap import _resolve_whatsapp_reply_language

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
