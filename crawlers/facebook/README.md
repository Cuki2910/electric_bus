# Facebook crawler

Collects comments from public Facebook posts and Watch videos matching the
Vietnam electric-bus topic.

Sources are generated from `crawlers/electric_bus/keywords.py`. The post gate runs on
metadata before comment capture. Publication dates outside the configured
window (2026-08-01 .. now) are rejected before comments are fetched.

Run through the shared supervisor:

```powershell
python -m crawlers.baseline_runner run --hours 24 --target 5000 --platform facebook
```

Facebook requires a logged-in browser session and `FB_HANDLE_SALT`.
