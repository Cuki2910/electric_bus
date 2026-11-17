import importlib
import os
from crawlers.electric_bus.config import SOURCES_VERSION
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("YOUTUBE_API_KEY", "test")
yt = importlib.import_module("crawlers.youtube.crawl_youtube")


class YouTubeCrawlerTests(unittest.TestCase):
    def test_gate_change_requeues_previously_processed_rejects_without_search(self):
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        with tempfile.TemporaryDirectory() as directory, BaselineStore(base_dir=directory) as store:
            store.kv_set(f"youtube_seeded:{SOURCES_VERSION}", True)
            store.kv_set("youtube_processed_videos", ["eligible", "off_topic"])
            for identity, body in (("eligible", "VinBus Hà Nội"), ("off_topic", "Xe máy Hà Nội")):
                store.upsert_context({
                    "platform": "youtube", "context_id": identity, "verdict": "reject", "reason": "old_gate",
                    "post_context": body, "post_published_at": "2021-01-01T12:00:00+07:00", "state": "rejected",
                })
            youtube_worker.seed_queue(store)
            youtube_worker.seed_queue(store)
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM discovery_queue").fetchone()[0], 0)
            self.assertEqual(store.context("youtube", "eligible")["verdict"], "accept")
            self.assertEqual(store.context("youtube", "eligible")["state"], "accepted")
            self.assertEqual(store.context("youtube", "off_topic")["verdict"], "reject")
            self.assertEqual(store.kv_get("youtube_processed_videos"), ["eligible", "off_topic"])
            with patch.object(youtube_worker, "_crawl_video") as crawl:
                youtube_worker._drain_accepted_contexts(store, {"run_id": "run"}, lambda: False, "batch", {"eligible"})
            self.assertEqual(crawl.call_args.args[1]["video_id"], "eligible")
            self.assertEqual(store.context("youtube", "eligible")["state"], "comments_done")

    def test_history_is_seeded_once_despite_legacy_flags(self):
        import json
        from crawlers.baseline_store import BaselineStore
        from crawlers.electric_bus.config import ELECTRIC_BUS_QUERIES
        from crawlers.youtube import youtube_worker

        output_dir = tempfile.mkdtemp(prefix="yt_window_")
        store = BaselineStore(base_dir=output_dir)
        try:
            store.kv_set("youtube_seeded", True)
            store.kv_set("youtube_june_2026_backfill_seeded", True)
            youtube_worker.seed_queue(store)
            youtube_worker.seed_queue(store)
            rows = store.conn.execute("SELECT payload FROM discovery_queue WHERE platform='youtube'").fetchall()
            self.assertEqual(len(rows), len(youtube_worker._search_specs()))
            intervals = {(p["after"], p["before"]) for row in rows for p in [json.loads(row["payload"])]}
            self.assertEqual(min(after for after, _before in intervals), "2020-12-31T17:00:00Z")
            self.assertGreater(len(intervals), 60)
        finally:
            store.close()
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_search_keeps_metadata(self):
        payload = {"items": [{"id": {"videoId": "v1"}, "snippet": {"title": "Xe buýt điện Hà Nội", "description": "xe buýt chậm", "publishedAt": "2026-01-01T00:00:00Z"}}]}
        with patch.object(yt, "_api_get", return_value=payload), patch.object(yt.time, "sleep"):
            yt.reset_counters()
            video = yt.search_videos("q", "2025-01-01T00:00:00Z", "2027-01-01T00:00:00Z", max_pages=1)[0]
        self.assertEqual(video["description"], "xe buýt chậm")
        self.assertEqual(video["published_at"], "2026-01-01T00:00:00Z")

    def test_search_page_keeps_cursor(self):
        with patch.object(yt, "_api_get", return_value={"items": [], "nextPageToken": "second"}) as api:
            self.assertEqual(yt.search_video_page("q", "after", "before", page_token="first"), ([], "second"))
        self.assertEqual(api.call_args.args[1]["pageToken"], "first")

    def test_history_pagination_resumes_after_quota_without_duplicate_comments(self):
        import json
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        with tempfile.TemporaryDirectory() as output_dir:
            store = BaselineStore(base_dir=output_dir)
            try:
                run = store.get_or_create_run(24, 5000)
                store.kv_set(f"youtube_seeded:{SOURCES_VERSION}", True)
                store.enqueue("youtube", "search", {"keyword": "VinBus", "after": "2020-12-31T17:00:00Z", "before": "2021-02-01T00:00:00Z"})
                video = {"video_id": "v1", "title": "VinBus", "description": "", "published_at": "2021-01-01T00:00:00Z"}
                snippet = {"textDisplay": "Good", "publishedAt": "2021-01-02T00:00:00Z"}
                thread = {"snippet": {"topLevelComment": {"id": "top", "snippet": snippet}, "totalReplyCount": 1}}
                reply = {"id": "reply", "snippet": snippet}
                with patch.object(youtube_worker, "_revisit_current_year"), \
                     patch.object(yt, "fetch_video_descriptions", return_value={"v1": "VinBus"}), \
                     patch.object(yt, "fetch_comments", return_value=[thread]), \
                     patch.object(yt, "fetch_replies", return_value=[reply]), \
                     patch.object(yt, "search_video_page", side_effect=[([video], "second"), yt.YouTubeAPIError(403, "quotaExceeded", {})]):
                    result = youtube_worker.run_once(store, run, lambda: False, "first")
                self.assertEqual(result["status"], "quota_wait")
                self.assertEqual(store.comment_count(), 2)
                task = store.conn.execute("SELECT payload,status FROM discovery_queue WHERE status='pending'").fetchone()
                self.assertEqual(json.loads(task["payload"])["page_token"], "second")
                store.close()
                store = BaselineStore(base_dir=output_dir)
                with patch.object(youtube_worker, "_revisit_current_year"), \
                     patch.object(yt, "search_video_page", return_value=([video], None)) as search, \
                     patch.object(yt, "fetch_video_descriptions") as descriptions, \
                     patch.object(youtube_worker, "_crawl_video") as crawl:
                    result = youtube_worker.run_once(store, run, lambda: False, "second")
                self.assertEqual(result["status"], "queue_empty")
                self.assertEqual(search.call_args.kwargs["page_token"], "second")
                crawl.assert_not_called()
                descriptions.assert_not_called()
                self.assertEqual(store.comment_count(), 2)
                self.assertEqual(store.pending_count("youtube"), 0)
            finally:
                store.close()

    def test_worker_retries_description_failure_without_rejecting_video(self):
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        with tempfile.TemporaryDirectory() as output_dir, BaselineStore(base_dir=output_dir) as store:
            run = store.get_or_create_run(24, 5000)
            store.kv_set(f"youtube_seeded:{SOURCES_VERSION}", True)
            store.enqueue("youtube", "search", {"keyword": "VinBus", "after": "2021-01-01T00:00:00Z", "before": "2022-01-01T00:00:00Z"})
            video = {"video_id": "v1", "title": "Review", "description": "", "published_at": "2021-02-01T00:00:00Z"}
            with patch.object(youtube_worker, "_revisit_current_year"), \
                 patch.object(yt, "search_video_page", return_value=([video], None)), \
                 patch.object(yt, "fetch_video_descriptions", side_effect=OSError("offline")):
                result = youtube_worker.run_once(store, run, lambda: False, "batch")
            self.assertEqual(result["status"], "retry_wait")
            self.assertIsNone(store.context("youtube", "v1"))
            self.assertEqual(store.pending_count("youtube"), 1)

    def test_expired_page_token_restarts_window_without_losing_task(self):
        import json
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        with tempfile.TemporaryDirectory() as output_dir, BaselineStore(base_dir=output_dir) as store:
            run = store.get_or_create_run(24, 5000)
            store.kv_set(f"youtube_seeded:{SOURCES_VERSION}", True)
            store.enqueue("youtube", "search", {"keyword": "VinBus", "after": "after", "before": "before", "page_token": "expired"})
            with patch.object(youtube_worker, "_revisit_current_year"), \
                 patch.object(yt, "search_video_page", side_effect=yt.YouTubeAPIError(400, "invalidPageToken", {})):
                youtube_worker.run_once(store, run, lambda: False, "batch")
            task = store.conn.execute("SELECT payload,status FROM discovery_queue").fetchone()
            self.assertEqual(task["status"], "pending")
            payload = json.loads(task["payload"])
            self.assertNotIn("page_token", payload)
            self.assertEqual(payload["page_restart"], 1)

    def test_worker_record_keeps_post_metadata_and_comment_labels(self):
        from crawlers.youtube import youtube_worker

        video = {"video_id": "v1", "title": "Xe buýt điện Hà Nội", "description": "xe buýt chậm", "published_at": "2026-08-01T00:00:00Z"}
        snippet = {"textDisplay": "Xe buýt điện Hà Nội, xe buýt chậm", "publishedAt": "2026-01-02T00:00:00Z", "likeCount": 1}
        record = youtube_worker._record(video, snippet, "c1", "top_level", None, "c1", 0, "run", "batch")
        self.assertEqual(record["post_context"], "Xe buýt điện Hà Nội\nxe buýt chậm")
        self.assertEqual(record["posted_at"], "2026-01-02T00:00:00Z")
        self.assertIn("electric_bus", record["matched_groups"])

    def test_fetch_video_comment_counts_batches_statistics(self):
        payload = {"items": [
            {"id": "v1", "statistics": {"commentCount": "123"}},
            {"id": "v2", "statistics": {}},
        ]}
        with patch.object(yt, "_api_get", return_value=payload) as api:
            counts = yt.fetch_video_comment_counts(["v1", "v2"])
        self.assertEqual(counts, {"v1": 123, "v2": 0})
        self.assertEqual(api.call_args.args[0], "videos")
        with self.assertRaises(ValueError):
            yt.fetch_video_comment_counts([str(i) for i in range(51)])

    def test_fetch_video_descriptions_returns_full_text(self):
        payload = {"items": [
            {"id": "v1", "snippet": {"description": "mô tả đầy đủ không bị cắt " * 5}},
        ]}
        with patch.object(yt, "_api_get", return_value=payload) as api:
            descs = yt.fetch_video_descriptions(["v1"])
        self.assertEqual(descs["v1"], "mô tả đầy đủ không bị cắt " * 5)
        self.assertEqual(api.call_args.args[0], "videos")
        self.assertEqual(api.call_args.args[1]["part"], "snippet")
        with self.assertRaises(ValueError):
            yt.fetch_video_descriptions([str(i) for i in range(51)])

    def test_backfill_regates_with_full_description_and_resumes(self):
        import sqlite3
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import backfill_descriptions as bf

        legacy_dir = tempfile.mkdtemp(prefix="yt_legacy_")
        output_dir = tempfile.mkdtemp(prefix="yt_backfill_")
        try:
            legacy_db = os.path.join(legacy_dir, "crawl.db")
            conn = sqlite3.connect(legacy_db)
            conn.execute(
                "CREATE TABLE contexts (platform TEXT, context_id TEXT, post_title TEXT, "
                "post_published_at_raw TEXT, post_published_at TEXT)"
            )
            # v1: title thiếu nhóm B nhưng description đầy đủ (bị search.list cắt mất phần đó) có nhóm B -> đổi accept.
            # v2: ngay cả description đầy đủ vẫn thiếu nhóm C -> vẫn reject.
            conn.execute("INSERT INTO contexts VALUES ('youtube','v1','Xe buýt điện','t','2026-08-01T00:00:00Z')")
            conn.execute("INSERT INTO contexts VALUES ('youtube','v2','Hà Nội xe buýt','t','2026-08-01T00:00:00Z')")
            conn.commit()
            conn.close()

            full_desc = {"v1": "giao thông xe buýt bị chậm", "v2": "giao thông bình thường"}
            with patch.object(bf.yt, "fetch_video_descriptions", return_value=full_desc), \
                 patch.object(bf, "_crawl_video", return_value=3) as crawl_mock:
                bf.run_backfill(legacy_db, batch_size=50, base_dir=output_dir)

            store = BaselineStore(base_dir=output_dir)
            v1 = store.conn.execute("SELECT * FROM contexts WHERE context_id='v1'").fetchone()
            v2 = store.conn.execute("SELECT * FROM contexts WHERE context_id='v2'").fetchone()
            self.assertEqual(v1["verdict"], "accept")
            self.assertEqual(v1["state"], "comments_done")
            self.assertIn("giao thông xe buýt", v1["post_context"])
            self.assertEqual(v2["verdict"], "reject")
            self.assertEqual(crawl_mock.call_count, 1)  # chỉ video accept mới crawl comment.
            done_ids = set(store.kv_get("youtube_backfill_done_ids", []))
            self.assertEqual(done_ids, {"v1", "v2"})
            store.close()

            # Chạy lại: không còn video pending -> không gọi lại API.
            with patch.object(bf.yt, "fetch_video_descriptions") as api_mock, \
                 patch.object(bf, "_crawl_video"):
                bf.run_backfill(legacy_db, batch_size=50, base_dir=output_dir)
            api_mock.assert_not_called()
        finally:
            shutil.rmtree(legacy_dir, ignore_errors=True)
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_worker_skips_old_refresh_tasks_and_continues_search(self):
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        with tempfile.TemporaryDirectory() as directory, BaselineStore(base_dir=directory) as store:
            run = store.get_or_create_run(24, 5000)
            store.kv_set(f"youtube_seeded:{SOURCES_VERSION}", True)
            store.upsert_context({"platform": "youtube", "context_id": "old", "verdict": "accept",
                                  "state": "comments_done", "comment_retry_count": 2})
            store.enqueue("youtube", "refresh_comments", {"video_id": "old", "deficit": 1}, priority=-1)
            store.enqueue("youtube", "search", {"keyword": "VinBus", "after": "after", "before": "before"})
            with patch.object(youtube_worker, "_revisit_current_year"), \
                 patch.object(yt, "fetch_video_comment_counts") as counts, \
                 patch.object(yt, "search_video_page", return_value=([], None)) as search, \
                 patch.object(youtube_worker, "_crawl_video") as capture:
                result = youtube_worker.run_once(store, run, lambda: False, "batch")
            self.assertEqual(result["status"], "queue_empty")
            counts.assert_not_called()
            capture.assert_not_called()
            search.assert_called_once_with("VinBus", "after", "before", page_token=None)
            self.assertEqual(store.context("youtube", "old")["comment_retry_count"], 2)
            self.assertEqual(store.conn.execute(
                "SELECT status FROM discovery_queue WHERE source='refresh_comments'"
            ).fetchone()[0], "skipped")

    def test_worker_skips_stale_running_refresh_task(self):
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        with tempfile.TemporaryDirectory() as directory, BaselineStore(base_dir=directory) as store:
            run = store.get_or_create_run(24, 5000)
            store.kv_set(f"youtube_seeded:{SOURCES_VERSION}", True)
            store.enqueue("youtube", "refresh_comments", {"video_id": "old", "deficit": 1})
            task = store.claim("youtube")
            store.conn.execute("UPDATE discovery_queue SET lease_at=0 WHERE id=?", (task["id"],))
            with patch.object(youtube_worker, "_revisit_current_year"), \
                 patch.object(youtube_worker, "_crawl_video") as capture:
                result = youtube_worker.run_once(store, run, lambda: False, "batch")
            self.assertEqual(result["status"], "queue_empty")
            capture.assert_not_called()
            self.assertEqual(store.conn.execute("SELECT status FROM discovery_queue").fetchone()[0], "skipped")

    def test_api_request_has_a_network_timeout(self):
        with patch.object(yt.urllib.request, "urlopen", side_effect=TimeoutError), \
             patch.object(yt.time, "sleep"):
            with self.assertRaises(TimeoutError):
                yt._api_get("search", {})
            self.assertEqual(yt.urllib.request.urlopen.call_args.kwargs["timeout"], 30)

    def test_api_get_reclassifies_daily_search_quota_as_quota_exceeded(self):
        import io
        import json as json_module
        from urllib.error import HTTPError

        body = json_module.dumps({"error": {"errors": [{
            "reason": "rateLimitExceeded",
            "message": "Quota exceeded for quota metric 'Search Queries' and limit "
                       "'Search Queries per day' of service 'youtube.googleapis.com'.",
        }]}}).encode()
        http_error = HTTPError("url", 429, "Too Many Requests", {}, io.BytesIO(body))
        with patch.object(yt, "_API_KEYS", ["key1"]), \
             patch.object(yt.urllib.request, "urlopen", side_effect=http_error), \
             patch.object(yt.time, "sleep"):
            with self.assertRaises(yt.YouTubeAPIError) as raised:
                yt._api_get("search", {})
        self.assertEqual(raised.exception.reason, "quotaExceeded")

    def test_api_get_rotates_to_next_key_when_daily_quota_is_exhausted(self):
        import io
        import json as json_module
        from urllib.error import HTTPError

        body = json_module.dumps({"error": {"errors": [{
            "reason": "rateLimitExceeded",
            "message": "Quota exceeded for Search Queries per day",
        }]}}).encode()
        http_error = HTTPError("url", 429, "Too Many Requests", {}, io.BytesIO(body))
        success = MagicMock()
        success.__enter__.return_value.read.return_value = b'{"items": []}'
        seen_urls = []

        def urlopen(req, timeout):
            self.assertEqual(timeout, 30)
            seen_urls.append(req.full_url)
            if len(seen_urls) == 1:
                raise http_error
            return success

        with patch.object(yt, "_API_KEYS", ["key1", "key2"]), \
             patch.object(yt, "_key_index", 0), \
             patch.object(yt.urllib.request, "urlopen", side_effect=urlopen), \
             patch.object(yt.time, "sleep"):
            self.assertEqual(yt._api_get("search", {}), {"items": []})
        self.assertIn("key=key1", seen_urls[0])
        self.assertIn("key=key2", seen_urls[1])

    def test_api_get_keeps_burst_rate_limit_as_is(self):
        import io
        import json as json_module
        from urllib.error import HTTPError

        body = json_module.dumps({"error": {"errors": [{
            "reason": "rateLimitExceeded",
            "message": "User Rate Limit Exceeded",
        }]}}).encode()
        http_error = HTTPError("url", 403, "Forbidden", {}, io.BytesIO(body))
        with patch.object(yt.urllib.request, "urlopen", side_effect=http_error), \
             patch.object(yt.time, "sleep"):
            with self.assertRaises(yt.YouTubeAPIError) as raised:
                yt._api_get("commentThreads", {})
        self.assertEqual(raised.exception.reason, "rateLimitExceeded")

    def test_search_propagates_rate_limit(self):
        error = yt.YouTubeAPIError(403, "rateLimitExceeded", {})
        with patch.object(yt, "_api_get", side_effect=error):
            with self.assertRaises(yt.YouTubeAPIError) as raised:
                yt.search_videos("q", "2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z", max_pages=1)
        self.assertEqual(raised.exception.reason, "rateLimitExceeded")

    def test_worker_defers_rate_limited_task(self):
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        output_dir = tempfile.mkdtemp(prefix="yt_rate_limit_")
        store = BaselineStore(base_dir=output_dir)
        try:
            run = store.get_or_create_run(24, 5000)
            store.kv_set(f"youtube_seeded:{SOURCES_VERSION}", True)
            store.enqueue("youtube", "search", {
                "keyword": "q", "after": "2026-01-01T00:00:00Z", "before": "2027-01-01T00:00:00Z",
            })
            error = yt.YouTubeAPIError(403, "rateLimitExceeded", {})
            with patch.object(yt, "search_video_page", side_effect=error):
                result = youtube_worker.run_once(store, run, lambda: False, "batch")

            task = store.conn.execute(
                "SELECT status, next_retry_at FROM discovery_queue WHERE platform='youtube'"
            ).fetchone()
            self.assertEqual(result["status"], "rate_limit_wait")
            self.assertEqual(task["status"], "pending")
            self.assertGreater(task["next_retry_at"], time.time() + 850)
        finally:
            store.close()
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_rate_limit_backoff_escalates_and_resets(self):
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        output_dir = tempfile.mkdtemp(prefix="yt_backoff_")
        store = BaselineStore(base_dir=output_dir)
        try:
            run = store.get_or_create_run(24, 5000)
            store.kv_set(f"youtube_seeded:{SOURCES_VERSION}", True)
            error = yt.YouTubeAPIError(403, "rateLimitExceeded", {})
            delays = []
            for index in range(3):
                store.enqueue("youtube", "search", {
                    "keyword": f"q{index}", "after": "2026-01-01T00:00:00Z", "before": "2027-01-01T00:00:00Z",
                })
                with patch.object(yt, "search_video_page", side_effect=error):
                    delays.append(youtube_worker.run_once(store, run, lambda: False, "batch")["retry_seconds"])
            self.assertEqual(delays, [900, 1800, 3600])

            pending = store.conn.execute(
                "SELECT id FROM discovery_queue WHERE status='pending' ORDER BY id LIMIT 1"
            ).fetchone()
            store.conn.execute("UPDATE discovery_queue SET next_retry_at=0 WHERE id=?", (pending["id"],))
            store.conn.commit()
            with patch.object(yt, "search_video_page", return_value=([], None)):
                youtube_worker.run_once(store, run, lambda: False, "batch")
            self.assertEqual(store.kv_get("youtube_rate_limit_failures"), 0)
        finally:
            store.close()
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_accepted_backlog_finishes_without_search(self):
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        output_dir = tempfile.mkdtemp(prefix="yt_backlog_")
        store = BaselineStore(base_dir=output_dir)
        try:
            run = store.get_or_create_run(24, 5000)
            store.upsert_context({
                "platform": "youtube", "context_id": "v1", "source_url": "u",
                "post_title": "Xe buýt điện Hà Nội", "post_context": "Xe buýt điện Hà Nội\nxe buýt chậm",
                "post_published_at_raw": "t", "post_published_at": "t", "verdict": "accept",
                "reason": "electric_bus", "metadata_resolved": 1, "matched_groups": "",
                "co_thanh_pho_khac": 0, "discovery_source": "search", "discovery_query": "q",
                "state": "accepted", "crawled_at": "now",
            })
            with patch.object(youtube_worker, "_crawl_video", return_value=3):
                result = youtube_worker._drain_accepted_contexts(store, run, lambda: False, "batch", set())
            self.assertIsNone(result)
            self.assertEqual(store.context_state("youtube", "v1"), "comments_done")
        finally:
            store.close()
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_accepted_backlog_preserves_retryable_rate_limit(self):
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        output_dir = tempfile.mkdtemp(prefix="yt_backlog_limit_")
        store = BaselineStore(base_dir=output_dir)
        try:
            run = store.get_or_create_run(24, 5000)
            store.upsert_context({
                "platform": "youtube", "context_id": "v1", "source_url": "u",
                "post_title": "t", "post_context": "t\nd", "post_published_at_raw": "t",
                "post_published_at": "t", "verdict": "accept", "reason": "electric_bus",
                "metadata_resolved": 1, "matched_groups": "", "co_thanh_pho_khac": 0,
                "discovery_source": "search", "discovery_query": "q", "state": "accepted",
                "crawled_at": "now",
            })
            error = yt.YouTubeAPIError(429, "rateLimitExceeded", {})
            with patch.object(youtube_worker, "_crawl_video", side_effect=error):
                result = youtube_worker._drain_accepted_contexts(store, run, lambda: False, "batch", set())
            self.assertEqual(result["status"], "rate_limit_wait")
            self.assertEqual(store.context_state("youtube", "v1"), "accepted")
        finally:
            store.close()
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_accepted_backlog_records_comments_disabled(self):
        from crawlers.baseline_store import BaselineStore
        from crawlers.youtube import youtube_worker

        output_dir = tempfile.mkdtemp(prefix="yt_backlog_disabled_")
        store = BaselineStore(base_dir=output_dir)
        try:
            run = store.get_or_create_run(24, 5000)
            store.upsert_context({
                "platform": "youtube", "context_id": "v1", "source_url": "u",
                "post_title": "t", "post_context": "t\nd", "post_published_at_raw": "t",
                "post_published_at": "t", "verdict": "accept", "reason": "electric_bus",
                "metadata_resolved": 1, "matched_groups": "", "co_thanh_pho_khac": 0,
                "discovery_source": "search", "discovery_query": "q", "state": "accepted",
                "crawled_at": "now",
            })
            error = yt.YouTubeAPIError(403, "commentsDisabled", {})
            with patch.object(youtube_worker, "_crawl_video", side_effect=error):
                result = youtube_worker._drain_accepted_contexts(store, run, lambda: False, "batch", set())
            self.assertIsNone(result)
            self.assertEqual(store.context_state("youtube", "v1"), "comments_disabled")
        finally:
            store.close()
            shutil.rmtree(output_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

class RevisitQuotaTests(unittest.TestCase):
    def test_revisit_uses_few_keywords_and_six_hour_buckets(self):
        from datetime import datetime, timezone
        from crawlers.youtube import youtube_worker

        def keys_at(hour):
            store = MagicMock()
            with patch.object(youtube_worker, "datetime") as dt:
                dt.now.return_value = datetime(2026, 10, 10, hour, 30, tzinfo=timezone.utc)
                youtube_worker._revisit_current_year(store)
            return [call.kwargs["dedup_key"] for call in store.enqueue.call_args_list]

        a, b, c = keys_at(0), keys_at(5), keys_at(6)
        self.assertEqual(a, b)                      # same 6h bucket: deduped by the store
        self.assertTrue(set(a).isdisjoint(c))       # next bucket: new tasks
        self.assertEqual(len(a), len(youtube_worker.REVISIT_KEYWORDS))
        self.assertLess(len(a) * 100, 2700)         # was 27 searches (2700 units) every hour
