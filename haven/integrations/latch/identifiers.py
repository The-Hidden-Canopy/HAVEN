"""Latch IDs are opaque data; encode once at the actual HTTP boundary."""
import unicodedata
from urllib.parse import quote


def identifier(value):
    if (not isinstance(value, str) or not value or '\\' in value or
            any(unicodedata.category(c) in ('Cc', 'Cs') for c in value) or
            any(part in ('', '.', '..') for part in value.split('/'))):
        from .service import LatchConfigError
        raise LatchConfigError('Ambiguous or invalid Latch identifier')
    return value


def segment(value):
    return quote(identifier(value), safe='', encoding='utf-8', errors='strict')
