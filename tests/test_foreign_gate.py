import unittest

from crawlers.topics import (
    gate_electric_bus_vietnam,
    gate_with_foreign_check,
    is_on_topic_news,
    is_on_topic_threads,
    is_on_topic_tiktok,
)
from crawlers.threads import discover_threads
from crawlers.tiktok import discover_tiktok


class ForeignCheckTests(unittest.TestCase):
    def test_foreign_launch_is_uncertain(self):
        verdict, rule = gate_with_foreign_check("Bus điện VinFast đến Mỹ tại triển lãm", "")
        self.assertEqual((verdict, rule), ("uncertain", "foreign_place:đến mỹ"))

    def test_foreign_continent_is_uncertain(self):
        self.assertEqual(gate_with_foreign_check("VinFast ra mắt bus điện tại châu Âu")[0], "uncertain")

    def test_domestic_post_still_accepted(self):
        self.assertEqual(gate_with_foreign_check("Xe buýt điện tuyến 34 Bến xe Mỹ Đình", "")[0], "accept")

    def test_southeast_asia_is_not_foreign(self):
        title = "Việt Nam là nước đầu tiên ở Đông Nam Á có xe buýt điện thuần điện"
        self.assertEqual(gate_with_foreign_check(title)[0], "accept")

    def test_vietnam_gate_failure_stays_a_hard_reject(self):
        verdict, rule = gate_with_foreign_check("Electric bus launched in Germany last week", "")
        self.assertEqual((verdict, rule), ("reject", "not_vietnam"))

    def test_missing_topic_term_stays_a_hard_reject(self):
        self.assertEqual(gate_with_foreign_check("Hôm nay đi chơi ở Hà Nội")[0], "reject")

    def test_shared_vietnam_gate_is_unchanged(self):
        # The Facebook/YouTube pipeline calls this directly and only knows accept/reject.
        self.assertEqual(gate_electric_bus_vietnam("Bus điện VinFast đến Mỹ")[0], "accept")

    def test_wrappers_use_foreign_check(self):
        text = "Bus điện VinFast đến Mỹ"
        self.assertEqual(is_on_topic_news(text)[0], "uncertain")
        self.assertEqual(is_on_topic_threads(text, [])[0], "uncertain")
        self.assertEqual(is_on_topic_tiktok(text, [])[0], "uncertain")


class KeepPrefillTests(unittest.TestCase):
    def test_uncertain_is_left_blank_for_review(self):
        for module in (discover_threads, discover_tiktok):
            self.assertEqual(module.keep_prefill("uncertain"), "")
            self.assertEqual(module.keep_prefill("accept"), "1")
            self.assertEqual(module.keep_prefill("reject"), "0")


if __name__ == "__main__":
    unittest.main()
