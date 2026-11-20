import csv
import json
import os
import tempfile
import unittest

from crawlers.common import review_pipeline as rp
from crawlers.threads.crawl_threads import canonical_post_url

FIELDS = ["post_id", "url", "discovery_query", "source_label", "post_text", "hashtags", "posted_at",
          "reply_count", "gate_verdict", "gate_rule", "keep"]


def threads_row(url, verdict, keep, text="xe buýt điện", rule="electric_bus"):
    return {"post_id": url[-3:], "url": url, "discovery_query": "q", "source_label": "kw_q",
            "post_text": text, "hashtags": "", "posted_at": "2026-10-01", "reply_count": "0",
            "gate_verdict": verdict, "gate_rule": rule, "keep": keep}


def write_csv(path, rows, fields=FIELDS):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.discovery = os.path.join(self.dir, "discovery.csv")
        write_csv(self.discovery, [
            threads_row("https://www.threads.com/@a/post/AAA", "accept", "1"),
            threads_row("https://www.threads.com/@b/post/BBB", "accept", "0"),        # human override
            threads_row("https://www.threads.com/@c/post/CCC", "reject", "0"),
            threads_row("https://www.threads.com/@d/post/DDD", "uncertain", "", "VinFast đến Mỹ", "foreign_place:đến mỹ"),
            threads_row("https://www.threads.com/@e/post/EEE", "uncertain", "", "bus điện Singapore", "foreign_place:singapore"),
        ])
        self.reviewed = os.path.join(self.dir, "uncertain.csv")
        self.urls = os.path.join(self.dir, "urls.txt")

    def run_uncertain(self):
        return rp.write_uncertain_csv([self.discovery], self.reviewed, "threads", "url",
                                      ["post_text", "hashtags", "posted_at"])

    def run_urls(self, reviewed=True, **kwargs):
        return rp.run_extract_urls(
            "threads", "url", canonical_post_url,
            ["--discovery", self.discovery, *(["--reviewed", self.reviewed] if reviewed else []),
             "--output", self.urls, "--crawled-dir", os.path.join(self.dir, "crawled")] + kwargs.get("extra", []))

    def fill_review(self, decisions):
        rows = rp.read_rows(self.reviewed)
        for row in rows:
            row["keep"] = decisions.get(row["url"], "")
        write_csv(self.reviewed, rows, fields=list(rows[0].keys()))

    def read_urls(self):
        with open(self.urls, encoding="utf-8") as handle:
            return handle.read().split()

    def test_extract_uncertain_writes_only_uncertain_rows_with_blank_keep(self):
        self.assertEqual(self.run_uncertain(), 2)
        rows = rp.read_rows(self.reviewed)
        self.assertEqual({r["url"] for r in rows},
                         {"https://www.threads.com/@d/post/DDD", "https://www.threads.com/@e/post/EEE"})
        self.assertTrue(all(r["keep"] == "" and r["platform"] == "threads" for r in rows))
        self.assertEqual(rows[0]["gate_rule"], "foreign_place:đến mỹ")
        self.assertEqual(rows[0]["source_file"], "discovery.csv")

    def test_extract_urls_stops_when_review_is_blank(self):
        self.run_uncertain()
        with self.assertRaises(rp.UnreviewedError) as ctx:
            rp.collect_keep_urls([self.discovery], [self.reviewed], "url", canonical_post_url)
        self.assertEqual(len(ctx.exception.args[0]), 2)
        self.assertEqual(self.run_urls(), 2)
        self.assertFalse(os.path.exists(self.urls))

    def test_extract_urls_stops_when_reviewed_file_is_missing(self):
        self.assertEqual(self.run_urls(reviewed=False), 2)

    def test_extract_urls_stops_on_partially_reviewed_file(self):
        self.run_uncertain()
        self.fill_review({"https://www.threads.com/@d/post/DDD": "1"})
        self.assertEqual(self.run_urls(), 2)

    def test_extract_urls_collects_keep_one_from_both_files(self):
        self.run_uncertain()
        self.fill_review({"https://www.threads.com/@d/post/DDD": "0", "https://www.threads.com/@e/post/EEE": "1"})
        self.assertEqual(self.run_urls(), 0)
        self.assertEqual(self.read_urls(), ["https://www.threads.com/@a/post/AAA",
                                            "https://www.threads.com/@e/post/EEE"])

    def test_duplicates_across_files_are_removed(self):
        self.run_uncertain()
        self.fill_review({"https://www.threads.com/@d/post/DDD": "1", "https://www.threads.com/@e/post/EEE": "1"})
        second = os.path.join(self.dir, "discovery2.csv")
        write_csv(second, [threads_row("https://www.threads.com/@a/post/AAA?utm=x", "accept", "1")])
        urls, stats = rp.collect_keep_urls([self.discovery, second], [self.reviewed], "url", canonical_post_url)
        self.assertEqual(urls.count("https://www.threads.com/@a/post/AAA"), 1)
        self.assertEqual(stats["duplicates_removed"], 1)

    def test_already_crawled_urls_are_skipped_unless_asked(self):
        self.run_uncertain()
        self.fill_review({"https://www.threads.com/@d/post/DDD": "1", "https://www.threads.com/@e/post/EEE": "0"})
        crawled = os.path.join(self.dir, "crawled")
        os.makedirs(crawled)
        with open(os.path.join(crawled, "threads_electric_bus_20261002_1604.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"source_url": "https://www.threads.com/@a/post/AAA?x=1"}) + "\n")
        self.run_urls()
        self.assertEqual(self.read_urls(), ["https://www.threads.com/@d/post/DDD"])
        self.run_urls(extra=["--include-crawled"])
        self.assertEqual(self.read_urls(), ["https://www.threads.com/@a/post/AAA", "https://www.threads.com/@d/post/DDD"])

    def test_no_uncertain_rows_needs_no_review_file(self):
        clean = os.path.join(self.dir, "clean.csv")
        write_csv(clean, [threads_row("https://www.threads.com/@a/post/AAA", "accept", "1")])
        urls, _ = rp.collect_keep_urls([clean], [], "url", canonical_post_url)
        self.assertEqual(urls, ["https://www.threads.com/@a/post/AAA"])


if __name__ == "__main__":
    unittest.main()
