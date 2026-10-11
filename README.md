# Electric Bus Crawler

Comment crawler for Vietnam electric-bus content (VinBus, xe buýt điện, ...)
on YouTube and Facebook. Publication window: `2026-06-01` through crawl time.

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

## Data

Existing Facebook and YouTube data from the parent repo is included in
`data/outputs/baseline_gate_v2/electric_bus/`.

## Tests

```bash
pytest -q
```
