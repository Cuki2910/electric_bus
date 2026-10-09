import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from crawlers.electric_bus.config import (
    ELECTRIC_BUS_QUERIES,
    post_in_window,
    youtube_search_specs,
    youtube_window,
)
from crawlers.topics import gate_electric_bus


class TopicConfigTests(unittest.TestCase):
    def test_crawlers_share_electric_bus_queries(self):
        from crawlers.facebook import crawl_facebook as facebook
        from crawlers.youtube import crawl_youtube as youtube

        self.assertEqual(youtube.KEYWORDS, list(ELECTRIC_BUS_QUERIES))
        search = [s for s in facebook.SOURCES if s["kind"] != "public_page"]
        self.assertEqual(len(search), len(ELECTRIC_BUS_QUERIES) * 2)
        self.assertTrue(all(source["query"] in ELECTRIC_BUS_QUERIES for source in search))
        self.assertEqual({spec["keyword"] for spec in youtube_search_specs()}, set(ELECTRIC_BUS_QUERIES))

    def test_publication_window_boundaries(self):
        now = datetime(2026, 9, 24, 8, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        self.assertTrue(post_in_window("2026-06-01T00:00:00", now))
        self.assertFalse(post_in_window("2026-05-31T23:59:59", now))
        self.assertTrue(post_in_window("2026-09-24T07:59:59", now))
        self.assertFalse(post_in_window("2026-09-24T08:00:00", now))
        self.assertEqual(youtube_window(now), ("2026-05-31T17:00:00Z", "2026-09-24T01:00:00Z"))

    def test_gate_requires_only_electric_bus_term(self):
        self.assertEqual(gate_electric_bus("", "Đi xe buýt điện rất êm")[0], "accept")
        self.assertEqual(gate_electric_bus("", "Hà Nội ngập nặng")[0], "reject")


if __name__ == "__main__":
    unittest.main()
