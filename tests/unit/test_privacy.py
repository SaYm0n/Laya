"""PII redaction (DG-1): what is replaced, what is left alone, and the input HMAC."""

from __future__ import annotations

import pytest

from laya_platform.privacy import input_hmac, redact, redact_text

# Synthetic identifiers with valid check digits (generated for tests; they belong to no one).
CPF = "529.982.247-25"
CNPJ = "11.222.333/0001-81"
CNPJ_ALNUM = "12.ABC.345/01DE-35"  # the alphanumeric format's published example
CARD = "4111 1111 1111 1111"  # a well-known test card number


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"meu CPF é {CPF}.", "meu CPF é [CPF]."),
        ("cpf 52998224725 ok", "cpf [CPF] ok"),
        (f"empresa {CNPJ}", "empresa [CNPJ]"),
        (f"empresa {CNPJ_ALNUM}", "empresa [CNPJ]"),
        (f"cartão {CARD}", "cartão [CARD]"),
        ("escreva para joao.silva@empresa.com.br", "escreva para [EMAIL]"),
        ("ligue (11) 98765-4321", "ligue [PHONE]"),
        ("whatsapp +55 11 987654321", "whatsapp [PHONE]"),
    ],
)
def test_identifiers_are_replaced(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "pedido 12345678901 confirmado",  # 11 digits, not a valid CPF
        "CPF 111.111.111-11",  # repeated digits are not a CPF
        "CNPJ 11.222.333/0001-82",  # wrong check digit
        "nota 4111 1111 1111 1112",  # fails Luhn
        "versão 1.2.3, total de 42 itens em 2026",
    ],
)
def test_ordinary_numbers_are_left_alone(text: str) -> None:
    assert redact_text(text) == text


def test_every_string_of_a_state_is_redacted_and_counted() -> None:
    state = {
        "body": f"CPF {CPF}, e-mail a@b.com",
        "history": [f"cartão {CARD}", {"note": "nada aqui"}],
        "cpf_field_name_is_kept": 3,
    }
    redacted, counts = redact(state)
    assert redacted == {
        "body": "CPF [CPF], e-mail [EMAIL]",
        "history": ["cartão [CARD]", {"note": "nada aqui"}],
        "cpf_field_name_is_kept": 3,
    }
    assert counts == {"CPF": 1, "EMAIL": 1, "CARD": 1}
    assert redact("sem nada")[1] == {}


def test_the_input_hmac_is_keyed_and_canonical() -> None:
    key = b"k" * 32
    assert input_hmac(key, {"a": 1, "b": 2}) == input_hmac(key, {"b": 2, "a": 1})
    assert input_hmac(key, "x") != input_hmac(b"j" * 32, "x")
    assert len(input_hmac(key, "x")) == 64
