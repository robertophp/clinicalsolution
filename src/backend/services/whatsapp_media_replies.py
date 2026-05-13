"""
Plantillas fijas para mensajes entrantes que no son texto puro (WhatsApp).
Bilingüe ES/EN según el idioma de la conversación guardado o detección.
"""
from __future__ import annotations

from typing import Literal

Language = Literal["es", "en"]

# Plantilla foto/video (Meta: siempre si type es image/video, aunque haya caption).
MSG_PHOTO_VIDEO_ES = (
    "Gracias por tu mensaje. Por este canal no puedo revisar fotos ni videos; "
    "para eso lo ideal es que te comuniques directamente con un profesional. "
    "Si prefieres, podemos seguir conversando por aquí solo por texto o te ayudo a que te comuniques con un profesional disponible. "
    "¿Cuál opción te parece mejor?"
)

MSG_PHOTO_VIDEO_EN = (
    "Thank you for your message. I can't review photos or videos through this channel; "
    "for that, it's best to contact a healthcare professional directly. "
    "If you prefer, we can keep chatting here by text only, or I can help you reach an available professional. "
    "Which option works better for you?"
)


def _generic_label(meta_type: str, lang: Language) -> str:
    """Etiqueta amigable para el placeholder del mensaje genérico."""
    t = (meta_type or "").lower()
    if lang == "en":
        mapping = {
            "audio": "voice message",
            "sticker": "sticker",
            "document": "file",
            "location": "location",
            "contacts": "contact card",
            "reaction": "reaction",
        }
        return mapping.get(t, "message")
    mapping = {
        "audio": "nota de voz",
        "sticker": "sticker",
        "document": "archivo",
        "location": "ubicación",
        "contacts": "tarjeta de contacto",
        "reaction": "reacción",
    }
    return mapping.get(t, "mensaje")


def reply_for_meta_media_type(*, meta_type: str, lang: Language) -> str:
    """
    Respuesta para webhook Meta cuando el mensaje no es texto puro
    (incl. imagen o video con caption: siempre plantilla, no pasa al modelo).
    """
    t = (meta_type or "").lower()
    if t in ("image", "video"):
        return MSG_PHOTO_VIDEO_EN if lang == "en" else MSG_PHOTO_VIDEO_ES
    label = _generic_label(t, lang)
    if lang == "en":
        return (
            f"I'm sorry I can't interpret your {label} in this chat ☹️. "
            "Could you please send your message in writing? That way I can help you better. 💙"
        )
    return (
        f"Lamento no poder interpretar tu {label} desde este chat ☹️. "
        "¿Podrías ayudarme escribiendo tu mensaje? Así podré atenderte mejor. 💙"
    )


def reply_for_twilio_media(*, mime_type: str | None, lang: Language) -> str:
    """
    Twilio: solo medio sin texto en Body. Clasifica por Content-Type del primer adjunto.
    """
    mt = (mime_type or "").lower()
    if mt.startswith("image/") or mt.startswith("video/"):
        return MSG_PHOTO_VIDEO_EN if lang == "en" else MSG_PHOTO_VIDEO_ES
    if mt.startswith("audio/"):
        return reply_for_meta_media_type(meta_type="audio", lang=lang)
    if "pdf" in mt or "document" in mt or mt.startswith("text/"):
        return reply_for_meta_media_type(meta_type="document", lang=lang)
    return reply_for_meta_media_type(meta_type="other", lang=lang)
