import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from google.genai import _transformers
from pydantic import ValidationError

from invoice_auditor.ingestion.gemini import ExtractionError, GeminiInvoiceExtractor, build_process_request
from invoice_auditor.schemas.api import FileMetadata, ProcessInvoiceRequest
from invoice_auditor.schemas.extraction import InvoiceExtraction

MINIMAL = {"confidence": 95}


def parse(**fields) -> InvoiceExtraction:
    return InvoiceExtraction.model_validate_json(json.dumps(MINIMAL | fields))


class TestValidation:
    def test_blank_strings_become_null(self):
        extraction = parse(vendor_name="  ", invoice_date="", currency="", subtotal="")
        assert extraction.vendor_name is None
        assert extraction.invoice_date is None
        assert extraction.currency is None
        assert extraction.subtotal is None

    def test_amounts_are_exact_decimals(self):
        extraction = InvoiceExtraction.model_validate_json(
            '{"confidence": 95, "subtotal": 123456789012.3456, "tax_amount": 0.1, "total_amount": 12.345}'
        )
        assert extraction.subtotal == Decimal("123456789012.3456")
        assert extraction.tax_amount == Decimal("0.1000")
        assert extraction.total_amount == Decimal("12.3450")  # three-decimal currencies survive

    def test_amounts_round_to_four_places(self):
        assert parse(total_amount=10.123456).total_amount == Decimal("10.1235")

    def test_discount_is_made_positive(self):
        assert parse(discount_amount=-50).discount_amount == Decimal("50.0000")

    def test_currency_is_upper_cased_and_validated(self):
        assert parse(currency="aed").currency == "AED"
        with pytest.raises(ValidationError):
            parse(currency="$")

    def test_line_item_needs_an_amount(self):
        with pytest.raises(ValidationError, match="unit_price or line_total"):
            parse(line_items=[{"description": "Note: thank you"}])

    def test_length_and_range_limits(self):
        with pytest.raises(ValidationError):
            parse(vendor_name="x" * 256)
        with pytest.raises(ValidationError):
            parse(confidence=101)
        with pytest.raises(ValidationError):
            InvoiceExtraction.model_validate({})  # confidence is required

    def test_api_rejects_unknown_extraction_fields(self):
        file = {"sha256": "ab" * 32, "filename": "a.pdf", "content_type": "application/pdf", "size_bytes": 10}
        extraction = {"confidence": 95, "tax": 50, "line_items": [{"unit_price": 1, "price": 1}]}
        with pytest.raises(ValidationError, match=r"line_items\[0\]\.price, tax"):
            ProcessInvoiceRequest.model_validate({"file": file, "extraction": extraction})
        # Gemini's output may carry extra keys; the extraction model itself ignores them.
        assert InvoiceExtraction.model_validate(extraction).tax_amount is None

    def test_file_metadata(self):
        meta = FileMetadata(sha256="AB" * 32, filename="a.pdf", content_type="application/pdf", size_bytes=10)
        assert meta.sha256 == "ab" * 32
        with pytest.raises(ValidationError):
            FileMetadata(sha256="xyz", filename="a.pdf", content_type="application/pdf", size_bytes=10)
        with pytest.raises(ValidationError):
            FileMetadata(sha256="ab" * 32, filename="a.gif", content_type="image/gif", size_bytes=10)


class TestGeminiSchema:
    def test_json_schema_avoids_keywords_gemini_rejects(self):
        text = json.dumps(InvoiceExtraction.model_json_schema())
        for keyword in ("additionalProperties", '"format"', '"pattern"', "maxLength"):
            assert keyword not in text

    def test_google_genai_accepts_the_schema(self):
        schema = _transformers.t_schema(None, InvoiceExtraction)
        props = schema.properties
        assert props["subtotal"].type.value == "NUMBER" and props["subtotal"].nullable
        assert "issued" in props["invoice_date"].description
        assert "due" in props["due_date"].description
        assert props["line_items"].items.properties["unit_price"].type.value == "NUMBER"
        assert schema.required == ["confidence"]


class FakeModels:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        candidate = SimpleNamespace(finish_reason="SAFETY")
        return SimpleNamespace(text=self.text, candidates=[candidate])


def extractor_with(text: str | None) -> tuple[GeminiInvoiceExtractor, FakeModels]:
    extractor = GeminiInvoiceExtractor(api_key="test-key", model="gemini-test")
    fake = FakeModels(text)
    extractor._client = SimpleNamespace(aio=SimpleNamespace(models=fake))
    return extractor, fake


class TestGeminiExtractor:
    async def test_parses_structured_output(self):
        extractor, fake = extractor_with('{"vendor_name": "ABC LLC", "total_amount": 1050.5, "confidence": 94}')
        extraction = await extractor.extract(b"%PDF-1.7", "application/pdf")
        assert extraction.vendor_name == "ABC LLC"
        assert extraction.total_amount == Decimal("1050.5000")
        [call] = fake.calls
        assert call["model"] == "gemini-test"
        assert call["config"].response_schema is InvoiceExtraction
        assert call["config"].response_mime_type == "application/json"

    async def test_empty_response(self):
        extractor, _ = extractor_with(None)
        with pytest.raises(ExtractionError, match="SAFETY"):
            await extractor.extract(b"%PDF-1.7", "application/pdf")

    async def test_output_not_matching_schema(self):
        extractor, _ = extractor_with('{"vendor_name": "ABC LLC"}')
        with pytest.raises(ExtractionError, match="does not match"):
            await extractor.extract(b"%PDF-1.7", "application/pdf")

    async def test_rejects_unsupported_input(self):
        extractor, fake = extractor_with("{}")
        with pytest.raises(ValueError, match="Unsupported"):
            await extractor.extract(b"GIF89a", "image/gif")
        with pytest.raises(ValueError, match="empty"):
            await extractor.extract(b"", "application/pdf")
        assert fake.calls == []

    def test_build_process_request(self):
        request = build_process_request(
            b"hello", filename="inv.pdf", mime_type="application/pdf", extraction=InvoiceExtraction(confidence=90)
        )
        assert request.file.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert request.file.size_bytes == 5
