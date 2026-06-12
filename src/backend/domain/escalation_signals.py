"""
Señales heurísticas de queja fuerte o tema fiscal en el mensaje del paciente.

Sirven como compuerta barata: si NO hay estas señales (ni petición de humano) y la intención
es claramente de rutina (servicios/citas/info), podemos omitir la llamada LLM de detección de
derivación para ahorrar latencia y tokens, sin perder las escalaciones importantes basadas en
palabras clave. La derivación por contacto humano explícito se maneja aparte.

Ante una falsa señal positiva, simplemente NO se omite la detección (se comporta como hoy);
nunca fuerza una derivación por sí sola.
"""
from __future__ import annotations

import re

_COMPLAINT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bquej\w*",
        r"\breclam\w*",
        r"\bp[eé]simo\w*",
        r"\bterrible\w*",
        r"\bmal[ií]simo\w*",
        r"\bmal\s+servicio\b",
        r"\bmala\s+atenci[oó]n\b",
        r"\bme\s+maltrat\w*",
        r"\bmaltrat\w*",
        r"\bestafa\w*",
        r"\bfraude\w*",
        r"\binaceptable\b",
        r"\bindignante\b",
        r"\bexij\w*",
        r"\bdenuncia\w*",
        r"\bdemanda\w*",
        r"\breembols\w*",
        r"\bdevoluci[oó]n\s+de\s+dinero\b",
        r"\bcobro\s+(incorrecto|indebido|de\s+m[aá]s)\b",
        r"\bme\s+cobraron\s+(de\s+m[aá]s|mal)\b",
        # Inglés
        r"\bcomplaint\b",
        r"\brefund\b",
        r"\bawful\b",
        r"\bterrible\b",
        r"\bworst\b",
        r"\bbad\s+service\b",
        r"\bscam\b",
        r"\blawsuit\b",
        r"\bunacceptable\b",
        r"\bovercharged\b",
        r"\bcharged\s+me\s+(too\s+much|wrong)\b",
    )
)

_FISCAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bfactura\w*",
        r"\bfacturaci[oó]n\b",
        r"\bcr[eé]dito\s+fiscal\b",
        r"\bcomprobante\s+fiscal\b",
        r"\bcfdi\b",
        r"\brfc\b",
        r"\bnit\b",
        r"\biva\b",
        r"\bdeducir\b",
        r"\bdeducci[oó]n\b",
        r"\bretenci[oó]n\b",
        # Inglés
        r"\binvoice\b",
        r"\btax\s+(receipt|invoice|deduction|credit)\b",
        r"\bvat\b",
    )
)


def message_signals_complaint_or_fiscal(message: str) -> bool:
    """True si el mensaje tiene señales de queja fuerte o tema fiscal (solo el turno actual)."""
    text = (message or "").strip()
    if not text:
        return False
    if any(p.search(text) for p in _COMPLAINT_PATTERNS):
        return True
    if any(p.search(text) for p in _FISCAL_PATTERNS):
        return True
    return False


__all__ = ["message_signals_complaint_or_fiscal"]
