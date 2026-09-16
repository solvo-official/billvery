"""Reusable annotated types.

Input types validate without emitting JSON-schema keywords that Gemini's structured-output
schema rejects (additionalProperties, format: date, pattern, ...): limits are checked in
validators instead of `Field(max_length=...)`.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, PlainSerializer, WithJsonSchema

from .. import money


def _blank_to_none(value: Any) -> Any:
    """Models often emit "" for a field they could not find; treat it as missing."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _max_length(limit: int):
    def check(value: str) -> str:
        if len(value) > limit:
            raise ValueError(f"must be at most {limit} characters")
        return value

    return check


def _currency_code(value: str) -> str:
    code = value.upper()
    if len(code) != 3 or not code.isascii() or not code.isalpha():
        raise ValueError("must be a 3-letter ISO 4217 currency code")
    return code


Text100 = Annotated[str, AfterValidator(_max_length(100))]
Text255 = Annotated[str, AfterValidator(_max_length(255))]
Text2000 = Annotated[str, AfterValidator(_max_length(2000))]

# Amounts are validated as Decimal (JSON numbers are parsed exactly), rounded to 4 places.
Amount = Annotated[Decimal, AfterValidator(money.quantize), WithJsonSchema({"type": "number"})]

# No description inside these overrides: for nullable fields Gemini's schema converter lets an
# inner description replace the field's own, and the field description carries the meaning.
IsoDate = Annotated[date, WithJsonSchema({"type": "string"})]

CurrencyCode = Annotated[str, AfterValidator(_currency_code), WithJsonSchema({"type": "string"})]

# Optional variants: blank strings become None *before* the union is validated.
OptionalText100 = Annotated[Text100 | None, BeforeValidator(_blank_to_none)]
OptionalText255 = Annotated[Text255 | None, BeforeValidator(_blank_to_none)]
OptionalText2000 = Annotated[Text2000 | None, BeforeValidator(_blank_to_none)]
OptionalAmount = Annotated[Amount | None, BeforeValidator(_blank_to_none)]
OptionalDate = Annotated[IsoDate | None, BeforeValidator(_blank_to_none)]
OptionalCurrency = Annotated[CurrencyCode | None, BeforeValidator(_blank_to_none)]

# Response-side money: exact decimal string, e.g. "1260.00" or "0.0125".
AmountOut = Annotated[
    Decimal,
    PlainSerializer(money.format_amount, return_type=str),
    WithJsonSchema({"type": "string", "examples": ["1260.00"]}),
]
