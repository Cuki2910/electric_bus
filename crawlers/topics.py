"""Topic gate for Vietnam electric-bus content."""

import re
import unicodedata

from crawlers.electric_bus.keywords import GROUPS

MATCH_GROUPS = ("electric_bus",)

def _norm(text):
    return unicodedata.normalize("NFC", text or "")

def _to_regex(entry):
    pattern = entry["term"]
    return pattern if entry["type"] == "regex" else r"(?<!\w)" + re.escape(pattern) + r"(?!\w)"

GROUP_RX = {
    group: re.compile("|".join(_to_regex(entry) for entry in GROUPS[group]), re.IGNORECASE)
    for group in MATCH_GROUPS
}
_OTHER_CITY = re.compile(
    r"hồ chí minh|tp\.? ?hcm|sài gòn|đà nẵng|da nang|hải phòng|hai phong|"
    r"cần thơ|can tho|nha trang|huế|hạ long|đà lạt|da lat|hà nam|ha nam",
    re.IGNORECASE,
)

def matched_groups(text):
    text = _norm(text)
    return [group for group in MATCH_GROUPS if GROUP_RX[group].search(text)]

def co_thanh_pho_khac(title="", body=""):
    """Flag explicit non-Hanoi city mentions for downstream review."""
    return bool(_OTHER_CITY.search(f"{_norm(title)} {_norm(body)}"))

def gate_electric_bus(title="", body=""):
    """Accept posts with an approved electric-bus term."""
    blob = f"{_norm(title)} {_norm(body)}"
    if not GROUP_RX["electric_bus"].search(blob):
        return "reject", "missing_group:electric_bus"
    return "accept", "electric_bus"

_VN_EVIDENCE = re.compile(
    r"[ăắằẳẵặấầẩẫậềếểễệồốổỗộơờớởỡợưừứửữựđảạẻẹẽỉịỏọủụỷỵỹ]|"
    r"(?<!\w)(?:vietnam|viet nam|hanoi|ha noi|saigon|ho chi minh|vinbus|vinfast|vingroup|"
    r"xanh sm|tp\.? ?hcm|da nang|"
    # Vietnamese typed without diacritics (queries include "xe buyt dien", "bus dien").
    r"xe bus|xe buyt|buyt dien|bus dien|tuyen|khong|duoc|cua|nhung|nhieu|"
    r"sai gon|ha noi|ben xe|tram dung)(?!\w)",
    re.IGNORECASE,
)

def gate_electric_bus_vietnam(title="", body=""):
    """Electric-bus gate plus Vietnam evidence (Vietnamese text or VN place/brand)."""
    verdict, reason = gate_electric_bus(title, body)
    if verdict != "accept":
        return verdict, reason
    if not _VN_EVIDENCE.search(f"{_norm(title)} {_norm(body)}"):
        return "reject", "not_vietnam"
    return verdict, reason


def is_on_topic_threads(post_text, hashtags=None, source=None):
    """Threads wrapper: post text + a plain space-joined hashtag blob.
    ``source`` accepted for call-site compatibility, not consulted."""
    hashtag_blob = " ".join((hashtags or []))
    return gate_electric_bus_vietnam(post_text or "", hashtag_blob)


def is_on_topic_tiktok(caption, hashtags, source=None):
    """TikTok wrapper: caption + a plain space-joined hashtag blob.
    ``source`` accepted for call-site compatibility, not consulted."""
    hashtag_blob = " ".join((hashtags or []))
    return gate_electric_bus_vietnam(caption or "", hashtag_blob)


def is_on_topic_news(title, body="", source=None):
    """News wrapper: same shape as gate_electric_bus_vietnam()."""
    return gate_electric_bus_vietnam(title, body)
