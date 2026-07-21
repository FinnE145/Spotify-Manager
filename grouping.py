import math


def _dist(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def group_cards(cards, labels, cutoff):
    """Phase 1 nearest-label grouping.

    Returns an ordered list of (label_or_none, [cards]) tuples, cards within
    each group ordered top-to-bottom by y then left-to-right by x, groups
    ordered top-to-bottom by label position with "Ungrouped" appended last.
    """
    placed = [c for c in cards if c["placement"] == "placed"]

    groups = {label["id"]: [] for label in labels}
    ungrouped = []

    for card in placed:
        if not labels:
            ungrouped.append(card)
            continue
        nearest = min(labels, key=lambda label: _dist(card, label))
        if _dist(card, nearest) > cutoff:
            ungrouped.append(card)
        else:
            groups[nearest["id"]].append(card)

    def sort_key(card):
        return (card["y"], card["x"])

    ordered_labels = sorted(labels, key=lambda label: (label["y"], label["x"]))

    result = []
    for label in ordered_labels:
        result.append((label, sorted(groups[label["id"]], key=sort_key)))
    result.append((None, sorted(ungrouped, key=sort_key)))
    return result


def render_export_text(cards, labels, cutoff):
    lines = []
    for label, group_cards_ in group_cards(cards, labels, cutoff):
        if label is None:
            lines.append("## Ungrouped")
        else:
            lines.append(f"## {label['text']}  (label @ {label['x']:.0f},{label['y']:.0f})")
        for card in group_cards_:
            lines.append(f"- {card['display_name']}  (card @ {card['x']:.0f},{card['y']:.0f})")
        lines.append("")

    tray_cards = [c for c in cards if c["placement"] == "tray"]
    lines.append("## Unplaced (in tray)")
    for card in sorted(tray_cards, key=lambda c: c["display_name"]):
        lines.append(f"- {card['display_name']}")

    return "\n".join(lines).rstrip() + "\n"
