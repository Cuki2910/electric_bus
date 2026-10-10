"""Re-run the current gate over every accepted context; reject + purge comments of failures.

upsert_context refuses accept->reject once a context has comments, so contexts accepted
by an older gate stay forever. Run this after every gate change.

    python -m crawlers.regate            # dry-run
    python -m crawlers.regate --apply    # backup, then write
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

from crawlers.baseline_runner import OUT_DIR
from crawlers.topics import gate_electric_bus_vietnam

DB = os.path.join(OUT_DIR, "crawl.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    bad = []
    for r in c.execute("SELECT platform, context_id, post_title, post_context FROM contexts WHERE verdict='accept'"):
        verdict, reason = gate_electric_bus_vietnam(r["post_title"], r["post_context"])
        if verdict != "accept":
            n = c.execute("SELECT COUNT(*) FROM comments WHERE platform=? AND context_id=?",
                          (r["platform"], r["context_id"])).fetchone()[0]
            bad.append((r["platform"], r["context_id"], reason, n, (r["post_title"] or r["post_context"] or "")[:70].replace("\n", " ")))
    for b in bad:
        print(*b, sep=" | ")
    print(f"{len(bad)} contexts fail, {sum(b[3] for b in bad)} comments", "(dry-run)" if not args.apply else "")
    if not args.apply or not bad:
        return

    backup = f"{DB}.bak_regate_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    dst = sqlite3.connect(backup)
    c.backup(dst)
    dst.close()
    print("backup", backup)

    c.isolation_level = None
    c.execute("BEGIN IMMEDIATE")
    try:
        for platform, cid, reason, _, _ in bad:
            c.execute("DELETE FROM comments WHERE platform=? AND context_id=?", (platform, cid))
            c.execute("UPDATE contexts SET verdict='reject', reason=?, state='rejected' WHERE platform=? AND context_id=?",
                      (reason, platform, cid))
        c.execute("INSERT INTO events (ts, kind, platform, detail) VALUES (?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), "regate", None,
                   f'{{"contexts": {len(bad)}, "comments": {sum(b[3] for b in bad)}}}'))
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    print("applied")


if __name__ == "__main__":
    sys.exit(main())
