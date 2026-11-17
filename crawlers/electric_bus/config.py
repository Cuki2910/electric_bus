"""Topic configuration shared by the YouTube and Facebook crawlers."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from crawlers.electric_bus.keywords import literal_terms

HCM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
UTC = timezone.utc
SOURCES_VERSION = "electric_bus_history_v1_2021"

ELECTRIC_BUS_QUERIES = tuple(literal_terms("electric_bus"))

def _parse_timestamp(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Facebook's absolute tooltip timestamps omit the offset but are local HCM time.
        parsed = parsed.replace(tzinfo=HCM_TZ)
    return parsed.astimezone(HCM_TZ)

def window(now=None):
    """Publication window: all of 2021 through now (Vietnam time)."""
    now = now or datetime.now(HCM_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=HCM_TZ)
    return datetime(2021, 1, 1, tzinfo=HCM_TZ), now.astimezone(HCM_TZ)

def post_in_window(value, now=None):
    published = _parse_timestamp(value)
    if published is None:
        return False
    start, end = window(now)
    return start <= published < end

def youtube_window(now=None):
    """Exclusive UTC API bounds for YouTube publishedAfter/Before."""
    start, end = window(now)
    return (
        start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    )

def youtube_search_specs(now=None):
    start, end = window(now)
    while start < end:
        next_month = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        before = min(next_month, end)
        for query in ELECTRIC_BUS_QUERIES:
            yield {
                "keyword": query,
                "after": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "before": before.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "topic": "electric_bus",
            }
        start = next_month

# Known-VN public pages: crawled by feed, not search, so the VN gate is near-free.
# Append more page URLs here (VinFast, Hanoi/HCMC bus operators, EV communities).
FACEBOOK_PAGES = (
    ("vinbus_official", "https://www.facebook.com/vinbus.official/"),
)

# Public VinBus groups (measured 2026-10-10: 55/75 posts pass the gate, vs 4/100 for
# general Hanoi/HCMC bus groups, which are deliberately not listed). Group name is carried
# on the source because the post HTML does not expose it.
FACEBOOK_GROUPS = (
    ("vinbus_group_official", "https://www.facebook.com/groups/vinbus.official/", "VinBus"),
    ("vinbus_group_vietnam", "https://www.facebook.com/groups/vinbusvietnam/", "VinBus Việt Nam"),
    ("vinbus_group_e01_e08", "https://www.facebook.com/groups/1458515971322273/", "Vinbus E01-E02-E03-E08"),
)

def facebook_sources():
    sources = [
        {
            "kind": "public_page",
            "label": f"electric_bus_page_{name}",
            "source_class": "electric_bus_page",
            "url": url,
            "max_posts": None,
        }
        for name, url in FACEBOOK_PAGES
    ]
    sources += [
        {
            "kind": "public_page",
            "label": f"electric_bus_{name}",
            "source_class": "electric_bus_group",
            "url": url,
            "max_posts": None,
            "owner_name": title,
        }
        for name, url, title in FACEBOOK_GROUPS
    ]
    for index, query in enumerate(ELECTRIC_BUS_QUERIES, 1):
        for kind, tag in (("search", "post"), ("video_search", "video")):
            sources.append({
                "kind": kind,
                "label": f"electric_bus_{tag}_{index:02d}",
                "source_class": "electric_bus_search",
                "query": query,
                "max_posts": None,
            })
    return sources

def facebook_history_sources(now=None):
    start, end = window(now)
    for source in facebook_sources():
        if source["kind"] in ("public_page", "video_search"):
            yield source
            continue
        for year in range(start.year, end.year + 1):
            yield {**source, "label": f"{source['label']}_{year}", "year": year}
