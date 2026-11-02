"""Deterministic document understanding: classification, entities, invoice/contract fields,
validation of extracted values, and near-duplicate signatures.

These run offline and are the baseline that the evaluation measures. When Claude is
configured, its classification/extraction is stored instead, but **the same validation
rules run on its output**, and every record says which source produced it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import numpy as np

# ---------------------------------------------------------------- classification
KEYWORDS: dict[str, tuple[str, ...]] = {
    "invoice": (
        "invoice",
        "bill to",
        "amount due",
        "invoice number",
        "invoice no",
        "subtotal",
        "due date",
        "facture",
        "factura",
        "total due",
        "payment terms",
    ),
    "receipt": (
        "receipt",
        "thank you for your purchase",
        "cashier",
        "change due",
        "paid with",
        "store #",
        "reçu",
        "recibo",
    ),
    "contract": (
        "agreement",
        "the parties",
        "hereinafter",
        "termination",
        "governing law",
        "in witness whereof",
        "effective date",
        "shall",
        "contrat",
        "contrato",
        "indemnif",
    ),
    "resume": (
        "curriculum vitae",
        "work experience",
        "education",
        "skills",
        "professional summary",
        "resume",
        "employment history",
    ),
    "report": (
        "executive summary",
        "findings",
        "recommendations",
        "quarter",
        "analysis",
        "conclusion",
        "methodology",
        "kpi",
    ),
    "letter": (
        "dear ",
        "sincerely",
        "kind regards",
        "yours faithfully",
        "best regards",
        "cher",
        "estimado",
        "atentamente",
        "cordialement",
    ),
    "policy": (
        "policy",
        "employees must",
        "this policy applies",
        "scope",
        "purpose",
        "procedure",
        "compliance",
        "politique",
        "política",
    ),
}


def classify_rules(text: str) -> tuple[str, float]:
    """Keyword scoring; returns (label, confidence in [0,1]). 'other' when nothing is convincing."""
    low = text[:20_000].lower()
    scores = {label: sum(low.count(k) ** 0.5 for k in kws) for label, kws in KEYWORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    total = sum(scores.values())
    if scores[best] < 1.5 or total == 0:
        return "other", 0.3
    return best, round(scores[best] / total, 3)


# ---------------------------------------------------------------- dates & money
MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        start=1,
    )
}
DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}"
    r"|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b"
)
MONEY_RE = re.compile(r"(?:(USD|EUR|GBP|\$|€|£)\s?)(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Organisation names: capitalised words on ONE line followed by a legal suffix and a word boundary.
ORG_RE = re.compile(
    r"\b([A-Z][\w&'-]*(?:[ \t]+[A-Z][\w&'-]*){0,4}[ \t]+(?:Inc|LLC|Ltd|GmbH|Corp|Corporation|S\.A\.|SAS|SARL|Co)\b\.?)"
)
# Concepts for the knowledge graph: acronyms (SLA, GDPR) and multi-word capitalised names
# ("Payments Service", "Project Atlas"). Phrases starting with a function word are dropped
# because they are usually just the start of a sentence.
ACRONYM_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,5})\b")
PHRASE_RE = re.compile(r"\b([A-Z][a-z][\w-]*(?:[ \t]+(?:of[ \t]+|and[ \t]+)?[A-Z][\w-]+){1,3})\b")
CONCEPT_STOP = {
    "The",
    "A",
    "An",
    "This",
    "That",
    "These",
    "Those",
    "We",
    "Our",
    "If",
    "When",
    "In",
    "On",
    "At",
    "For",
    "To",
    "And",
    "Or",
    "But",
    "It",
    "Its",
    "As",
    "By",
    "From",
    "With",
    "All",
    "Each",
    "Any",
    "Please",
    "See",
}
ACRONYM_STOP = {"OK", "PDF", "ID", "AM", "PM", "US", "UK", "EU", "TBD", "FAQ", "NA", "IT", "II", "III", "IV"}
CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "USD": "USD", "EUR": "EUR", "GBP": "GBP"}


def parse_date(s: str) -> date | None:
    s = s.strip().rstrip(".")
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_money(s: str) -> Decimal | None:
    try:
        return Decimal(re.sub(r"[^\d.]", "", s))
    except InvalidOperation:
        return None


def extract_entities(page_texts: list[tuple[int, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str, normalized: str | None, page: int) -> None:
        key = (kind, normalized or value)
        if key not in seen and len(out) < 500:
            seen.add(key)
            out.append({"entity_type": kind, "value": value[:500], "normalized": normalized, "page_no": page})

    for page_no, text in page_texts:
        for m in DATE_RE.finditer(text):
            d = parse_date(m.group(1))
            add("date", m.group(1), d.isoformat() if d else None, page_no)
        for m in MONEY_RE.finditer(text):
            amt = parse_money(m.group(2))
            add("money", m.group(0), f"{CURRENCY[m.group(1)]} {amt:.2f}" if amt is not None else None, page_no)
        for m in EMAIL_RE.finditer(text):
            add("email", m.group(0), m.group(0).lower(), page_no)
        for m in ORG_RE.finditer(text):
            add("organization", m.group(1), m.group(1).strip(), page_no)
        for m in ACRONYM_RE.finditer(text):
            if m.group(1) not in ACRONYM_STOP and not m.group(1).isdigit():
                add("concept", m.group(1), m.group(1).lower(), page_no)
        for m in PHRASE_RE.finditer(text):
            phrase = re.sub(r"[ \t]+", " ", m.group(1))
            words = phrase.split(" ")
            while words and words[0] in CONCEPT_STOP:
                words = words[1:]
            if len(words) >= 2:
                add("concept", " ".join(words), " ".join(words).lower(), page_no)
    return out


# ---------------------------------------------------------------- invoices
INVOICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": ["string", "null"]},
        "vendor": {"type": ["string", "null"]},
        "customer": {"type": ["string", "null"]},
        "invoice_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "currency": {"type": ["string", "null"]},
        "subtotal": {"type": ["number", "null"]},
        "tax": {"type": ["number", "null"]},
        "total": {"type": ["number", "null"]},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": ["number", "null"]},
                    "unit_price": {"type": ["number", "null"]},
                    "amount": {"type": ["number", "null"]},
                },
                "required": ["description", "quantity", "unit_price", "amount"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "invoice_number",
        "vendor",
        "customer",
        "invoice_date",
        "due_date",
        "currency",
        "subtotal",
        "tax",
        "total",
        "line_items",
    ],
    "additionalProperties": False,
}


def _label_value(text: str, labels: str, value: str) -> str | None:
    m = re.search(rf"(?:{labels})\s*[:#]?\s*{value}", text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _money_after(text: str, labels: str) -> float | None:
    # Allow a parenthetical between label and value, e.g. "Sales Tax (8%): 171.92".
    m = re.search(
        rf"(?:^|\n)\s*(?:{labels})\s*(?:\([^)\n]*\))?\s*:?\s*(?:USD|EUR|GBP|\$|€|£)?\s*([\d,]+\.\d{{2}})",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    amt = parse_money(m.group(1))
    return float(amt) if amt is not None else None


def _iso(raw: str | None) -> str | None:
    parsed = parse_date(raw) if raw else None
    return parsed.isoformat() if parsed else None


def extract_invoice_rules(text: str, tables: list[list[list[str]]]) -> dict[str, Any]:
    date_pat = r"(\d{4}-\d{2}-\d{2}|[A-Z][a-z]+ \d{1,2}, \d{4})"
    inv_date = _label_value(text, r"invoice date|date of issue|issue date", date_pat)
    due = _label_value(text, r"due date|payment due", date_pat)
    cur_m = re.search(r"\b(USD|EUR|GBP)\b|([$€£])", text)
    currency = CURRENCY.get(cur_m.group(1) or cur_m.group(2)) if cur_m else None
    items = []
    for table in tables:
        if not table:
            continue
        header = [h.lower() for h in table[0]]
        if not any("desc" in h or "item" in h for h in header) or not any(
            "amount" in h or "total" in h for h in header
        ):
            continue
        for row in table[1:]:
            nums = [parse_money(c) if re.search(r"\d", c or "") else None for c in row]
            if not row or not (row[0] or "").strip() or nums[-1] is None:
                continue
            items.append(
                {
                    "description": row[0].strip(),
                    "quantity": float(nums[1]) if len(nums) > 2 and nums[1] is not None else None,
                    "unit_price": float(nums[2]) if len(nums) > 3 and nums[2] is not None else None,
                    "amount": float(nums[-1]),
                }
            )
    vendor_m = re.search(r"^\s*(?:From|Vendor|Seller)\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    cust_m = re.search(r"^\s*(?:Bill To|Customer|Billed To)\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return {
        "invoice_number": _label_value(text, r"invoice (?:number|no\.?|#)", r"([A-Z0-9][A-Z0-9-]{2,})"),
        "vendor": vendor_m.group(1).strip() if vendor_m else None,
        "customer": cust_m.group(1).strip() if cust_m else None,
        "invoice_date": _iso(inv_date),
        "due_date": _iso(due),
        "currency": currency,
        "subtotal": _money_after(text, r"subtotal"),
        "tax": _money_after(text, r"tax|vat|sales tax"),
        "total": _money_after(text, r"total due|amount due|total"),
        "line_items": items,
    }


def validate_invoice(d: dict[str, Any]) -> dict[str, Any]:
    """Deterministic checks on extracted invoice data (whoever extracted it)."""
    issues: list[str] = []
    missing = [k for k in ("invoice_number", "vendor", "invoice_date", "due_date", "total") if not d.get(k)]
    items = d.get("line_items") or []
    if items and d.get("subtotal") is not None:
        s = round(sum(i.get("amount") or 0 for i in items), 2)
        if abs(s - d["subtotal"]) > 0.011:
            issues.append(f"Line items sum to {s:.2f} but subtotal is {d['subtotal']:.2f}")
    if (
        None not in (d.get("subtotal"), d.get("tax"), d.get("total"))
        and abs(d["subtotal"] + d["tax"] - d["total"]) > 0.011
    ):
        issues.append("Subtotal + tax does not equal total")
    if d.get("invoice_date") and d.get("due_date") and d["due_date"] < d["invoice_date"]:
        issues.append("Due date is before invoice date")
    return {"missing_fields": missing, "issues": issues, "valid": not missing and not issues}


# ---------------------------------------------------------------- contracts
CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "parties": {"type": "array", "items": {"type": "string"}},
        "effective_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "term": {"type": ["string", "null"]},
        "auto_renewal": {"type": ["boolean", "null"]},
        "termination_notice_days": {"type": ["integer", "null"]},
        "governing_law": {"type": ["string", "null"]},
        "payment_terms": {"type": ["string", "null"]},
        "clauses": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "parties",
        "effective_date",
        "term",
        "auto_renewal",
        "termination_notice_days",
        "governing_law",
        "payment_terms",
        "clauses",
    ],
    "additionalProperties": False,
}


def extract_contract_rules(text: str) -> dict[str, Any]:
    # Prefer the defined-term sentence: between X (the "Provider") and Y (the "Client").
    parties_m = re.search(
        r"between\s+([^()\n]+?)\s*\((?:the\s+)?[\"“]?\w+[\"”]?\)\s*,?\s*and\s+([^()\n]+?)\s*\(",
        text,
        re.IGNORECASE,
    ) or re.search(r"between\s+([^\n]+?)\s+and\s+([^\n]+?)\s*$", text, re.IGNORECASE | re.MULTILINE)
    eff = re.search(
        r"effective (?:as of |date[:\s]*)?(\d{4}-\d{2}-\d{2}|[A-Z][a-z]+ \d{1,2}, \d{4})", text, re.IGNORECASE
    )
    term = re.search(r"term of\s+([\w\s()]+?(?:months?|years?))", text, re.IGNORECASE)
    notice = re.search(r"(\d+)\s*(?:\(\w+\)\s*)?days'?\s*(?:prior\s+)?written notice", text, re.IGNORECASE)
    law = re.search(
        r"governed by(?: and construed in accordance with)? the laws of\s+(?:the State of\s+)?([A-Z][\w\s]+?)[.,]", text
    )
    pay = re.search(r"(?:payment|invoices?)[^.\n]*?(net\s*\d+|within\s+\d+\s+days)", text, re.IGNORECASE)
    clauses = re.findall(r"^\s*\d{1,2}\.\s+([A-Z][A-Za-z &-]{2,60})\s*$", text, re.MULTILINE)
    eff_d = parse_date(eff.group(1)) if eff else None
    return {
        "parties": [p.strip() for p in parties_m.groups()] if parties_m else [],
        "effective_date": eff_d.isoformat() if eff_d else None,
        "term": term.group(1).strip() if term else None,
        "auto_renewal": bool(re.search(r"automatically renew", text, re.IGNORECASE)) if text else None,
        "termination_notice_days": int(notice.group(1)) if notice else None,
        "governing_law": law.group(1).strip() if law else None,
        "payment_terms": pay.group(1) if pay else None,
        "clauses": clauses[:30],
    }


def validate_contract(d: dict[str, Any]) -> dict[str, Any]:
    missing = [k for k in ("parties", "effective_date", "term", "governing_law") if not d.get(k)]
    issues = []
    if d.get("parties") and len(d["parties"]) < 2:
        issues.append("Fewer than two parties identified")
    return {"missing_fields": missing, "issues": issues, "valid": not missing and not issues}


# ---------------------------------------------------------------- near-duplicates (MinHash)
NUM_PERM = 128
_MASK = (1 << 64) - 1
_PERM = np.arange(NUM_PERM, dtype=np.uint64)


def minhash(text: str, shingle: int = 5) -> np.ndarray:
    """128-permutation MinHash over 5-word shingles. Signature equality rate ≈ Jaccard similarity."""
    words = re.findall(r"\w+", text.lower())
    shingles = {" ".join(words[i : i + shingle]) for i in range(max(1, len(words) - shingle + 1))}
    sig = np.full(NUM_PERM, _MASK, dtype=np.uint64)
    for sh in shingles:
        base = hashlib.blake2b(sh.encode(), digest_size=16).digest()
        a = np.uint64(int.from_bytes(base[:8], "little"))
        b = np.uint64(int.from_bytes(base[8:], "little") | 1)
        # NUM_PERM hash functions from two base hashes (Kirsch–Mitzenmacher double hashing);
        # uint64 array arithmetic wraps modulo 2**64, which is what we want.
        hs = a + _PERM * b
        np.minimum(sig, hs, out=sig)
    return sig


def jaccard_estimate(a: bytes | np.ndarray, b: bytes | np.ndarray) -> float:
    sa = np.frombuffer(a, dtype=np.uint64) if isinstance(a, bytes) else a
    sb = np.frombuffer(b, dtype=np.uint64) if isinstance(b, bytes) else b
    return float(np.mean(sa == sb))
