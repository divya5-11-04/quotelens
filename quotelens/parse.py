"""Turn extracted text into a header and line items."""
import re
from dataclasses import dataclass, field

from .catalog import UNIT_ALIASES

import difflib
import itertools

AMBIG = r"\dOoUuIl|SsBZz/\u00a2"
NUM = rf"[{AMBIG}][{AMBIG},]*(?:\.[{AMBIG}]+)?"
CUR = r"(?:\u20b9|Rs\.?|INR)?\s*"
ITEM = re.compile(
    rf"^\s*(?:(?P<no>[^\s]{{1,3}})[.)]?\s+)?(?P<desc>.+?)\s+(?P<qty>{NUM})\s+(?P<unit>[A-Za-z]{{2,7}})\s+"
    rf"{CUR}(?P<rate>{NUM})\s+{CUR}(?P<amount>{NUM})\s*$")
DIGIT_FIX = {"O": "0", "o": "0", "U": "0", "u": "0", "I": "1", "l": "1", "|": "1", "/": "71", "S": "5", "s": "5",
             "B": "8", "Z": "2", "z": "2", "\u00a2": "6098"}
LOOKS_LIKE_ITEM = re.compile(r"^\s*\S{1,3}[.)]?\s+\S+.*\d.*\d")
TOTAL = re.compile(rf"^\s*(?:grand\s+)?total\b[^\d]*(?P<v>{NUM})\s*$", re.IGNORECASE)
RFQ = re.compile(r"\bRFQ\s*(?:Ref|No|Number)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{3,})", re.IGNORECASE)
QNO = re.compile(r"\b(?:Quotation|Quote)\s*(?:No|Number|#)\.?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})", re.IGNORECASE)
DATE = re.compile(r"\bDate\s*[:#]?\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})", re.IGNORECASE)
SUPPLIER = re.compile(r"^\s*(?:Supplier|From|Vendor)\s*[:#]\s*(.+)$", re.IGNORECASE)


@dataclass
class ParsedItem:
    line_no: int
    desc: str
    qty: float
    unit: str
    rate: float
    amount: float
    ocr_repaired: bool = False


@dataclass
class ParsedDoc:
    supplier: str = None
    rfq_ref: str = None
    quote_no: str = None
    quote_date: str = None
    stated_total: float = None
    items: list = field(default_factory=list)
    unparsed: list = field(default_factory=list)


def _candidates(tok: str) -> list:
    """All digit strings an OCR token could stand for. Unambiguous tokens give one candidate."""
    tok = tok.replace(",", "")
    options = []
    for ch in tok:
        if ch.isdigit() or ch == ".":
            options.append(ch)
        elif ch in DIGIT_FIX:
            options.append(DIGIT_FIX[ch])
        else:
            return []
    out = ["".join(c) for c in itertools.islice(itertools.product(*options), 64)]
    return [o for o in out if o.count(".") <= 1 and o not in (".", "")]


def to_number(tok: str) -> float:
    c = _candidates(tok)
    if not c:
        raise ValueError(tok)
    return float(c[0])


def resolve_numbers(qty_s, rate_s, amount_s):
    """Returns (qty, rate, amount, repaired) or None.

    If a token holds characters OCR commonly confuses with digits, use qty x rate = amount to choose among
    the possible readings. If no reading satisfies it, return None instead of guessing."""
    cands = [_candidates(t) for t in (qty_s, rate_s, amount_s)]
    if not all(cands):
        return None
    ambiguous = any(not re.fullmatch(r"[\d,]*\.?\d*", t) for t in (qty_s, rate_s, amount_s))
    if not ambiguous:
        return float(cands[0][0]), float(cands[1][0]), float(cands[2][0]), False
    best = None
    for q, r, a in itertools.islice(itertools.product(*cands), 4096):
        q, r, a = float(q), float(r), float(a)
        err = abs(q * r - a)
        if err <= max(1.0, 0.005 * a) and (best is None or err < best[0]):
            best = (err, q, r, a)
    return (best[1], best[2], best[3], True) if best else None


def resolve_unit(tok: str):
    t = tok.lower()
    if t in UNIT_ALIASES:
        return UNIT_ALIASES[t]
    close = difflib.get_close_matches(t, list(UNIT_ALIASES), n=1, cutoff=0.75)
    return UNIT_ALIASES[close[0]] if close else None


def parse_text(text: str) -> ParsedDoc:
    doc = ParsedDoc()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if (m := RFQ.search(line)) and not doc.rfq_ref:
            doc.rfq_ref = m.group(1).upper()
        if (m := QNO.search(line)) and not doc.quote_no:
            doc.quote_no = m.group(1).upper()
        if (m := DATE.search(line)) and not doc.quote_date:
            d, mo, y = m.groups()
            doc.quote_date = f"{y}-{int(mo):02d}-{int(d):02d}"
        if (m := SUPPLIER.match(line)) and not doc.supplier:
            doc.supplier = m.group(1).strip()
        if (m := TOTAL.match(line)):
            doc.stated_total = to_number(m.group("v"))
            continue
        if (m := ITEM.match(line)) and (unit := resolve_unit(m["unit"])) and (nums := resolve_numbers(m["qty"], m["rate"], m["amount"])):
            qty, rate, amount, repaired = nums
            doc.items.append(ParsedItem(len(doc.items) + 1, m["desc"].strip(" |-_"), qty, unit, rate, amount, repaired))
        elif LOOKS_LIKE_ITEM.match(line) and not DATE.search(line) and "RFQ" not in line.upper():
            doc.unparsed.append(line)
        elif ITEM.match(line):
            doc.unparsed.append(line)
    if not doc.supplier and lines:
        first = lines[0]
        if not any(c.isdigit() for c in first) and len(first) < 60:
            doc.supplier = first.title() if first.isupper() else first
    return doc
