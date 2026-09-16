import pytest

from invoice_auditor.services.normalization import (
    normalize_description,
    normalize_invoice_number,
    normalize_tax_id,
    normalize_vendor_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ABC Trading LLC", "abc trading"),
        ("ABC Trading L.L.C", "abc trading"),
        ("ABC Trading L.L.C.", "abc trading"),
        ("  abc   TRADING, llc ", "abc trading"),
        ("ABC Tradings", "abc tradings"),
        ("The Acme Company", "acme"),
        ("Müller GmbH & Co. KG", "muller"),
        ("Johnson & Johnson", "johnson and johnson"),
        ("Smith & Co", "smith"),
        ("O'Neil Supplies Pvt. Ltd.", "oneil supplies"),
        ("Gulf Star Trading FZ-LLC", "gulf star trading"),
        ("Company", "company"),  # never strips a name down to nothing
        ("شركة النور للتجارة", "شركة النور للتجارة"),
    ],
)
def test_vendor_name(raw, expected):
    assert normalize_vendor_name(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "..."])
def test_vendor_name_empty(raw):
    assert normalize_vendor_name(raw) is None


def test_tax_id():
    assert normalize_tax_id("TRN 100-234-567 003") == "TRN100234567003"
    assert normalize_tax_id("gb 123.456.789") == "GB123456789"
    assert normalize_tax_id(" - ") is None


def test_invoice_number():
    assert normalize_invoice_number("INV-10022") == "INV10022"
    assert normalize_invoice_number("inv 10022") == "INV10022"
    assert normalize_invoice_number("#INV/10022") == "INV10022"
    assert normalize_invoice_number("INV-0010022") == "INV0010022"  # leading zeros are significant
    assert normalize_invoice_number("--") is None


def test_description():
    assert normalize_description("Printer Cartridge - Black") == "printer cartridge black"
    assert normalize_description("printer  cartridge (black)") == "printer cartridge black"
    assert len(normalize_description("x " * 1000)) <= 500
