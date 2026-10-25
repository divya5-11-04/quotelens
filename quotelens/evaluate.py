"""Measure extraction, matching and flagging on freshly generated quotations with known ground truth."""
import json
import random
import tempfile
from collections import defaultdict
from pathlib import Path

from .datagen import make_rfq, seed_history, write_doc
from .db import connect, init_db
from .match import Matcher
from .pipeline import ingest_file

EXPECT = {"price_high": "price_high", "price_low": "price_low", "unit_suspect": "unit_suspect"}


def _same(a, b):
    return abs(a["qty"] - b["qty"]) < 0.01 and abs(a["rate"] - b["rate"]) < 0.01 and abs(a["amount"] - b["amount"]) < 0.01


def run(n_rfq=60, layouts=(0.5, 0.5, 0.0), seed=2026, history_seed=999, hard=False):
    rng = random.Random(seed)
    conn = connect(":memory:")
    init_db(conn)
    seed_history(conn, random.Random(history_seed))
    matcher = Matcher()
    stats = defaultdict(lambda: defaultdict(int))
    with tempfile.TemporaryDirectory() as tmp:
        for idx in range(n_rfq):
            for doc in make_rfq(rng, idx, layout_weights=layouts, hard=hard):
                path = write_doc(doc, Path(tmp), rng)
                doc_id = ingest_file(conn, path, path.name, matcher)
                s = stats[("scan" if doc.layout == "scan" else "text") + (" (hard)" if hard else "")]
                got = [dict(r) for r in conn.execute("SELECT * FROM line_items WHERE document_id=?", (doc_id,))]
                status = conn.execute("SELECT status FROM documents WHERE id=?", (doc_id,)).fetchone()[0]
                s["docs"] += 1
                s["docs_needs_review"] += status == "needs_review"
                s["truth_items"] += len(doc.items)
                s["parsed_items"] += len(got)
                for t in doc.items:
                    hit = next((g for g in got if _same(g, t)), None)
                    s["found"] += hit is not None
                    if hit is None:
                        continue
                    s["matched_ok"] += hit["catalog_id"] == t["catalog_id"]
                    codes = {f["code"] for f in json.loads(hit["flags"])}
                    blocking = {f["code"] for f in json.loads(hit["flags"]) if f["severity"] in ("warn", "error")}
                    a = t["anomaly"]
                    if a is None:
                        s["clean"] += 1
                        s["clean_flagged"] += bool(blocking)
                    else:
                        s[f"{a}_n"] += 1
                        want = {"arithmetic_mismatch": {"arithmetic_mismatch", "ocr_uncertain"}}.get(a, {a})
                        s[f"{a}_caught"] += bool(codes & want)
                        s["anomalous_n"] += 1
                        s["anomalous_flagged"] += bool(blocking)
                s["parsed_correct"] += sum(any(_same(g, t) for t in doc.items) for g in got)
    return {k: dict(v) for k, v in stats.items()}


def pct(a, b):
    return "n/a" if not b else f"{100 * a / b:.1f}% ({a}/{b})"


def to_markdown(res):
    out = []
    for name, s in res.items():
        out += [f"### {name} documents ({s['docs']} documents, {s['truth_items']} line items)", "",
                "| Measure | Result |", "|---|---|",
                f"| Items recovered exactly (qty, rate, amount) | {pct(s.get('found', 0), s['truth_items'])} |",
                f"| Parsed items that are correct (precision) | {pct(s.get('parsed_correct', 0), s['parsed_items'])} |",
                f"| Catalog match correct, among recovered items | {pct(s.get('matched_ok', 0), s.get('found', 0))} |"]
        for a in ("price_high", "price_low", "unit_suspect", "arithmetic_mismatch"):
            if s.get(f"{a}_n"):
                out.append(f"| Injected `{a}` caught | {pct(s.get(f'{a}_caught', 0), s[f'{a}_n'])} |")
        out += [f"| Clean items wrongly flagged (warn/error) | {pct(s.get('clean_flagged', 0), s.get('clean', 0))} |",
                f"| Documents sent to review | {pct(s['docs_needs_review'], s['docs'])} |", ""]
    return "\n".join(out)
