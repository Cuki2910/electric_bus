"""Step 2 of the review pipeline for tiktok: write the URL file for crawl_tiktok.py.

    python crawlers/tiktok/extract_urls_tiktok.py --discovery <discovery csv> --reviewed <reviewed uncertain csv>

Collects every keep=1 URL from the discovery CSV(s) and the reviewed uncertain CSV(s),
removes duplicates and URLs already crawled, and stops if any uncertain row has no 0/1
decision yet. Logic is shared: crawlers/common/review_pipeline.py.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from crawlers.common.review_pipeline import run_extract_urls  # noqa: E402
from crawlers.tiktok.crawl_tiktok import canonical_video_url  # noqa: E402

URL_FIELD = "url"


if __name__ == "__main__":
    sys.exit(run_extract_urls("tiktok", URL_FIELD, canonical_video_url))
