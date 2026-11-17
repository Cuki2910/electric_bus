"""Facebook worker cho baseline_gate_v2.

Persistent profile (headful). Discovery round-robin nguồn seed + query tổ hợp
A+B+C. Metadata → gate → comments/replies. Contamination guard: comment phải
thuộc đúng bài đích. CAPTCHA/checkpoint → auth_required, pause worker Facebook.
"""
import os
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from crawlers.common.records import require_env_salt, salted_hash
from crawlers.facebook import crawl_facebook as fb
from crawlers.facebook import facebook_session as fbs
from crawlers.electric_bus.config import SOURCES_VERSION, facebook_history_sources
from crawlers.topics import co_thanh_pho_khac, matched_groups

_SESSIONS = threading.local()
# Chromium renderer phình ~2.6GB/giờ khi cuộn FB; restart giữa hai bài để trả RAM (profile/cookie giữ nguyên).
_SESSION_MAX_AGE_SECONDS = 30 * 60


def _session():
    browser = getattr(_SESSIONS, "browser", None)
    if browser is not None and browser.closed:
        close_session()
        browser = None
    if browser is None:
        browser = fbs.FacebookSession().start()
        _SESSIONS.browser = browser
        _SESSIONS.started = time.monotonic()
    return browser

def _recycle_if_old():
    """Đóng và mở lại browser nếu quá tuổi. Chỉ gọi giữa hai bài. Trả None hoặc dict auth_required."""
    started = getattr(_SESSIONS, "started", None)
    browser = getattr(_SESSIONS, "browser", None)
    if browser is not None:
        if not browser.closed and (started is None or time.monotonic() - started < _SESSION_MAX_AGE_SECONDS):
            return None
        close_session()
    browser = _session()
    fb.use_browser_session(browser)
    state, detail = browser.ensure_login()
    return None if state == fbs.AUTH_OK else {"state": state, "detail": detail}


def close_session():
    browser = getattr(_SESSIONS, "browser", None)
    try:
        if browser is not None:
            browser.close()
    finally:
        _SESSIONS.browser = None
        _SESSIONS.started = None
        fb.use_browser_session(None)

def _retry_task(store, task):
    store.retry_task(task["id"], delay=120)
    store.log_event("fb_task_deferred", "facebook", {"task_id": task["id"], "retry_seconds": 120})
    close_session()


def _auth_required(store, task=None, state=fbs.AUTH_LOGIN_FORM):
    if task is not None:
        store.defer_task(task["id"], delay=0)
    store.kv_set("facebook_status", "auth_required")
    store.log_event("auth_required", "facebook", {"state": state})
    return {"status": "auth_required", "state": state}


def _sources():
    return list(fb.SOURCES)


def seed_queue(store):
    store.conn.execute(
        "UPDATE discovery_queue SET status='skipped', lease_at=0 "
        "WHERE platform='facebook' AND status='pending' AND source='reconcile'"
    )
    key = f"facebook_seeded:{SOURCES_VERSION}"
    if not store.kv_get(key):
        for source in facebook_history_sources():
            priority = 10 if source["kind"] == "search" else 20
            store.enqueue("facebook", "discover", source, priority=priority,
                          dedup_key=f"facebook:{SOURCES_VERSION}:{source['label']}")
        store.kv_set(key, True)


def _revisit_sources(store):
    """Nạp lại toàn bộ SOURCES + combo mỗi giờ để bắt bài mới đăng sau khi seed ban đầu.

    seed_queue() chỉ chạy 1 lần (flag facebook_seeded); không có vòng lặp này, worker
    dừng hẳn ở queue_empty sau khi xử lý hết danh sách cố định 1 lần, kể cả khi
    trang/nguồn có bài mới. dedup_key theo giờ tránh spam khi queue rỗng bị check lại
    liên tục trong cùng giờ. Seen-set vẫn lọc bài đã xử lý.
    """
    stamp = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y%m%d%H")
    for source in _sources():
        store.enqueue("facebook", "discover", source, priority=50,
                      dedup_key=f"facebook:revisit:{source['label']}:{stamp}")


def _contamination_ok(post_url, comments):
    """Reject nếu có dấu hiệu comment lẫn từ bài khác (fail-closed, threshold 0)."""
    # Capture đã giới hạn trong target article scope; đây là guard thứ hai:
    # comment rỗng đã bị store bỏ; kiểm tra không có comment nào quá dài bất thường
    # (thường là toàn bộ post body của bài khác lọt vào).
    for c in comments:
        if not c.get("post_url") or not fb.url_matches_target(c["post_url"], post_url):
            return False
        if len(c.get("comment_text", "")) > 8000:
            return False
    return True


def _to_records(store, post_url, post_context, metadata, comments, run_id, batch_id):
    handle_salt = require_env_salt("FB_HANDLE_SALT")
    records = []
    for c in comments:
        text = c.get("comment_text", "")
        posted_at_raw = c.get("posted_at_raw", "")
        posted_at = fb.parse_comment_tooltip(posted_at_raw)
        if not posted_at:
            continue
        canonical = fb.canonical_post_url(post_url)
        import hashlib
        raw_key = f"{canonical}|{fb._norm_text(text)}|{fb._norm_text(c.get('posted_at_raw',''))}|{c.get('parent_comment_id','')}"
        cid = c.get("comment_id") or hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:20]
        comment_type = c.get("comment_type", "top_level")
        parent_id = c.get("parent_comment_id") or None
        author_raw = c.get("author_raw", "")
        author_key = salted_hash(author_raw, handle_salt) if author_raw else None
        records.append({
            "platform": "facebook", "comment_id": cid, "context_id": fb.canonical_post_identity(post_url),
            "id": f"facebook_c_{cid}", "source_url": canonical, "post_context": post_context,
            "post_title": metadata.get("post_title", ""), "post_published_at_raw": metadata.get("post_published_at_raw", ""),
            "post_published_at": metadata.get("post_published_at", ""),
            "comment_text": text, "comment_type": comment_type, "parent_comment_id": parent_id,
            "thread_id": parent_id or cid, "reply_depth": 1 if comment_type == "reply" else 0,
            "posted_at_raw": posted_at_raw, "posted_at": posted_at,
            "likes_count": c.get("likes_count", 0), "author_key": author_key,
            "matched_groups": ",".join(matched_groups(text)),
            "co_thanh_pho_khac": int(co_thanh_pho_khac("", post_context)),
            "parent_unresolved": int(bool(c.get("parent_unresolved"))),
            "crawl_batch_id": batch_id, "run_id": run_id,
            "crawled_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
        })
    return records


def _comment_reconciliation(capture, captured_count, max_comments):
    """Classify one capture without claiming completeness when the UI count is absent."""
    expected = capture.get("expected_total") if isinstance(capture, dict) else None
    if not isinstance(expected, int) or expected < 0:
        return None, None, "unverified"
    missing = max(expected - captured_count, 0)
    if max_comments is not None and expected > captured_count:
        return expected, missing, "limited"
    return expected, missing, "complete" if missing == 0 else "incomplete"


def run_once(store, run, deadline_check, batch_id, max_comments=None, allow_revisit=True):
    """Xử lý task discover cho tới khi hết queue/giờ hoặc gặp auth challenge."""
    try:
        browser = _session()
        fb.use_browser_session(browser)
        state, detail = browser.ensure_login()
    except fbs.FacebookProfileBusyError:
        store.kv_set("facebook_status", "profile_busy")
        store.log_event("profile_busy", "facebook", {"action": "close_other_facebook_profile_window"})
        return {"status": "auth_required", "state": "profile_busy"}
    except Exception as exc:
        store.kv_set("facebook_status", "retry_wait")
        store.log_event("fb_session_error", "facebook", {"error": repr(exc)[:200]})
        close_session()
        return {"status": "retry_wait", "retry_seconds": 120}
    if state != fbs.AUTH_OK:
        store.kv_set("facebook_status", "auth_required")
        store.log_event("auth_required", "facebook", {"state": state, **detail})
        return {"status": "auth_required", "detail": detail, "state": state}
    store.kv_set("facebook_status", "running")
    seed_queue(store)
    store.kv_set("facebook_revisited_this_pass", not allow_revisit)
    seen = set(store.kv_get("facebook_seen_posts", []))
    seen.update(row["context_id"] for row in store.conn.execute(
        "SELECT context_id FROM contexts WHERE platform='facebook' AND "
        "(verdict='reject' OR state LIKE 'comments_%' OR state='quarantined') "
        "UNION SELECT context_id FROM comments WHERE platform='facebook'"
    ))
    store.kv_set("facebook_seen_posts", list(seen))
    deferred = False

    while True:
        if deadline_check():
            store.kv_set("facebook_status", "deadline")
            return {"status": "deadline"}
        task = store.claim("facebook")
        if task is None:
            if deferred:
                store.kv_set("facebook_status", "retry_wait")
                return {"status": "retry_wait", "retry_seconds": 120}
            if allow_revisit and not store.kv_get("facebook_revisited_this_pass"):
                _revisit_sources(store)
                store.kv_set("facebook_revisited_this_pass", True)
                continue
            store.kv_set("facebook_status", "queue_empty")
            return {"status": "queue_empty"}
        store.kv_set("facebook_revisited_this_pass", False)

        source = task["payload"]
        if task.get("source") == "reconcile":
            store.complete_task(task["id"], status="skipped")
            continue
        try:
            stale_auth = _recycle_if_old()
            if stale_auth:
                return _auth_required(store, task, stale_auth["state"])
            if "post_urls" in source:
                post_urls = source["post_urls"]
            else:
                discovered = []

                def checkpoint(post_urls):
                    discovered.extend(post_urls)
                    store.checkpoint_task(task["id"], {
                        **source, "post_urls": list(dict.fromkeys(discovered)), "discovery_complete": False,
                    })

                post_urls = fb.discover_posts(source, deadline_check=deadline_check, on_posts=checkpoint)
                source = {**source, "post_urls": post_urls, "discovery_complete": not deadline_check()}
                store.checkpoint_task(task["id"], source)
        except fbs.FacebookAuthRequiredError as exc:
            return _auth_required(store, task, exc.state)
        except fb.CookieExpiredError:
            return _auth_required(store, task)
        except Exception as exc:
            store.log_event("fb_discover_error", "facebook", {"source": source.get("label"), "error": repr(exc)[:200]})
            _retry_task(store, task)
            deferred = True
            continue

        failed_urls = []

        def retry_posts(urls):
            store.checkpoint_task(task["id"], {**source, "post_urls": urls})
            store.kv_set("facebook_seen_posts", list(seen))
            _retry_task(store, task)

        for index, post_url in enumerate(post_urls):
            if deadline_check():
                store.kv_set("facebook_seen_posts", list(seen))
                store.defer_task(task["id"], delay=0)
                return {"status": "deadline"}
            identity = fb.canonical_post_identity(post_url)
            if identity in seen:
                continue
            try:
                stale_auth = _recycle_if_old()
                if stale_auth:
                    return _auth_required(store, task, stale_auth["state"])
                metadata, verdict, reason, post_context, comments = fb.fetch_eligible_post(
                    post_url, max_comments=max_comments, source=source, deadline_check=deadline_check
                )
            except fbs.FacebookAuthRequiredError as exc:
                return _auth_required(store, task, exc.state)
            except fb.FacebookTargetUnresolvedError as exc:
                store.log_event("fb_capture_error", "facebook", {"url": post_url, "error": str(exc)[:200]})
                failed_urls.append(post_url)
                continue
            except fb.FacebookCaptureError as exc:
                store.log_event("fb_capture_error", "facebook", {"url": post_url, "error": str(exc)[:200]})
                failed_urls.append(post_url)
                continue
            except Exception as exc:
                store.log_event("fb_fetch_error", "facebook", {"url": post_url, "error": repr(exc)[:200]})
                failed_urls.append(post_url)
                close_session()
                continue
            seen.add(identity)
            discovered_url = post_url
            post_url = metadata.get("resolved_post_url") or post_url
            identity = fb.canonical_post_identity(post_url)
            seen.add(identity)
            prior = store.context("facebook", identity)
            prior_retries = prior.get("comment_retry_count", 0) if isinstance(prior, dict) else 0
            context = {
                "platform": "facebook", "context_id": identity, "source_url": fb.canonical_post_url(post_url),
                "post_title": metadata.get("post_title", ""), "post_context": metadata.get("post_context", ""),
                "post_published_at_raw": metadata.get("post_published_at_raw", ""),
                "post_published_at": metadata.get("post_published_at", ""),
                "verdict": verdict, "reason": reason,
                "metadata_resolved": int(bool(metadata.get("metadata_resolved"))),
                "matched_groups": ",".join(matched_groups(f"{metadata.get('post_title', '')} {metadata.get('post_context', '')}")),
                "co_thanh_pho_khac": int(co_thanh_pho_khac(metadata.get("post_title", ""), metadata.get("post_context", ""))),
                "discovery_source": source.get("label", ""), "discovery_query": source.get("query", ""),
                "state": "accepted" if verdict == "accept" else "rejected",
                "comment_retry_count": int(prior_retries or 0),
                "page_or_group_name": source.get("owner_name") or metadata.get("page_or_group_name", ""),
                "owner_url": metadata.get("owner_url", ""),
                "owner_type": metadata.get("owner_type", ""),
                "crawled_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
            }
            store.upsert_context(context)
            if verdict != "accept":
                continue
            if not _contamination_ok(post_url, comments):
                store.set_context_state("facebook", identity, "quarantined")
                store.kv_set("facebook_status", "quarantined")
                store.log_event("contamination", "facebook", {"url": post_url})
                store.complete_task(task["id"])
                store.kv_set("facebook_seen_posts", list(seen))
                return {"status": "quarantined", "url": post_url}
            records = _to_records(store, post_url, post_context, metadata, comments, run["run_id"], batch_id)
            added = store.add_comments(records)
            captured_count = store.comment_count_for_context("facebook", identity)
            expected_count, missing_count, capture_status = _comment_reconciliation(
                metadata.get("comment_capture", {}), captured_count, max_comments
            )
            retry_count = int(prior_retries or 0)
            context.update({
                "expected_comment_count": expected_count,
                "captured_comment_count": captured_count,
                "missing_comment_count": missing_count,
                "comment_capture_status": capture_status,
                "comment_retry_count": retry_count,
                "state": "comments_done" if capture_status == "complete" else f"comments_{capture_status}",
                "crawled_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
            })
            store.upsert_context(context)
            store.log_event("fb_comment_reconciliation", "facebook", {
                "url": context["source_url"], "expected": expected_count,
                "captured": captured_count, "missing": missing_count,
                "status": capture_status, "retry_count": retry_count,
                "discovered_url": discovered_url, "added": added, "records_count": len(records),
                **metadata.get("comment_capture", {}),
            })
        if failed_urls:
            retry_posts(failed_urls)
            deferred = True
            continue
        if source.get("discovery_complete", True):
            store.complete_task(task["id"])
        else:
            store.checkpoint_task(task["id"], {
                key: value for key, value in source.items() if key not in ("post_urls", "discovery_complete")
            })
            store.defer_task(task["id"], delay=0)
        store.kv_set("facebook_seen_posts", list(seen))
        time.sleep(fb.DELAY_BETWEEN_SOURCES)
