import csv
import inspect
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from crawlers.facebook import crawl_facebook as fb
from crawlers.facebook import facebook_session as fbs
from crawlers.facebook import facebook_worker as worker


COMMENT_TIME = "Thứ bảy, 11 Tháng 7, 2026 lúc 17:43"
COMMENT_TIME_ISO = "2026-07-11T17:43:00+07:00"
SECOND_COMMENT_TIME = "Thứ Tư, 1 Tháng 7, 2026 lúc 12:57"

SOURCE = {
    "kind": "search",
    "label": "hanoi_flood_transport",
    "source_class": "topic_search",
    "query": "ngập Hà Nội giao thông",
    "max_posts": 8,
}


class FacebookCrawlerTests(unittest.TestCase):
    def test_historical_search_encodes_year_filter(self):
        import base64
        from urllib.parse import parse_qs, urlsplit

        params = parse_qs(urlsplit(fb._search_url("posts", "VinBus", 2021)).query)
        self.assertEqual(params["q"], ["VinBus"])
        filters = json.loads(base64.b64decode(params["filters"][0]))
        creation = json.loads(filters["rp_creation_time:0"])
        self.assertEqual(creation["name"], "creation_time")
        args = json.loads(creation["args"])
        self.assertEqual((args["start_day"], args["end_day"]), ("2021-1-1", "2021-12-31"))
        with self.assertRaises(ValueError):
            fb._search_url("posts", "VinBus", 2020)

    def test_search_accepts_article_only_results_before_scrolling(self):
        response = MagicMock()
        response.css.return_value = []
        with patch.object(fb, "_fetch", return_value=response) as fetch:
            self.assertEqual(fb.search_posts("VinBus", year=2022), [])
        self.assertEqual(fetch.call_args.kwargs["wait_selector"], 'div[role="feed"], div[role="article"]')

    def test_uncapped_discovery_collects_virtualized_posts_past_old_scroll_limit(self):
        page = MagicMock()
        batches = [[[f"/example/posts/{index}"]] for index in range(20)]
        page.evaluate.side_effect = batches + [batches[-1]] * 5
        collected = []
        checkpoints = []
        fb._scroll_collect_links(None, collected, on_posts=checkpoints.extend)(page)
        self.assertEqual(len(fb._post_urls_from_href_groups(collected, None)), 20)
        self.assertEqual(len(checkpoints), 20)
        self.assertGreater(page.mouse.wheel.call_count, fb.SCROLL_ROUNDS)

    def test_uncapped_comment_capture_continues_past_old_scroll_limit(self):
        page = MagicMock()
        counts = iter(range(110))

        def evaluate(script, *args):
            if fb._TARGET_RESOLVED_ATTR in script and "getAttribute" in script:
                return True
            if "Object.keys(window.__scrapedComments" in script:
                return next(counts, 110)
            return None

        page.evaluate.side_effect = evaluate
        fb._scroll_and_expand_page("https://www.facebook.com/example/posts/123", None)(page)
        self.assertGreater(page.mouse.wheel.call_count, fb.POST_SCROLL_ROUNDS * 6)

    def test_discovered_posts_survive_deadline_and_store_reopen(self):
        from crawlers.baseline_store import BaselineStore

        with tempfile.TemporaryDirectory() as output_dir:
            store = BaselineStore(base_dir=output_dir)
            stopped = False
            urls = ["https://www.facebook.com/example/posts/first", "https://www.facebook.com/example/posts/second"]

            def discover(source, deadline_check=None, on_posts=None):
                nonlocal stopped
                on_posts(urls)
                stopped = True
                return urls

            browser = MagicMock()
            browser.ensure_login.return_value = (fbs.AUTH_OK, {})
            try:
                store.enqueue("facebook", "discover", SOURCE)
                with patch.object(worker, "_session", return_value=browser), \
                     patch.object(worker, "_recycle_if_old", return_value=None), \
                     patch.object(worker, "seed_queue"), \
                     patch.object(fb, "discover_posts", side_effect=discover):
                    result = worker.run_once(store, {"run_id": "run"}, lambda: stopped, "first", allow_revisit=False)
                self.assertEqual(result["status"], "deadline")
                payload = json.loads(store.conn.execute("SELECT payload FROM discovery_queue").fetchone()["payload"])
                self.assertEqual(payload["post_urls"], urls)
                self.assertFalse(payload["discovery_complete"])
                store.close()
                store = BaselineStore(base_dir=output_dir)
                rejected = ({"post_context": "", "metadata_resolved": 1}, "reject", "missing_group:electric_bus", "", [])
                with patch.object(worker, "_session", return_value=browser), \
                     patch.object(worker, "_recycle_if_old", return_value=None), \
                     patch.object(worker, "seed_queue"), \
                     patch.object(fb, "discover_posts", return_value=urls) as search, \
                     patch.object(fb, "fetch_eligible_post", return_value=rejected) as fetch, \
                     patch.object(worker.time, "sleep"):
                    result = worker.run_once(store, {"run_id": "run"}, lambda: False, "second", allow_revisit=False)
                self.assertEqual(result["status"], "queue_empty")
                self.assertEqual(fetch.call_count, 2)
                search.assert_called_once()
                self.assertEqual(store.pending_count("facebook"), 0)
            finally:
                store.close()
                fb.use_browser_session(None)

    def test_publication_window_starts_in_2021(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime(2026, 10, 9, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        self.assertFalse(fb.post_in_window("2020-12-31T23:59:59+07:00", now))
        self.assertTrue(fb.post_in_window("2021-01-01T00:00:00+07:00", now))

    def test_canonical_identity_matches_permalink_variants(self):
        a = "https://www.facebook.com/hanoi_traffic/posts/pfbid02abc?comment_id=1"
        b = "https://m.facebook.com/hanoi_traffic/posts/pfbid02abc/"
        other = "https://www.facebook.com/hanoi_traffic/posts/pfbid02other"
        group_permalink = "https://www.facebook.com/groups/125948028050014/permalink/1999491540695644"
        self.assertEqual(fb.canonical_post_identity(a), fb.canonical_post_identity(b))
        self.assertNotEqual(fb.canonical_post_identity(a), fb.canonical_post_identity(other))
        self.assertEqual(fb.canonical_post_url(a), "https://www.facebook.com/hanoi_traffic/posts/pfbid02abc")
        self.assertTrue(fb.url_matches_target(b, a))
        self.assertFalse(fb.url_matches_target(other, a))
        self.assertEqual(fb.canonical_post_identity(group_permalink), "1999491540695644")

    def test_video_canonicalization_distinguishes_reel_and_watch_urls(self):
        reel = "https://m.facebook.com/reel/123456789/?comment_id=42"
        watch = "https://www.facebook.com/watch/?v=987654321&ref=sharing"
        video = "https://www.facebook.com/example/videos/555"
        self.assertEqual(fb.canonical_post_url(reel), "https://www.facebook.com/reel/123456789")
        self.assertEqual(fb.canonical_post_identity(reel), "123456789")
        self.assertEqual(fb.canonical_post_url(watch), "https://www.facebook.com/watch?v=987654321")
        self.assertEqual(fb.canonical_post_identity(watch), "987654321")
        self.assertEqual(fb.content_type_for_url(reel), "reel")
        self.assertEqual(fb.content_type_for_url(watch), "video")
        self.assertEqual(fb.content_type_for_url(video), "video")

    def test_reply_js_uses_permalink_ids_and_vietnamese_controls(self):
        for script in (fb._CAPTURE_COMMENTS_JS, fb._CAPTURE_REEL_COMMENTS_JS):
            self.assertIn("searchParams.get('reply_comment_id')", script)
            self.assertIn("searchParams.get('comment_id')", script)
            self.assertIn("const commentId = replyId || topId", script)
            self.assertIn("permalinkLink.getAttribute('aria-label')", script)
            self.assertIn(".filter(belongsToBlock)", script)
            self.assertIn("block.getAttribute('aria-label')", script)
        self.assertIn("B\\u00ecnh lu\\u1eadn d\\u01b0\\u1edbi t\\u00ean", fb._CAPTURE_COMMENTS_JS)
        post_expand_source = inspect.getsource(fb._scroll_and_expand_page)
        for script in (post_expand_source, fb._EXPAND_REEL_COMMENTS_JS):
            self.assertIn("câu trả lời", script)
            self.assertIn("button, span", script)
            self.assertIn("closest('[role=\"button\"], button')", script)

    def test_extract_creation_story_message_text_decodes_vietnamese(self):
        html = r'<script>{"creation_story":{"message":{"text":"Xe buýt điện Hà Nội, xe buýt chậm"}}}</script>'
        self.assertEqual(fb.extract_creation_story_message_text(html), "Xe buýt điện Hà Nội, xe buýt chậm")

    def test_reel_metadata_prefers_creation_story_body(self):
        captured = {"resolved": True, "body": "title fallback", "published_at_raw": "2026-07-01T10:30:00Z"}
        response = _FakeResponse(captured, r'{"creation_story":{"message":{"text":"Xe buýt điện Hà Nội, xe buýt chậm"}}}')
        metadata = fb._metadata_from_response(response, "https://www.facebook.com/reel/123")
        self.assertEqual(metadata["post_context"], "Xe buýt điện Hà Nội, xe buýt chậm")
        self.assertTrue(metadata["metadata_resolved"])
        self.assertEqual(metadata["post_published_at"], "2026-07-01T10:30:00+00:00")

    def test_post_metadata_keeps_title_and_captured_article_body(self):
        response = _FakeResponse({"resolved": True, "title": "Xe buýt điện Hà Nội", "body": "Xe buýt bị chậm", "published_at_raw": "2 giờ"})
        metadata = fb._metadata_from_response(response, "https://www.facebook.com/example/posts/123")
        self.assertEqual(metadata["post_title"], "Xe buýt điện Hà Nội")
        self.assertEqual(metadata["post_context"], "Xe buýt bị chậm")
        self.assertEqual(metadata["post_published_at_raw"], "2 giờ")
        self.assertEqual(metadata["post_published_at"], "")
        self.assertEqual(fb.gate_metadata(metadata), ("accept", "electric_bus"))

    def test_post_metadata_never_uses_background_creation_story(self):
        background = r'{"creation_story":{"message":{"text":"Unrelated background story"}}}'
        for resolved in (True, False):
            with self.subTest(resolved=resolved):
                captured = {"resolved": resolved, "body": "VinBus electric bus" if resolved else ""}
                metadata = fb._metadata_from_response(
                    _FakeResponse(captured, background), "https://www.facebook.com/example/posts/123"
                )
                self.assertEqual(metadata["post_context"], captured["body"])
                self.assertEqual(metadata["metadata_resolved"], resolved)
        metadata = fb._metadata_from_response(
            _FakeResponse({"resolved": True, "target_scoped": True, "body": "Scoped photo body"}, background),
            "https://www.facebook.com/photo?fbid=123",
        )
        self.assertEqual(metadata["post_context"], "Scoped photo body")

    def test_collected_links_survive_feed_virtualization(self):
        # Items scrolled out of the DOM still count: collected hrefs come first, deduped.
        collected = [
            ["/groups/g/posts/111/", "/user/1"],
            ["/photo/?fbid=222", "/user/2"],
            ["/user/3"],
            ["/groups/g/posts/111/?__cft__=x"],
        ]
        urls = fb._post_urls_from_items([], 10, collected)
        self.assertEqual([fb.canonical_post_identity(u) for u in urls], ["111", "222"])
        self.assertEqual(len(fb._post_urls_from_items([], 1, collected)), 1)

    def test_collected_links_precede_final_dom_items(self):
        class Link:
            attrib = {"href": "/groups/g/posts/999/"}
        class Item:
            def css(self, selector):
                return [Link()]
        urls = fb._post_urls_from_items([Item()], 10, [["/groups/g/posts/111/"]])
        self.assertEqual([fb.canonical_post_identity(u) for u in urls], ["111", "999"])

    def test_extract_owner_picks_actor_nearest_creation_time(self):
        html = (
            '{"__typename":"User","name":"Nguoi Binh Luan","url":"https:\\/\\/web.facebook.com\\/other"}' + "x" * 100000
            + '{"name":"VinBus","id":"1","url":"https:\\/\\/web.facebook.com\\/vinbus.official"}'
            + '"creation_time":1790762725'
        )
        owner = fb.extract_owner(html, "https://www.facebook.com/photo?fbid=1")
        self.assertEqual(owner, {"page_or_group_name": "VinBus", "owner_url": "https://www.facebook.com/vinbus.official", "owner_type": "page_or_profile"})

    def test_extract_owner_group_url_and_missing(self):
        self.assertEqual(fb.extract_owner("", "https://www.facebook.com/groups/123/posts/9")["owner_type"], "group")
        self.assertEqual(fb.extract_owner("<html></html>", "https://www.facebook.com/photo?fbid=1")["owner_url"], "")

    def test_absolute_post_time_is_extracted_from_tooltip_text(self):
        raw = "Đã chia sẻ: 25/07/2026 14:30"
        self.assertEqual(fb._parse_absolute_post_time(raw), "2026-07-25T14:30:00")
        response = _FakeResponse({
            "resolved": True, "title": "Xe buýt điện Hà Nội", "body": "xe buýt chậm",
            "published_at_candidates": ["2 giờ", raw],
        })
        metadata = fb._metadata_from_response(response, "https://www.facebook.com/example/posts/123")
        self.assertEqual(metadata["post_published_at_raw"], raw)
        self.assertEqual(metadata["post_published_at"], "2026-07-25T14:30:00")
        self.assertIn("[role=\"tooltip\"]", fb._CAPTURE_POST_METADATA_JS)
        self.assertIn("data-tooltip-content", fb._CAPTURE_POST_METADATA_JS)

    def test_photo_metadata_uses_creation_story_body(self):
        response = _FakeResponse(
            {
                "resolved": False,
                "body": "",
                "published_at_candidates": ["2026-09-18T05:13:34.000Z"],
                "published_at_raw": "2026-09-18T05:13:34.000Z",
            },
            r'{"creation_story":{"message":{"text":"Xe buýt điện Hà Nội, xe buýt chậm"}}}',
        )
        metadata = fb._metadata_from_response(response, "https://www.facebook.com/photo?fbid=123")
        self.assertTrue(metadata["metadata_resolved"])
        self.assertEqual(metadata["post_context"], "Xe buýt điện Hà Nội, xe buýt chậm")
        self.assertEqual(metadata["post_published_at"], "2026-09-18T05:13:34+00:00")
        self.assertIn('"creation_time":', fb._CAPTURE_POST_METADATA_JS)

    def test_reject_and_unresolved_metadata_never_fetch_comments(self):
        for metadata, reason in (
            ({"metadata_resolved": True, "post_title": "VinBus", "post_context": "xe buýt điện", "post_published_at": "2020-12-31T23:59:59+07:00"}, "out_of_window"),
            ({"metadata_resolved": True, "post_title": "Hà Nội ngập", "post_context": "người dân", "post_published_at": "2026-08-02T00:00:00+07:00"}, "missing_group:electric_bus"),
            ({"metadata_resolved": False, "post_context": "", "post_published_at": "2026-08-02T00:00:00+07:00"}, "metadata_unresolved"),
        ):
            with self.subTest(reason=reason), patch.object(fb, "fetch_post_metadata", return_value=metadata), patch.object(fb, "fetch_post") as fetch_comments:
                _, verdict, got_reason, _, comments = fb.fetch_eligible_post("https://facebook.com/posts/1")
                self.assertEqual((verdict, got_reason, comments), ("reject", reason, []))
                fetch_comments.assert_not_called()

    def test_electric_bus_source_uses_its_own_gate(self):
        source = {"source_class": "electric_bus_search"}
        metadata = {
            "metadata_resolved": True,
            "post_title": "Review VinBus",
            "post_context": "Trải nghiệm xe buýt điện",
            "post_published_at": "2026-06-01T00:00:00+07:00",
        }
        with patch.object(fb, "fetch_post_metadata", return_value=metadata), \
             patch.object(fb, "fetch_post", return_value=("body", [])):
            _, verdict, reason, _, _ = fb.fetch_eligible_post("https://facebook.com/posts/1", source=source)
        self.assertEqual((verdict, reason), ("accept", "electric_bus"))

    def test_js_has_no_broad_capture_fallback(self):
        self.assertNotIn("div[role=\"main\"]", fb._INIT_COMMENT_CAPTURE_JS)
        self.assertIn("identity(href) === targetId", fb._INIT_COMMENT_CAPTURE_JS)
        self.assertNotIn("topLevel.length ? [topLevel[0]]", fb._INIT_COMMENT_CAPTURE_JS)
        self.assertIn("mappedIdentity === targetId", fb._INIT_COMMENT_CAPTURE_JS)
        self.assertIn("isPhotoTarget || bodyMatches", fb._INIT_COMMENT_CAPTURE_JS)
        self.assertIn("commentContextIds.length === 1", fb._INIT_COMMENT_CAPTURE_JS)
        self.assertIn("chưa có bình luận nào", fb._INIT_COMMENT_CAPTURE_JS)
        self.assertIn("div[role=\"article\"]", fb._INIT_COMMENT_CAPTURE_JS)
        self.assertIn("scope.querySelectorAll", fb._CAPTURE_REEL_COMMENTS_JS)
        self.assertNotIn("document.querySelectorAll('div[role=\"article\"]')", fb._CAPTURE_REEL_COMMENTS_JS)

    def test_is_on_topic_requires_electric_bus_term(self):
        accepts = [
            "Xe buýt điện Hà Nội, xe buýt chậm",
            "Trải nghiệm đi VinBus hôm nay",
            "Người đi xe bus điện bị trễ",
        ]
        for text in accepts:
            ok, rule = fb.is_on_topic(text, SOURCE)
            self.assertTrue(ok, (text, rule))

        for text in ("Xe buýt Hà Nội chờ lâu", "Hà Nội ngập sâu", "miễn phí xe buýt"):
            ok, _ = fb.is_on_topic(text, SOURCE)
            self.assertFalse(ok, text)

    def test_is_on_topic_rejects_off_topic(self):
        rejects = [
            "tour du lịch đảo cuối tuần",
            "Ứng dụng định danh điện tử mới",
            "người lớn tuổi cần được hỗ trợ y tế",
            "sale vé ca nhạc giải trí",
        ]
        neutral = {"kind": "search", "label": "generic", "source_class": "generic", "query": "ứng dụng định danh", "max_posts": 3}
        for text in rejects:
            ok, _ = fb.is_on_topic(text, neutral)
            self.assertFalse(ok, text)

    def test_comment_tooltip_parser_is_strict_and_uses_vietnam_time(self):
        self.assertEqual(fb.parse_comment_tooltip(COMMENT_TIME), COMMENT_TIME_ISO)
        self.assertEqual(
            fb.parse_comment_tooltip("Thứ bảy, 11 Tháng 7, 2026 lúc 17:43"),
            COMMENT_TIME_ISO,
        )
        self.assertEqual(
            fb.parse_comment_tooltip("Saturday, July 11, 2026 at 5:43 PM"),
            COMMENT_TIME_ISO,
        )
        for raw in ("", "2 tuần trước", "Bình luận dưới tên A vào 2 tuần trước",
                    "Thứ bảy, 31 Tháng 2, 2026 lúc 17:43", f"{COMMENT_TIME} Đã chỉnh sửa"):
            with self.subTest(raw=raw):
                self.assertEqual(fb.parse_comment_tooltip(raw), "")

    def test_fetch_post_filters_invalid_timestamps_before_cap(self):
        raw_comments = [
            {"comment_text": "invalid", "posted_at_raw": "2 tuần trước"},
            {"comment_text": "valid one", "posted_at_raw": COMMENT_TIME},
            {"comment_text": "valid two", "posted_at_raw": SECOND_COMMENT_TIME},
        ]
        response = _FakeCommentResponse(raw_comments)
        metadata = {"post_context": "ctx"}
        with patch.object(fb, "_fetch", return_value=response):
            _, comments = fb.fetch_post(
                "https://www.facebook.com/watch?v=998112859703879",
                max_comments=1,
                metadata=metadata,
            )
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["comment_text"], "valid one")
        self.assertEqual(comments[0]["posted_at_raw"], COMMENT_TIME)
        self.assertEqual(comments[0]["posted_at"], COMMENT_TIME_ISO)
        self.assertEqual(metadata["comment_capture"]["valid_count"], 2)
        self.assertEqual(metadata["comment_capture"]["invalid_timestamp_count"], 1)

    def test_to_record_rejects_missing_or_relative_comment_time(self):
        for raw in ("", "2 tuần trước"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                fb.to_record("https://www.facebook.com/posts/1", "ctx", {
                    "comment_text": "comment", "posted_at_raw": raw,
                }, "batch")

    def test_to_record_keeps_legacy_fields_adds_provenance_and_full_text_ids(self):
        first = {"comment_text": "a" * 50 + " one", "posted_at_raw": COMMENT_TIME, "likes_count": 2}
        second = {"comment_text": "a" * 50 + " two", "posted_at_raw": COMMENT_TIME, "likes_count": 2}
        r1 = fb.to_record("https://www.facebook.com/hanoi_traffic/posts/pfbid02abc?x=1", "ctx", first, "batch", SOURCE, "electric_bus")
        r2 = fb.to_record("https://www.facebook.com/hanoi_traffic/posts/pfbid02abc", "ctx", second, "batch", SOURCE, "electric_bus")

        for key in ("id", "platform", "source_url", "post_context", "comment_text", "posted_at_raw", "posted_at", "likes_count", "crawled_at", "crawl_batch_id"):
            self.assertIn(key, r1)
        self.assertEqual(r1["posted_at_raw"], COMMENT_TIME)
        self.assertEqual(r1["posted_at"], COMMENT_TIME_ISO)
        self.assertEqual(r1["capture_scope"], "target_permalink_article")
        self.assertEqual(r1["content_type"], "post")
        self.assertEqual(r1["source_kind"], "search")
        self.assertEqual(r1["source_class"], "topic_search")
        self.assertEqual(r1["source_label"], "hanoi_flood_transport")
        self.assertEqual(r1["discovery_query"], SOURCE["query"])
        self.assertEqual(r1["topic_rule"], "electric_bus")
        self.assertNotEqual(r1["id"], r2["id"])

    def test_to_record_marks_reel_metadata(self):
        source = {"kind": "public_page", "label": "future_reel_page", "source_class": "official_page", "url": "https://www.facebook.com/example/reels/", "max_posts": 8}
        comment = {"comment_text": "Xe buyt rat tien", "posted_at_raw": COMMENT_TIME, "likes_count": 1}
        record = fb.to_record("https://www.facebook.com/reel/123456789?x=1", "ctx", comment, "batch", source, "electric_bus")
        self.assertEqual(record["content_type"], "reel")
        self.assertEqual(record["capture_scope"], "target_reel_comments")
        self.assertEqual(record["source_kind"], "public_page")
        self.assertEqual(record["source_url"], "https://www.facebook.com/reel/123456789")

    def test_to_record_marks_watch_video_metadata(self):
        source = {"kind": "video_search", "label": "hanoi_flood_video", "source_class": "topic_search", "query": "ngap Ha Noi giao thong", "max_posts": 8}
        comment = {"comment_text": "Xe buyt rat tien", "posted_at_raw": COMMENT_TIME, "likes_count": 1}
        record = fb.to_record("https://www.facebook.com/watch/?v=987654321", "ctx", comment, "batch", source, "electric_bus")
        self.assertEqual(record["content_type"], "video")
        self.assertEqual(record["capture_scope"], "target_video_comments")
        self.assertEqual(record["source_kind"], "video_search")
        self.assertEqual(record["source_url"], "https://www.facebook.com/watch?v=987654321")

    def test_to_record_same_text_different_posted_at_raw_ids_differ(self):
        same_text = {"comment_text": "same comment text", "posted_at_raw": COMMENT_TIME, "likes_count": 0}
        same_text_later = {"comment_text": "same comment text", "posted_at_raw": SECOND_COMMENT_TIME, "likes_count": 0}
        url = "https://www.facebook.com/hanoi_traffic/posts/pfbid02abc"
        r1 = fb.to_record(url, "ctx", same_text, "batch", SOURCE, "electric_bus")
        r2 = fb.to_record(url, "ctx", same_text_later, "batch", SOURCE, "electric_bus")
        self.assertNotEqual(r1["id"], r2["id"])

    def test_fetch_reuses_injected_browser_session(self):
        session = MagicMock()
        session.fetch.side_effect = [
            _FakeAuthResponse("ok"),
            _FakeAuthResponse("ok"),
        ]
        fb.use_browser_session(session)
        try:
            fb._fetch("https://www.facebook.com/a")
            fb._fetch("https://www.facebook.com/b")
        finally:
            fb.use_browser_session(None)
        self.assertEqual(session.fetch.call_count, 2)

    def test_stateless_navigation_failure_is_not_retried_inside_queue_attempt(self):
        with patch.object(fb, "_BROWSER_SESSION", None), \
             patch.object(fb, "_fb_cookies", return_value=[]), \
             patch.object(fb, "StealthyFetcher") as fetcher:
            fetcher.fetch.side_effect = TimeoutError("navigation timed out")
            with self.assertRaises(TimeoutError):
                fb._fetch("https://www.facebook.com/example/posts/123")
        fetcher.fetch.assert_called_once()
        self.assertEqual(fetcher.fetch.call_args.kwargs["timeout"], 30000)
        self.assertEqual(fetcher.fetch.call_args.kwargs["retries"], 1)

    def test_fetch_raises_precise_midcrawl_auth_loss(self):
        session = MagicMock()
        session.fetch.return_value = _FakeAuthResponse("login_form")
        fb.use_browser_session(session)
        try:
            with self.assertRaises(fbs.FacebookAuthRequiredError) as raised:
                fb._fetch("https://www.facebook.com/search/posts")
        finally:
            fb.use_browser_session(None)
        self.assertEqual(raised.exception.state, "login_form")

    def test_fetch_rejects_missing_action_sentinel(self):
        session = MagicMock()
        session.fetch.return_value = _FakeResponse({}, "")
        fb.use_browser_session(session)
        try:
            with self.assertRaises(fb.FacebookCaptureError):
                fb._fetch("https://www.facebook.com/posts/1")
        finally:
            fb.use_browser_session(None)

    def test_existing_legacy_output_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "facebook_comments.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["id", "posted_at_raw"])
                writer.writeheader()
                writer.writerow({"id": "legacy", "posted_at_raw": "2 tuần trước"})
            with self.assertRaisesRegex(ValueError, "incompatible schema"):
                fb._load_existing_ids(path)

    def test_direct_post_discovery_is_canonical(self):
        source = {"kind": "direct_post", "url": "https://m.facebook.com/example/posts/123/?comment_id=4"}
        self.assertEqual(fb.discover_posts(source), ["https://www.facebook.com/example/posts/123"])

    def test_photo_fbid_is_stable_identity(self):
        url = "https://www.facebook.com/photo/?fbid=123&set=pcb.456"
        self.assertEqual(fb.canonical_post_url(url), "https://www.facebook.com/photo?fbid=123")
        self.assertEqual(fb.canonical_post_identity(url), "123")

    def test_comment_cap_stops_before_expansion(self):
        page = MagicMock()
        page.evaluate.side_effect = [None, True, None, 10, None, None]
        action = fb._scroll_and_expand_page("https://www.facebook.com/example/posts/123", 45, 10)
        action(page)
        page.mouse.wheel.assert_not_called()

    def test_unresolved_target_stops_without_scrolling_unrelated_content(self):
        page = MagicMock()
        page.evaluate.return_value = False
        fb._scroll_and_expand_page("https://www.facebook.com/example/posts/123", None)(page)
        page.mouse.wheel.assert_not_called()
        self.assertEqual(sum(call.args[0] == fb._INIT_COMMENT_CAPTURE_JS
                             for call in page.evaluate.call_args_list), 4)

    def test_delayed_target_is_resolved_before_comment_capture(self):
        page = MagicMock()
        page.evaluate.side_effect = [None, False, None, True, None, 10, None, None]
        fb._scroll_and_expand_page("https://www.facebook.com/example/posts/123", None, 10)(page)
        page.mouse.wheel.assert_not_called()
        page.wait_for_timeout.assert_any_call(1000)
        self.assertIn(unittest.mock.call(fb._CAPTURE_COMMENTS_JS), page.evaluate.call_args_list)

    def test_session_profile_busy_is_classified(self):
        engine = MagicMock()
        engine.start.side_effect = RuntimeError("Failed to create a ProcessSingleton; profile is already in use")
        with patch.object(fbs.os, "makedirs"):
            browser = fbs.FacebookSession(session_factory=lambda **kwargs: engine)
            with self.assertRaises(fbs.FacebookProfileBusyError):
                browser.start()

    def test_session_fetch_waits_for_feed_before_action_and_closes_page(self):
        engine = MagicMock()
        page = engine.context.new_page.return_value
        page.url = "https://www.facebook.com/search/posts/"
        page.content.return_value = '<html data-test="captured"><body>Feed</body></html>'
        events = []
        page.locator.return_value.first.wait_for.side_effect = lambda **kwargs: events.append("feed")
        with tempfile.TemporaryDirectory() as directory:
            browser = fbs.FacebookSession(profile_dir=directory, session_factory=lambda **kwargs: engine).start()
            try:
                response = browser.fetch(page.url, wait_selector='div[role="feed"]',
                                         page_action=lambda current: events.append("action"))
                self.assertEqual(events, ["feed", "action"])
                page.goto.assert_called_once_with(page.url, wait_until="domcontentloaded", timeout=30000)
                page.close.assert_called_once()
                engine.fetch.assert_not_called()
                self.assertEqual(response.css("html")[0].attrib["data-test"], "captured")
            finally:
                browser.close()

    def test_session_fetch_preserves_auth_failure_when_feed_is_missing(self):
        engine = MagicMock()
        page = engine.context.new_page.return_value
        page.locator.return_value.first.wait_for.side_effect = TimeoutError("feed missing")
        with tempfile.TemporaryDirectory() as directory:
            browser = fbs.FacebookSession(profile_dir=directory, session_factory=lambda **kwargs: engine).start()
            try:
                with patch.object(fbs, "_auth_state", return_value=fbs.AUTH_CHALLENGE):
                    with self.assertRaises(fbs.FacebookAuthRequiredError):
                        browser.fetch("https://www.facebook.com/search/posts/", wait_selector='div[role="feed"]')
                page.close.assert_called_once()
            finally:
                browser.close()

    @unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 for Chromium DOM checks")
    def test_session_fetch_does_not_wait_for_stalled_image_load(self):
        from patchright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            engine = playwright.chromium.launch(headless=True)
            context = engine.new_context()
            context.route("**/*", lambda route: None if route.request.resource_type == "image" else
                          route.fulfill(content_type="text/html", body='<html><body><img src="/pending.png"><div role="feed">Ready</div></body></html>'))
            browser = fbs.FacebookSession()
            browser.session = MagicMock(context=context)
            try:
                response = browser.fetch("https://www.facebook.com/search/posts/", timeout=2000,
                                         wait_selector='div[role="feed"]')
                self.assertEqual(response.css('div[role="feed"]')[0].text, "Ready")
                self.assertEqual(context.pages, [])
            finally:
                engine.close()

    def test_session_loads_optional_env_cookies(self):
        engine = MagicMock()
        captured = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return engine

        profile_dir = tempfile.mkdtemp()
        try:
            with patch.dict(fbs.os.environ, {"FB_COOKIE_C_USER": "user", "FB_COOKIE_XS": "session"}, clear=False):
                browser = fbs.FacebookSession(profile_dir=profile_dir, session_factory=factory)
                browser.start()
            self.assertEqual(captured["cookies"][0]["name"], "c_user")
            self.assertEqual(captured["cookies"][1]["name"], "xs")
            self.assertEqual(captured["timeout"], 30000)
            self.assertEqual(captured["retries"], 1)
            browser.close()
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)

    def test_session_preserves_existing_profile_cookies(self):
        engine = MagicMock()
        captured = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return engine

        profile_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(profile_dir, "Default", "Network"))
        open(os.path.join(profile_dir, "Default", "Network", "Cookies"), "a").close()
        try:
            with patch.dict(fbs.os.environ, {"FB_COOKIE_C_USER": "user", "FB_COOKIE_XS": "session"}, clear=False):
                browser = fbs.FacebookSession(profile_dir=profile_dir, session_factory=factory)
                browser.start()
            self.assertNotIn("cookies", captured)
            browser.close()
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)

    def test_auth_challenge_keeps_browser_open(self):
        engine = MagicMock()
        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://www.facebook.com/checkpoint/"
        page.evaluate.return_value = fbs.AUTH_CHALLENGE
        engine.context.new_page.return_value = page
        browser = fbs.FacebookSession(session_factory=lambda **kwargs: engine)
        with patch.object(fbs, "load_credentials", return_value=("email", "password")):
            browser.start()
            state, detail = browser.ensure_login()
        self.assertEqual(state, fbs.AUTH_CHALLENGE)
        self.assertEqual(detail["challenge_shot"], fbs.CHALLENGE_SHOT)
        engine.close.assert_not_called()
        browser.close()
        engine.close.assert_called_once()

    def test_authenticated_profile_does_not_require_login_credentials(self):
        engine = MagicMock()
        page = MagicMock()
        page.is_closed.return_value = False
        page.url = "https://www.facebook.com/"
        page.evaluate.return_value = fbs.AUTH_OK
        engine.context.new_page.return_value = page
        browser = fbs.FacebookSession(session_factory=lambda **kwargs: engine)
        with patch.object(fbs, "load_credentials") as load_credentials:
            browser.start()
            state, detail = browser.ensure_login()
        self.assertEqual(state, fbs.AUTH_OK)
        self.assertIsNone(detail["challenge_shot"])
        load_credentials.assert_not_called()
        browser.close()

    def test_worker_auth_loss_requeues_task(self):
        store = MagicMock()
        task = {"id": 1, "payload": SOURCE}
        store.claim.side_effect = [task]
        store.kv_get.side_effect = lambda key, default=None: default
        browser = MagicMock()
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        with patch.object(worker, "_session", return_value=browser), \
             patch.object(worker.fb, "discover_posts", side_effect=fbs.FacebookAuthRequiredError("challenge")):
            result = worker.run_once(store, {"run_id": "run"}, lambda: False, "batch")
        self.assertEqual(result["status"], "auth_required")
        store.defer_task.assert_called_once_with(1, delay=0)

    def test_worker_target_unresolved_continues_to_next_post(self):
        store = MagicMock()
        task = {"id": 1, "payload": SOURCE}
        store.claim.side_effect = [task, None]
        # Đã "revisit" sẵn trong pass này -> None thứ 2 trả thẳng queue_empty, không gọi
        # thêm _revisit_sources() (thứ không phải trọng tâm test này).
        store.kv_get.side_effect = lambda key, default=None: True if key == "facebook_revisited_this_pass" else default
        browser = MagicMock()
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        good = ({"post_context": "", "post_published_at_raw": "", "post_published_at": "", "metadata_resolved": 1},
                "reject", "thieu_nhom:B_chinh_sach", "", [])
        with patch.object(worker, "_session", return_value=browser), \
             patch.object(worker.fb, "discover_posts", return_value=["https://facebook.com/posts/bad", "https://facebook.com/posts/good"]), \
             patch.object(worker.fb, "fetch_eligible_post", side_effect=[fb.FacebookTargetUnresolvedError("target_unresolved"), good]) as fetch:
            result = worker.run_once(store, {"run_id": "run"}, lambda: False, "batch")
        self.assertEqual(result["status"], "retry_wait")
        self.assertEqual(fetch.call_count, 2)
        store.retry_task.assert_called_once_with(1, delay=120)
        store.complete_task.assert_not_called()
        self.assertEqual(store.checkpoint_task.call_args.args[1]["post_urls"],
                         ["https://facebook.com/posts/bad"])

    def test_revisit_sources_reseeds_queue_after_hourly_stamp_changes(self):
        import shutil
        import tempfile
        from crawlers.baseline_store import BaselineStore

        output_dir = tempfile.mkdtemp(prefix="fb_revisit_")
        store = BaselineStore(base_dir=output_dir)
        try:
            worker.seed_queue(store)
            for row in store.conn.execute("SELECT id FROM discovery_queue WHERE platform='facebook'").fetchall():
                store.complete_task(row["id"])
            self.assertEqual(store.pending_count("facebook"), 0)
            worker._revisit_sources(store)
            self.assertGreater(store.pending_count("facebook"), 0)
            queued_before = store.pending_count("facebook")
            worker._revisit_sources(store)  # cùng giờ -> dedup, không tăng thêm.
            self.assertEqual(store.pending_count("facebook"), queued_before)
        finally:
            store.close()
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_history_seeding_does_not_requeue_previously_rejected_posts(self):
        from crawlers.baseline_store import BaselineStore

        output_dir = tempfile.mkdtemp(prefix="fb_backfill_")
        store = BaselineStore(base_dir=output_dir)
        try:
            store.kv_set("facebook_seeded", True)
            store.kv_set("facebook_june_2026_backfill_seeded", True)
            store.kv_set("facebook_comment_reconciliation_seeded", True)
            store.conn.executemany(
                "INSERT INTO contexts(platform,context_id,source_url,post_published_at,reason) VALUES ('facebook',?,?,?,'out_of_window')",
                [("first", "https://facebook.com/posts/first", "2021-01-01T00:00:00+07:00"),
                 ("old", "https://facebook.com/posts/old", "2020-12-31T23:59:59+07:00")],
            )
            worker.seed_queue(store)
            worker.seed_queue(store)
            rows = store.conn.execute("SELECT payload FROM discovery_queue WHERE platform='facebook' AND priority=5").fetchall()
            self.assertEqual(rows, [])
            self.assertGreater(store.pending_count("facebook"), 0)
        finally:
            store.close()
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_worker_generic_capture_error_defers_source(self):
        store = MagicMock()
        task = {"id": 1, "payload": SOURCE}
        store.claim.side_effect = [task, None]
        store.kv_get.side_effect = lambda key, default=None: default
        browser = MagicMock()
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        with patch.object(worker, "_session", return_value=browser), \
             patch.object(worker.fb, "discover_posts", return_value=["https://facebook.com/posts/bad"]), \
             patch.object(worker.fb, "fetch_eligible_post", side_effect=fb.FacebookCaptureError("error:TimeoutError")):
            result = worker.run_once(store, {"run_id": "run"}, lambda: False, "batch")
        self.assertEqual(result, {"status": "retry_wait", "retry_seconds": 120})
        store.retry_task.assert_called_once_with(1, delay=120)
        store.complete_task.assert_not_called()

    def test_old_reconciliation_tasks_are_skipped_without_deleting_data(self):
        from crawlers.baseline_store import BaselineStore

        with tempfile.TemporaryDirectory() as directory, BaselineStore(base_dir=directory) as store:
            store.kv_set(f"facebook_seeded:{worker.SOURCES_VERSION}", True)
            store.kv_set("facebook_seen_posts", ["old"])
            store.enqueue("facebook", "reconcile", {"kind": "direct_post", "url": "https://www.facebook.com/posts/old"}, priority=5)
            store.enqueue("facebook", "scope_retry", {"kind": "direct_post", "url": "https://www.facebook.com/posts/retry"}, priority=9)
            store.enqueue("facebook", "discover", {**SOURCE, "label": "historical"}, priority=10)
            store.enqueue("facebook", "browser_recovery", {"kind": "direct_post", "url": "https://www.facebook.com/posts/new"}, priority=15)
            store.enqueue("facebook", "discover", {**SOURCE, "label": "backfill"}, priority=5)
            store.enqueue("youtube", "reconcile", {"video_id": "other"}, priority=5)
            worker.seed_queue(store)
            worker.seed_queue(store)
            sources = []
            while task := store.claim("facebook"):
                sources.append(task["source"])
                store.complete_task(task["id"])
            self.assertEqual(sources, ["discover", "scope_retry", "discover", "browser_recovery"])
            self.assertEqual(store.conn.execute(
                "SELECT status FROM discovery_queue WHERE platform='facebook' AND source='reconcile'"
            ).fetchone()[0], "skipped")
            self.assertEqual(store.claim("youtube")["priority"], 5)
            self.assertEqual(store.kv_get("facebook_seen_posts"), ["old"])

    def test_smoke_rejects_production_directory(self):
        from crawlers.facebook import facebook_smoke

        with self.assertRaises(ValueError):
            facebook_smoke.run_smoke(facebook_smoke.PRODUCTION_DIR)
        nested = os.path.join(facebook_smoke.PRODUCTION_DIR, "smoke")
        with self.assertRaises(ValueError):
            facebook_smoke.run_smoke(nested)

    def test_smoke_stays_isolated_and_accepts_intentional_comment_cap(self):
        from crawlers.facebook import facebook_smoke

        url = "https://www.facebook.com/example/posts/first"
        metadata = {
            "post_title": "VinBus", "post_context": "VinBus", "metadata_resolved": True,
            "post_published_at": "2021-12-01T00:00:00+07:00",
            "comment_capture": {"expected_total": 10},
        }
        comment = {"comment_id": "first", "comment_text": "Good", "post_url": url,
                   "posted_at_raw": COMMENT_TIME, "posted_at": COMMENT_TIME_ISO}
        browser = MagicMock()
        browser.start.return_value = browser
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        with tempfile.TemporaryDirectory() as output_dir, \
             patch.dict(os.environ, {"FB_HANDLE_SALT": "test-salt"}), \
             patch.object(fbs, "FacebookSession", return_value=browser), \
             patch.object(facebook_smoke, "_production_snapshot", return_value={}), \
             patch.object(facebook_smoke, "_youtube_pids", return_value=[]), \
             patch.object(worker, "facebook_history_sources", side_effect=AssertionError("History must not run during smoke")), \
             patch.object(fb, "fetch_eligible_post", return_value=(metadata, "accept", "electric_bus", "VinBus", [comment])), \
             patch.object(worker.time, "sleep"):
            result = facebook_smoke.run_smoke(output_dir, max_comments=1, url=url, year=2021)
            self.assertTrue(result["ok"])
            self.assertEqual(result["comments"], 1)
            self.assertTrue(result["qc"]["ok"])


    def test_closed_context_is_detected_and_session_replaced(self):
        engine = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            browser = fbs.FacebookSession(profile_dir=directory, session_factory=lambda **kwargs: engine).start()
            self.assertFalse(browser.closed)
            callback = engine.context.on.call_args.args[1]
            callback(engine.context)
            self.assertTrue(browser.closed)
            replacement = MagicMock(closed=False)
            worker._SESSIONS.browser = browser
            try:
                with patch.object(fbs, "FacebookSession") as factory:
                    factory.return_value.start.return_value = replacement
                    self.assertIs(worker._session(), replacement)
                engine.close.assert_called_once()
            finally:
                worker.close_session()

    def test_discovery_failure_returns_to_queue_before_waiting(self):
        store = MagicMock()
        task = {"id": 1, "payload": SOURCE}
        store.claim.side_effect = [task, None]
        store.kv_get.side_effect = lambda key, default=None: default
        browser = MagicMock(closed=False)
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        with patch.object(worker, "_session", return_value=browser), \
             patch.object(worker, "seed_queue"), \
             patch.object(worker, "close_session") as close, \
             patch.object(fb, "discover_posts", side_effect=RuntimeError("browser closed")):
            result = worker.run_once(store, {"run_id": "run"}, lambda: False, "batch")
        self.assertEqual(result["status"], "retry_wait")
        self.assertEqual(store.claim.call_count, 2)
        store.retry_task.assert_called_once_with(1, delay=120)
        store.complete_task.assert_not_called()
        close.assert_called_once()

    def test_post_errors_do_not_block_ready_tasks_or_lose_urls(self):
        from crawlers.baseline_store import BaselineStore

        bad = "https://www.facebook.com/example/posts/bad"
        good = "https://www.facebook.com/example/posts/good"
        other = "https://www.facebook.com/example/posts/other"
        rejected = ({"metadata_resolved": True}, "reject", "missing_group:electric_bus", "", [])
        browser = MagicMock(closed=False)
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        for error in (fb.FacebookTargetUnresolvedError("target_unresolved"),
                      fb.FacebookCaptureError("error:TimeoutError"),
                      TimeoutError("navigation timed out")):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory, \
                 BaselineStore(base_dir=directory) as store:
                run = store.get_or_create_run(1, 5000)
                store.enqueue("facebook", "discover", {**SOURCE, "post_urls": [bad, good]})
                store.enqueue("facebook", "discover", {"kind": "direct_post", "url": other})
                with patch.object(worker, "_session", return_value=browser), \
                     patch.object(worker, "seed_queue"), \
                     patch.object(worker, "close_session"), \
                     patch.object(worker.time, "sleep") as sleep, \
                     patch.object(fb, "fetch_eligible_post", side_effect=[error, rejected, rejected]) as fetch:
                    result = worker.run_once(store, run, lambda: False, "batch", allow_revisit=False)
                self.assertEqual(result["status"], "retry_wait")
                called_urls = [call.args[0] for call in fetch.call_args_list]
                self.assertEqual(called_urls[-1], other)
                self.assertEqual(called_urls, [bad, good, other])
                tasks = store.conn.execute("SELECT status,payload FROM discovery_queue ORDER BY id").fetchall()
                self.assertEqual([row["status"] for row in tasks], ["pending", "done"])
                self.assertEqual(json.loads(tasks[0]["payload"])["post_urls"], [bad])
                self.assertTrue(all(call.args[0] == fb.DELAY_BETWEEN_SOURCES for call in sleep.call_args_list))

    def test_failed_source_keeps_remaining_urls_and_resumes(self):
        from crawlers.baseline_store import BaselineStore

        urls = [f"https://www.facebook.com/example/posts/{name}" for name in ("first", "bad", "last")]
        browser = MagicMock(closed=False)
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        rejected = ({"metadata_resolved": True}, "reject", "missing_group:electric_bus", "", [])
        with tempfile.TemporaryDirectory() as directory, BaselineStore(base_dir=directory) as store:
            run = store.get_or_create_run(1, 5000)
            store.enqueue("facebook", "discover", {**SOURCE, "post_urls": urls, "discovery_complete": True})
            with patch.object(worker, "_session", return_value=browser), \
                 patch.object(worker, "seed_queue"), \
                 patch.object(worker, "close_session"), \
                 patch.object(worker.time, "sleep"), \
                 patch.object(fb, "fetch_eligible_post", side_effect=[rejected, RuntimeError("browser closed"), rejected]) as fetch:
                result = worker.run_once(store, run, lambda: False, "batch", allow_revisit=False)
            self.assertEqual(result["status"], "retry_wait")
            self.assertEqual(fetch.call_count, 3)
            task = dict(store.conn.execute("SELECT * FROM discovery_queue").fetchone())
            self.assertEqual(task["status"], "pending")
            self.assertEqual(json.loads(task["payload"])["post_urls"], [urls[1]])
            self.assertIn("first", store.kv_get("facebook_seen_posts"))
            store.conn.execute("UPDATE discovery_queue SET next_retry_at=0")
            with patch.object(worker, "_session", return_value=browser), \
                 patch.object(worker, "seed_queue"), \
                 patch.object(worker.time, "sleep"), \
                 patch.object(fb, "fetch_eligible_post", return_value=rejected) as fetch:
                result = worker.run_once(store, run, lambda: False, "batch", allow_revisit=False)
            self.assertEqual(result["status"], "queue_empty")
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(store.conn.execute("SELECT status FROM discovery_queue").fetchone()[0], "done")

    def test_seeding_does_not_recheck_old_rejects_or_reset_seen(self):
        from crawlers.baseline_store import BaselineStore

        with tempfile.TemporaryDirectory() as directory, BaselineStore(base_dir=directory) as store:
            store.kv_set(f"facebook_seeded:{worker.SOURCES_VERSION}", True)
            store.kv_set("facebook_comment_reconciliation_seeded", True)
            for identity, body, date in (
                ("eligible", "VinBus Hà Nội", "2021-01-01T12:00:00+07:00"),
                ("off_topic", "Xe máy Hà Nội", "2021-01-01T12:00:00+07:00"),
                ("too_old", "VinBus Hà Nội", "2020-12-31T12:00:00+07:00"),
            ):
                store.upsert_context({
                    "platform": "facebook", "context_id": identity, "verdict": "reject", "reason": "old_gate",
                    "source_url": f"https://www.facebook.com/example/posts/{identity}", "post_context": body,
                    "metadata_resolved": True, "post_published_at": date, "state": "rejected",
                })
            store.kv_set("facebook_seen_posts", ["eligible", "off_topic", "too_old"])
            worker.seed_queue(store)
            worker.seed_queue(store)
            tasks = list(store.conn.execute("SELECT payload FROM discovery_queue WHERE source='gate_recheck'"))
            self.assertEqual(tasks, [])
            self.assertEqual(len(store.kv_get("facebook_seen_posts")), 3)
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM discovery_queue").fetchone()[0], 0)

    def test_incomplete_capture_keeps_metrics_without_scheduling_a_reread(self):
        from crawlers.baseline_store import BaselineStore

        url = "https://www.facebook.com/example/posts/123"
        browser = MagicMock(closed=False)
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        capture = {"expected_total": 4, "rendered_count": 3, "valid_count": 2, "invalid_timestamp_count": 1}
        photo_url = "https://www.facebook.com/photo?fbid=456"
        metadata = {"post_context": "VinBus Hà Nội", "metadata_resolved": True, "comment_capture": capture,
                    "resolved_post_url": url}
        comment = {"comment_id": "first", "comment_text": "Good", "post_url": url, "posted_at_raw": COMMENT_TIME}
        with tempfile.TemporaryDirectory() as directory, BaselineStore(base_dir=directory) as store:
            run = store.get_or_create_run(1, 5000)
            store.upsert_context({"platform": "facebook", "context_id": "123", "verdict": "accept", "comment_retry_count": 2,
                                  "state": "accepted"})
            store.enqueue("facebook", "discover", {"kind": "direct_post", "url": photo_url})
            task = store.claim("facebook")
            with patch.object(worker, "_session", return_value=browser), \
                 patch.object(worker, "seed_queue"), \
                 patch.object(store, "claim", side_effect=[task, None]), \
                 patch.object(worker.time, "sleep"), \
                 patch.dict(os.environ, {"FB_HANDLE_SALT": "test-salt"}), \
                 patch.object(fb, "fetch_eligible_post", return_value=(metadata, "accept", "electric_bus", "VinBus Hà Nội", [comment])):
                worker.run_once(store, run, lambda: False, "batch", allow_revisit=False)
            context = store.context("facebook", "123")
            self.assertEqual(context["comment_retry_count"], 2)
            self.assertEqual(context["comment_capture_status"], "incomplete")
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM discovery_queue").fetchone()[0], 1)
            event = json.loads(store.conn.execute("SELECT detail FROM events WHERE kind='fb_comment_reconciliation'").fetchone()[0])
            self.assertEqual(event["added"], 1)
            self.assertEqual(event["records_count"], 1)
            self.assertEqual(event["rendered_count"], 3)
            self.assertEqual(event["invalid_timestamp_count"], 1)
            self.assertEqual(event["url"], url)
            self.assertEqual(event["discovered_url"], photo_url)
            self.assertEqual(store.conn.execute("SELECT source_url FROM comments").fetchone()[0], url)

    def test_direct_tasks_skip_processed_posts_even_without_seen_cache(self):
        from crawlers.baseline_store import BaselineStore

        browser = MagicMock(closed=False)
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        with tempfile.TemporaryDirectory() as directory, BaselineStore(base_dir=directory) as store:
            run = store.get_or_create_run(1, 5000)
            for state in ("comments_done", "comments_incomplete", "comments_unverified", "comments_limited", "rejected"):
                store.upsert_context({"platform": "facebook", "context_id": state, "state": state,
                                      "verdict": "reject" if state == "rejected" else "accept"})
                store.enqueue("facebook", "browser_recovery", {
                    "kind": "direct_post", "url": f"https://www.facebook.com/example/posts/{state}",
                })
            with patch.object(worker, "_session", return_value=browser), \
                 patch.object(worker, "seed_queue"), \
                 patch.object(worker.time, "sleep"), \
                 patch.object(fb, "fetch_eligible_post") as fetch:
                result = worker.run_once(store, run, lambda: False, "batch", allow_revisit=False)
            self.assertEqual(result["status"], "queue_empty")
            fetch.assert_not_called()
            self.assertEqual(store.pending_count("facebook"), 0)

    def test_unverified_new_capture_is_not_requeued_or_fetched_twice(self):
        from crawlers.baseline_store import BaselineStore

        url = "https://www.facebook.com/example/posts/new"
        browser = MagicMock(closed=False)
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        metadata = {"metadata_resolved": True, "post_context": "VinBus Hà Nội", "comment_capture": {}}
        with tempfile.TemporaryDirectory() as directory, BaselineStore(base_dir=directory) as store:
            run = store.get_or_create_run(1, 5000)
            for source in ("browser_recovery", "scope_retry"):
                store.enqueue("facebook", source, {"kind": "direct_post", "url": url})
            with patch.object(worker, "_session", return_value=browser), \
                 patch.object(worker, "seed_queue"), \
                 patch.object(worker.time, "sleep"), \
                 patch.dict(os.environ, {"FB_HANDLE_SALT": "test-salt"}), \
                 patch.object(fb, "fetch_eligible_post", return_value=(metadata, "accept", "electric_bus", "VinBus Hà Nội", [])) as fetch:
                result = worker.run_once(store, run, lambda: False, "batch", allow_revisit=False)
            self.assertEqual(result["status"], "queue_empty")
            fetch.assert_called_once()
            self.assertEqual(store.context("facebook", "new")["comment_capture_status"], "unverified")
            self.assertIn("new", store.kv_get("facebook_seen_posts"))
            self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM discovery_queue").fetchone()[0], 2)

    def test_photo_parent_links_are_validated_and_parent_is_gated_again(self):
        photo = "https://www.facebook.com/photo?fbid=456"
        parent = "https://www.facebook.com/example/posts/123"
        captured = {"resolved": True, "body": "VinBus Hà Nội", "parent_post_url": parent}
        metadata = fb._metadata_from_response(_FakeResponse(captured), photo)
        self.assertEqual(metadata["parent_post_url"], parent)
        for invalid in ("https://evil.example/posts/123", "https://www.facebook.com/reel/123",
                        parent + "?comment_id=456", "https://www.facebook.com/story.php"):
            with self.subTest(invalid=invalid):
                result = fb._metadata_from_response(_FakeResponse({**captured, "parent_post_url": invalid}), photo)
                self.assertNotIn("parent_post_url", result)
        parent_metadata = {"metadata_resolved": True, "post_context": "Xe máy Hà Nội",
                           "post_published_at": "2021-01-01T12:00:00+07:00"}
        with patch.object(fb, "fetch_post_metadata", side_effect=[metadata, parent_metadata]) as fetch_metadata, \
             patch.object(fb, "fetch_post") as fetch_comments:
            result = fb.fetch_eligible_post(photo)
        self.assertEqual(fetch_metadata.call_args.args, (parent,))
        self.assertEqual(result[1], "reject")
        fetch_comments.assert_not_called()

    def test_capture_failure_has_bounded_retries_without_completing_task(self):
        from crawlers.baseline_store import BaselineStore

        url = "https://www.facebook.com/example/posts/123"
        browser = MagicMock(closed=False)
        browser.ensure_login.return_value = (fbs.AUTH_OK, {})
        with tempfile.TemporaryDirectory() as directory, BaselineStore(base_dir=directory) as store:
            run = store.get_or_create_run(1, 5000)
            store.enqueue("facebook", "discover", {"kind": "direct_post", "url": url})
            with patch.object(worker, "_session", return_value=browser), \
                 patch.object(worker, "seed_queue"), \
                 patch.object(worker, "close_session"), \
                 patch.object(fb, "fetch_eligible_post", side_effect=fb.FacebookCaptureError("timeout")):
                for attempt in range(5):
                    store.conn.execute("UPDATE discovery_queue SET next_retry_at=0")
                    self.assertEqual(worker.run_once(store, run, lambda: False, "batch")["status"], "retry_wait")
            task = dict(store.conn.execute("SELECT * FROM discovery_queue").fetchone())
            self.assertEqual(task["status"], "failed")
            self.assertEqual(task["attempts"], 5)
            self.assertEqual(json.loads(task["payload"])["post_urls"], [url])

    def test_comment_stats_require_target_scope(self):
        for script in (fb._STORE_COMMENTS_JS, fb._CAPTURE_COMMENT_STATS_JS):
            self.assertNotIn("|| document.body", script)
            self.assertIn("window.__commentScope", script)

    @unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 for Chromium DOM checks")
    def test_comment_permalink_alias_requires_verified_dialog_and_excludes_background(self):
        from patchright.sync_api import sync_playwright

        url = "https://www.facebook.com/example/posts/pfbidOriginal"
        body = "VinBus electric bus service in Vietnam makes every trip comfortable"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<html></html>"))
                page.goto(url)
                for variant in ("valid", "wrong_body", "missing_permalink", "ambiguous", "hidden", "foreign"):
                    with self.subTest(variant=variant):
                        post_url = "/example/posts/pfbidOther" if variant == "missing_permalink" else url
                        comment_url = ("https://evil.example" if variant == "foreign" else "") + "/example/posts/pfbidAlias?comment_id=7"
                        dialog_body = "An unrelated bus post" if variant == "wrong_body" else body
                        extra = '<a href="/example/posts/pfbidSecond?comment_id=8">Other comment</a>' if variant == "ambiguous" else ""
                        style = 'style="display:none"' if variant == "hidden" else ""
                        page.set_content(f'''<div role="article"><div dir="auto">Background comment</div>
                            <a href="/example/posts/pfbidAlias?comment_id=99" title="{COMMENT_TIME}">Time</a></div>
                            <div role="dialog" {style}><a href="{post_url}">Post timestamp</a>
                            <div dir="auto">{dialog_body}</div><div role="article">
                            <div dir="auto">Correct target comment</div>
                            <a href="{comment_url}" title="{COMMENT_TIME}">Time</a></div>{extra}
                            <div role="article"><div dir="auto">Untrusted foreign comment</div>
                            <a href="https://evil.example/example/posts/pfbidAlias?comment_id=88"
                            title="{COMMENT_TIME}">Time</a></div></div>''')
                        page.evaluate(fb._INIT_COMMENT_CAPTURE_JS, {"targetUrl": url, "expectedBody": body})
                        page.evaluate(fb._CAPTURE_COMMENTS_JS)
                        self.assertEqual(page.get_attribute("html", fb._TARGET_RESOLVED_ATTR),
                                         "1" if variant == "valid" else "0")
                        self.assertEqual(page.evaluate("Object.keys(window.__scrapedComments)"),
                                         ["7"] if variant == "valid" else [])
            finally:
                browser.close()

    @unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 for Chromium DOM checks")
    def test_permalink_dialog_metadata_ignores_background_feed(self):
        from patchright.sync_api import sync_playwright

        body = "VinBus electric bus service in Vietnam makes every trip comfortable"
        url = "https://www.facebook.com/example/posts/pfbid123"
        background = r'{"creation_time":1790762725,"creation_story":{"message":{"text":"Wrong background story"}}}'
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<html></html>"))
                page.goto(url)
                page.set_content(f'''<title>(8) VinBus - {body}... | Facebook</title>
                    <script type="application/json">{background}</script>
                    <main role="main"><div role="article"><div dir="auto">{body} background feed</div>
                    <time datetime="2026-11-12T12:00:00Z"></time></div></main>
                    <div role="dialog"><section role="dialog"><div dir="auto">{body}</div>
                    <a id="post-time" href="?__cft__[0]=tracking" aria-describedby="date">2 years ago</a>
                    <span id="date">2024-08-05T09:45:16Z</span>
                    <div role="article"><div dir="auto">Actual target comment</div>
                    <a href="/example/posts/pfbid123?comment_id=7" title="{COMMENT_TIME}">Comment time</a>
                    </div></section></div>''')
                page.evaluate(fb._CAPTURE_POST_METADATA_JS)
                captured = json.loads(page.get_attribute("html", fb._METADATA_DATA_ATTR))
                metadata = fb._metadata_from_response(_FakeResponse(captured, background), url)
                self.assertTrue(captured["target_scoped"])
                self.assertTrue(metadata["metadata_resolved"])
                self.assertEqual(metadata["post_context"], body)
                self.assertEqual(metadata["post_published_at"], "2024-08-05T09:45:16+00:00")
                page.evaluate(fb._INIT_COMMENT_CAPTURE_JS, {"targetUrl": url, "expectedBody": metadata["post_context"]})
                self.assertEqual(page.get_attribute("html", fb._TARGET_RESOLVED_ATTR), "1")
                page.evaluate(fb._CAPTURE_COMMENTS_JS)
                self.assertEqual(page.evaluate("Object.keys(window.__scrapedComments)"), ["7"])

                page.locator("#date").evaluate("node => node.remove()")
                page.evaluate(fb._CAPTURE_POST_METADATA_JS)
                captured = json.loads(page.get_attribute("html", fb._METADATA_DATA_ATTR))
                metadata = fb._metadata_from_response(_FakeResponse(captured, background), url)
                self.assertEqual(metadata["post_published_at"], "")

                page.locator("#post-time").evaluate('''node => node.addEventListener('mouseenter', () => {
                    const tooltip = document.createElement('span');
                    tooltip.id = 'date';
                    tooltip.textContent = '2024-08-05T09:45:16Z';
                    document.body.appendChild(tooltip);
                }, {once: true})''')
                fb._capture_post_metadata_page(page)
                captured = json.loads(page.get_attribute("html", fb._METADATA_DATA_ATTR))
                metadata = fb._metadata_from_response(_FakeResponse(captured, background), url)
                self.assertEqual(metadata["post_published_at"], "2024-08-05T09:45:16+00:00")

                page.locator('section[role="dialog"] > div[dir="auto"]').evaluate("node => node.remove()")
                page.evaluate(fb._CAPTURE_POST_METADATA_JS)
                captured = json.loads(page.get_attribute("html", fb._METADATA_DATA_ATTR))
                metadata = fb._metadata_from_response(_FakeResponse(captured, background), url)
                self.assertFalse(metadata["metadata_resolved"])

                page.set_content(f'''<title>VinBus - {body} | Facebook</title>
                    <div role="dialog"><div role="article"><div dir="auto">{body}</div>
                    <a href="/example/posts/pfbid123" title="2024-08-05T09:45:16Z">Post time</a>
                    <div role="article"><div dir="auto">Nested comment</div>
                    <a href="/example/posts/pfbid123?comment_id=7" title="{COMMENT_TIME}">Comment time</a>
                    </div></div></div>''')
                page.evaluate(fb._CAPTURE_POST_METADATA_JS)
                captured = json.loads(page.get_attribute("html", fb._METADATA_DATA_ATTR))
                metadata = fb._metadata_from_response(_FakeResponse(captured, background), url)
                self.assertEqual(metadata["post_context"], body)
                self.assertEqual(metadata["post_published_at"], "2024-08-05T09:45:16+00:00")
            finally:
                browser.close()

    @unittest.skipUnless(os.getenv("RUN_BROWSER_TESTS") == "1", "set RUN_BROWSER_TESTS=1 for Chromium DOM checks")
    def test_comment_dom_scope_timestamp_and_photo_parent(self):
        from patchright.sync_api import sync_playwright

        body = "Review VinBus electric bus service in Vietnam"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<html></html>"))
                page.goto("https://www.facebook.com/watch?v=123")
                page.set_content(f'''<div role="main"><div id="watch_feed">
                    <section><span dir="auto">{body}</span>
                    <p>Hãy là người đầu tiên bình luận.</p><div role="textbox"></div></section>
                    <section>999 comments<div role="article"><a href="/watch?v=456&amp;comment_id=9"
                    title="{COMMENT_TIME}"><div dir="auto">Unrelated comment</div></a></div></section>
                    </div></div>''')
                args = {"targetUrl": page.url, "expectedBody": body}
                page.evaluate(fb._INIT_REEL_COMMENT_CAPTURE_JS, args)
                self.assertTrue(page.evaluate(fb._LOCATE_REEL_COMMENT_SCOPE_JS))
                page.evaluate(fb._CAPTURE_REEL_COMMENTS_JS)
                page.evaluate(fb._STORE_COMMENTS_JS)
                self.assertEqual(json.loads(page.get_attribute("html", fb._COMMENT_DATA_ATTR)), [])
                self.assertEqual(json.loads(page.get_attribute("html", fb._COMMENT_STATS_ATTR)), {"expected_total": 0})

                page.evaluate("window.__commentScope.querySelector('p').textContent = '3\\n12 comments'")
                page.evaluate(fb._CAPTURE_COMMENT_STATS_JS)
                self.assertEqual(page.evaluate("window.__scrapedCommentStats.expected_total"), 12)

                page.set_content(f'''<section><div dir="auto">{body}</div><p>2 comments</p>
                    <div role="article"><div dir="auto">Video comment text</div>
                    <a href="/person?comment_id=opaque">Author</a><span id="video-time">{COMMENT_TIME}</span>
                    <a href="/watch?v=123&amp;comment_id=8" aria-describedby="video-time"
                    aria-label="2 weeks ago">2 weeks</a></div><div role="textbox"></div></section>
                    <section>999 comments</section>''')
                page.evaluate(fb._INIT_REEL_COMMENT_CAPTURE_JS, args)
                self.assertTrue(page.evaluate(fb._LOCATE_REEL_COMMENT_SCOPE_JS))
                page.evaluate(fb._CAPTURE_REEL_COMMENTS_JS)
                self.assertEqual(page.evaluate("Object.values(window.__scrapedComments)[0].posted_at_raw"), COMMENT_TIME)
                page.evaluate(fb._INIT_REEL_COMMENT_CAPTURE_JS, {**args, "expectedBody": "Missing target body"})
                page.set_content("<section>999 comments</section>")
                self.assertFalse(page.evaluate(fb._LOCATE_REEL_COMMENT_SCOPE_JS))
                page.evaluate(fb._CAPTURE_REEL_COMMENTS_JS)
                page.evaluate(fb._CAPTURE_COMMENT_STATS_JS)
                self.assertEqual(page.evaluate("window.__scrapedCommentStats.expected_total"), None)
                self.assertEqual(page.evaluate("Object.values(window.__scrapedComments)"), [])

                page.set_content(f'''<div role="article"><a href="/example/posts/123">Post</a>
                    <div dir="auto">{body}</div><div role="article" aria-label="Comment by Example">
                    <div dir="auto">Correct comment text</div><div id="comment-time">{COMMENT_TIME}</div>
                    <a href="/example/posts/123?comment_id=7" aria-describedby="comment-time"
                    aria-label="2 weeks ago">2 weeks</a></div></div>''')
                page.evaluate(fb._INIT_COMMENT_CAPTURE_JS, {"targetUrl": "https://www.facebook.com/example/posts/123", "expectedBody": body})
                page.evaluate(fb._CAPTURE_COMMENTS_JS)
                comments = page.evaluate("Object.values(window.__scrapedComments)")
                self.assertEqual(len(comments), 1)
                self.assertEqual(comments[0]["comment_id"], "7")
                self.assertEqual(comments[0]["posted_at_raw"], COMMENT_TIME)
                self.assertEqual(fb.parse_comment_tooltip(comments[0]["posted_at_raw"]), COMMENT_TIME_ISO)

                page.goto("https://www.facebook.com/photo?fbid=42")
                page.set_content(''.join(f'''<div role="article"><a href="#">Menu</a>
                    <div dir="auto">Photo comment {comment_id}</div><span id="time-{comment_id}">{COMMENT_TIME}</span>
                    <a href="/groups/bus/posts/123?comment_id={comment_id}" aria-describedby="time-{comment_id}"
                    aria-label="2 weeks ago">2 weeks</a></div>''' for comment_id in (10, 11)))
                page.evaluate(fb._INIT_COMMENT_CAPTURE_JS, {"targetUrl": page.url, "expectedBody": body})
                self.assertEqual(page.get_attribute("html", fb._TARGET_RESOLVED_ATTR), "1")
                page.evaluate(fb._CAPTURE_COMMENTS_JS)
                self.assertEqual(len(page.evaluate("Object.values(window.__scrapedComments)")), 2)

                page.set_content('''<div role="main">Photo canvas</div><aside><div role="article"></div>
                    <a href="/example/posts/123">View post</a></aside>''')
                fb._capture_post_metadata_page(page)
                metadata = json.loads(page.get_attribute("html", fb._METADATA_DATA_ATTR))
                self.assertEqual(metadata["parent_post_url"], "https://www.facebook.com/example/posts/123")
            finally:
                browser.close()

class _FakeNode:
    def __init__(self, attrib):
        self.attrib = attrib


class _FakeResponse:
    def __init__(self, captured, html_content=""):
        self._html = [_FakeNode({fb._METADATA_DATA_ATTR: json.dumps(captured, ensure_ascii=False)})]
        self.html_content = html_content

    def css(self, selector):
        return self._html if selector == "html" else []


class _FakeCommentResponse:
    def __init__(self, comments):
        self._html = [_FakeNode({
            fb._TARGET_RESOLVED_ATTR: "1",
            fb._COMMENT_DATA_ATTR: json.dumps(comments, ensure_ascii=False),
        })]
        self.html_content = ""

    def css(self, selector):
        return self._html if selector == "html" else []


class _FakeAuthResponse:
    def __init__(self, state):
        self._html = [_FakeNode({
            "data-auth-state": state,
            fb._ACTION_STATE_ATTR: "ok" if state == "ok" else "auth",
        })]

    def css(self, selector):
        return self._html if selector == "html" else []


if __name__ == "__main__":
    unittest.main()


class SessionRecycleTests(unittest.TestCase):
    def test_recycle_only_when_old(self):
        import time
        from unittest.mock import MagicMock, patch
        from crawlers.facebook import facebook_worker as worker
        old = MagicMock(); new = MagicMock(); new.ensure_login.return_value = (fbs.AUTH_OK, {})
        old.closed = new.closed = False
        worker._SESSIONS.browser, worker._SESSIONS.started = old, time.monotonic()
        self.assertIsNone(worker._recycle_if_old()); old.close.assert_not_called()
        worker._SESSIONS.started = time.monotonic() - worker._SESSION_MAX_AGE_SECONDS - 1
        with patch.object(fbs, "FacebookSession") as cls:
            cls.return_value.start.return_value = new
            self.assertIsNone(worker._recycle_if_old())
        old.close.assert_called_once(); self.assertIs(worker._SESSIONS.browser, new)
        worker.close_session()
