"""Electric-bus keyword group (from the "Xe bus điện" sheet of the keyword workbook)."""

ELECTRIC_BUS = [
    {"term": "xe buýt điện", "type": "literal"},
    {"term": "xe bus điện", "type": "literal"},
    {"term": "xe buyt dien", "type": "literal"},
    {"term": "xe buyt điện", "type": "literal"},
    {"term": "xe buýt dien", "type": "literal"},
    {"term": "bus điện", "type": "literal"},
    {"term": "bus dien", "type": "literal"},
    {"term": "buýt điện", "type": "literal"},
    {"term": "buyt dien", "type": "literal"},
    {"term": "electric bus", "type": "literal"},
    {"term": "e-bus", "type": "literal"},
    {"term": "ebus", "type": "literal"},
    {"term": "VinBus", "type": "literal"},
    {"term": "Vin Bus", "type": "literal"},
    {"term": "vinbus", "type": "literal"},
    {"term": "ô tô bus điện", "type": "literal"},
    {"term": "ô tô buýt điện", "type": "literal"},
    {"term": "xe điện công cộng", "type": "literal"},
    {"term": "tuyến xe buýt điện", "type": "literal"},
    {"term": "tuyến bus điện", "type": "literal"},
    {"term": "trạm xe buýt điện", "type": "literal"},
    {"term": "điểm dừng xe buýt điện", "type": "literal"},
    {"term": "xe bus điện hài lòng", "type": "literal"},
    {"term": "review bus điện", "type": "literal"},
    {"term": "trải nghiệm xe buýt điện", "type": "literal"},
    {"term": "vận hành xe điện vinbus", "type": "literal"},
    {"term": "chất lượng vinbus", "type": "literal"},
    {"term": "xe bus điện hài lòng", "type": "literal"},
    {"term": "review bus điện", "type": "literal"},
    {"term": "trải nghiệm xe buýt điện", "type": "literal"},
    {"term": "vận hành xe điện vinbus", "type": "literal"},
    {"term": "chất lượng vinbus", "type": "literal"},
    {"term": "đánh giá xe buýt điện", "type": "literal"},
]

GROUPS = {"electric_bus": ELECTRIC_BUS}


def literal_terms(group_name, groups=GROUPS):
    """Order-preserving dedup of literal terms; regex entries excluded (search input only)."""
    seen, out = set(), []
    for entry in groups[group_name]:
        if entry["type"] != "literal":
            continue
        key = entry["term"].strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(entry["term"].strip())
    return out
