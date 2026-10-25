import json
import os
import uuid
from pathlib import Path

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from . import qa
from .catalog import BY_ID, CATALOG
from .compare import compare_rfq
from .config import Config
from .db import connect, init_db
from .match import Matcher
from .pipeline import approve_document, ingest_file, override_match

ALLOWED = {".pdf", ".png", ".jpg", ".jpeg"}


def create_app(cfg: Config = None) -> Flask:
    cfg = cfg or Config.from_env()
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=cfg.max_upload_mb * 1024 * 1024, SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only"))
    Path(cfg.upload_dir).mkdir(parents=True, exist_ok=True)
    boot = connect(cfg.db_path)
    init_db(boot)
    boot.close()
    matcher = Matcher()

    def db():
        if "db" not in g:
            g.db = connect(cfg.db_path)
        return g.db

    @app.teardown_appcontext
    def close(_):
        conn = g.pop("db", None)
        if conn:
            conn.close()

    def require_token():
        if cfg.api_token and request.headers.get("Authorization") != f"Bearer {cfg.api_token}":
            abort(401)

    def save_upload():
        f = request.files.get("file")
        if f is None or not f.filename:
            return None, ("no file uploaded (form field 'file')", 400)
        name = secure_filename(f.filename)
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED:
            return None, (f"unsupported file type {ext or '(none)'}; use PDF, PNG or JPG", 400)
        stored = Path(cfg.upload_dir) / f"{uuid.uuid4().hex}{ext}"
        f.save(stored)
        return (stored, name), None

    def ingest_upload():
        saved, err = save_upload()
        if err:
            return None, err
        stored, name = saved
        return ingest_file(db(), stored, name, matcher, stored_path=str(stored)), None

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error=f"file larger than {cfg.max_upload_mb} MB"), 413

    @app.get("/healthz")
    def healthz():
        db().execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/")
    def index():
        docs = db().execute("SELECT d.*, (SELECT COUNT(*) FROM line_items i WHERE i.document_id=d.id) n_items "
                            "FROM documents d ORDER BY d.id DESC LIMIT 100").fetchall()
        counts = dict(db().execute("SELECT status, COUNT(*) FROM documents GROUP BY status").fetchall())
        rfqs = [r[0] for r in db().execute("SELECT DISTINCT rfq_ref FROM documents WHERE rfq_ref IS NOT NULL ORDER BY rfq_ref DESC LIMIT 20")]
        return render_template("index.html", docs=docs, counts=counts, rfqs=rfqs)

    @app.route("/upload", methods=["GET", "POST"])
    def upload():
        if request.method == "GET":
            return render_template("upload.html")
        doc_id, err = ingest_upload()
        if err:
            flash(err[0])
            return redirect(url_for("upload"))
        return redirect(url_for("document", doc_id=doc_id))

    @app.get("/documents/<int:doc_id>")
    def document(doc_id):
        d = db().execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if d is None:
            abort(404)
        items = [dict(r, flag_list=json.loads(r["flags"]), material=BY_ID[r["catalog_id"]].name if r["catalog_id"] else None)
                 for r in db().execute("SELECT * FROM line_items WHERE document_id=? ORDER BY line_no", (doc_id,))]
        return render_template("document.html", d=d, items=items, catalog=CATALOG)

    @app.post("/items/<int:item_id>/match")
    def item_match(item_id):
        try:
            override_match(db(), item_id, request.form.get("catalog_id", ""))
        except ValueError:
            abort(400)
        except LookupError:
            abort(404)
        doc = db().execute("SELECT document_id FROM line_items WHERE id=?", (item_id,)).fetchone()[0]
        return redirect(url_for("document", doc_id=doc))

    @app.post("/documents/<int:doc_id>/approve")
    def approve(doc_id):
        try:
            approve_document(db(), doc_id)
        except LookupError:
            abort(404)
        except ValueError:
            abort(400)
        return redirect(url_for("document", doc_id=doc_id))

    @app.get("/rfq/<ref>")
    def rfq(ref):
        c = compare_rfq(db(), ref)
        if c is None:
            abort(404)
        return render_template("rfq.html", c=c)

    @app.route("/ask", methods=["GET", "POST"])
    def ask():
        question = request.form.get("question", "").strip() if request.method == "POST" else ""
        result = qa.answer(question, db(), matcher, cfg) if question else None
        return render_template("ask.html", question=question, result=result)

    # ---- JSON API ----
    @app.post("/api/documents")
    def api_upload():
        require_token()
        doc_id, err = ingest_upload()
        if err:
            return jsonify(error=err[0]), err[1]
        return api_document(doc_id, 201)

    @app.get("/api/documents/<int:doc_id>")
    def api_document(doc_id, status=200):
        d = db().execute("SELECT id, filename, supplier, rfq_ref, quote_no, quote_date, kind, status, message, stated_total "
                         "FROM documents WHERE id=?", (doc_id,)).fetchone()
        if d is None:
            return jsonify(error="not found"), 404
        items = [dict(r, flags=json.loads(r["flags"])) for r in db().execute(
            "SELECT line_no, raw_desc, qty, unit, rate, amount, catalog_id, match_score, rate_norm, flags "
            "FROM line_items WHERE document_id=? ORDER BY line_no", (doc_id,))]
        return jsonify(dict(d, items=items)), status

    @app.get("/api/rfq/<ref>/comparison")
    def api_rfq(ref):
        c = compare_rfq(db(), ref)
        return (jsonify(c), 200) if c else (jsonify(error="no documents for this RFQ"), 404)

    @app.post("/api/ask")
    def api_ask():
        body = request.get_json(silent=True) or {}
        q = body.get("question")
        if not isinstance(q, str) or not q.strip():
            return jsonify(error="'question' is required"), 400
        return jsonify(qa.answer(q.strip(), db(), matcher, cfg))

    return app
