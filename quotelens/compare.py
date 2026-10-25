"""Side-by-side supplier comparison for one RFQ."""
import json

from .catalog import BY_ID


def compare_rfq(conn, rfq_ref):
    docs = conn.execute("SELECT * FROM documents WHERE rfq_ref=? AND status!='failed' ORDER BY id", (rfq_ref,)).fetchall()
    if not docs:
        return None
    suppliers = []
    for d in docs:
        label = d["supplier"] or f"document {d['id']}"
        suppliers.append({"doc_id": d["id"], "supplier": label, "status": d["status"], "kind": d["kind"]})
    by_material = {}
    for i, d in enumerate(docs):
        for r in conn.execute("SELECT * FROM line_items WHERE document_id=? AND catalog_id IS NOT NULL", (d["id"],)):
            flags = json.loads(r["flags"])
            by_material.setdefault(r["catalog_id"], {})[i] = {
                "rate": r["rate_norm"], "qty": r["qty"], "unit": r["unit"], "item_id": r["id"],
                "problem": any(f["severity"] == "error" for f in flags),
                "flags": [f["code"] for f in flags if f["severity"] != "info"]}
    rows = []
    for cid, quotes in by_material.items():
        mat = BY_ID[cid]
        usable = {i: q for i, q in quotes.items() if q["rate"] and not q["problem"]}
        best = min(usable, key=lambda i: usable[i]["rate"]) if usable else None
        rates = [q["rate"] for q in usable.values()]
        spread = (max(rates) / min(rates) - 1) * 100 if len(rates) > 1 else None
        rows.append({"catalog_id": cid, "material": mat.name, "unit": mat.unit, "quotes": quotes,
                     "best": best, "spread_pct": spread})
    rows.sort(key=lambda r: r["material"])
    full = [r for r in rows if len(r["quotes"]) == len(docs) and not any(q["problem"] or not q["rate"] for q in r["quotes"].values())]
    return {"rfq": rfq_ref, "suppliers": suppliers, "rows": rows, "comparable_items": len(full),
            "totals_on_comparable": _totals(conn, docs, full)}


def _totals(conn, docs, full):
    """Total per supplier over materials quoted by everyone, using each supplier's stated amount."""
    out = []
    for i, d in enumerate(docs):
        ids = [r["quotes"][i]["item_id"] for r in full]
        if not ids:
            out.append(None)
            continue
        marks = ",".join("?" * len(ids))
        out.append(conn.execute(f"SELECT SUM(amount) FROM line_items WHERE id IN ({marks})", ids).fetchone()[0])
    return out
