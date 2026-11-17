# Electric Bus Crawler

Comment crawler for Vietnam electric-bus content (VinBus, xe buýt điện, ...)
on YouTube and Facebook. Publication window: `2021-01-01` through crawl time
(Vietnam time). The full year 2021 is included to avoid missing launch-period posts.

Split out of the Hanoi flood-transport crawler; this repo contains only the
electric-bus topic.

## Layout

- `crawlers/electric_bus/` -- keyword group (`keywords.py`), search queries
  (`queries.py`), window + per-platform source lists (`config.py`).
- `crawlers/topics.py` -- the gate. `gate_electric_bus_vietnam()` accepts a
  post only if it has an electric-bus term **and** Vietnam evidence
  (Vietnamese text or a VN place/brand). Queries only widen recall; the gate
  alone decides what is kept.
- `crawlers/baseline_runner.py` -- supervisor for YouTube + Facebook (shared
  SQLite store in `data/outputs/baseline_gate_v2/electric_bus/`).

## Setup

Python 3.10+ (tested on 3.12).

```bash
python3.12 -m venv ~/.venvs/electric_bus   # keep the venv off exFAT/external drives
~/.venvs/electric_bus/bin/pip install -r requirements.txt pytest
~/.venvs/electric_bus/bin/patchright install chromium   # Facebook StealthyFetcher
~/.venvs/electric_bus/bin/playwright install chromium   # Facebook
```

Create `.env` (never commit):

```text
YOUTUBE_API_KEY=...
YOUTUBE_API_KEY_2=...   # optional, rotated when the first key's search quota runs out
FB_HANDLE_SALT=...
```

Salts: `python -c "import secrets; print(secrets.token_hex(16))"`. Back them up;
losing a salt breaks handle-hash continuity with existing data.

Facebook login: copy `docs/facebook.env.example` to `.secrets/facebook.env`.

## Run

```bash
python -m crawlers.baseline_runner run --hours 24 --target 5000 --platform all
python -m crawlers.baseline_runner status
python -m crawlers.baseline_runner export
```

`run` and `resume` automatically start the monitor at http://127.0.0.1:8765.
An existing monitor on that port is reused. The automatic monitor runs with the
supervisor; `python -m crawlers.monitor` keeps it running independently.

YouTube API data must be deleted/refreshed within 30 days of `crawled_at`.

## Historical Collection

- YouTube searches each keyword in monthly windows from January 2021, following
  every returned `nextPageToken`. Each page is a durable SQLite task; quota waits
  and new runs retain pending pages and deduplicate videos/comments already stored.
  Invalid page tokens restart their window without deleting collected comments.
- Facebook searches posts separately for each year using the date filter, plus
  public page/group feeds and video search. Discovery has no fixed post/scroll
  cap; comment collection in the supervisor has no fixed comment/scroll cap.
  Scrolling stops after five consecutive rounds without new posts/comments.
  Discovered URLs are checkpointed during scrolling and retained across restarts.
- Versioned history seeds run even when the old June-2026 campaign was already
  seeded. Previously audited Facebook posts rejected as `out_of_window` are
  retried when their dates now qualify. Existing data is not deleted.
- `--target 5000` remains a milestone, not a stopping condition. `--hours` limits
  one run, not historical scope. After its deadline, start another `run` to
  continue pending history tasks; use `resume` only before an active run expires.
- `progress.json` and `final_report.md` include counts by **post publication year**
  and platform, including empty years. Progress/report also lists discovery task
  states so pending or failed work is visible. These are not counts by comment year.
- YouTube gate changes recheck cached rejections once per gate fingerprint.
  Facebook skips processed posts, including cached rejections and direct-post
  retries; incomplete comment counts no longer schedule rereads.
- Facebook retries retain unfinished URLs and use bounded backoff. A closed browser
  is replaced rather than reused across the queue. Explicit photo-page "View post"
  links are followed only to validated Facebook post URLs, then metadata and gate
  are checked again. Empty pages stop retrying when the UI confirms zero comments.
- Facebook reconciliation events distinguish rendered comments, valid timestamps,
  records submitted, and newly stored comments (`added`). A running worker or an
  accepted post alone does not mean the comment count increased.

This is best-effort collection of accessible public content, not proof of 100%
coverage. Deleted/private content, Facebook ranking/date-filter changes,
lazy-loading stalls, YouTube search coverage, quota and authentication can leave
gaps. Review empty years, failed tasks and Facebook comment reconciliation status
before treating a dataset as complete. No live crawl is needed to run the tests.

## Data

Existing Facebook and YouTube data from the parent repo is included in
`data/outputs/baseline_gate_v2/electric_bus/`.

## Tests

```bash
pytest -q
```
