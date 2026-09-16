"""The extraction contract. The same model is Gemini's structured-output schema and the
`extraction` part of POST /api/v1/invoices/process, so the two cannot drift apart."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .types import (
    OptionalAmount,
    OptionalCurrency,
    OptionalDate,
    OptionalText100,
    OptionalText255,
    OptionalText2000,
)

MAX_LINE_ITEMS = 500


class ExtractedLineItem(BaseModel):
    """One priced line of the invoice."""

    model_config = ConfigDict(str_strip_whitespace=True)

    description: OptionalText2000 = Field(None, description="Line description exactly as printed.")
    quantity: OptionalAmount = Field(None, description="Quantity. null if not printed; it is then treated as 1.")
    unit_price: OptionalAmount = Field(None, description="Price per unit before tax, in the invoice currency.")
    line_total: OptionalAmount = Field(None, description="Amount printed for the line, usually quantity x unit price.")

    @model_validator(mode="after")
    def _requires_an_amount(self) -> "ExtractedLineItem":
        if self.unit_price is None and self.line_total is None:
            raise ValueError("a line item needs unit_price or line_total")
        return self


class InvoiceExtraction(BaseModel):
    """Structured fields read from one invoice document."""

    model_config = ConfigDict(str_strip_whitespace=True, title="InvoiceExtraction")

    vendor_name: OptionalText255 = Field(
        None, description="Name of the business that issued the invoice (the seller), exactly as printed."
    )
    vendor_tax_id: OptionalText100 = Field(
        None, description="The seller's tax registration number (VAT, GST, TRN, EIN, ...). The number only, no label."
    )
    vendor_confidence: float | None = Field(
        None, ge=0, le=100, description="0-100: how certain you are that vendor_name is correct."
    )
    invoice_number: OptionalText100 = Field(None, description="Invoice number or ID exactly as printed.")
    invoice_date: OptionalDate = Field(None, description="Date the invoice was issued, as YYYY-MM-DD.")
    due_date: OptionalDate = Field(None, description="Date payment is due, as YYYY-MM-DD.")
    currency: OptionalCurrency = Field(
        None, description="ISO 4217 code of the currency of all amounts, e.g. USD, EUR, AED, KWD."
    )
    subtotal: OptionalAmount = Field(None, description="Sum of the line items before tax, shipping and discount.")
    tax_amount: OptionalAmount = Field(None, description="Total tax charged (VAT, GST, sales tax).")
    shipping_amount: OptionalAmount = Field(None, description="Shipping, freight or delivery charges.")
    discount_amount: OptionalAmount = Field(None, description="Total discount, as a positive number.")
    total_amount: OptionalAmount = Field(None, description="Grand total payable.")
    line_items: list[ExtractedLineItem] = Field(
        default_factory=list, description="Every priced line of the invoice, in document order."
    )
    confidence: float = Field(
        ..., ge=0, le=100, description="0-100: overall certainty that every extracted field is correct."
    )

    @field_validator("discount_amount")
    @classmethod
    def _discount_is_positive(cls, value: Decimal | None) -> Decimal | None:
        # Invoices print discounts as "-50.00"; the math rule subtracts the discount itself.
        return abs(value) if value is not None else None

    @field_validator("line_items")
    @classmethod
    def _limit_line_items(cls, items: list[ExtractedLineItem]) -> list[ExtractedLineItem]:
        if len(items) > MAX_LINE_ITEMS:
            raise ValueError(f"at most {MAX_LINE_ITEMS} line items are supported")
        return items
