import re


def matching_group_ids(settings: dict, title: str | None, lines: list, total: float | None,
                       kinds: list[str] | None = None) -> set[int]:
    """Return applicable organization booking rules for this request."""
    document = " ".join([title or "", *((line.position_description or "") for line in lines)])
    words = " ".join(word.rstrip("s") for word in re.findall(r"[a-z0-9]+", document.casefold()))
    matches = set()
    for rule in settings.get("category_rules", []):
        if rule.get("item_kind"):
            if kinds is None or rule["item_kind"] not in kinds:
                continue
            limit = rule.get("max_total")
            if limit is None or (total is not None and total <= limit):
                matches.add(rule["commodity_group_id"])
            continue
        term = " ".join(word.rstrip("s") for word in re.findall(r"[a-z0-9]+", rule["term"].casefold()))
        if not term or not re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", words):
            continue
        limit = rule.get("max_total")
        if limit is None or (total is not None and total <= limit):
            matches.add(rule["commodity_group_id"])
    return matches
