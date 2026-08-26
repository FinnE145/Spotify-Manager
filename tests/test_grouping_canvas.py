"""`grouping.py` -- the org canvas's proximity/chained card grouping and
export-text renderer.

Source: `docs/specs/org-canvas.md` §"Corrections to current behavior
(P1-012)" **only**. The body below that section's divider is explicitly
historical design intent, not current behavior -- "Reading only the Phase 1
section and testing against it would test dead code." Every test here cites
the corrections section.

`group_cards`/`render_export_text` take plain dicts, no DB -- most tests here
build fixtures directly rather than through `builders`. The one exception
(`card.note` exclusion) goes through the real `/api/export` route, because
`card.note`'s absence from the export is only a meaningful assertion against
a dict that actually *has* the key (`_board_state`'s row does -- `note` is in
its column list; a hand-built dict omitting it entirely would pass the same
assertion vacuously).
"""

import builders
from grouping import group_cards, render_export_text


def card(id, x, y, placement="placed", display_name=None):
    return {
        "id": id,
        "x": x,
        "y": y,
        "placement": placement,
        "display_name": display_name or f"Card {id}",
    }


def label(id, x, y, text=None):
    return {"id": id, "x": x, "y": y, "text": text or f"Label {id}"}


def group_for(result, label_id):
    for lbl, cards in result:
        if lbl is not None and lbl["id"] == label_id:
            return cards
    raise AssertionError(f"no group for label {label_id}")


def ungrouped(result):
    lbl, cards = result[-1]
    assert lbl is None
    return cards


# -- The headline: nearest-neighbor dead-end vs. the real backtracking search


def test_a_dead_ending_nearest_neighbor_backtracks_to_a_farther_candidate(conn):
    # source: org-canvas.md "Corrections to current behavior (P1-012)" --
    # "The actual rule in grouping.py's group_cards() is a full nearest-first
    # search, not a single hop. ... resolve() ... walks that sorted list and
    # only commits to Ungrouped once every candidate within the cutoff has
    # been tried and none reaches a label ... So a card whose nearest
    # neighbor dead-ends can still attach via a longer alternate path
    # through a different, farther-but-still-in-cutoff neighbor."
    #
    # Card A's nearest neighbor is card B, whose only candidate within
    # cutoff is A itself (a dead end once A is already being resolved). A's
    # farther second candidate, C, reaches label L directly. A naive
    # single-chain-follow implementation (no backtracking past the nearest
    # neighbor) would send A straight to Ungrouped; the real search must not.
    cutoff = 60
    a = card("a", 0, 0)
    b = card("b", -8, 0)  # A's nearest (dist 8); B's only candidate is A
    c = card("c", 55, 0)  # A's farther candidate (dist 55, within cutoff);
    #                        B-C distance is 63, outside B's own cutoff
    lbl = label("l", 55, 55)  # reachable from C (dist 55) but not from A or B
    #                            directly (dist ~77.8 and ~83.6)

    result = group_cards([a, b, c], [lbl], cutoff)

    assert a in group_for(result, "l")


def test_a_cycle_skip_backtracks_past_the_visited_card_not_just_stops(conn):
    # source: org-canvas.md, same section -- "a mutually-nearest cluster
    # still resolves to whichever label is reachable from it" and the
    # `visiting` set exists so a cycle is skipped ("continue") rather than
    # aborting the whole search ("break"). This must hold even when the
    # visited card isn't the last candidate tried: A's only candidate is B;
    # B's nearest candidate is A (visited, must be *skipped*, not treated as
    # a dead end), and B's next candidate D chains on to a label. A fixture
    # where the visited candidate is always the last one tried can't tell
    # "skip and keep going" apart from "stop" -- this one can.
    cutoff = 50
    a = card("a", 0, 0)
    b = card("b", 10, 0)  # A's only candidate (dist 10); A-D is 55, out of range
    d = card("d", 55, 0)  # B's second candidate (dist 45); reaches the label
    lbl = label("l", 95, 0)  # reachable only from D (dist 40)

    result = group_cards([a, b, d], [lbl], cutoff)

    assert a in group_for(result, "l")


def test_two_mutually_nearest_cards_with_no_reachable_label_both_go_ungrouped(conn):
    # source: org-canvas.md, same section -- "If a card's nearest neighbor
    # leads into a cycle (e.g. two cards that are each other's nearest
    # neighbor) instead of a label, it falls back to its next-nearest
    # unvisited neighbor rather than giving up immediately." Here there IS
    # no next-nearest neighbor at all -- the visiting set has to actually
    # terminate the cycle (no RecursionError, no infinite loop) and both
    # cards correctly land in Ungrouped rather than crashing or hanging.
    cutoff = 20
    x = card("x", 0, 0)
    y = card("y", 10, 0)  # mutually nearest to x; nothing else in range
    far_label = label("l", 1000, 1000)

    result = group_cards([x, y], [far_label], cutoff)

    assert x in ungrouped(result)
    assert y in ungrouped(result)


def test_cutoff_applies_per_link_a_wide_gap_breaks_the_chain(conn):
    # source: org-canvas.md -- "The max-distance cutoff applies to each link
    # -- a gap larger than the cutoff breaks the chain."
    cutoff = 50
    a = card("a", 0, 0)
    far_label = label("l", 200, 0)  # distance 200, far outside cutoff

    result = group_cards([a], [far_label], cutoff)

    assert a in ungrouped(result)


# -- Tie-breaking (ratified as implemented, per the corrections section)


def test_a_tied_label_and_card_the_label_wins(conn):
    # source: org-canvas.md -- "ties broken by nearest-distance, then
    # label-before-card, then lower id." A label and a card at IDENTICAL
    # distance from A: the label must win the tie, so A never even
    # considers the card.
    #
    # The tied card has to lead to a DIFFERENT label, or the assertion
    # cannot fail (found in session 4's Verify, P2-008). With only one label
    # on the board, losing the tie costs nothing: resolve() backtracks, so A
    # reaches that same label one hop later through the card, and a
    # card-before-label tie-break produces the identical answer. Here the
    # tied card c2 sits next to its own label M, out of A's cutoff -- so the
    # wrong tie-break lands A in M rather than in L.
    cutoff = 15
    a = card("a", 0, 0)
    c2 = card("c2", 0, 10)  # dist 10 from A, same as label L
    lbl_l = label("L", 10, 0)  # dist 10 from A
    lbl_m = label("M", 0, 20)  # dist 10 from c2; dist 20 from A, outside cutoff

    result = group_cards([a, c2], [lbl_l, lbl_m], cutoff)

    assert a in group_for(result, "L")
    assert c2 in group_for(result, "M")


def test_a_card_exactly_at_the_cutoff_distance_still_links(conn):
    # source: org-canvas.md -- "The max-distance cutoff applies to each link
    # -- a gap larger than the cutoff breaks the chain." Larger than, so the
    # boundary itself is inside: group_cards compares `> cutoff`. Pinned
    # because nothing else here sits on the boundary, and `>=` -- the
    # off-by-one a reader could introduce in either direction -- passed the
    # whole suite otherwise (P2-008), exactly as the play_stats week
    # boundary would have.
    cutoff = 50
    a = card("a", 0, 0)
    lbl = label("l", 50, 0)  # distance exactly == cutoff

    result = group_cards([a], [lbl], cutoff)

    assert a in group_for(result, "l")


def test_tied_labels_the_lower_id_wins(conn):
    # source: org-canvas.md -- "... then lower id." Two labels at identical
    # distance from a card; the lower-id label must be chosen.
    cutoff = 30
    a = card("a", 0, 0)
    l1 = label(1, 10, 0)
    l2 = label(2, 0, 10)

    result = group_cards([a], [l1, l2], cutoff)

    assert a in group_for(result, 1)
    assert group_for(result, 2) == []


# -- Ordering rules


def test_cards_within_a_group_sort_top_to_bottom_then_left_to_right(conn):
    # characterization -- group_cards' documented sort_key: (y, x).
    cutoff = 100
    lbl = label("l", 0, 0)
    top = card("top", 50, 0, display_name="Top")
    bottom_left = card("bl", 0, 50, display_name="BottomLeft")
    bottom_right = card("br", 50, 50, display_name="BottomRight")

    result = group_cards([bottom_right, top, bottom_left], [lbl], cutoff)

    ordered = group_for(result, "l")
    assert [c["id"] for c in ordered] == ["top", "bl", "br"]


def test_groups_are_ordered_by_label_position_with_ungrouped_always_last(conn):
    # source: org-canvas.md -- group_cards' docstring: "groups ordered
    # top-to-bottom by label position with 'Ungrouped' appended last."
    cutoff = 20
    l_bottom = label("bottom", 0, 100)
    l_top = label("top", 0, 0)
    a = card("a", 0, 0)  # near l_top
    b = card("b", 0, 100)  # near l_bottom
    orphan = card("orphan", 1000, 1000)  # near neither

    result = group_cards([a, b, orphan], [l_bottom, l_top], cutoff)

    label_order = [lbl["id"] for lbl, _ in result if lbl is not None]
    assert label_order == ["top", "bottom"]
    assert result[-1][0] is None  # Ungrouped is always last


def test_ungrouped_header_renders_even_with_zero_cards_under_it(conn):
    # source: org-canvas.md -- "## Ungrouped always renders, even with zero
    # cards under it (render_export_text, unconditional)."
    cutoff = 100
    lbl = label("l", 0, 0)
    a = card("a", 5, 0)  # grouped; nothing left over for Ungrouped

    text = render_export_text([a], [lbl], cutoff)

    assert "## Ungrouped" in text


def test_only_placed_cards_are_considered_tray_cards_never_appear(conn):
    # source: org-canvas.md -- placement in ('tray', 'placed'); group_cards
    # filters to placement == 'placed' on its first line.
    cutoff = 100
    lbl = label("l", 0, 0)
    placed = card("p", 5, 0)
    tray_card = card("t", 5, 0, placement="tray")

    result = group_cards([placed, tray_card], [lbl], cutoff)

    all_ids = {c["id"] for _, cards in result for c in cards}
    assert "t" not in all_ids
    assert "p" in all_ids


# -- render_export_text ------------------------------------------------


def test_export_text_format_and_unplaced_section(conn):
    # source: org-canvas.md §Export -- the "## {label}  (label @ x,y)" /
    # "- {name}  (card @ x,y)" line format, and "## Unplaced (in tray)"
    # always rendering.
    cutoff = 100
    lbl = label("l", 10, 20, text="Upbeat")
    a = card("a", 15, 20, display_name="A Song")
    tray_card = card("t", 0, 0, placement="tray", display_name="Zebra")

    text = render_export_text([a, tray_card], [lbl], cutoff)

    assert "## Upbeat  (label @ 10,20)" in text
    assert "- A Song  (card @ 15,20)" in text
    assert "## Unplaced (in tray)" in text
    assert "- Zebra" in text


def test_tray_cards_sort_alphabetically_by_display_name(conn):
    # source: org-canvas.md -- "Tray cards sort alphabetically by
    # display_name." Inserted in non-alphabetical order to prove sorting,
    # not insertion order, decides it.
    zebra = card("z", 0, 0, placement="tray", display_name="Zebra")
    apple = card("a", 0, 0, placement="tray", display_name="Apple")
    mango = card("m", 0, 0, placement="tray", display_name="Mango")

    text = render_export_text([zebra, apple, mango], [], 100)

    tray_section = text.split("## Unplaced (in tray)")[1]
    positions = [tray_section.index(name) for name in ("Apple", "Mango", "Zebra")]
    assert positions == sorted(positions)


def test_card_note_never_appears_in_the_export_text(app, client, conn):
    # source: org-canvas.md -- "card.note ... entirely undocumented here, and
    # not included in the export text either." Exercised through the real
    # /api/export route so the card dict genuinely carries `note` (it is in
    # _board_state's column list) -- a hand-built dict omitting the key would
    # pass this assertion vacuously.
    builders.make_card(
        conn, x=5, y=5, display_name="Real Card", note="a distinctive secret note xyz123"
    )
    conn.commit()

    resp = client.get("/api/export")

    assert resp.status_code == 200
    assert "a distinctive secret note xyz123" not in resp.get_json()["text"]


def test_export_cutoff_defaults_to_300_not_301(app, client, conn):
    # source: S_sweep.md §3 -- num at app.py:1069. `export()`'s
    # `float(request.args.get("cutoff", 300))` default is only observable
    # through a request that omits `cutoff` entirely -- and only at a
    # distance where 300 and 301 disagree about whether the link holds
    # (group_cards breaks a link only when the distance is strictly greater
    # than cutoff, so 301 sits exactly on the 301-default's boundary and
    # strictly outside the real 300 one).
    builders.make_card(conn, x=0.0, y=0.0, display_name="Solo")
    builders.make_label(conn, x=301.0, y=0.0, text="Far")
    conn.commit()

    resp = client.get("/api/export")

    text = resp.get_json()["text"]
    ungrouped_section = text.split("## Ungrouped")[1].split("## Unplaced")[0]
    assert "Solo" in ungrouped_section


# -- The write routes: /api/card and /api/label must scope every write to the
# named row on the default board, not "the first row" or "every row on the
# board" -- and their unconditional `{"ok": True}` ack is meaningful only once
# the write itself is proven, since the same literal is returned whether the
# UPDATE/DELETE actually matched a row or not.


def test_update_card_scopes_the_write_to_the_named_card_and_board(app, client, conn):
    # source: S_sweep.md §3 -- sql= at app.py:1003 (col 66, `id = ?` ->
    # `id <> ?`), sqlAND at app.py:1003 (col 70, `AND` -> `OR`), sql= at
    # app.py:1003 (col 83, `board_id = ?` -> `board_id <> ?`), true at
    # app.py:1007 (`{"ok": True}` -> `{"ok": False}`). Two cards on the one
    # (default) board disagree with all three SQL mutants at once: `id <> ?`
    # updates the *other* card instead of the named one, `OR` updates *both*
    # (they share a board_id), and `board_id <> ?` updates *neither*.
    a_id = builders.make_card(conn, x=1.0, y=2.0, display_name="A")
    b_id = builders.make_card(conn, x=3.0, y=4.0, display_name="B")
    conn.commit()

    resp = client.post(f"/api/card/{a_id}", json={"placement": "placed", "x": 50.0, "y": 60.0})

    assert resp.get_json() == {"ok": True}
    a_row = conn.execute("SELECT x, y FROM card WHERE id = ?", (a_id,)).fetchone()
    b_row = conn.execute("SELECT x, y FROM card WHERE id = ?", (b_id,)).fetchone()
    assert (a_row["x"], a_row["y"]) == (50.0, 60.0)
    assert (b_row["x"], b_row["y"]) == (3.0, 4.0)


def test_patch_card_scopes_the_write_to_the_named_card_and_board(app, client, conn):
    # source: S_sweep.md §3 -- sql= at app.py:1021 (col 63, `id = ?` ->
    # `id <> ?`), sqlAND at app.py:1021 (col 67, `AND` -> `OR`), sql= at
    # app.py:1021 (col 80, `board_id = ?` -> `board_id <> ?`), true at
    # app.py:1024 (`{"ok": True}` -> `{"ok": False}`). Same reasoning as the
    # POST route above, against patch_card's dynamically built `fields`/
    # `params` UPDATE.
    a_id = builders.make_card(conn, note="old-a")
    b_id = builders.make_card(conn, note="old-b")
    conn.commit()

    resp = client.patch(f"/api/card/{a_id}", json={"note": "new-a"})

    assert resp.get_json() == {"ok": True}
    a_note = conn.execute("SELECT note FROM card WHERE id = ?", (a_id,)).fetchone()["note"]
    b_note = conn.execute("SELECT note FROM card WHERE id = ?", (b_id,)).fetchone()["note"]
    assert a_note == "new-a"
    assert b_note == "old-b"


def test_update_label_scopes_the_write_to_the_named_label_and_board(app, client, conn):
    # source: S_sweep.md §3 -- sql= at app.py:1051 (col 64, `id = ?` ->
    # `id <> ?`), sqlAND at app.py:1051 (col 68, `AND` -> `OR`), sql= at
    # app.py:1051 (col 81, `board_id = ?` -> `board_id <> ?`), true at
    # app.py:1054 (`{"ok": True}` -> `{"ok": False}`). Same reasoning again,
    # against update_label's PATCH.
    a_id = builders.make_label(conn, text="old-a")
    b_id = builders.make_label(conn, text="old-b")
    conn.commit()

    resp = client.patch(f"/api/label/{a_id}", json={"text": "new-a"})

    assert resp.get_json() == {"ok": True}
    a_text = conn.execute("SELECT text FROM label WHERE id = ?", (a_id,)).fetchone()["text"]
    b_text = conn.execute("SELECT text FROM label WHERE id = ?", (b_id,)).fetchone()["text"]
    assert a_text == "new-a"
    assert b_text == "old-b"


def test_delete_label_scopes_the_delete_to_the_named_label_and_board(app, client, conn):
    # source: S_sweep.md §3 -- sql= at app.py:1060 (col 40, `id = ?` ->
    # `id <> ?`), sqlAND at app.py:1060 (col 44, `AND` -> `OR`), sql= at
    # app.py:1060 (col 57, `board_id = ?` -> `board_id <> ?`), true at
    # app.py:1063 (`{"ok": True}` -> `{"ok": False}`). `id <> ?` deletes the
    # *other* label and leaves the named one; `OR` deletes both (same
    # board_id); `board_id <> ?` deletes neither -- two labels on the one
    # board disagree with all three.
    a_id = builders.make_label(conn)
    b_id = builders.make_label(conn)
    conn.commit()

    resp = client.delete(f"/api/label/{a_id}")

    assert resp.get_json() == {"ok": True}
    remaining_ids = {row["id"] for row in conn.execute("SELECT id FROM label")}
    assert a_id not in remaining_ids
    assert b_id in remaining_ids


def test_a_weight_tie_between_label_and_card_the_label_still_wins_by_kind_not_id(conn):
    # source: S_sweep.md §3 -- num at grouping.py:17 (col 51, the
    # label-before-card sort weight `0 if n["kind"] == "label" else 1` ->
    # `1 if n["kind"] == "label" else 1`, which erases the kind-based tie
    # priority entirely and leaves both kinds sharing weight 1). The existing
    # test_a_tied_label_and_card_the_label_wins does not kill this: its
    # label id "L" happens to sort before its rival card's id "c2" in plain
    # string order, so an id-only tie-break reaches the identical answer by
    # coincidence and the mutation goes unobserved. Here the label's id "z"
    # sorts *after* the rival card's id "a", so the kind-based and
    # id-based tie-breaks disagree -- confirmed empirically before writing
    # this (real code: target groups under "z"; under the mutant, target's
    # first-tried candidate becomes the rival card "a", which resolves to
    # its own label "m", and target follows it there instead).
    cutoff = 15
    target = card("t", 0, 0)
    rival = card("a", 0, 10)  # dist 10 from target, tied with the label
    lbl_z = label("z", 10, 0)  # dist 10 from target; id sorts after rival's "a"
    lbl_m = label("m", 0, 20)  # dist 10 from rival; dist 20 from target (out of cutoff)

    result = group_cards([target, rival], [lbl_z, lbl_m], cutoff)

    assert target in group_for(result, "z")
    assert rival in group_for(result, "m")
