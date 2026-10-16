"""Shared logic for the human-review step between discovery and crawl.

    discover_<platform>.py
      -> extract_uncertain_<platform>.py   (rows the gate was unsure about -> one CSV to review)
      -> [human fills the `keep` column: 1 = crawl it, 0 = drop it]
      -> extract_urls_<platform>.py        (every keep=1 URL -> one plain URL file)
      -> crawl_<platform>.py --url-file <that file>

The per-platform scripts are thin wrappers (URL column name, context columns, URL
canonicalizer); everything else lives here so it is fixed in one place.

Rules, in order of importance:

- Only ``keep == "1"`` is crawled. Blank or ``0`` never is.
- ``gate_verdict == "uncertain"`` rows have a blank ``keep`` in the discovery CSV. They are
  decided exclusively by the reviewed file; if any of them is missing from it, or its
  ``keep`` is still blank, extraction stops (``UnreviewedError``) rather than silently
  dropping them.
- URLs are de-duplicated after canonicalization, and URLs already present in earlier crawl
  JSONL output (``source_url``) are skipped unless asked otherwise.
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from crawlers.common.records import batch_id, now_hcm  # noqa: E402

REVIEW_TRAILER = ["gate_rule", "discovery_query", "source_file", "keep"]


class UnreviewedError(Exception):
    """Uncertain rows exist that a human has not marked keep=1 or keep=0."""


def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _clean(value):
    return (value or "").strip()


# --------------------------------------------------------------------------
# Step 1 -- extract_uncertain
# --------------------------------------------------------------------------

def write_uncertain_csv(discovery_paths, output_path, platform, url_field, context_fields):
    """Write every ``uncertain`` discovery row (de-duplicated by URL) to one review CSV with
    a blank ``keep`` column. Returns the number of rows written."""
    fieldnames = ["platform", url_field, *context_fields, *REVIEW_TRAILER]
    seen, out_rows = set(), []
    for path in discovery_paths:
        for row in read_rows(path):
            if _clean(row.get("gate_verdict")) != "uncertain":
                continue
            url = _clean(row.get(url_field))
            if not url or url in seen:
                continue
            seen.add(url)
            out = {"platform": platform, url_field: url, "source_file": os.path.basename(path), "keep": ""}
            for field in context_fields:
                out[field] = row.get(field, "")
            out["gate_rule"] = row.get("gate_rule", "")
            out["discovery_query"] = row.get("discovery_query", "")
            out_rows.append(out)

    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)
    return len(out_rows)


# --------------------------------------------------------------------------
# Step 2 -- extract_urls
# --------------------------------------------------------------------------

def collect_keep_urls(discovery_paths, reviewed_paths, url_field, canonical=None):
    """URLs with keep=1 from the discovery CSVs plus the reviewed uncertain CSVs.

    Raises UnreviewedError if any uncertain discovery row has no 0/1 decision in the
    reviewed files. Returns (urls, stats) with urls in first-seen order, de-duplicated.
    """
    canon = canonical or (lambda url: url)

    decisions, unreviewed = {}, []
    for path in reviewed_paths:
        for row in read_rows(path):
            url = canon(_clean(row.get(url_field)))
            if not url:
                continue
            keep = _clean(row.get("keep"))
            if keep in ("0", "1"):
                decisions[url] = keep
            else:
                unreviewed.append(url)

    from_discovery, uncertain_total = [], 0
    for path in discovery_paths:
        for row in read_rows(path):
            url = canon(_clean(row.get(url_field)))
            if not url:
                continue
            if _clean(row.get("gate_verdict")) == "uncertain":
                uncertain_total += 1
                if url not in decisions:
                    unreviewed.append(url)
                continue
            if _clean(row.get("keep")) == "1":
                from_discovery.append(url)

    if unreviewed:
        raise UnreviewedError(sorted(set(unreviewed)))

    from_reviewed = [url for url, keep in decisions.items() if keep == "1"]
    candidates = from_discovery + from_reviewed
    urls = list(dict.fromkeys(candidates))
    stats = {
        "from_discovery": len(from_discovery),
        "from_reviewed": len(from_reviewed),
        "uncertain_in_discovery": uncertain_total,
        "duplicates_removed": len(candidates) - len(urls),
    }
    return urls, stats


def load_crawled_urls(output_dir, platform, canonical=None):
    """``source_url`` of every record in earlier ``<platform>_*.jsonl`` crawl output."""
    canon = canonical or (lambda url: url)
    seen = set()
    if not os.path.isdir(output_dir):
        return seen
    for name in os.listdir(output_dir):
        if not (name.startswith(f"{platform}_") and name.endswith(".jsonl")):
            continue
        try:
            with open(os.path.join(output_dir, name), encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        url = json.loads(line).get("source_url")
                    except json.JSONDecodeError:
                        continue
                    if url:
                        seen.add(canon(url.strip()))
        except OSError:
            continue
    return seen


def write_url_file(urls, output_path):
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        handle.write("".join(f"{url}\n" for url in urls))


# --------------------------------------------------------------------------
# CLI drivers shared by the per-platform scripts
# --------------------------------------------------------------------------

def run_extract_uncertain(platform, url_field, context_fields, argv=None):
    parser = argparse.ArgumentParser(
        description=f"Collect the uncertain {platform} discovery rows into one CSV for manual review. "
                    "Fill the `keep` column (1 = crawl, 0 = drop), then run extract_urls_" + platform + ".py.")
    parser.add_argument("--discovery", nargs="+", required=True, help="discovery CSV(s) from discover_" + platform + ".py")
    parser.add_argument("--output", default=None,
                        help=f"review CSV to write (default: data/outputs/{platform}/uncertain_<timestamp>.csv)")
    args = parser.parse_args(argv)

    output = args.output or os.path.join(
        "data", "outputs", platform, batch_id(f"uncertain_{platform}_electric_bus", now_hcm()) + ".csv")
    count = write_uncertain_csv(args.discovery, output, platform, url_field, context_fields)
    print(f"uncertain rows written: {count} -> {output}")
    if count:
        print("Next: open that CSV, set `keep` to 1 (crawl) or 0 (drop) on every row, save,")
        print(f"then run extract_urls_{platform}.py with --reviewed <that file>.")
    else:
        print(f"Nothing to review. Run extract_urls_{platform}.py directly.")
    return 0


def run_extract_urls(platform, url_field, canonical, argv=None):
    parser = argparse.ArgumentParser(
        description=f"Write the {platform} URL file (every keep=1 URL) for crawl_{platform}.py --url-file.")
    parser.add_argument("--discovery", nargs="+", required=True, help="discovery CSV(s) from discover_" + platform + ".py")
    parser.add_argument("--reviewed", nargs="*", default=[],
                        help="reviewed uncertain CSV(s) from extract_uncertain_" + platform + ".py")
    parser.add_argument("--output", default=None,
                        help=f"URL file to write (default: data/inputs/{platform}/urls_electric_bus_<timestamp>.txt)")
    parser.add_argument("--crawled-dir", default=os.path.join("data", "outputs", platform),
                        help="folder with earlier crawl JSONL output; its URLs are skipped")
    parser.add_argument("--include-crawled", action="store_true",
                        help="do not skip URLs that were already crawled")
    args = parser.parse_args(argv)

    try:
        urls, stats = collect_keep_urls(args.discovery, args.reviewed, url_field, canonical)
    except UnreviewedError as exc:
        unreviewed = exc.args[0]
        print(f"STOP: {len(unreviewed)} uncertain URL(s) have no keep=0/1 decision yet:", file=sys.stderr)
        for url in unreviewed[:10]:
            print(f"  {url}", file=sys.stderr)
        if len(unreviewed) > 10:
            print(f"  ... and {len(unreviewed) - 10} more", file=sys.stderr)
        print(f"Fill `keep` in the reviewed CSV (and pass it with --reviewed), then re-run.", file=sys.stderr)
        return 2

    skipped = 0
    if not args.include_crawled:
        crawled = load_crawled_urls(args.crawled_dir, platform, canonical)
        kept = [url for url in urls if url not in crawled]
        skipped = len(urls) - len(kept)
        urls = kept

    output = args.output or os.path.join(
        "data", "inputs", platform, batch_id("urls_electric_bus", now_hcm()) + ".txt")
    write_url_file(urls, output)
    print(f"keep=1 from discovery: {stats['from_discovery']} | keep=1 from review: {stats['from_reviewed']} | "
          f"duplicates removed: {stats['duplicates_removed']} | already crawled skipped: {skipped}")
    print(f"urls written: {len(urls)} -> {output}")
    if not urls:
        print("WARNING: the URL file is empty.", file=sys.stderr)
    print(f"Next: python crawlers/{platform}/crawl_{platform}.py --url-file {output}")
    return 0
