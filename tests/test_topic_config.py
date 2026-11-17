import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from crawlers.electric_bus.config import (
    ELECTRIC_BUS_QUERIES,
    facebook_history_sources,
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
        self.assertTrue(post_in_window("2021-01-01T00:00:00", now))
        self.assertFalse(post_in_window("2020-12-31T23:59:59", now))
        self.assertTrue(post_in_window("2026-05-31T23:59:59", now))
        self.assertTrue(post_in_window("2026-09-24T07:59:59", now))
        self.assertFalse(post_in_window("2026-09-24T08:00:00", now))
        self.assertEqual(youtube_window(now), ("2020-12-31T17:00:00Z", "2026-09-24T01:00:00Z"))

    def test_monthly_search_windows_cover_history_without_gaps(self):
        now = datetime(2026, 9, 24, 8, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        specs = list(youtube_search_specs(now))
        intervals = sorted({(spec["after"], spec["before"]) for spec in specs})
        self.assertEqual(len(intervals), 69)
        self.assertEqual(intervals[0][0], youtube_window(now)[0])
        self.assertEqual(intervals[-1][1], youtube_window(now)[1])
        self.assertTrue(all(left[1] == right[0] for left, right in zip(intervals, intervals[1:])))
        self.assertEqual(len(specs), len(intervals) * len(ELECTRIC_BUS_QUERIES))

    def test_facebook_history_searches_every_year_without_post_cap(self):
        now = datetime(2026, 9, 24, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        sources = list(facebook_history_sources(now))
        search = [source for source in sources if source["kind"] == "search"]
        self.assertEqual({source["year"] for source in search}, set(range(2021, 2027)))
        self.assertEqual(len(search), 6 * len(ELECTRIC_BUS_QUERIES))
        self.assertTrue(all(source["max_posts"] is None for source in sources))

    def test_gate_requires_only_electric_bus_term(self):
        self.assertEqual(gate_electric_bus("", "Đi xe buýt điện rất êm")[0], "accept")
        self.assertEqual(gate_electric_bus("", "Hà Nội ngập nặng")[0], "reject")




class FacebookGroupSourceTests(unittest.TestCase):
    def test_vinbus_groups_are_public_page_sources_with_owner_name(self):
        from crawlers.electric_bus.config import facebook_sources
        groups = [x for x in facebook_sources() if x["source_class"] == "electric_bus_group"]
        self.assertEqual(len(groups), 3)
        for source in groups:
            self.assertEqual(source["kind"], "public_page")
            self.assertIn("/groups/", source["url"])
            self.assertTrue(source["owner_name"])


if __name__ == "__main__":
    unittest.main()
