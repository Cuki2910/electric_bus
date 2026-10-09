# Electric Bus Crawler

Comment crawler for Vietnam electric-bus content (VinBus, xe buýt điện, ...)
on YouTube, Facebook and Threads. Publication window: `2026-08-01` through
crawl time.

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
- `crawlers/threads/` -- discover -> human review (`keep` column) -> crawl.

## Setup

Python 3.10+ (tested on 3.12).

```bash
python3.12 -m venv ~/.venvs/electric_bus   # keep the venv off exFAT/external drives
~/.venvs/electric_bus/bin/pip install -r requirements.txt pytest
~/.venvs/electric_bus/bin/patchright install chromium   # Threads
~/.venvs/electric_bus/bin/playwright install chromium   # Facebook
```

Create `.env` (never commit):

```text
YOUTUBE_API_KEY=...
YOUTUBE_API_KEY_2=...   # optional, rotated when the first key's search quota runs out
FB_HANDLE_SALT=...
THREADS_HANDLE_SALT=...
```

Salts: `python -c "import secrets; print(secrets.token_hex(16))"`. Back them up;
losing a salt breaks handle-hash continuity with existing data.

Facebook login: copy `docs/facebook.env.example` to `.secrets/facebook.env`.
Threads needs a one-time manual login: `python -m crawlers.threads.threads_session`.

## Run

```bash
python -m crawlers.baseline_runner run --hours 24 --target 5000 --platform all
python -m crawlers.baseline_runner status
python -m crawlers.baseline_runner export

python crawlers/threads/discover_threads.py --headful --since 2026-08-01 --user-data-dir data/outputs/threads/.browser_profile
python crawlers/threads/crawl_threads.py --url-file <reviewed url list> --headful --user-data-dir data/outputs/threads/.browser_profile --max-posts <n>
```

Threads discovery CSVs have a `keep` column (pre-gated); export the `keep=1`
`url` values to a plain list for `--url-file`. There is no committed script
for that step:

```bash
python -c "import csv; print('\n'.join(r['url'] for r in csv.DictReader(open('<discovery csv>', encoding='utf-8')) if r['keep']=='1'))" > urls.txt
```

YouTube API data must be deleted/refreshed within 30 days of `crawled_at`.

## Data

Existing data from the parent repo is included: `data/outputs/baseline_gate_v2/electric_bus/`
(Facebook + YouTube) and `data/outputs/threads/` (Threads).

## Tests

```bash
pytest -q
```
