"""Utilidad de test: contar emojis en un texto para verificar el límite (máx. 2)."""
from __future__ import annotations

_EMOJI_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F300, 0x1FAFF),  # símbolos, pictogramas, emoticones extendidos
    (0x2600, 0x26FF),    # símbolos misceláneos
    (0x2700, 0x27BF),    # dingbats
    (0x2B00, 0x2BFF),    # flechas/estrellas decorativas
    (0x2764, 0x2764),    # corazón rojo
    (0x1F1E6, 0x1F1FF),  # indicadores regionales (banderas)
)

# Caracteres modificadores que no cuentan como emoji independiente.
_IGNORED = {0xFE0F, 0x200D, 0x20E3}


def count_emojis(text: str) -> int:
    """Cuenta emojis visibles, ignorando selectores de variación y ZWJ."""
    total = 0
    for ch in text or "":
        code = ord(ch)
        if code in _IGNORED:
            continue
        if 0x1F3FB <= code <= 0x1F3FF:  # modificadores de tono de piel
            continue
        if any(lo <= code <= hi for lo, hi in _EMOJI_RANGES):
            total += 1
    return total
