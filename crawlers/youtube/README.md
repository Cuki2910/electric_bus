# YouTube crawler

Uses YouTube Data API v3 to search topic queries, fetch video descriptions,
then collect top-level comments and replies.

Queries come from `crawlers/electric_bus/keywords.py`. The search window
is `2021-01-01` through crawl time (Vietnam time), split into monthly windows.
The worker follows every search page and checkpoints page tokens in SQLite.
Quota waits and new runs preserve pending pages; comments remain deduplicated.
Completed videos are not automatically reread to reconcile API comment counts.
Legacy comment-refresh tasks are marked skipped without deleting stored data;
unfinished accepted videos retain their existing checkpoints and retries.
API requests have a 30-second network timeout instead of waiting indefinitely.

Run through the shared supervisor:

```powershell
python -m crawlers.baseline_runner run --hours 24 --target 5000 --platform youtube
```

Set `YOUTUBE_API_KEY` (and optionally `YOUTUBE_API_KEY_2`, `_3`, ... for key rotation) locally. YouTube API data follows the existing 30-day
retention constraint documented by the project.
