"""Ingest a file: extract, parse, match, validate, store."""
import json

from .catalog import BY_ID
from .db import history_rates, now_iso
from .extract import ExtractionError, extract
from .parse import parse_text
from .validate import check_item

BLOCKING = ("error", "warn")


def _insert_failed(conn, filename, stored_path, message, kind="pdf_text"):
    cur = conn.execute("INSERT INTO documents (filename, stored_path, kind, status, message, created_at) "
                       "VALUES (?,?,?,?,?,?)", (filename, stored_path, kind, "failed", message, now_iso()))
    conn.commit()
    return cur.lastrowid


def ingest_file(conn, path, filename, matcher, stored_path=None):
    """Returns the new document id. Never raises for bad input; the document is stored as 'failed'."""
    try:
        kind, text = extract(path)
    except ExtractionError as exc:
        return _insert_failed(conn, filename, stored_path, str(exc))
    parsed = parse_text(text)
    notes = []
    if not parsed.items:
        notes.append("no line items were recognised")
    if kind != "pdf_text":
        notes.append("read by OCR: check the values against the original before approving")
    if not parsed.supplier:
        notes.append("supplier name not found")
    if parsed.unparsed:
        notes.append(f"{len(parsed.unparsed)} line(s) looked like items but could not be read")
    total = sum(i.amount for i in parsed.items)
    if parsed.stated_total is not None and parsed.items and abs(total - parsed.stated_total) > max(1.0, 0.01 * parsed.stated_total):
        notes.append(f"stated total {parsed.stated_total:,.2f} differs from the sum of items {total:,.2f}")
    cur = conn.execute(
        "INSERT INTO documents (filename, stored_path, supplier, rfq_ref, quote_no, quote_date, kind, status, message,"
        " stated_total, raw_text, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (filename, stored_path, parsed.supplier, parsed.rfq_ref, parsed.quote_no, parsed.quote_date, kind,
         "parsed", "", parsed.stated_total, text, now_iso()))
    doc_id = cur.lastrowid
    flagged = False
    for it in parsed.items:
        cid, score = matcher.match(it.desc)
        rate_norm, flags = check_item(it.qty, it.unit, it.rate, it.amount, cid, history_rates(conn, cid) if cid else [],
                                      ocr=kind != "pdf_text", repaired=it.ocr_repaired)
        flagged |= any(f["severity"] in BLOCKING for f in flags)
        conn.execute("INSERT INTO line_items (document_id, line_no, raw_desc, qty, unit, rate, amount, catalog_id,"
                     " match_score, rate_norm, flags) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (doc_id, it.line_no, it.desc, it.qty, it.unit, it.rate, it.amount, cid, score, rate_norm,
                      json.dumps(flags)))
    status = "needs_review" if (notes or flagged) else "parsed"
    if flagged:
        notes.append("some items have warnings or errors")
    conn.execute("UPDATE documents SET status=?, message=? WHERE id=?", (status, "; ".join(notes), doc_id))
    conn.commit()
    return doc_id


def override_match(conn, item_id, catalog_id):
    """A reviewer picks the right material; the item is re-validated against that material's history."""
    if catalog_id not in BY_ID:
        raise ValueError("unknown catalog id")
    row = conn.execute("SELECT * FROM line_items WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise LookupError("item not found")
    kind = conn.execute("SELECT kind FROM documents WHERE id=?", (row["document_id"],)).fetchone()[0]
    rate_norm, flags = check_item(row["qty"], row["unit"], row["rate"], row["amount"], catalog_id,
                                  history_rates(conn, catalog_id), ocr=kind != "pdf_text")
    conn.execute("UPDATE line_items SET catalog_id=?, rate_norm=?, flags=?, overridden=1 WHERE id=?",
                 (catalog_id, rate_norm, json.dumps(flags), item_id))
    conn.commit()


def approve_document(conn, doc_id):
    """Marks the document approved and adds its clean items to the price history.
    Items with any warning or error are left out so a bad quote cannot shift the baseline."""
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if doc is None:
        raise LookupError("document not found")
    if doc["status"] == "failed":
        raise ValueError("a failed document cannot be approved")
    if doc["status"] == "approved":
        return 0
    added = 0
    for r in conn.execute("SELECT * FROM line_items WHERE document_id=?", (doc_id,)).fetchall():
        flags = json.loads(r["flags"])
        if r["catalog_id"] and r["rate_norm"] and not any(f["severity"] in BLOCKING for f in flags):
            conn.execute("INSERT INTO price_history (catalog_id, rate, supplier, observed_on, source) VALUES (?,?,?,?,?)",
                         (r["catalog_id"], r["rate_norm"], doc["supplier"], doc["quote_date"] or now_iso()[:10],
                          "approved_quote"))
            added += 1
    conn.execute("UPDATE documents SET status='approved' WHERE id=?", (doc_id,))
    conn.commit()
    return added
