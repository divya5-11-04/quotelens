import unittest

from quotelens.match import Matcher
from quotelens.parse import parse_text, resolve_numbers, resolve_unit
from quotelens.validate import check_item, robust_z

M = Matcher()
TEXT = """KAVERI TRADERS
Quotation No: Q-1043 Date: 12/03/2026
RFQ Ref: RFQ-2026-014
Sl Description Qty Unit Rate Amount
1 Kaveri OPC-53 Cement (50 kg bag) 100 bag 385.00 38,500.00
2 TMT Bars Fe500D 12mm 2,000 kg 60.50 121,000.00
Total 159,500.00
"""


class ParseTests(unittest.TestCase):
    def test_header_and_items(self):
        d = parse_text(TEXT)
        self.assertEqual((d.supplier, d.rfq_ref, d.quote_no, d.quote_date), ("Kaveri Traders", "RFQ-2026-014", "Q-1043", "2026-03-12"))
        self.assertEqual(len(d.items), 2)
        self.assertEqual((d.items[1].qty, d.items[1].rate, d.items[1].amount), (2000, 60.5, 121000))
        self.assertEqual(d.stated_total, 159500)

    def test_unreadable_item_line_is_reported_not_dropped_silently(self):
        d = parse_text(TEXT + "3 Mystery item with numbers 12 34\n")
        self.assertEqual(len(d.items), 2)
        self.assertEqual(len(d.unparsed), 1)

    def test_ocr_repair_uses_arithmetic(self):
        self.assertEqual(resolve_numbers("135", "383.4/", "51,768.45")[:3], (135.0, 383.47, 51768.45))
        self.assertTrue(resolve_numbers("135", "383.4/", "51,768.45")[3])

    def test_ocr_repair_refuses_to_guess(self):
        self.assertIsNone(resolve_numbers("135", "383.4/", "99,999.99"))

    def test_clean_numbers_are_not_touched(self):
        self.assertEqual(resolve_numbers("10", "5.00", "999.00"), (10.0, 5.0, 999.0, False))   # real arithmetic error stays visible

    def test_units(self):
        self.assertEqual((resolve_unit("Tonnes"), resolve_unit("tonne"), resolve_unit("xyzq")), ("tonne", "tonne", None))
        self.assertEqual(resolve_unit("tonnc"), "tonne")


class MatchTests(unittest.TestCase):
    def test_diameter_is_respected(self):
        self.assertEqual(M.match("TMT Bars Fe500D 12mm")[0], "tmt_12")
        self.assertEqual(M.match("TMT Bars Fe500D 16 mm")[0], "tmt_16")

    def test_variants_and_unknown(self):
        self.assertEqual(M.match("AAC Blocks 600 x 200 x 200 mm - Orion")[0], "aac_block")
        self.assertEqual(M.match("Cement OPC 53 Grade")[0], "cement_opc53")
        self.assertIsNone(M.match("office chair")[0])
        self.assertIsNone(M.match("")[0])


class ValidateTests(unittest.TestCase):
    HIST = [60 + (i % 5) * 0.4 for i in range(20)]

    def codes(self, *a, **k):
        return {f["code"] for f in check_item(*a, **k)[1]}

    def test_clean_item(self):
        self.assertEqual(self.codes(100, "kg", 60.5, 6050, "tmt_12", self.HIST), set())

    def test_arithmetic(self):
        self.assertIn("arithmetic_mismatch", self.codes(100, "kg", 60.5, 7000, "tmt_12", self.HIST))
        self.assertIn("ocr_uncertain", self.codes(100, "kg", 60.5, 7000, "tmt_12", self.HIST, ocr=True))

    def test_price_bands(self):
        self.assertIn("price_high", self.codes(100, "kg", 95, 9500, "tmt_12", self.HIST))
        self.assertIn("price_low", self.codes(100, "kg", 35, 3500, "tmt_12", self.HIST))
        self.assertNotIn("price_high", self.codes(100, "kg", 66, 6600, "tmt_12", self.HIST))   # +10 percent is normal noise

    def test_unit_handling(self):
        rn, flags = check_item(2, "tonne", 60500, 121000, "tmt_12", self.HIST)
        self.assertAlmostEqual(rn, 60.5)
        self.assertEqual({f["code"] for f in flags}, {"unit_converted"})
        self.assertIn("unit_suspect", self.codes(100, "kg", 60500, 6050000, "tmt_12", self.HIST))
        self.assertIn("unit_mismatch", self.codes(10, "bag", 60, 600, "tmt_12", self.HIST))

    def test_missing_history_and_unmatched(self):
        self.assertIn("no_history", self.codes(100, "kg", 60.5, 6050, "tmt_12", [60, 61]))
        self.assertIn("unmatched", self.codes(100, "kg", 60.5, 6050, None, []))

    def test_tight_history_does_not_explode_z(self):
        z, med = robust_z(60.1, [60.0] * 10)
        self.assertLess(abs(z), 1)


if __name__ == "__main__":
    unittest.main()
