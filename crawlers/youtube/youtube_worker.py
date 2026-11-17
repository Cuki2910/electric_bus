"""YouTube worker cho baseline_gate_v2.

Discovery = (keyword x monthly window). Gate title+description. Video accept lấy
top-level comment + toàn bộ reply. Checkpoint cursor/quota vào SQLite để resume.

Retention: dữ liệu comment API phải xóa/refresh trong 30 ngày kể từ crawled_at.
"""
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from crawlers.youtube import crawl_youtube as yt
from crawlers.electric_bus.config import HCM_TZ, SOURCES_VERSION, post_in_window, youtube_search_specs
from crawlers.topics import GATE_VERSION, co_thanh_pho_khac, gate_electric_bus_vietnam, matched_groups

def _search_specs(now=None):
    return list(youtube_search_specs(now))


def seed_queue(store):
    """Seed monthly history independently of legacy campaign flags."""
    store.conn.execute(
        "UPDATE discovery_queue SET status='skipped', lease_at=0 "
        "WHERE platform='youtube' AND status='pending' AND source='refresh_comments'"
    )
    key = f"youtube_seeded:{SOURCES_VERSION}"
    if not store.kv_get(key):
        for payload in _search_specs():
            store.enqueue("youtube", "search", payload, priority=100,
                          dedup_key=f"youtube:{SOURCES_VERSION}:{payload['keyword']}:{payload['after']}")
        store.kv_set(key, True)
    gate_key = f"youtube_gate_rechecked:{GATE_VERSION}"
    if not store.kv_get(gate_key):
        accepted = 0
        for row in store.conn.execute("SELECT * FROM contexts WHERE platform='youtube' AND verdict='reject'"):
            context = dict(row)
            verdict, reason = gate_electric_bus_vietnam(context.get("post_title"), context.get("post_context"))
            if verdict == "accept" and post_in_window(context.get("post_published_at")):
                context.update(verdict=verdict, reason=reason, state="accepted", comment_retry_count=0)
                store.upsert_context(context)
                accepted += 1
        store.kv_set(gate_key, True)
        store.log_event("youtube_gate_recheck_seeded", "youtube", {"gate_version": GATE_VERSION, "accepted": accepted})


def _revisit_current_year(store):
    """Nạp lại cửa sổ năm hiện tại để bắt video mới đăng trong lúc job đang chạy.

    Seed ban đầu là snapshot lịch sử cố định; sau khi done, không còn task nào
    tự sinh ra để phát hiện video mới. dedup_key theo giờ (stamp) tránh spam
    quota search.list khi hàng đợi rỗng được kiểm tra lại mỗi vài phút.
    """
    now = datetime.now(timezone.utc)
    month_start = now.astimezone(HCM_TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    after = month_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    # Hourly revisit of all keywords burned ~2700 quota units/hour (27 searches x 100) for
    # ~0-1 new accepted videos per hour (measured from contexts), exhausting the daily
    # quota in under 4 hours. Bucket by REVISIT_HOURS and use only distinct queries.
    stamp = f"{now:%Y%m%d}{now.hour // REVISIT_HOURS:02d}"
    for payload in _search_specs(now):
        if payload["keyword"] not in REVISIT_KEYWORDS:
            continue
        if payload["after"] != after:
            continue
        store.enqueue("youtube", "search", payload, priority=50,
                      dedup_key=f"youtube:revisit:{payload['topic']}:{payload['keyword']}:{stamp}")


REVISIT_HOURS = 6
# One term per spelling family; the gate, not the query, decides what is kept.
REVISIT_KEYWORDS = frozenset({
    "xe buýt điện", "xe bus điện", "bus điện", "electric bus", "VinBus", "xe điện công cộng",
})

def _record(video, snippet, comment_id, comment_type, parent_id, thread_id, depth, run_id, batch_id):
    title = video.get("title", "")
    desc = video.get("description", "")
    text = snippet.get("textDisplay", "")
    return {
        "platform": "youtube",
        "comment_id": comment_id,
        "context_id": video["video_id"],
        "id": f"youtube_c_{comment_id}",
        "source_url": f"https://www.youtube.com/watch?v={video['video_id']}",
        "post_context": f"{title}\n{desc}".strip(),
        "post_title": title,
        "post_published_at_raw": video.get("published_at", ""),
        "post_published_at": video.get("published_at", ""),
        "comment_text": text,
        "comment_type": comment_type,
        "parent_comment_id": parent_id,
        "thread_id": thread_id,
        "reply_depth": depth,
        "posted_at_raw": snippet.get("publishedAt", ""),
        "posted_at": snippet.get("publishedAt", ""),
        "likes_count": snippet.get("likeCount", 0),
        "author_key": snippet.get("authorChannelId", {}).get("value") if isinstance(snippet.get("authorChannelId"), dict) else None,
        "matched_groups": ",".join(matched_groups(text)),
        "co_thanh_pho_khac": int(co_thanh_pho_khac(title, desc)),
        "parent_unresolved": 0,
        "crawl_batch_id": batch_id,
        "run_id": run_id,
        "crawled_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
    }


def _crawl_video(store, video, run_id, batch_id, deadline_check=None):
    """Top-level + toàn bộ replies. Trả về số comment mới thêm vào store."""
    added = 0
    for thread in yt.fetch_comments(video["video_id"]):
        if deadline_check and deadline_check():
            raise InterruptedError("Crawl deadline reached")
        top = thread["snippet"]["topLevelComment"]
        top_id = top["id"]
        batch = [_record(video, top["snippet"], top_id, "top_level", None, top_id, 0, run_id, batch_id)]
        if thread["snippet"].get("totalReplyCount", 0):
            for reply in yt.fetch_replies(top_id):
                if deadline_check and deadline_check():
                    raise InterruptedError("Crawl deadline reached")
                batch.append(_record(video, reply["snippet"], reply["id"], "reply", top_id, top_id, 1, run_id, batch_id))
        added += store.add_comments(batch)
    return added


def _rate_limit_wait(store, task_id=None):
    failures = min(int(store.kv_get("youtube_rate_limit_failures", 0)) + 1, 3)
    delay = (900, 1800, 3600)[failures - 1]
    store.kv_set("youtube_rate_limit_failures", failures)
    if task_id is not None:
        store.defer_task(task_id, delay=delay)
    store.kv_set("youtube_status", "rate_limit_wait")
    store.log_event("rate_limit", "youtube", {"retry_seconds": delay})
    return {"status": "rate_limit_wait", "retry_seconds": delay}


def _drain_accepted_contexts(store, run, deadline_check, batch_id, processed_videos):
    rows = store.conn.execute(
        """SELECT context_id, post_title, post_context, post_published_at
           FROM contexts WHERE platform='youtube' AND state='accepted'
           ORDER BY crawled_at"""
    ).fetchall()
    for row in rows:
        if deadline_check():
            return {"status": "deadline"}
        title = row["post_title"] or ""
        context = row["post_context"] or ""
        prefix = f"{title}\n"
        video = {
            "video_id": row["context_id"],
            "title": title,
            "description": context[len(prefix):] if context.startswith(prefix) else context,
            "published_at": row["post_published_at"] or "",
        }
        try:
            _crawl_video(store, video, run["run_id"], batch_id, deadline_check)
        except yt.YouTubeAPIError as e:
            if e.reason == "quotaExceeded":
                store.kv_set("youtube_status", "quota_wait")
                store.log_event("quota", "youtube", {"endpoint": "comments"})
                return {"status": "quota_wait"}
            if e.reason == "rateLimitExceeded":
                return _rate_limit_wait(store)
            terminal_states = {
                "commentsDisabled": "comments_disabled",
                "videoNotFound": "video_not_found",
                "commentNotFound": "comment_not_found",
            }
            if e.reason in terminal_states:
                store.set_context_state("youtube", video["video_id"], terminal_states[e.reason])
                processed_videos.add(video["video_id"])
                continue
            store.log_event("yt_comment_error", "youtube", {"video": video["video_id"], "reason": e.reason})
            return {"status": "retry_wait", "retry_seconds": 60}
        except InterruptedError:
            return {"status": "deadline"}
        except OSError as exc:
            store.log_event("yt_comment_error", "youtube", {"video": video["video_id"], "reason": repr(exc)[:200]})
            return {"status": "retry_wait", "retry_seconds": 60}
        store.set_context_state("youtube", video["video_id"], "comments_done")
        processed_videos.add(video["video_id"])
        store.kv_set("youtube_processed_videos", list(processed_videos))
        store.kv_set("youtube_rate_limit_failures", 0)
    return None


def run_once(store, run, deadline_check, batch_id):
    """Xử lý các task search cho tới khi hết queue, hết giờ, hoặc hết quota.

    Trả về dict trạng thái để supervisor báo cáo.
    """
    yt.reset_counters()
    seed_queue(store)
    store.kv_set("youtube_status", "running")
    store.kv_set("youtube_revisited_this_pass", False)
    processed_videos = set(store.kv_get("youtube_processed_videos", []))
    backlog_result = _drain_accepted_contexts(store, run, deadline_check, batch_id, processed_videos)
    if backlog_result:
        return backlog_result
    while True:
        if deadline_check():
            store.kv_set("youtube_status", "deadline")
            return {"status": "deadline"}
        task = store.claim("youtube")
        if task is None:
            if not store.kv_get("youtube_revisited_this_pass"):
                _revisit_current_year(store)
                store.kv_set("youtube_revisited_this_pass", True)
                continue
            store.kv_set("youtube_status", "queue_empty")
            return {"status": "queue_empty"}
        store.kv_set("youtube_revisited_this_pass", False)

        payload = task["payload"]
        if task["source"] == "refresh_comments":
            store.complete_task(task["id"], "skipped")
            continue

        try:
            videos, next_page = yt.search_video_page(
                payload["keyword"], payload["after"], payload["before"],
                page_token=payload.get("page_token"),
            )
        except yt.YouTubeAPIError as e:
            if e.reason == "quotaExceeded":
                store.defer_task(task["id"], delay=0)  # trả lại queue, chưa xử lý.
                store.kv_set("youtube_status", "quota_wait")
                store.log_event("quota", "youtube", {"endpoint": "search"})
                return {"status": "quota_wait"}
            if e.reason == "rateLimitExceeded":
                return _rate_limit_wait(store, task["id"])
            if e.reason == "invalidPageToken" and payload.get("page_token"):
                payload = {key: value for key, value in payload.items() if key != "page_token"}
                payload["page_restart"] = payload.get("page_restart", 0) + 1
                store.checkpoint_task(task["id"], payload)
            store.retry_task(task["id"], delay=120)
            continue
        except OSError:
            store.retry_task(task["id"], delay=60)
            continue

        store.kv_set("youtube_rate_limit_failures", 0)
        new_ids = [v["video_id"] for v in videos if v["video_id"] not in processed_videos]
        full_descriptions = {}
        for offset in range(0, len(new_ids), 50):
            try:
                full_descriptions.update(yt.fetch_video_descriptions(new_ids[offset:offset + 50]))
            except yt.YouTubeAPIError as e:
                if e.reason == "quotaExceeded":
                    store.kv_set("youtube_processed_videos", list(processed_videos))
                    store.defer_task(task["id"], delay=0)
                    store.kv_set("youtube_status", "quota_wait")
                    return {"status": "quota_wait"}
                if e.reason == "rateLimitExceeded":
                    store.kv_set("youtube_processed_videos", list(processed_videos))
                    return _rate_limit_wait(store, task["id"])
                store.log_event("yt_desc_error", "youtube", {"reason": e.reason})
                store.retry_task(task["id"], delay=120)
                return {"status": "retry_wait", "retry_seconds": 120}
            except OSError:
                store.retry_task(task["id"], delay=60)
                return {"status": "retry_wait", "retry_seconds": 60}
        for video in videos:
            if deadline_check():
                store.kv_set("youtube_processed_videos", list(processed_videos))
                store.defer_task(task["id"], delay=0)
                return {"status": "deadline"}
            if not post_in_window(video.get("published_at")):
                continue
            vid = video["video_id"]
            if vid in processed_videos:
                continue
            if vid in full_descriptions:
                video["description"] = full_descriptions[vid]
            verdict, reason = gate_electric_bus_vietnam(video.get("title", ""), video.get("description", ""))
            store.upsert_context({
                "platform": "youtube", "context_id": vid,
                "source_url": f"https://www.youtube.com/watch?v={vid}",
                "post_title": video.get("title", ""), "post_context": f"{video.get('title','')}\n{video.get('description','')}".strip(),
                "post_published_at_raw": video.get("published_at", ""), "post_published_at": video.get("published_at", ""),
                "verdict": verdict, "reason": reason, "metadata_resolved": 1,
                "matched_groups": ",".join(matched_groups(video.get("title", "") + " " + video.get("description", ""))),
                "co_thanh_pho_khac": int(co_thanh_pho_khac(video.get("title", ""), video.get("description", ""))),
                "discovery_source": "search", "discovery_query": payload["keyword"],
                "state": "accepted" if verdict == "accept" else "rejected",
                "crawled_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
            })
            if verdict != "accept":
                processed_videos.add(vid)
                continue
            try:
                _crawl_video(store, video, run["run_id"], batch_id, deadline_check)
                store.set_context_state("youtube", vid, "comments_done")
                processed_videos.add(vid)
            except yt.YouTubeAPIError as e:
                if e.reason == "quotaExceeded":
                    store.kv_set("youtube_processed_videos", list(processed_videos))
                    store.defer_task(task["id"], delay=0)
                    store.kv_set("youtube_status", "quota_wait")
                    return {"status": "quota_wait"}
                if e.reason == "rateLimitExceeded":
                    store.kv_set("youtube_processed_videos", list(processed_videos))
                    return _rate_limit_wait(store, task["id"])
                if e.reason not in ("commentsDisabled", "videoNotFound", "commentNotFound"):
                    store.log_event("yt_comment_error", "youtube", {"video": vid, "reason": e.reason})
                    store.kv_set("youtube_processed_videos", list(processed_videos))
                    store.retry_task(task["id"], delay=120)
                    return {"status": "retry_wait", "retry_seconds": 120}
                store.set_context_state("youtube", vid, {
                    "commentsDisabled": "comments_disabled", "videoNotFound": "video_not_found",
                    "commentNotFound": "comment_not_found",
                }[e.reason])
                processed_videos.add(vid)
            except InterruptedError:
                store.kv_set("youtube_processed_videos", list(processed_videos))
                store.defer_task(task["id"], delay=0)
                return {"status": "deadline"}
            except OSError:
                store.kv_set("youtube_processed_videos", list(processed_videos))
                store.retry_task(task["id"], delay=60)
                return {"status": "retry_wait", "retry_seconds": 60}

        if next_page:
            if next_page == payload.get("page_token"):
                store.retry_task(task["id"], delay=120)
                continue
            store.enqueue("youtube", "search", {**payload, "page_token": next_page},
                          priority=task["priority"])
        store.complete_task(task["id"])
        store.kv_set("youtube_processed_videos", list(processed_videos))
