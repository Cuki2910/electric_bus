"""Step 2 of the review pipeline for threads: write the URL file for crawl_threads.py.

    python crawlers/threads/extract_urls_threads.py --discovery <discovery csv> --reviewed <reviewed uncertain csv>

Collects every keep=1 URL from the discovery CSV(s) and the reviewed uncertain CSV(s),
removes duplicates and URLs already crawled, and stops if any uncertain row has no 0/1
decision yet. Logic is shared: crawlers/common/review_pipeline.py.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from crawlers.common.review_pipeline import run_extract_urls  # noqa: E402
from crawlers.threads.crawl_threads import canonical_post_url  # noqa: E402

URL_FIELD = "url"


if __name__ == "__main__":
    sys.exit(run_extract_urls("threads", URL_FIELD, canonical_post_url))
