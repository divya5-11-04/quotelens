import argparse
import random
import sys
from pathlib import Path

from .config import Config
from .db import connect, init_db


def main(argv=None):
    ap = argparse.ArgumentParser(prog="quotelens")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the database and seed synthetic price history")
    g = sub.add_parser("generate", help="write synthetic quotations (PDF/JPG) and ground-truth JSON")
    g.add_argument("--out", default="samples")
    g.add_argument("--rfqs", type=int, default=3)
    g.add_argument("--seed", type=int, default=5)
    i = sub.add_parser("ingest", help="ingest quotation files or folders")
    i.add_argument("paths", nargs="+")
    e = sub.add_parser("evaluate", help="measure extraction and flagging on generated data")
    e.add_argument("--rfqs", type=int, default=60)
    e.add_argument("--scan-rfqs", type=int, default=12)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5000)
    args = ap.parse_args(argv)
    cfg = Config.from_env()

    if args.cmd == "init":
        from .datagen import seed_history
        conn = connect(cfg.db_path)
        init_db(conn)
        if conn.execute("SELECT COUNT(*) FROM price_history WHERE source='synthetic'").fetchone()[0] == 0:
            seed_history(conn, random.Random(1))
        print(f"database ready at {cfg.db_path}")
    elif args.cmd == "generate":
        from .datagen import make_rfq, write_doc
        rng = random.Random(args.seed)
        for n in range(1, args.rfqs + 1):
            for doc in make_rfq(rng, n):
                print(write_doc(doc, args.out, rng))
    elif args.cmd == "ingest":
        from .match import Matcher
        from .pipeline import ingest_file
        conn = connect(cfg.db_path)
        init_db(conn)
        matcher = Matcher()
        files = [f for p in args.paths for f in (sorted(Path(p).iterdir()) if Path(p).is_dir() else [Path(p)])
                 if f.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}]
        for f in files:
            doc_id = ingest_file(conn, f, f.name, matcher, stored_path=str(f))
            row = conn.execute("SELECT status, message FROM documents WHERE id=?", (doc_id,)).fetchone()
            print(f"{f.name}: document {doc_id}, {row['status']} {row['message']}")
    elif args.cmd == "evaluate":
        from . import evaluate
        res = evaluate.run(args.rfqs)
        res.update(evaluate.run(args.rfqs, hard=True, seed=7))
        if args.scan_rfqs:
            res.update(evaluate.run(args.scan_rfqs, layouts=(0, 0, 1), hard=False, seed=11))
        print(evaluate.to_markdown(res))
    elif args.cmd == "serve":
        from .app import create_app
        create_app(cfg).run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
