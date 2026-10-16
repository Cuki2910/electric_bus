"""Step 1 of the review pipeline for tiktok: collect the uncertain discovery rows into one CSV.

    python crawlers/tiktok/extract_uncertain_tiktok.py --discovery <discovery csv> [<more csvs>]

Open the written CSV, set `keep` to 1 (crawl) or 0 (drop) on every row, then run
extract_urls_tiktok.py. Logic is shared: crawlers/common/review_pipeline.py.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from crawlers.common.review_pipeline import run_extract_uncertain  # noqa: E402

URL_FIELD = "url"
CONTEXT_FIELDS = ['caption', 'hashtags', 'create_time']


if __name__ == "__main__":
    sys.exit(run_extract_uncertain("tiktok", URL_FIELD, CONTEXT_FIELDS))
