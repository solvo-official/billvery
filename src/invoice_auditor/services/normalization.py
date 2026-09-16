"""Canonical forms used for matching. Changing any of these changes which records match,
so existing rows would need re-normalizing."""

import re
import unicodedata

# Trailing legal-form tokens, compared after punctuation is removed ("L.L.C." -> "llc").
LEGAL_SUFFIXES = frozenset(
    {
        "llc", "pllc", "llp", "lp", "inc", "incorporated", "corp", "corporation", "co", "company",
        "ltd", "limited", "plc", "pty", "pvt", "pte", "private", "gmbh", "ag", "kg", "sa", "sas",
        "sarl", "srl", "spa", "bv", "nv", "fze", "fzco", "fzc", "fzllc", "fz", "est", "establishment",
        "wll", "spc",
    }
)  # fmt: skip
LEADING_ARTICLES = frozenset({"the"})

_JOINING_PUNCTUATION = re.compile(r"[.'’`´]")  # removed outright: "L.L.C" -> "LLC", "O'Neil" -> "ONeil"
_SEPARATORS = re.compile(r"[\W_]+")  # everything else that is not a letter or digit splits words
_NOT_ALNUM = re.compile(r"[\W_]+")


def _strip_latin_accents(text: str) -> str:
    """"Müller" -> "Muller", without touching combining marks in scripts such as Devanagari."""
    kept: list[str] = []
    for char in unicodedata.normalize("NFKD", text):
        if unicodedata.combining(char) and kept and kept[-1].isascii():
            continue
        kept.append(char)
    return unicodedata.normalize("NFC", "".join(kept))


def _words(text: str) -> list[str]:
    text = _strip_latin_accents(unicodedata.normalize("NFKC", text)).casefold()
    text = text.replace("&", " and ")
    text = _JOINING_PUNCTUATION.sub("", text)
    return _SEPARATORS.sub(" ", text).split()


def normalize_vendor_name(raw: str | None) -> str | None:
    """Lower-case, punctuation-free vendor name without legal suffixes.

    "ABC Trading L.L.C." and "ABC Trading LLC" both become "abc trading";
    "Müller GmbH & Co. KG" becomes "muller".
    """
    if not raw:
        return None
    words = _words(raw)
    while len(words) > 1 and words[0] in LEADING_ARTICLES:
        words.pop(0)
    stripped_suffix = False
    while len(words) > 1 and (words[-1] in LEGAL_SUFFIXES or (stripped_suffix and words[-1] == "and")):
        words.pop()
        stripped_suffix = True
    normalized = " ".join(words)[:255].rstrip()
    return normalized or None


def normalize_tax_id(raw: str | None) -> str | None:
    """Upper-case alphanumerics only: "TRN 100-234-567" -> "TRN100234567"."""
    if not raw:
        return None
    normalized = _NOT_ALNUM.sub("", unicodedata.normalize("NFKC", raw)).upper()[:100]
    return normalized or None


def normalize_invoice_number(raw: str | None) -> str | None:
    """"INV-10022", "inv 10022" and "#INV10022" all become "INV10022"."""
    if not raw:
        return None
    normalized = _NOT_ALNUM.sub("", unicodedata.normalize("NFKC", raw)).upper()[:100]
    return normalized or None


def normalize_description(raw: str | None) -> str | None:
    """Key for price history: "Printer Cartridge - Black" -> "printer cartridge black"."""
    if not raw:
        return None
    normalized = " ".join(_words(raw))[:500].rstrip()
    return normalized or None
