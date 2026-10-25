import io
import json
import os
import unittest

from quotelens.db import connect
from quotelens.match import Matcher
from quotelens.pipeline import approve_document, ingest_file, override_match
from tests.helpers import clean_docs, make_env, memory_db

M = Matcher()


class PipelineTests(unittest.TestCase):
    def test_clean_text_quote_is_parsed_without_flags(self):
        conn = memory_db()
        doc, path = clean_docs(1)[0]
        did = ingest_file(conn, path, path.name, M)
        d = conn.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
        self.assertEqual((d["status"], d["kind"], d["supplier"]), ("parsed", "pdf_text", doc.supplier))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM line_items WHERE document_id=?", (did,)).fetchone()[0], len(doc.items))

    def test_bad_files_become_failed_documents(self):
        conn = memory_db()
        import tempfile
        d = tempfile.mkdtemp()
        cases = {"corrupt.pdf": b"not a pdf", "note.txt": b"hello", "broken.jpg": b"\x00\x01"}
        for name, data in cases.items():
            p = os.path.join(d, name)
            from pathlib import Path; Path(p).write_bytes(data)
            did = ingest_file(conn, p, name, M)
            self.assertEqual(conn.execute("SELECT status FROM documents WHERE id=?", (did,)).fetchone()[0], "failed", name)

    def test_pdf_without_items_goes_to_review(self):
        from reportlab.pdfgen import canvas
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "empty.pdf")
        c = canvas.Canvas(p)
        c.drawString(50, 780, "This is a letter about something else entirely, with no quotation table in it at all.")
        c.save()
        conn = memory_db()
        did = ingest_file(conn, p, "empty.pdf", M)
        d = conn.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
        self.assertEqual(d["status"], "needs_review")
        self.assertIn("no line items", d["message"])

    def test_injected_errors_are_flagged_and_approval_skips_them(self):
        conn = memory_db()
        import random
        from quotelens.datagen import make_rfq, write_doc
        import tempfile
        rng = random.Random(8)
        tmp = tempfile.mkdtemp()
        target = None
        for i in range(1, 30):
            for doc in make_rfq(rng, i, layout_weights=(1, 0, 0), anomaly_rate=4):
                if any(it["anomaly"] for it in doc.items) and target is None:
                    target = (doc, write_doc(doc, tmp, rng))
        doc, path = target
        did = ingest_file(conn, path, path.name, M)
        self.assertEqual(conn.execute("SELECT status FROM documents WHERE id=?", (did,)).fetchone()[0], "needs_review")
        before = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        added = approve_document(conn, did)
        flagged = sum(1 for r in conn.execute("SELECT flags FROM line_items WHERE document_id=?", (did,))
                      if any(f["severity"] in ("warn", "error") for f in json.loads(r[0])))
        total = conn.execute("SELECT COUNT(*) FROM line_items WHERE document_id=?", (did,)).fetchone()[0]
        self.assertGreaterEqual(flagged, 1)
        self.assertEqual(added, total - flagged)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0], before + added)
        self.assertEqual(approve_document(conn, did), 0)   # second approval adds nothing

    def test_override_revalidates(self):
        conn = memory_db()
        doc, path = clean_docs(1)[0]
        did = ingest_file(conn, path, path.name, M)
        item = conn.execute("SELECT id FROM line_items WHERE document_id=? LIMIT 1", (did,)).fetchone()[0]
        override_match(conn, item, "plywood_18")
        row = conn.execute("SELECT * FROM line_items WHERE id=?", (item,)).fetchone()
        self.assertEqual((row["catalog_id"], row["overridden"]), ("plywood_18", 1))
        with self.assertRaises(ValueError):
            override_match(conn, item, "nope")
        with self.assertRaises(LookupError):
            override_match(conn, 99999, "plywood_18")


class AppTests(unittest.TestCase):
    def setUp(self):
        self.app, self.cfg, self.dir = make_env()
        self.c = self.app.test_client()

    def upload(self, path, url="/api/documents", headers=None):
        return self.c.post(url, data={"file": (open(path, "rb"), os.path.basename(path))},
                           content_type="multipart/form-data", headers=headers or {})

    def test_upload_flow_and_comparison(self):
        docs = clean_docs(1, directory=self.dir)
        for _, p in docs:
            r = self.upload(p)
            self.assertEqual(r.status_code, 201)
        body = r.get_json()
        self.assertEqual(body["status"], "parsed")
        self.assertTrue(all(i["catalog_id"] for i in body["items"]))
        comp = self.c.get(f"/api/rfq/{docs[0][0].rfq}/comparison").get_json()
        self.assertEqual(len(comp["suppliers"]), len(docs))
        self.assertTrue(any(r["best"] is not None for r in comp["rows"]))
        for url in ("/", "/documents/1", f"/rfq/{docs[0][0].rfq}", "/ask"):
            self.assertEqual(self.c.get(url).status_code, 200, url)

    def test_upload_validation(self):
        self.assertEqual(self.c.post("/api/documents").status_code, 400)
        bad = self.c.post("/api/documents", data={"file": (io.BytesIO(b"x"), "evil.exe")}, content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 400)
        traversal = self.c.post("/api/documents", data={"file": (io.BytesIO(b"x"), "../../etc/passwd.pdf")}, content_type="multipart/form-data")
        self.assertEqual(traversal.status_code, 201)             # stored under a random name, document marked failed
        self.assertEqual(traversal.get_json()["status"], "failed")
        self.assertEqual(os.listdir(self.cfg.upload_dir)[0].count("/"), 0)

    def test_size_limit(self):
        app, cfg, _ = make_env(max_upload_mb=1)
        r = app.test_client().post("/api/documents", data={"file": (io.BytesIO(b"0" * 1_200_000), "big.pdf")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 413)

    def test_token(self):
        app, cfg, d = make_env(api_token="tok")
        _, p = clean_docs(1, directory=d)[0]
        c = app.test_client()
        self.assertEqual(c.post("/api/documents", data={"file": (open(p, "rb"), "a.pdf")}, content_type="multipart/form-data").status_code, 401)
        ok = c.post("/api/documents", data={"file": (open(p, "rb"), "a.pdf")}, content_type="multipart/form-data", headers={"Authorization": "Bearer tok"})
        self.assertEqual(ok.status_code, 201)

    def test_missing_things_404(self):
        for url in ("/documents/9", "/rfq/NOPE", "/api/documents/9", "/api/rfq/NOPE/comparison"):
            self.assertEqual(self.c.get(url).status_code, 404, url)

    def test_empty_app_pages(self):
        for url in ("/", "/upload", "/ask", "/healthz"):
            self.assertEqual(self.c.get(url).status_code, 200, url)

    def test_scan_upload_goes_to_review(self):
        docs = clean_docs(1, layout=(0, 0, 1), directory=self.dir)
        r = self.upload(docs[0][1])
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["status"], "needs_review")
        self.assertEqual(r.get_json()["kind"], "image")
        self.assertIn("OCR", r.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
