# YouTube crawler

Uses YouTube Data API v3 to search topic queries, fetch video descriptions,
then collect top-level comments and replies.

Queries come from `crawlers/electric_bus/keywords.py`. The search window
is `2026-06-01` through crawl time.

Run through the shared supervisor:

```powershell
python -m crawlers.baseline_runner run --hours 24 --target 5000 --platform youtube
```

Set `YOUTUBE_API_KEY` (and optionally `YOUTUBE_API_KEY_2`, `_3`, ... for key rotation) locally. YouTube API data follows the existing 30-day
retention constraint documented by the project.
