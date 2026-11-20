"""Step 2 of the review pipeline for news: write the URL file for crawl_news.py.

    python crawlers/news/extract_urls_news.py --discovery <discovery csv> --reviewed <reviewed uncertain csv>

Collects every keep=1 URL from the discovery CSV(s) and the reviewed uncertain CSV(s),
removes duplicates and URLs already crawled, and stops if any uncertain row has no 0/1
decision yet. Logic is shared: crawlers/common/review_pipeline.py.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from crawlers.common.review_pipeline import run_extract_urls  # noqa: E402

URL_FIELD = "article_url"
# News URLs are used as-is (crawl_news / load_seen_urls compare raw article URLs).


if __name__ == "__main__":
    sys.exit(run_extract_urls("news", URL_FIELD, None))
