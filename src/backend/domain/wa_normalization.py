from __future__ import annotations


def _normalize_wa_id_for_storage(wa_id: str) -> str:
    """
    Meta envía el remitente como dígitos (ej. 50370211900).
    Unificamos con el formato usado con Twilio: whatsapp:+<código país><número>.
    """
    digits = "".join(c for c in (wa_id or "") if c.isdigit())
    if not digits:
        return (wa_id or "").strip() or "unknown"
    return f"whatsapp:+{digits}"
