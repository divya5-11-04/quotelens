import os
import random
import shutil
import tempfile

from quotelens.app import create_app
from quotelens.config import Config
from quotelens.datagen import make_rfq, seed_history, write_doc
from quotelens.db import connect, init_db


def make_env(**cfg_kw):
    d = tempfile.mkdtemp()
    cfg = Config(db_path=os.path.join(d, "t.db"), upload_dir=os.path.join(d, "up"), **cfg_kw)
    app = create_app(cfg)
    app.config["TESTING"] = True
    conn = connect(cfg.db_path)
    seed_history(conn, random.Random(1))
    conn.close()
    return app, cfg, d


def memory_db():
    conn = connect(":memory:")
    init_db(conn)
    seed_history(conn, random.Random(1))
    return conn


def clean_docs(n_rfq=1, layout=(1, 0, 0), seed=3, directory=None, anomaly_rate=0.0):
    rng = random.Random(seed)
    directory = directory or tempfile.mkdtemp()
    out = []
    for i in range(1, n_rfq + 1):
        for doc in make_rfq(rng, i, layout_weights=layout, anomaly_rate=anomaly_rate or 1e-9):
            out.append((doc, write_doc(doc, directory, rng)))
    return out
