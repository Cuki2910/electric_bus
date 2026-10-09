import unittest

from crawlers.electric_bus.queries import KEYWORDS as ELECTRIC_BUS_KEYWORDS
from crawlers.topics import (
    gate_electric_bus,
    gate_electric_bus_vietnam,
)


class ElectricBusGateTests(unittest.TestCase):
    def test_accepts_on_brand_term(self):
        verdict, rule = gate_electric_bus("Trải nghiệm đi VinBus hôm nay")
        self.assertEqual(verdict, "accept")
        self.assertEqual(rule, "electric_bus")

    def test_rejects_unrelated_text(self):
        verdict, rule = gate_electric_bus("Hôm nay trời đẹp đi chơi công viên")
        self.assertEqual(verdict, "reject")
        self.assertIn("missing_group", rule)

    def test_does_not_require_location_terms(self):
        # Single-group gate: an electric-bus term alone is enough.
        verdict, _rule = gate_electric_bus("xe buýt điện rất êm")
        self.assertEqual(verdict, "accept")


class ElectricBusVietnamGateTests(unittest.TestCase):
    def test_accepts_vietnamese_text(self):
        self.assertEqual(gate_electric_bus_vietnam("Xe buýt điện rất êm")[0], "accept")

    def test_accepts_english_with_vn_brand(self):
        self.assertEqual(gate_electric_bus_vietnam("VinBus electric bus review")[0], "accept")

    def test_rejects_foreign_electric_bus(self):
        self.assertEqual(
            gate_electric_bus_vietnam("Electric bus launch in London", "BYD electric bus fleet"),
            ("reject", "not_vietnam"),
        )

    def test_accepts_vietnamese_without_diacritics(self):
        self.assertEqual(gate_electric_bus_vietnam("di xe buyt dien rat em, khong on")[0], "accept")

    def test_rejects_foreign_with_stray_words(self):
        self.assertEqual(
            gate_electric_bus_vietnam("Electric bus tuned for the cuando route")[0], "reject")


class ElectricBusQueryListTests(unittest.TestCase):
    def test_no_duplicates(self):
        self.assertEqual(len(ELECTRIC_BUS_KEYWORDS), len(set(k.lower() for k in ELECTRIC_BUS_KEYWORDS)))

    def test_query_list_is_large_enough(self):
        # Not every expanded query is required to literally contain a gate
        # term (that's the point -- they widen recall).
        self.assertGreater(len(ELECTRIC_BUS_KEYWORDS), 30)

    def test_includes_both_hanoi_and_hcmc_city_queries(self):
        # Vietnam's two biggest electric-bus deployments -- added alongside
        # gate_electric_bus_vietnam() to counter generic English terms
        # ("electric bus"/"e-bus") pulling in foreign results.
        blob = " ".join(ELECTRIC_BUS_KEYWORDS).lower()
        self.assertIn("hà nội", blob)
        self.assertTrue("hồ chí minh" in blob or "tphcm" in blob or "sài gòn" in blob)


if __name__ == "__main__":
    unittest.main()
