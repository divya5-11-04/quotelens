# Quotelens

Read supplier quotations (PDFs and photos), check the prices, and compare suppliers for the same request.

## The problem

A site or procurement engineer asks three or four suppliers for rates on the same list of materials. The replies come back as PDFs, scanned sheets and photos, each worded differently ("TMT Rebar Fe 500D 12 mm dia", "Fe500D TMT bars 12mm"). Someone retypes them into a spreadsheet, and mistakes get through: a rate quoted per tonne but typed as per kg, a line total that does not add up, a rate far from what was paid last month. Quotelens reads the files, matches each line to a materials catalog, flags the suspicious ones, and shows a side-by-side comparison.

## What is implemented

- **Reading files** (`extract.py`): PDFs with a text layer are read directly. Scanned PDFs and photos go through OpenCV preprocessing (upscale, deskew by projection profile, binarise, remove table borders) and Tesseract OCR.
- **Parsing** (`parse.py`): header (supplier, RFQ ref, quote number, date, stated total) and line items. For OCR text, unclear characters are resolved using qty x rate = amount; if no reading fits, the line is reported as unreadable instead of guessed.
- **Matching** (`match.py`): free-text description to a 16-item catalog with TF-IDF over words and character n-grams, with a size check so 12mm does not match 16mm. A reviewer can correct a match.
- **Checks** (`validate.py`): arithmetic, unit conversion (kg and tonne), probable unit errors (~1000x), and price against history using median and MAD.
- **Review and comparison** (web UI): per-document review with flags, approve (adds clean items to price history), per-RFQ supplier comparison with cheapest quote and like-for-like totals.
- **Questions** (`/ask`, `qa.py`): "who is cheapest for TMT 12mm", "which quotes are flagged", "what are the payment terms". Prices come from database tools; the optional LLM mode lets Claude choose among the same tools, with a rule-based fallback.
- **JSON API**: `POST /api/documents`, `GET /api/documents/<id>`, `GET /api/rfq/<ref>/comparison`, `POST /api/ask`, `GET /healthz`.
- **Synthetic data generator**: text PDFs, bordered tables and degraded scans with ground truth, used for tests and evaluation.

## Quick start

Requires Python 3.11+ and the Tesseract OCR program (`sudo apt install tesseract-ocr`, `brew install tesseract`, or the Windows installer).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m quotelens init                       # creates the database and synthetic price history
python -m quotelens generate --out samples     # writes sample quotations (PDF and JPG) with .json ground truth
python -m quotelens ingest samples             # or upload them in the web UI
python -m quotelens serve                      # http://127.0.0.1:5000
```

The app reads environment variables directly; see `.env.example`. Price history from `init` is synthetic. Replace it with your own purchase prices before relying on any flag.

```bash
curl -s -F file=@samples/RFQ-2026-001_Q-1010_table.pdf localhost:5000/api/documents
curl -s localhost:5000/api/rfq/RFQ-2026-001/comparison
curl -s -X POST localhost:5000/api/ask -H 'content-type: application/json' -d '{"question":"which quotes are flagged?"}'
```

(Sample file names depend on the generated layout; list `samples/` to see them.)

## Tests and evaluation

```bash
python -m unittest discover -s tests -t . -v      # 31 tests
python -m quotelens evaluate --rfqs 60 --scan-rfqs 8
```

Full results and caveats: [docs/evaluation.md](docs/evaluation.md). In short, on generated data: clean text PDFs are read 100% exactly; scanned photos only 58.6% exactly, with 18% of parsed items wrong, which is why every OCR document goes to human review. Price deviations of 10 to 30 percent are not flagged by design.

## Docker

```bash
docker build -t quotelens . && docker run -p 8000:8000 -v quotelens-data:/data quotelens
```

Run `python -m quotelens init` inside the container once (`docker exec`) to seed the demo price history.

## Layout

```
quotelens/   app, extract, parse, match, validate, pipeline, compare, qa, datagen, evaluate, cli
tests/       unit, pipeline, HTTP and QA tests
docs/        architecture and evaluation
```

## Limitations

- Tested only on generated quotations. Real quotations (wrapped descriptions, multi-page tables, GST and discount lines, different column orders, Hindi or Kannada text) are not handled.
- OCR digit errors are common on photos. Always compare scanned values with the original; the app forces review for them.
- The catalog has 16 materials, and the demo base prices are illustrative, not market data.
- Price checks need at least 8 history points per material and will miss moderate overpricing (below 1.3x).
- No login on web pages; run behind your own access control. Upload and comparison are single-user.
- The LLM mode for `/ask` is implemented and tested with a fake model response, not against the live API.
- SQLite and a single process.

## Next steps

- Add layout-aware table extraction (pdfplumber table settings, or a document layout model) for wrapped and multi-page tables.
- Learn the catalog from approved quotes and reviewer corrections.
- Export a comparison as a purchase-order draft.
