from __future__ import annotations

from langdetect import LangDetectException, detect


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

    return "es"
