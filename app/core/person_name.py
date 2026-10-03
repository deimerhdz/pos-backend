"""Reglas del nombre completo de una persona (spec 091, A-100).

Definición única en `specs/091-admin-subdominio-nombre-completo/contracts/full-name-rules.md`;
el frontend la replica en `shared/validators/full-name.validator.ts` y ambas
suites recorren los mismos vectores. Sin dependencias: `unicodedata` y `re`.
"""
import re
import unicodedata

FULL_NAME_REQUIRED_MESSAGE = "El nombre es obligatorio"
FULL_NAME_LENGTH_MESSAGE = "El nombre debe tener entre 2 y 100 caracteres"
FULL_NAME_FORMAT_MESSAGE = (
    "El nombre solo puede contener letras, espacios, apóstrofes y guiones, y al menos dos letras"
)

FULL_NAME_MIN_LENGTH = 2
FULL_NAME_MAX_LENGTH = 100

# Letra latina: rangos explícitos (no `\p{L}`, que `re` no soporta) para que
# Python y JavaScript coincidan. Excluye × (U+00D7) y ÷ (U+00F7).
_LATIN_LETTER = "A-Za-zÀ-ÖØ-öø-ÿĀ-ɏḀ-ỿ"
_ALLOWED_CHARS = re.compile(rf"[{_LATIN_LETTER} '’\-]+")
_LETTER = re.compile(rf"[{_LATIN_LETTER}]")


def normalize_full_name(value: str | None) -> str:
    """Recorta, normaliza a NFC y valida. Devuelve el valor a guardar o lanza
    `ValueError` con el primer error en el orden obligatorio → longitud → formato."""
    text = unicodedata.normalize("NFC", (value or "").strip())

    if not text:
        raise ValueError(FULL_NAME_REQUIRED_MESSAGE)

    # `len` de `str` cuenta puntos de código, no unidades UTF-16.
    if not FULL_NAME_MIN_LENGTH <= len(text) <= FULL_NAME_MAX_LENGTH:
        raise ValueError(FULL_NAME_LENGTH_MESSAGE)

    if _ALLOWED_CHARS.fullmatch(text) is None or len(_LETTER.findall(text)) < 2:
        raise ValueError(FULL_NAME_FORMAT_MESSAGE)

    return text
