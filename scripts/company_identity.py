#!/usr/bin/env python3
"""Extract evidence-backed Romanian company identity candidates from HTML."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable

from bs4 import BeautifulSoup


SPACE_RE = re.compile(r"\s+")
MALFORMED_NUMERIC_ENTITY_RE = re.compile(rb"&#([0-9]{1,7})(?=[^0-9;])")


def sanitize_html_bytes(body: bytes) -> bytes:
    """Terminate legacy numeric entities that Python's HTML parser can over-consume."""
    return MALFORMED_NUMERIC_ENTITY_RE.sub(rb"&#\1;", body)
CUI_LABEL_RE = re.compile(
    r"(?ix)\b(?:"
    r"C\.?\s*U\.?\s*I\.?|C\.?\s*I\.?\s*F\.?|"
    r"cod(?:ul)?\s+(?:unic\s+de\s+[iî]nregistrare|de\s+identificare\s+fiscal[aă]|fiscal)|"
    r"tax\s*id|vat(?:\s*(?:id|number|no\.?))?"
    r")\s*[:#-]?\s*(RO\s*)?([0-9]{2,10})\b"
)
REGISTRATION_RE = re.compile(
    r"(?ix)\b(?:nr\.?\s*(?:de\s*)?(?:ordine\s*)?(?:la\s*)?(?:registrul\s+comer[tț]ului|reg\.?\s*com\.?)\s*[:#-]?\s*)?"
    r"([JFC]\s*\d{1,2}\s*[/\\]\s*\d{1,7}\s*[/\\]\s*(?:19|20)\d{2})\b"
)
LEGAL_SUFFIX = (
    r"(?:S\.?\s*C\.?\s*)?(?:S\.?\s*R\.?\s*L\.?(?:\s*-?\s*D\.?)?|"
    r"P\.?\s*F\.?\s*A\.?|(?-i:S\.\s*A\.|I\.\s*I\.|I\.\s*F\.|"
    r"S\.\s*N\.\s*C\.|S\.\s*C\.\s*S\.|S\.\s*C\.\s*A\.|"
    r"S\.\s*P\.\s*R\.\s*L\.|C\.\s*A\.\s*B\.|SA|II|IF|SNC|SCS|SCA|SPRL|CAB))"
)
COMPANY_WITH_SUFFIX_RE = re.compile(
    rf"(?iu)(?<![\w])((?:S\.?\s*C\.?\s+)?(?-i:[A-ZĂÂÎȘȚ0-9])[\wĂÂÎȘȚăâîșț&'’+.,-]*"
    rf"(?:\s+(?-i:[A-ZĂÂÎȘȚ0-9])[\wĂÂÎȘȚăâîșț&'’+.,-]*){{0,7}}\s+{LEGAL_SUFFIX})(?![\w])"
)
COMPANY_LABEL_RE = re.compile(
    rf"(?iu)\b(?:denumirea\s+(?:societ[aă][tț]ii|companiei)|societatea|compania|"
    rf"operatorul(?:\s+(?:site-ului|website-ului|economic))?|administratorul|"
    rf"proprietarul(?:\s+(?:site-ului|website-ului))?|furnizorul|prestatorul)"
    rf"\s*(?:este|denumit[aă]?|:|[-–—])?\s*(.{2,180}?{LEGAL_SUFFIX})(?=\s*[,;|]|\s+(?:cu\s+sediul|av[aâ]nd|C\.?\s*U\.?\s*I|C\.?\s*I\.?\s*F|J\d)|$)"
)
ADDRESS_LABEL_RE = re.compile(
    r"(?iu)\b(?:sediul\s+(?:social|profesional)|adres[ăa]\s+(?:sediului|societ[aă][tț]ii|companiei|operatorului))"
    r"\s*(?:este|situat(?:[ăa])?\s+(?:[iî]n)?|:|[-–—])?\s*([^|]{8,240})"
)
SITE_OPERATED_RE = re.compile(
    rf"(?iu)\b(?:site-ul|website-ul)\s+(?:este\s+)?(?:operat|administrat|de[tț]inut)\s+de\s+"
    rf"(.{{2,180}}?{LEGAL_SUFFIX})(?=\s*[,;|]|\s+(?:cu\s+sediul|av[aâ]nd|C\.?\s*U\.?\s*I|C\.?\s*I\.?\s*F|J\d)|$)"
)
NEXT_FIELD_RE = re.compile(
    r"(?iu)\s+(?=(?:C\.?\s*U\.?\s*I|C\.?\s*I\.?\s*F|cod(?:ul)?\s+fiscal|"
    r"registrul\s+comer[tț]ului|telefon|e-?mail|capital\s+social)\b)"
)


def clean_text(value: Any, limit: int = 500) -> str:
    text = SPACE_RE.sub(" ", str(value or "")).strip(" \t\r\n,;:|–—-")
    return text[:limit]


def normalized_company_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char)).upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = SPACE_RE.sub(" ", text).strip()
    text = re.sub(r"^(?:S C|SC)\s+", "", text)
    for spaced, compact in (("S R L", "SRL"), ("S A", "SA"), ("P F A", "PFA"), ("I I", "II"), ("I F", "IF")):
        text = re.sub(rf"\b{spaced}\b", compact, text)
    return text


def clean_company_match(value: str) -> str:
    candidate = clean_text(value, 180)
    sc_matches = list(re.finditer(r"(?iu)\bS\.?\s*C\.?\s+", candidate))
    if sc_matches:
        candidate = candidate[sc_matches[-1].start():]
    candidate = re.sub(r"(?iu)^(?:compania|societatea|contact)\s+", "", candidate)
    return clean_text(candidate, 180)


def plausible_jsonld_name(value: str) -> bool:
    candidate = clean_text(value, 180)
    if not 2 <= len(candidate) <= 180:
        return False
    return not re.match(r"(?i)^(?:https?://|www\.)", candidate)


def normalize_cui(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if not 2 <= len(digits) <= 10:
        return None
    digits = digits.lstrip("0") or "0"
    return f"RO{digits}"


def valid_romanian_cui(value: str) -> bool:
    normalized = normalize_cui(value)
    if not normalized:
        return False
    digits = normalized[2:]
    if len(digits) < 2 or len(digits) > 10:
        return False
    control = int(digits[-1])
    base = digits[:-1].zfill(9)
    total = sum(int(digit) * int(weight) for digit, weight in zip(base, "753217532"))
    expected = (total * 10) % 11
    if expected == 10:
        expected = 0
    return control == expected


def normalize_registration_number(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("\\", "/").upper()


def iter_jsonld_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_jsonld_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_jsonld_nodes(child)


def organizationish(node: dict[str, Any]) -> bool:
    node_type = node.get("@type", [])
    types = [node_type] if isinstance(node_type, str) else node_type if isinstance(node_type, list) else []
    joined = " ".join(str(value).lower() for value in types)
    excluded = ("webpage", "website", "breadcrumb", "product", "article", "imageobject")
    if any(value in joined for value in excluded):
        return False
    signals = ("organization", "corporation", "business", "company", "store", "service", "agency")
    return any(value in joined for value in signals) or any(
        key in node for key in ("legalName", "taxID", "vatID")
    )


def jsonld_address(value: Any) -> str | None:
    if isinstance(value, str):
        return clean_text(value, 500) or None
    if not isinstance(value, dict):
        return None
    parts = [
        value.get("streetAddress"), value.get("postalCode"), value.get("addressLocality"),
        value.get("addressRegion"), value.get("addressCountry"),
    ]
    result = clean_text(", ".join(str(part) for part in parts if part), 500)
    return result or None


def add_unique(items: list[dict[str, Any]], seen: set[tuple[str, str]], item: dict[str, Any], key: str) -> None:
    value = clean_text(item.get(key), 500)
    if not value:
        return
    identity_value = clean_text(item.get("normalized") or value, 500)
    identity = (key, identity_value.casefold())
    if identity in seen:
        return
    seen.add(identity)
    item[key] = value
    items.append(item)


def extract_company_identity(body: bytes, url: str) -> dict[str, Any]:
    """Return candidates with source and confidence; do not assert domain ownership."""
    body = sanitize_html_bytes(body)
    soup = BeautifulSoup(body, "html.parser")
    jsonld_values: list[Any] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}, limit=50):
        try:
            jsonld_values.append(json.loads(script.string or script.get_text()))
        except (json.JSONDecodeError, TypeError):
            continue

    html_addresses = [clean_text(tag.get_text(" ", strip=True), 500) for tag in soup.find_all("address", limit=100)]
    for tag in soup(("script", "style", "noscript", "svg")):
        tag.decompose()
    text = SPACE_RE.sub(" ", soup.get_text(" ", strip=True))[:750_000]
    blocks: list[str] = []
    seen_blocks: set[str] = set()
    # Avoid nested div/section containers: they duplicate whole-page text and make
    # both parsing and evidence deduplication needlessly expensive.
    for tag in soup.find_all(("address", "p", "li", "td"), limit=8_000):
        block = clean_text(tag.get_text(" ", strip=True), 1200)
        if 3 <= len(block) <= 1200 and block not in seen_blocks:
            seen_blocks.add(block)
            blocks.append(block)
    if text and text not in seen_blocks:
        blocks.append(text)

    names: list[dict[str, Any]] = []
    cuis: list[dict[str, Any]] = []
    registrations: list[dict[str, Any]] = []
    addresses: list[dict[str, Any]] = []
    seen_names: set[tuple[str, str]] = set()
    seen_cuis: set[tuple[str, str]] = set()
    seen_regs: set[tuple[str, str]] = set()
    seen_addresses: set[tuple[str, str]] = set()

    for document in jsonld_values:
        for node in iter_jsonld_nodes(document):
            if not organizationish(node):
                continue
            for field, confidence in (("legalName", 100), ("name", 85)):
                value = node.get(field)
                if isinstance(value, str) and plausible_jsonld_name(value):
                    add_unique(names, seen_names, {
                        "name": clean_text(value, 180), "normalized": normalized_company_name(value),
                        "source": f"jsonld.{field}", "confidence": confidence,
                    }, "name")
            for field in ("taxID", "vatID"):
                raw = node.get(field)
                if raw is None:
                    continue
                cui = normalize_cui(str(raw))
                if cui:
                    add_unique(cuis, seen_cuis, {
                        "cui": cui, "valid_checksum": valid_romanian_cui(cui),
                        "source": f"jsonld.{field}", "confidence": 100,
                    }, "cui")
            address = jsonld_address(node.get("address"))
            if address:
                add_unique(addresses, seen_addresses, {
                    "address": address, "source": "jsonld.address", "confidence": 95,
                }, "address")

    for match in CUI_LABEL_RE.finditer(text):
        cui = normalize_cui(match.group(2))
        if cui:
            add_unique(cuis, seen_cuis, {
                "cui": cui, "valid_checksum": valid_romanian_cui(cui),
                "source": "text.label", "confidence": 95,
            }, "cui")

    for match in REGISTRATION_RE.finditer(text):
        value = normalize_registration_number(match.group(1))
        add_unique(registrations, seen_regs, {
            "registration_number": value, "source": "text.pattern", "confidence": 95,
        }, "registration_number")

    for block in blocks:
        for match in list(COMPANY_LABEL_RE.finditer(block)) + list(SITE_OPERATED_RE.finditer(block)):
            name = clean_company_match(match.group(1))
            suffix_match = COMPANY_WITH_SUFFIX_RE.search(name)
            if suffix_match:
                name = clean_company_match(suffix_match.group(1))
            add_unique(names, seen_names, {
                "name": name, "normalized": normalized_company_name(name),
                "source": "text.label", "confidence": 95,
            }, "name")
        for match in COMPANY_WITH_SUFFIX_RE.finditer(block):
            name = clean_company_match(match.group(1))
            # Long navigation/legal boilerplate captured before a suffix is unreliable.
            if len(name.split()) > 11 or len(name) < 4:
                continue
            add_unique(names, seen_names, {
                "name": name, "normalized": normalized_company_name(name),
                "source": "text.legal_suffix", "confidence": 75,
            }, "name")

    for match in ADDRESS_LABEL_RE.finditer(text):
        address = NEXT_FIELD_RE.split(clean_text(match.group(1), 300), maxsplit=1)[0]
        address = clean_text(address, 240)
        if len(address) >= 8:
            add_unique(addresses, seen_addresses, {
                "address": address, "source": "text.label", "confidence": 85,
            }, "address")
    for address in html_addresses:
        if len(address) >= 8 and (re.search(r"\d", address) or re.search(r"(?iu)\b(?:strada|str\.|bulevard|bd\.|calea)\b", address)):
            add_unique(addresses, seen_addresses, {
                "address": address, "source": "html.address", "confidence": 80,
            }, "address")

    result = {
        "url": url,
        "company_names": names[:100],
        "cuis": cuis[:100],
        "registration_numbers": registrations[:100],
        "addresses": addresses[:100],
    }
    soup.decompose()
    return result
