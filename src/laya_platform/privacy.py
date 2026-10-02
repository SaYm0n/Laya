"""PII redaction before data leaves the platform (DG-1; full guardrails are F9).

Replaces, inside every string of a state (keys are kept), the identifiers that must not reach an
external LLM or a training file: e-mail addresses, CPF and CNPJ (numeric and the alphanumeric
CNPJ format, both checked by their check digits, so ordinary numbers are left alone), payment
card numbers (Luhn-checked) and Brazilian phone numbers with their area code. Each kind becomes
a fixed placeholder (``[CPF]``, ``[EMAIL]``...) so the text keeps its shape for the model.

Deliberately narrow: names, addresses and free-text health or financial details are not detected
here. That is the F9 detector set, and the reason real data still needs an approved DG-1.
"""

from __future__ import annotations

import hmac
import json
import re
from collections import Counter
from collections.abc import Callable
from hashlib import sha256
from typing import Any

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
CNPJ = re.compile(
    r"(?<![A-Za-z0-9])[A-Z0-9]{2}\.?[A-Z0-9]{3}\.?[A-Z0-9]{3}/?[A-Z0-9]{4}-?\d{2}(?![A-Za-z0-9])"
)
CPF = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
#: With the area code (DDD): 8 bare digits are too often something else (a card group, an id).
PHONE = re.compile(r"(?<!\d)(?<!\d )(?:\+?55[ ]?)?(?:\(\d{2}\)|\d{2})[ ]?9?\d{4}[- ]?\d{4}(?!\d)")


def _digits(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", text.upper())


def _cpf_valid(text: str) -> bool:
    digits = [int(c) for c in _digits(text)]
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(d * w for d, w in zip(digits[:size], range(size + 1, 1, -1), strict=True))
        if (total * 10 % 11) % 10 != digits[size]:
            return False
    return True


def _cnpj_valid(text: str) -> bool:
    """Numeric and alphanumeric CNPJ: each character counts as its code point minus 48."""
    chars = _digits(text)
    if len(chars) != 14 or not chars[12:].isdigit() or len(set(chars)) == 1:
        return False
    values = [ord(c) - 48 for c in chars]
    for size, weights in (
        (12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
        (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
    ):
        remainder = sum(v * w for v, w in zip(values[:size], weights, strict=True)) % 11
        if (0 if remainder < 2 else 11 - remainder) != values[size]:
            return False
    return True


def _luhn_valid(text: str) -> bool:
    digits = [int(c) for c in re.sub(r"\D", "", text)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


#: (placeholder, pattern, validator) in the order they apply: longer identifiers first.
DETECTORS: tuple[tuple[str, re.Pattern[str], Callable[[str], bool] | None], ...] = (
    ("EMAIL", EMAIL, None),
    ("CNPJ", CNPJ, _cnpj_valid),
    ("CPF", CPF, _cpf_valid),
    ("CARD", CARD, _luhn_valid),
    ("PHONE", PHONE, None),
)


def redact_text(text: str, counts: Counter[str] | None = None) -> str:
    for kind, pattern, valid in DETECTORS:

        def replace(match: re.Match[str], kind: str = kind, valid: Any = valid) -> str:
            if valid is not None and not valid(match.group(0)):
                return match.group(0)
            if counts is not None:
                counts[kind] += 1
            return f"[{kind}]"

        text = pattern.sub(replace, text)
    return text


def redact(value: Any) -> tuple[Any, dict[str, int]]:
    """``value`` with every string redacted (dict keys kept), and how many of each kind."""
    counts: Counter[str] = Counter()

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            return redact_text(item, counts)
        if isinstance(item, dict):
            return {key: walk(inner) for key, inner in item.items()}
        if isinstance(item, list | tuple):
            return [walk(inner) for inner in item]
        return item

    return walk(value), dict(counts)


def input_hmac(key: bytes, state: Any) -> str:
    """Keyed pseudonym of an input (canonical JSON): audited instead of the input itself, and
    used to check that an exported input is the one that was decided."""
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, canonical.encode("utf-8"), sha256).hexdigest()
