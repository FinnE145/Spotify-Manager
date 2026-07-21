import math


def _dist(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _nodes(placed_cards, labels):
    nodes = [{"kind": "label", "id": l["id"], "x": l["x"], "y": l["y"]} for l in labels]
    nodes += [{"kind": "card", "id": c["id"], "x": c["x"], "y": c["y"]} for c in placed_cards]
    return nodes


def _sorted_candidates(node, all_nodes):
    """Every other node, nearest-first, ties broken by (label-before-card, lower id)."""
    candidates = [n for n in all_nodes if not (n["kind"] == node["kind"] and n["id"] == node["id"])]
    candidates.sort(key=lambda n: (_dist(node, n), 0 if n["kind"] == "label" else 1, n["id"]))
    return candidates


def group_cards(cards, labels, cutoff):
    """Phase 1.5 chained/proximity grouping.

    Each placed card links to its nearest node among {all labels, all other
    placed cards}. A label neighbor assigns the group directly; a card
    neighbor's group is inherited, transitively, up the chain to a label.
    The cutoff applies per-link, so a gap wider than it breaks the chain.

    If a card's nearest neighbor leads into a cycle (e.g. two cards that are
    each other's nearest neighbor) instead of a label, it falls back to its
    next-nearest unvisited neighbor rather than giving up immediately -
    a mutually-nearest cluster still resolves to whichever label is
    reachable from it, rather than the whole cluster going Ungrouped.

    Returns an ordered list of (label_or_none, [cards]) tuples, cards within
    each group ordered top-to-bottom by y then left-to-right by x, groups
    ordered top-to-bottom by label position with "Ungrouped" appended last.
    """
    placed = [c for c in cards if c["placement"] == "placed"]
    all_nodes = _nodes(placed, labels)
    node_by_key = {(n["kind"], n["id"]): n for n in all_nodes}

    candidates_by_card = {
        card["id"]: _sorted_candidates(node_by_key[("card", card["id"])], all_nodes)
        for card in placed
    }

    def resolve(card_id, visiting):
        visiting.add(card_id)
        result = None
        node = node_by_key[("card", card_id)]
        for candidate in candidates_by_card[card_id]:
            if _dist(node, candidate) > cutoff:
                break  # sorted nearest-first, so nothing further qualifies either
            if candidate["kind"] == "label":
                result = candidate["id"]
                break
            if candidate["id"] in visiting:
                continue  # would cycle back on the current path - try the next-nearest
            sub_result = resolve(candidate["id"], visiting)
            if sub_result is not None:
                result = sub_result
                break
        visiting.discard(card_id)
        return result

    groups = {label["id"]: [] for label in labels}
    ungrouped = []
    for card in placed:
        label_id = resolve(card["id"], set())
        if label_id is None:
            ungrouped.append(card)
        else:
            groups[label_id].append(card)

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
