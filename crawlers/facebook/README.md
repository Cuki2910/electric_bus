# Facebook crawler

Collects comments from public Facebook posts and Watch videos matching the
Vietnam electric-bus topic.

Sources are generated from `crawlers/electric_bus/keywords.py`. The post gate runs on
metadata before comment capture. Publication dates outside the configured
window (2021-01-01 .. now, Vietnam time) are rejected before comments are fetched.
Historical post searches use one date-filtered source per year, alongside page,
group and video discovery. The supervisor does not cap posts or comments and
scrolls until five consecutive rounds yield nothing new. Discovered post URLs
are checkpointed in SQLite. Facebook ranking and lazy loading can still leave gaps.

Run through the shared supervisor:

```powershell
python -m crawlers.baseline_runner run --hours 24 --target 5000 --platform facebook
```

Facebook requires a logged-in browser session and `FB_HANDLE_SALT`.

Failed tasks retain unfinished URLs and wait 120 seconds in the queue while the
worker continues other ready tasks. Each task has at most five attempts; failed
tasks remain recorded for review. Browser navigation times out after 30 seconds
without nested browser retries. Unresolved post scopes are checked again briefly
after rendering, but are never replaced with an unverified page-wide scope.
Facebook's alternate comment permalinks are accepted only inside a visible dialog
containing the requested post permalink and matching post body, with one unique
comment context. Background-feed comments are excluded from that dialog scope.
Processed posts are not read again, including direct-post recovery tasks and
previously rejected posts. SQLite capture states and stored comments supplement
the seen cache after restart. Comment coverage is still reported, but incomplete
or unverified counts do not schedule another capture. Pending reconciliation
tasks are marked skipped without deleting data. Failed, unfinished captures
retain bounded retries; source discovery can continue looking for unseen posts.
Browser fetches wait for DOM content and required selectors before discovery,
not the full-page load event, which can stall on unrelated media. The existing
authenticated browser context and post/authentication guards are retained.
