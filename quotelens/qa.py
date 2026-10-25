"""Question answering over ingested quotations.

Retrieval is BM25 over short chunks (one per line item, plus each document's terms line). Tools give exact
answers from the database for price questions. With ANTHROPIC_API_KEY the model chooses tools; without it,
or if the call fails, a rule-based router uses the same tools."""
import json
import logging
import math
import re
from collections import Counter

import requests

from .catalog import BY_ID

log = logging.getLogger(__name__)
API_URL = "https://api.anthropic.com/v1/messages"
TOKEN = re.compile(r"[a-z0-9]+")
SYSTEM = ("You answer questions about supplier quotations for a construction procurement team. Use the tools. "
          "Quote supplier names and document ids from tool results, say when the data is missing or comes from an "
          "OCR scan, and never invent prices.")
TOOLS = [
    {"name": "search_quotes", "description": "Full-text search over quotation line items and terms.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                      "required": ["query"]}},
    {"name": "best_price", "description": "Lowest unflagged rates per supplier for a material (any wording).",
     "input_schema": {"type": "object", "properties": {"material": {"type": "string"}}, "required": ["material"]}},
    {"name": "list_flagged", "description": "Line items with warnings or errors, optionally for one RFQ.",
     "input_schema": {"type": "object", "properties": {"rfq": {"type": "string"}}}},
    {"name": "supplier_terms", "description": "Payment, delivery and validity terms, optionally for one supplier.",
     "input_schema": {"type": "object", "properties": {"supplier": {"type": "string"}}}},
]


def tokens(text):
    return TOKEN.findall(text.lower())


class Bm25:
    def __init__(self, docs, k1=1.4, b=0.75):
        self.tf = [Counter(tokens(d)) for d in docs]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if docs else 1
        df = Counter(t for c in self.tf for t in c)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.k1, self.b = k1, b

    def top(self, query, k):
        q, out = set(tokens(query)), []
        for i, (tf, ln) in enumerate(zip(self.tf, self.len)):
            s = sum(self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * ln / self.avg))
                    for t in q if t in tf)
            if s > 0:
                out.append((s, i))
        return [i for _, i in sorted(out, reverse=True)[:k]]


class Tools:
    def __init__(self, conn, matcher):
        self.conn, self.matcher = conn, matcher
        self.chunks = self._chunks()
        self.bm = Bm25([c["text"] for c in self.chunks])

    def _chunks(self):
        out = []
        for d in self.conn.execute("SELECT * FROM documents WHERE status != 'failed'"):
            who = d["supplier"] or f"document {d['id']}"
            for line in d["raw_text"].splitlines():
                if line.lower().startswith("terms"):
                    out.append({"kind": "terms", "doc_id": d["id"], "supplier": who, "text": f"{who} {line.strip()}"})
            for r in self.conn.execute("SELECT * FROM line_items WHERE document_id=?", (d["id"],)):
                out.append({"kind": "item", "doc_id": d["id"], "supplier": who, "item_id": r["id"],
                            "text": f"{who} {d['rfq_ref'] or ''} {r['raw_desc']} qty {r['qty']:g} {r['unit']} rate {r['rate']:,.2f}"})
        return out

    def run(self, name, args):
        fn = getattr(self, f"_{name}", None) if name in {t["name"] for t in TOOLS} else None
        if fn is None:
            return {"error": f"unknown tool {name!r}"}
        try:
            return fn(**(args or {}))
        except TypeError as exc:
            return {"error": f"bad arguments: {exc}"}

    def _search_quotes(self, query, limit=5):
        limit = max(1, min(limit, 10)) if isinstance(limit, int) else 5
        return {"results": [{k: self.chunks[i][k] for k in ("doc_id", "supplier", "text")} for i in self.bm.top(str(query), limit)]}

    def _best_price(self, material):
        cid, score = self.matcher.match(str(material))
        if cid is None:
            return {"error": f"could not tell which material {material!r} is"}
        rows = self.conn.execute(
            "SELECT i.rate_norm, i.flags, d.id doc, d.supplier, d.rfq_ref, d.kind FROM line_items i "
            "JOIN documents d ON d.id=i.document_id WHERE i.catalog_id=? AND i.rate_norm IS NOT NULL AND d.status!='failed'",
            (cid,)).fetchall()
        clean = [r for r in rows if not any(f["severity"] in ("error", "warn") for f in json.loads(r["flags"]))]
        best = {}
        for r in sorted(clean, key=lambda r: r["rate_norm"]):
            best.setdefault(r["supplier"] or f"document {r['doc']}", r)
        mat = BY_ID[cid]
        return {"material": mat.name, "unit": mat.unit, "excluded_flagged_quotes": len(rows) - len(clean),
                "quotes": [{"supplier": s, "rate": round(r["rate_norm"], 2), "doc_id": r["doc"], "rfq": r["rfq_ref"],
                            "from_scan": r["kind"] != "pdf_text"} for s, r in list(best.items())[:5]]}

    def _list_flagged(self, rfq=None):
        sql = ("SELECT i.id, i.raw_desc, i.flags, d.id doc, d.supplier, d.rfq_ref FROM line_items i "
               "JOIN documents d ON d.id=i.document_id WHERE i.flags LIKE '%\"warn\"%' OR i.flags LIKE '%\"error\"%'")
        rows = self.conn.execute(sql).fetchall()
        out = []
        for r in rows:
            if rfq and (r["rfq_ref"] or "").upper() != str(rfq).upper():
                continue
            fl = [f for f in json.loads(r["flags"]) if f["severity"] in ("warn", "error")]
            out.append({"doc_id": r["doc"], "supplier": r["supplier"], "item": r["raw_desc"][:60], "flags": [f["message"] for f in fl]})
        return {"count": len(out), "items": out[:15]}

    def _supplier_terms(self, supplier=None):
        res = [{"doc_id": c["doc_id"], "supplier": c["supplier"], "text": c["text"]} for c in self.chunks
               if c["kind"] == "terms" and (not supplier or str(supplier).lower() in c["supplier"].lower())]
        return {"results": res[:10]}


def _llm(question, tools, cfg, call):
    messages, used = [{"role": "user", "content": question}], []
    for _ in range(5):
        data = call(cfg, messages)
        content = data.get("content", [])
        if data.get("stop_reason") != "tool_use":
            return "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip(), used
        messages.append({"role": "assistant", "content": content})
        results = []
        for b in content:
            if b.get("type") == "tool_use":
                out = tools.run(b["name"], b.get("input"))
                used.append(b["name"])
                results.append({"type": "tool_result", "tool_use_id": b["id"], "content": json.dumps(out)[:6000]})
        messages.append({"role": "user", "content": results})
    raise RuntimeError("too many tool steps")


def call_anthropic(cfg, messages):
    r = requests.post(API_URL, timeout=30, headers={"x-api-key": cfg.anthropic_api_key, "anthropic-version": "2023-06-01"},
                      json={"model": cfg.llm_model, "max_tokens": 1024, "system": SYSTEM, "tools": TOOLS, "messages": messages})
    r.raise_for_status()
    return r.json()


def local_answer(question, tools):
    q = question.lower()
    if re.search(r"cheap|lowest|best price|least|minimum", q):
        res = tools.run("best_price", {"material": question})
        if "error" in res:
            return "Tell me which material you mean, for example 'TMT 12mm' or 'OPC 53 cement'.", ["best_price"]
        if not res["quotes"]:
            return f"No clean quotes for {res['material']} yet.", ["best_price"]
        lines = [f"- {x['supplier']}: {x['rate']:,.2f} per {res['unit']} (document {x['doc_id']}{', scan' if x['from_scan'] else ''})"
                 for x in res["quotes"]]
        note = f" {res['excluded_flagged_quotes']} flagged quote(s) were left out." if res["excluded_flagged_quotes"] else ""
        return f"Lowest quotes for {res['material']}:\n" + "\n".join(lines) + note, ["best_price"]
    if re.search(r"flag|anomal|suspicious|wrong|error|issue|problem", q):
        m = re.search(r"RFQ-[A-Z0-9\-]+", question, re.IGNORECASE)
        res = tools.run("list_flagged", {"rfq": m.group(0)} if m else {})
        if not res["count"]:
            return "No flagged items.", ["list_flagged"]
        lines = [f"- document {x['doc_id']} ({x['supplier']}): {x['item']}: {x['flags'][0]}" for x in res["items"][:8]]
        return f"{res['count']} flagged item(s):\n" + "\n".join(lines), ["list_flagged"]
    if re.search(r"payment|delivery|terms|credit|valid|advance", q):
        sup = next((c["supplier"] for c in tools.chunks if c["supplier"].lower() in q), None)
        res = tools.run("supplier_terms", {"supplier": sup} if sup else {})
        if not res["results"]:
            return "No terms found.", ["supplier_terms"]
        return "\n".join(f"- {r['text']} (document {r['doc_id']})" for r in res["results"][:6]), ["supplier_terms"]
    res = tools.run("search_quotes", {"query": question, "limit": 5})
    if not res["results"]:
        return "Nothing in the ingested quotations matches that.", ["search_quotes"]
    return "Closest matches:\n" + "\n".join(f"- {r['text']} (document {r['doc_id']})" for r in res["results"]), ["search_quotes"]


def answer(question, conn, matcher, cfg, call=call_anthropic):
    tools = Tools(conn, matcher)
    if cfg.anthropic_api_key:
        try:
            text, used = _llm(question, tools, cfg, call)
            return {"answer": text or "(empty answer)", "mode": "llm", "tools": used}
        except Exception as exc:
            log.warning("LLM failed, using local router: %s", exc)
            text, used = local_answer(question, tools)
            return {"answer": text, "mode": "local-fallback", "tools": used}
    text, used = local_answer(question, tools)
    return {"answer": text, "mode": "local", "tools": used}
