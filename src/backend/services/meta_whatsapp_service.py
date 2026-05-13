"""
Cliente y utilidades para WhatsApp Cloud API (Meta).

- Verificación de firma X-Hub-Signature-256 del webhook.
- Envío de mensajes de texto vía Graph API (sin plantillas).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Límite oficial de caracteres por mensaje de texto en WhatsApp.
MAX_TEXT_BODY_CHARS = 4096


def verify_webhook_signature(payload: bytes, x_hub_signature_256: str | None, app_secret: str) -> bool:
    """
    Valida la cabecera X-Hub-Signature-256: sha256=<hex>.
    Si app_secret está vacío, devuelve False (no confiar en webhooks sin secreto).
    """
    if not app_secret or not x_hub_signature_256:
        return False
    sig = x_hub_signature_256.strip()
    if not sig.startswith("sha256="):
        return False
    expected_hex = sig[7:]
    mac = hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha256)
    digest = mac.hexdigest()
    return hmac.compare_digest(digest, expected_hex)


def split_text_chunks(text: str, max_chars: int = MAX_TEXT_BODY_CHARS) -> list[str]:
    """Parte un texto largo en trozos <= max_chars."""
    t = (text or "").strip()
    if not t:
        return []
    return [t[i : i + max_chars] for i in range(0, len(t), max_chars)]


async def send_text_message(
    *,
    graph_version: str,
    phone_number_id: str,
    to_wa_id: str,
    body: str,
    access_token: str,
) -> None:
    """
    Envía un mensaje de texto al usuario (wa_id sin prefijo whatsapp:).
    Lanza httpx.HTTPStatusError si Graph API rechaza la petición.
    """
    url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}"}
    chunks = split_text_chunks(body)
    if not chunks:
        chunks = [" "]

    async with httpx.AsyncClient(timeout=60.0) as client:
        for part in chunks:
            payload: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_wa_id,
                "type": "text",
                "text": {"preview_url": False, "body": part},
            }
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "Graph API error sending WhatsApp message: %s %s",
                    resp.status_code,
                    resp.text[:500],
                )
            resp.raise_for_status()


def send_text_message_sync(
    *,
    graph_version: str,
    phone_number_id: str,
    to_wa_id: str,
    body: str,
    access_token: str,
) -> None:
    """
    Igual que ``send_text_message`` pero bloqueante (httpx sync).

    Usado cuando la respuesta al paciente debe enviarse solo después de notificar al especialista.
    """
    url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {access_token}"}
    chunks = split_text_chunks(body)
    if not chunks:
        chunks = [" "]

    with httpx.Client(timeout=60.0) as client:
        for part in chunks:
            payload: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_wa_id,
                "type": "text",
                "text": {"preview_url": False, "body": part},
            }
            resp = client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "Graph API error sending WhatsApp message (sync): %s %s",
                    resp.status_code,
                    resp.text[:500],
                )
            resp.raise_for_status()


@dataclass(frozen=True)
class MetaWhatsappIncoming:
    """Un mensaje entrante del webhook Meta, normalizado."""

    is_text: bool
    phone_number_id: str
    wa_from: str
    text_body: str = ""
    media_type: str = ""


def extract_incoming_whatsapp_events(data: dict[str, Any]) -> list[MetaWhatsappIncoming]:
    """
    Parsea el JSON del webhook y devuelve eventos por mensaje.

    - Solo ``type == "text"`` con cuerpo no vacío se marca como texto (pasa al agente).
    - Cualquier otro tipo (imagen con caption, audio, etc.) es ``is_text=False``;
      el caption no se usa como entrada del modelo.
    """
    out: list[MetaWhatsappIncoming] = []
    if data.get("object") != "whatsapp_business_account":
        return out

    for entry in data.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            meta = value.get("metadata") or {}
            phone_number_id = (meta.get("phone_number_id") or "").strip()
            for msg in value.get("messages") or []:
                from_id = (msg.get("from") or "").strip()
                if not phone_number_id or not from_id:
                    continue
                mtype = (msg.get("type") or "").strip().lower()
                if mtype == "text":
                    text_obj = msg.get("text") or {}
                    body = (text_obj.get("body") or "").strip()
                    if body:
                        out.append(
                            MetaWhatsappIncoming(
                                True,
                                phone_number_id,
                                from_id,
                                text_body=body,
                            )
                        )
                    continue
                if mtype:
                    out.append(
                        MetaWhatsappIncoming(
                            False,
                            phone_number_id,
                            from_id,
                            media_type=mtype,
                        )
                    )
    return out


def extract_incoming_text_messages(
    data: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """
    Parsea el JSON del webhook y devuelve lista de (phone_number_id, wa_id_from, text_body).

    Solo mensajes entrantes tipo text. Ignora statuses y otros tipos.
    """
    return [
        (e.phone_number_id, e.wa_from, e.text_body)
        for e in extract_incoming_whatsapp_events(data)
        if e.is_text
    ]
