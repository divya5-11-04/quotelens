"""Synthetic quotation generator: text PDFs, table PDFs and degraded scans, plus ground truth.

All prices derive from illustrative base prices in catalog.py. Supplier names and brands are fictional.
"""
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import cv2
import numpy as np
import pdfplumber
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .catalog import CATALOG, BY_ID
from .db import now_iso

SUPPLIERS = ["Kaveri Traders", "Sahyadri Steel and Cement", "Orion Build Supply", "Meridian Materials",
             "Lakeview Enterprises", "Granite Hill Suppliers"]
BRANDS = ["Kaveri", "Sahyadri", "Orion", "Meridian", "Lakeview", "Granite Hill"]
VARIANTS = {
    "cement_opc53": ["{b} OPC-53 Cement (50 kg bag)", "Cement OPC 53 Grade - {b}", "{b} 53 Grade Ordinary Portland Cement"],
    "cement_ppc": ["{b} PPC Cement 50kg", "Portland Pozzolana Cement - {b}"],
    "river_sand": ["River Sand (washed)", "Natural River Sand - {b}"],
    "msand": ["M Sand (Manufactured Sand)", "Manufactured Sand - {b}"],
    "agg_20": ["20 mm Coarse Aggregate", "Blue Metal 20mm - {b}"],
    "brick_red": ["Red Brick (Wirecut) - {b}", "Clay Brick First Class"],
    "brick_flyash": ["Fly Ash Bricks - {b}", "Fly ash brick 230x110x75"],
    "aac_block": ["AAC Blocks 600 x 200 x 200 mm - {b}", "Autoclaved Aerated Concrete Block 600x200x200"],
    "rmc_m25": ["Ready Mix Concrete M25 - {b}", "RMC M25 Grade Concrete"],
    "binding_wire": ["Binding Wire 18G - {b}", "MS Binding Wire 18 gauge"],
    "plywood_18": ["{b} MR Plywood 18 mm", "Plywood 18mm (MR grade)"],
}
for _d in (8, 10, 12, 16, 20):
    VARIANTS[f"tmt_{_d}"] = [f"{{b}} TMT Bars Fe500D {_d}mm", f"Fe 500D TMT Rebar {_d} mm dia - {{b}}",
                             f"TMT Steel {_d}mm Fe500D ({{b}})"]
QTY = {"bag": (50, 600), "kg": (500, 6000), "tonne": (5, 60), "nos": (1000, 12000), "cum": (10, 120), "sheet": (20, 150)}
TERMS = ["Payment: 30 days from invoice. Delivery within 7 days of PO. Prices valid for 15 days.",
         "Payment: 50 percent advance, balance on delivery. Delivery 10 days. GST extra at applicable rates.",
         "Payment: 45 days credit. Free delivery within 25 km. Unloading by buyer.",
         "Payment: against delivery. Prices valid for 7 days. Transport charges extra."]


@dataclass
class GenDoc:
    supplier: str
    rfq: str
    quote_no: str
    quote_date: str
    items: list
    terms: str
    layout: str
    path: str = ""
    extra: dict = field(default_factory=dict)

    def truth(self):
        return {"supplier": self.supplier, "rfq": self.rfq, "quote_no": self.quote_no, "date": self.quote_date,
                "layout": self.layout, "items": self.items, "total": round(sum(i["amount"] for i in self.items), 2)}


def seed_history(conn, rng, per_material=40):
    today = date.today()
    for m in CATALOG:
        for _ in range(per_material):
            rate = round(m.base_price * rng.gauss(1, 0.04), 2)
            conn.execute("INSERT INTO price_history (catalog_id, rate, supplier, observed_on, source) VALUES (?,?,?,?,?)",
                         (m.id, rate, rng.choice(SUPPLIERS), (today - timedelta(days=rng.randrange(180))).isoformat(),
                          "synthetic"))
    conn.commit()


def noisy(rng, text):
    """Typos and shorthand a human typist or a spreadsheet export might produce."""
    choice = rng.randrange(5)
    if choice == 0 and len(text) > 8:
        i = rng.randrange(2, len(text) - 3)
        return text[:i] + text[i + 1] + text[i] + text[i + 2:]
    if choice == 1:
        return text.upper()
    if choice == 2:
        return text.replace(" mm", "mm").replace("mm", " mm")
    if choice == 3:
        return text.replace("Cement", "Cmnt").replace("Concrete", "Conc.").replace("Bars", "Brs")
    return text.replace(" - ", " ")


def make_rfq(rng, idx, layout_weights=(0.4, 0.3, 0.3), anomaly_rate=1.0, hard=False):
    n_items, n_sup = rng.randint(5, 9), rng.randint(3, 4)
    mats = rng.sample(CATALOG, n_items)
    qtys = {m.id: rng.randint(*QTY[m.unit]) for m in mats}
    sup_ids = rng.sample(range(len(SUPPLIERS)), n_sup)
    rfq = f"RFQ-2026-{idx:03d}"
    docs = []
    for k, si in enumerate(sup_ids):
        factor = rng.gauss(1, 0.03)
        items = []
        for m in mats:
            if rng.random() > 0.85:
                continue
            rate, unit = round(m.base_price * factor * rng.gauss(1, 0.03), 2), m.unit
            anomaly = None
            roll = rng.random() / anomaly_rate
            if roll < 0.05:
                rate, anomaly = round(rate * rng.uniform(*((1.1, 1.5) if hard else (1.5, 2.2))), 2), "price_high"
            elif roll < 0.07:
                rate, anomaly = round(rate * rng.uniform(*((0.6, 0.9) if hard else (0.45, 0.65))), 2), "price_low"
            elif roll < 0.10 and m.unit == "kg":
                rate, anomaly = round(rate * 1000, 2), "unit_suspect"
            elif m.unit == "kg" and rng.random() < 0.25:
                rate, unit = round(rate * 1000, 2), "tonne"      # valid: quoted per tonne
            qty = qtys[m.id] if unit != "tonne" or m.unit == "tonne" else round(qtys[m.id] / 1000, 2)
            amount = round(qty * rate, 2)
            if rng.random() / anomaly_rate < 0.03:
                amount, anomaly = round(amount * rng.uniform(1.06, 1.3), 2), (anomaly or "arithmetic_mismatch")
            desc = rng.choice(VARIANTS[m.id]).format(b=BRANDS[si])
            if hard and rng.random() < 0.35:
                desc = noisy(rng, desc)
            items.append({"desc": desc, "catalog_id": m.id, "qty": qty, "unit": unit, "rate": rate, "amount": amount,
                          "anomaly": anomaly})
        layout = rng.choices(["table", "plain", "scan"], weights=layout_weights)[0]
        docs.append(GenDoc(SUPPLIERS[si], rfq, f"Q-{1000 + idx * 10 + k}",
                           (date.today() - timedelta(days=rng.randrange(10))).strftime("%d/%m/%Y"),
                           items, rng.choice(TERMS), layout))
    return docs


def _fmt(x):
    return f"{x:,.2f}"


def render_pdf(doc: GenDoc, path, grid):
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    y = h - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, y, doc.supplier.upper())
    c.setFont("Helvetica", 9)
    for line in (f"Quotation No: {doc.quote_no}     Date: {doc.quote_date}", f"RFQ Ref: {doc.rfq}",
                 "Project: Whitefield Residential Block B"):
        y -= 16
        c.drawString(40, y, line)
    y -= 28
    cols = {"no": 44, "desc": 70, "qty": 355, "unit": 372, "rate": 480, "amount": 555}
    top = y + 12

    def header():
        c.setFont("Helvetica-Bold", 9)
        c.drawString(cols["no"], y, "Sl")
        c.drawString(cols["desc"], y, "Description")
        c.drawRightString(cols["qty"], y, "Qty")
        c.drawString(cols["unit"], y, "Unit")
        c.drawRightString(cols["rate"], y, "Rate")
        c.drawRightString(cols["amount"], y, "Amount")
        c.setFont("Helvetica", 9)

    header()
    for n, it in enumerate(doc.items, 1):
        y -= 18
        c.drawString(cols["no"], y, str(n))
        c.drawString(cols["desc"], y, it["desc"])
        c.drawRightString(cols["qty"], y, f"{it['qty']:g}" if it["qty"] != int(it["qty"]) else str(int(it["qty"])))
        c.drawString(cols["unit"], y, it["unit"])
        c.drawRightString(cols["rate"], y, _fmt(it["rate"]))
        c.drawRightString(cols["amount"], y, _fmt(it["amount"]))
    y -= 24
    c.setFont("Helvetica-Bold", 9)
    c.drawString(cols["desc"], y, "Total")
    c.drawRightString(cols["amount"], y, _fmt(sum(i["amount"] for i in doc.items)))
    c.setFont("Helvetica", 8)
    y -= 30
    c.drawString(40, y, "Terms: " + doc.terms)
    if grid:
        c.setLineWidth(0.5)
        bottom = y + 40 - 12 + 10
        for x in (40, 62, 66, 358, 368, 420, 490, 560):
            c.line(x, top, x, bottom)
        for yy in [top] + [top - 6 - 18 * i for i in range(len(doc.items) + 2)]:
            c.line(40, yy, 560, yy)
    c.save()


def degrade(img_bgr, rng):
    h, w = img_bgr.shape[:2]
    angle = rng.uniform(-2.0, 2.0)
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(img_bgr, m, (w, h), borderValue=(255, 255, 255))
    out = cv2.GaussianBlur(out, (3, 3), 0.7)
    noise = np.random.default_rng(rng.randrange(1 << 30)).normal(0, 9, out.shape)
    out = np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def write_doc(doc: GenDoc, out_dir, rng, resolution=300):
    """Writes the document in its layout and a .json ground-truth file. Returns the document path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{doc.rfq}_{doc.quote_no}_{doc.layout}"
    pdf_path = out_dir / f"{stem}.pdf"
    render_pdf(doc, pdf_path, grid=doc.layout in ("table", "scan"))
    final = pdf_path
    if doc.layout == "scan":
        with pdfplumber.open(pdf_path) as pdf:
            img = cv2.cvtColor(np.array(pdf.pages[0].to_image(resolution=resolution).original.convert("RGB")),
                               cv2.COLOR_RGB2BGR)
        final = out_dir / f"{stem}.jpg"
        cv2.imwrite(str(final), degrade(img, rng), [cv2.IMWRITE_JPEG_QUALITY, 70])
        pdf_path.unlink()
    (out_dir / f"{stem}.json").write_text(json.dumps(doc.truth(), indent=1))
    doc.path = str(final)
    return final
