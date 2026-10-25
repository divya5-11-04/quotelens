import json
import unittest

from quotelens.config import Config
from quotelens.match import Matcher
from quotelens.pipeline import ingest_file
from quotelens.qa import Tools, answer
from tests.helpers import clean_docs, memory_db

M = Matcher()


def loaded():
    conn = memory_db()
    docs = clean_docs(2)
    for _, p in docs:
        ingest_file(conn, p, p.name, M)
    return conn, docs


class QaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn, cls.docs = loaded()

    def test_best_price_matches_database(self):
        mat = next(it for it in self.docs[0][0].items)["catalog_id"]
        res = Tools(self.conn, M).run("best_price", {"material": {"cement_opc53": "OPC 53 cement"}.get(mat, mat.replace("_", " "))})
        self.assertNotIn("error", res)
        rates = [q["rate"] for q in res["quotes"]]
        self.assertEqual(rates, sorted(rates))

    def test_local_router(self):
        for q, tool in (("who is cheapest for opc 53 cement", "best_price"), ("which quotes are flagged", "list_flagged"),
                        ("what are the payment terms", "supplier_terms"), ("river sand rate", "search_quotes")):
            r = answer(q, self.conn, M, Config())
            self.assertEqual((r["mode"], r["tools"]), ("local", [tool]), q)

    def test_unknown_material_asks_back(self):
        r = answer("cheapest unicorn", self.conn, M, Config())
        self.assertIn("which material", r["answer"])

    def test_bad_tool_calls(self):
        t = Tools(self.conn, M)
        self.assertIn("error", t.run("drop_table", {}))
        self.assertIn("error", t.run("best_price", {"wrong": 1}))

    def test_llm_loop_and_fallback(self):
        cfg = Config(anthropic_api_key="k")
        n = []

        def fake(cfg_, messages):
            n.append(1)
            if len(n) == 1:
                return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t", "name": "list_flagged", "input": {}}]}
            payload = json.loads(messages[-1]["content"][0]["content"])
            return {"stop_reason": "end_turn", "content": [{"type": "text", "text": f"{payload['count']} flagged"}]}

        r = answer("anything flagged?", self.conn, M, cfg, call=fake)
        self.assertEqual((r["mode"], r["tools"]), ("llm", ["list_flagged"]))

        def boom(cfg_, messages):
            raise ConnectionError("offline")
        self.assertEqual(answer("flagged?", self.conn, M, cfg, call=boom)["mode"], "local-fallback")


if __name__ == "__main__":
    unittest.main()
