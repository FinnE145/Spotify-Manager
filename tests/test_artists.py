"""Artist identity: alias resolution, the merge/unmerge writes, and the
duplicate-candidate queue.

Authority is **`detection-artist-model.md` §1** (stamped Audited; the
canonical-artist tiebreak paragraph was rewritten 2026-08-17 under P1-010) and
**`async-recompute-N.md` §4.1/§4.2** for the recompute call sites.

**P1-010's fixture warning is the shape of this file.** The tiebreak "runs on
**alias-resolved** credits... so an id already merged into a group scores at
the display floor", which means a fixture built from two ids that are already
merged cannot test the tiebreak at all. Every tiebreak test below therefore
starts from two **unmerged** ids.

Artist scores aggregate the **version tier** (`scoring.artist_scores` ->
`_artist_role_rows` over `track_group`), so an artist only has a score if its
tracks sit in version groups that carry `score` rows. `scored_artist` below is
what builds that.
"""

import artists
import builders
import canonical_detect as detect
import normalize


def scored_artist(conn, artist_id, name, track_ids, version_scores):
    """An artist credited on one track per entry in `version_scores`, each in
    its own scored version group.

    Track *count* and score are therefore independently controllable, which is
    exactly what P1-010's tiebreak needs: the id with more `track_artist` rows
    and the id with the higher score have to be different ids.

    Callers must also pick ids whose **alphabetical order** disagrees with the
    score, or the test passes against an implementation that ignores scores and
    falls through to `_canonical_of`'s id-ascending tiebreak (P2-005).
    """
    builders.make_artist(conn, artist_id, name=name)
    for track_id, score in zip(track_ids, version_scores):
        builders.make_track(conn, track_id, artists=[artist_id])
        group = builders.make_group(conn, [track_id])
        builders.make_score(conn, "version", group["version"], all_time=score)
    return artist_id


def alias_rows(conn):
    return {
        (row["artist_id"], row["canonical_artist_id"])
        for row in conn.execute("SELECT artist_id, canonical_artist_id FROM artist_alias")
    }


def reviewed_artist_pairs(conn):
    return {
        (row["artist_id_a"], row["artist_id_b"])
        for row in conn.execute("SELECT artist_id_a, artist_id_b FROM reviewed_artist_pair")
    }


def track_credit_counts(conn):
    return {
        row["artist_id"]: row["n"]
        for row in conn.execute(
            "SELECT artist_id, COUNT(*) AS n FROM track_artist GROUP BY artist_id"
        )
    }


# -- The canonical-artist tiebreak (P1-010) ---------------------------------


def test_the_higher_scoring_id_wins_a_merge_not_the_busier_one(conn):
    """P1-010's case: score and raw credit count disagree, and score wins.

    `ar-strong` holds one credit and a strong version; `ar-busy` holds two weak
    ones. The pre-H rule ("the id with the most `track_artist` rows") elects
    `ar-busy`.

    **The ids are named so that *both* wrong rules elect `ar-busy`.** The
    retired one does because it has more credits; a rule that dropped the score
    term entirely and fell through to `_canonical_of`'s id-ascending tiebreak
    would too, because `ar-busy` < `ar-strong`. Named the obvious way round
    (`ar-few` beating `ar-many`) the winner is also the alphabetically-first
    id, and this test passes against an implementation that never reads a score
    at all -- it did, until Verify mutated `_canonical_of` to sort by id alone
    and the whole suite stayed green. See P2-005.
    """
    # source: detection-artist-model.md §1 -- "canonical_artist_id was
    # originally the id with the most track_artist rows... **superseded by
    # scoring-H.md §11.3** -- it's now the id with the **highest
    # scoring.artist_scores(...)["all_time"]**, ties still broken by id
    # ascending, in artists._canonical_of()."
    scored_artist(conn, "ar-strong", "half alive", ["t1"], [90.0])
    scored_artist(conn, "ar-busy", "half alive", ["t2", "t3"], [20.0, 20.0])

    counts = track_credit_counts(conn)
    assert counts["ar-busy"] > counts["ar-strong"]  # the retired rule disagrees
    assert "ar-busy" < "ar-strong"  # and so does the id-ascending tiebreak
    scores = {a: artists.scoring.artist_scores(conn, [a])[a]["all_time"] for a in counts}
    assert scores["ar-strong"] > scores["ar-busy"]

    artists.mark_same(conn, "ar-strong", "ar-busy")

    assert alias_rows(conn) == {("ar-busy", "ar-strong")}


def test_equal_scores_are_broken_by_id_ascending(conn):
    # source: detection-artist-model.md §1 -- "ties still broken by id
    # ascending", which _canonical_of's docstring explains is "so the choice is
    # stable across runs".
    scored_artist(conn, "ar-b", "BONES", ["t1"], [50.0])
    scored_artist(conn, "ar-a", "BONES", ["t2"], [50.0])

    artists.mark_same(conn, "ar-b", "ar-a")

    assert alias_rows(conn) == {("ar-b", "ar-a")}


def test_the_canonical_artist_never_points_at_itself(conn):
    # source: detection-artist-model.md §1 -- "The canonical artist never gets
    # a row pointing at itself." artist_alias is sparse: only merged ids get
    # rows, and a self-row would make every resolution lookup ambiguous.
    scored_artist(conn, "ar-a", "BONES", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "BONES", ["t2"], [10.0])

    artists.mark_same(conn, "ar-a", "ar-b")

    assert all(a != canonical for a, canonical in alias_rows(conn))


# -- Merging ----------------------------------------------------------------


def test_a_third_id_folds_the_whole_existing_group_in(conn):
    """Merging into an already-merged artist must not strand the first pair.

    All three end up on one canonical id, rather than `ar-c` pairing off with
    whichever id it was merged against.
    """
    # source: detection-artist-model.md §1 -- "Merging three or more ids works
    # for free -- they all point at one canonical id", and mark_same's own
    # docstring: "Merging an already-merged artist folds its whole group in,
    # so a third id joining two doesn't strand the first pair."
    scored_artist(conn, "ar-a", "half alive", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "half alive", ["t2"], [50.0])
    scored_artist(conn, "ar-c", "half alive", ["t3"], [10.0])
    artists.mark_same(conn, "ar-a", "ar-b")

    # Merged against the *alias*, not the canonical -- the case that strands
    # the first pair if the whole group is not read first.
    artists.mark_same(conn, "ar-b", "ar-c")

    assert alias_rows(conn) == {("ar-b", "ar-a"), ("ar-c", "ar-a")}


def test_merging_records_the_pair_as_reviewed(conn):
    # source: detection-artist-model.md §1 -- "reviewed_artist_pair records
    # only that a pair was *looked at*... which is what stops a decided pair
    # resurfacing", stored with "artist_id_a < artist_id_b, matching
    # reviewed_pair's _pair_key". Passed unsorted here on purpose.
    scored_artist(conn, "ar-b", "BONES", ["t1"], [90.0])
    scored_artist(conn, "ar-a", "BONES", ["t2"], [10.0])

    artists.mark_same(conn, "ar-b", "ar-a")

    assert reviewed_artist_pairs(conn) == {("ar-a", "ar-b")}


def test_marking_not_same_records_the_review_without_merging(conn):
    # source: detection-artist-model.md §1 -- "the verdict is implicit in
    # whether the two now resolve to the same canonical id". A "not same"
    # decision is a review with no alias row, which is what suppresses the
    # pair without merging it.
    scored_artist(conn, "ar-a", "LiSA", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "LISA", ["t2"], [10.0])

    artists.mark_not_same(conn, "ar-a", "ar-b")

    assert alias_rows(conn) == set()
    assert reviewed_artist_pairs(conn) == {("ar-a", "ar-b")}


# -- Unmerging --------------------------------------------------------------


def test_unmerge_forgets_a_review_recorded_against_a_sibling(conn):
    """The subtle half of unmerge, and the reason it reads the group first.

    `ar-c` was merged by a decision recorded against `ar-b`, not against the
    canonical `ar-a`. Clearing only the pair against the canonical would leave
    `(ar-b, ar-c)` suppressed forever despite the two no longer being merged.
    """
    # source: unmerge's docstring, per detection-artist-model.md §1's review
    # model -- "in a 3+-id group the review that merged this artist may have
    # been recorded against a sibling, not the canonical, so that pair would
    # stay suppressed forever despite no longer being merged. The group has to
    # be read before the alias row goes, or it's no longer derivable."
    scored_artist(conn, "ar-a", "half alive", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "half alive", ["t2"], [50.0])
    scored_artist(conn, "ar-c", "half alive", ["t3"], [10.0])
    artists.mark_same(conn, "ar-a", "ar-b")
    artists.mark_same(conn, "ar-b", "ar-c")
    assert ("ar-b", "ar-c") in reviewed_artist_pairs(conn)

    artists.unmerge(conn, "ar-c")

    assert alias_rows(conn) == {("ar-b", "ar-a")}
    assert ("ar-b", "ar-c") not in reviewed_artist_pairs(conn)
    assert ("ar-a", "ar-c") not in reviewed_artist_pairs(conn)
    # The review that merged the *other* pair is untouched -- unmerging one id
    # is not a reset of the group.
    assert ("ar-a", "ar-b") in reviewed_artist_pairs(conn)


def test_unmerge_returns_the_pair_to_the_queue(conn):
    # source: unmerge's docstring -- "returning those pairs to the queue --
    # nothing here is a one-way door."
    scored_artist(conn, "ar-a", "BONES", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "BONES", ["t2"], [10.0])
    artists.mark_same(conn, "ar-a", "ar-b")
    assert artists.candidate_pairs(conn) == []

    artists.unmerge(conn, "ar-b")

    assert [(p["a"]["artist_id"], p["b"]["artist_id"]) for p in artists.candidate_pairs(conn)] == [
        ("ar-a", "ar-b")
    ]


def test_unmerging_an_unmerged_artist_does_nothing(conn):
    # characterization -- the early return. artist_alias is sparse, so an
    # unmerged artist simply has no row, and the UI can call this without
    # first checking.
    scored_artist(conn, "ar-a", "BONES", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "BONES", ["t2"], [10.0])
    artists.mark_not_same(conn, "ar-a", "ar-b")

    artists.unmerge(conn, "ar-a")

    assert reviewed_artist_pairs(conn) == {("ar-a", "ar-b")}


# -- Candidate detection ----------------------------------------------------


def pair_ids(conn):
    return [(p["a"]["artist_id"], p["b"]["artist_id"]) for p in artists.candidate_pairs(conn)]


def test_candidates_are_ids_whose_names_normalize_equal(conn):
    # source: detection-artist-model.md §1 "Candidate detection" -- "Two
    # artists are a candidate when their names normalize equal (the existing
    # _normalize_base_string pipeline: NFKD, strip combining marks,
    # lowercase, drop non-alphanumerics, collapse whitespace) but their ids
    # differ."
    # Case and punctuation differ; both normalize to "tyler the creator".
    # NOT a "half*alive" / "Half Alive" pair: the base normalizer *deletes*
    # non-alphanumerics rather than spacing them (E §1), so those two
    # normalize to "halfalive" and "half alive" and are not candidates.
    scored_artist(conn, "ar-a", "Tyler, The Creator", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "tyler the creator", ["t2"], [10.0])
    scored_artist(conn, "ar-c", "Someone Else", ["t3"], [50.0])

    assert pair_ids(conn) == [("ar-a", "ar-b")]


def test_a_reviewed_pair_is_suppressed(conn):
    # source: detection-artist-model.md §1 -- "A pair is suppressed once it
    # appears in reviewed_artist_pair". This is what keeps LiSA/LISA from
    # being asked about forever once ruled different.
    scored_artist(conn, "ar-a", "LiSA", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "LISA", ["t2"], [10.0])
    assert pair_ids(conn) == [("ar-a", "ar-b")]

    artists.mark_not_same(conn, "ar-a", "ar-b")

    assert pair_ids(conn) == []


def test_two_ids_already_resolving_to_one_canonical_are_suppressed(conn):
    # source: detection-artist-model.md §1 -- "...or once both ids already
    # resolve to the same canonical id." The second, independent suppression:
    # two aliases of one canonical are not a question even if the pair between
    # *them* was never reviewed directly.
    scored_artist(conn, "ar-a", "half alive", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "half alive", ["t2"], [50.0])
    scored_artist(conn, "ar-c", "half alive", ["t3"], [10.0])
    artists.mark_same(conn, "ar-a", "ar-b")
    artists.mark_same(conn, "ar-a", "ar-c")
    conn.execute("DELETE FROM reviewed_artist_pair WHERE artist_id_a = 'ar-b'")
    conn.commit()

    assert ("ar-b", "ar-c") not in reviewed_artist_pairs(conn)
    assert pair_ids(conn) == []


def test_the_queue_is_ordered_by_the_pairs_combined_score(conn):
    """`/dev/artists`' ordering, which §11.1 changed from name to score."""
    # source: scoring-H.md §11.1 -- "/dev/artists duplicate-pair queue
    # (artists.py:191) | name" is listed among the sites score replaces; and
    # artists.candidate_pairs' comment: "the pair's score is the score of
    # everything either one credits", scored as one 2-artist collection.
    scored_artist(conn, "ar-a", "Aaa", ["t1"], [10.0])
    scored_artist(conn, "ar-b", "Aaa", ["t2"], [10.0])
    scored_artist(conn, "ar-y", "Zzz", ["t3"], [90.0])
    scored_artist(conn, "ar-z", "Zzz", ["t4"], [90.0])

    # Alphabetically "Aaa" leads; by score "Zzz" does.
    assert pair_ids(conn) == [("ar-y", "ar-z"), ("ar-a", "ar-b")]


def test_candidate_pairs_carry_sample_tracks_for_the_page(conn):
    # characterization -- the queue renders a few track names per side so the
    # decision can be made without leaving the page.
    scored_artist(conn, "ar-a", "BONES", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "BONES", ["t2"], [10.0])

    pair = artists.candidate_pairs(conn)[0]

    assert pair["a"]["sample_tracks"] == ["Track t1"]
    assert pair["a"]["track_count"] == 1


def test_merged_groups_lists_one_entry_per_canonical(conn):
    # characterization -- the unmerge list on /dev/artists: one row per
    # canonical artist carrying the ids folded into it.
    scored_artist(conn, "ar-a", "half alive", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "half alive", ["t2"], [50.0])
    scored_artist(conn, "ar-c", "half alive", ["t3"], [10.0])
    artists.mark_same(conn, "ar-a", "ar-b")
    artists.mark_same(conn, "ar-a", "ar-c")

    groups = artists.merged_groups(conn)

    assert len(groups) == 1
    assert groups[0]["canonical"]["artist_id"] == "ar-a"
    assert sorted(a["artist_id"] for a in groups[0]["aliases"]) == ["ar-b", "ar-c"]


# -- Per-track artist sets --------------------------------------------------


def test_artist_sets_resolve_through_aliases(conn):
    # source: artists.artist_sets' docstring -- "{track_id: {...}} with every
    # id resolved through artist_alias", which is what makes detection compare
    # artists rather than Spotify ids.
    scored_artist(conn, "ar-a", "half alive", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "half alive", ["t2"], [10.0])
    assert artists.artist_sets(conn)["t2"]["artist_ids"] == {"ar-b"}

    artists.mark_same(conn, "ar-a", "ar-b")

    assert artists.artist_sets(conn)["t2"]["artist_ids"] == {"ar-a"}


def test_artist_sets_split_primary_from_featured(conn):
    # source: artists.artist_sets' docstring and canonical_detect's comment --
    # "primary_ids drives the song prefill, so a shared *featured* credit
    # alone never merges two songs silently." A credit is featured when some
    # *other* credit on the track holds the album credit.
    builders.make_artist(conn, "ar-main", name="Main")
    builders.make_artist(conn, "ar-guest", name="Guest")
    builders.make_album(conn, album_id="al-1", name="Album", artists=["ar-main"])
    builders.make_track(conn, "t1", album_id="al-1", artists=["ar-main", "ar-guest"])

    sets = artists.artist_sets(conn)["t1"]

    assert sets["primary_ids"] == {"ar-main"}
    assert sets["featured_ids"] == {"ar-guest"}


def test_a_track_with_no_credits_is_absent(conn):
    # source: artists.artist_sets' docstring -- "Tracks with no credits are
    # absent." Callers substitute an empty record rather than expecting a key.
    builders.make_track(conn, "t1")
    conn.execute("DELETE FROM track_artist WHERE track_id = 't1'")
    conn.commit()

    assert "t1" not in artists.artist_sets(conn)


# -- Recompute call sites (async-recompute-N.md §4.2) -----------------------


def test_merging_requests_an_async_recompute(conn, recompute_calls):
    # source: async-recompute-N.md §4.2 -- artists.mark_same() is one of the
    # five async sites: "commit first, then request", because "/dev/artists'
    # mark-same and unmerge are... the same per-decision shape as the review
    # queue".
    scored_artist(conn, "ar-a", "BONES", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "BONES", ["t2"], [10.0])

    artists.mark_same(conn, "ar-a", "ar-b")

    assert len(recompute_calls) == 1
    # The order §4.2 calls load-bearing: the worker reads through its own
    # connection, so the write must already be committed when it is asked.
    assert not conn.in_transaction


def test_unmerging_requests_an_async_recompute(conn, recompute_calls):
    # source: async-recompute-N.md §4.2 -- artists.unmerge() is the fifth
    # async site.
    scored_artist(conn, "ar-a", "BONES", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "BONES", ["t2"], [10.0])
    artists.mark_same(conn, "ar-a", "ar-b")
    recompute_calls.clear()

    artists.unmerge(conn, "ar-b")

    assert len(recompute_calls) == 1
    assert not conn.in_transaction


def test_marking_not_same_does_not_recompute(conn, recompute_calls):
    # source: async-recompute-N.md §4.2's table -- only mark_same() and
    # unmerge() are listed. mark_not_same changes no scoring input: nothing
    # resolves differently afterwards, so there is nothing to recompute.
    scored_artist(conn, "ar-a", "LiSA", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "LISA", ["t2"], [10.0])

    artists.mark_not_same(conn, "ar-a", "ar-b")

    assert recompute_calls == []


# -- Module invariant (codebase-health-P.md §6) -----------------------------


def test_artists_never_writes_track_or_membership(conn):
    # source: artists.py's module docstring -- "Owns artist_alias and
    # reviewed_artist_pair only; never touches track or membership."
    from test_canonical_engine import executed_sql, writes_to

    scored_artist(conn, "ar-a", "BONES", ["t1"], [90.0])
    scored_artist(conn, "ar-b", "BONES", ["t2"], [10.0])

    statements = executed_sql(
        conn,
        lambda: (
            artists.mark_same(conn, "ar-a", "ar-b"),
            artists.unmerge(conn, "ar-b"),
            artists.candidate_pairs(conn),
            artists.artist_sets(conn),
        ),
    )

    assert statements
    assert not writes_to(statements, "track")
    assert not writes_to(statements, "membership")
    assert writes_to(statements, "artist_alias")


def test_artist_names_normalize_through_the_title_base_pipeline(conn):
    """Artist names go through the *base* normalizer, which deletes
    punctuation -- not the suffix one, which replaces it with a space.

    The consequence is worth pinning: `half*alive` does **not** collide with
    `Half Alive`, because deleting the bullet closes the gap. The real
    duplicate-id pair in the library shares the same spelling, so nothing
    depends on it doing otherwise.
    """
    # source: detection-artist-model.md §1 -- candidate detection uses "the
    # existing _normalize_base_string pipeline: NFKD, strip combining marks,
    # lowercase, drop non-alphanumerics, collapse whitespace"; and E §1's
    # P1-013 amendment -- the two normalizers "are not the same function",
    # the base half deleting punctuation and the suffix half spacing it.
    #
    # That pipeline is now `normalize.base_string` (P3_refactor.md §4.2): it
    # was `canonical_detect._normalize_base_string` with a `normalize_name`
    # alias beside it, and this test used to assert the two were the same
    # object. Both names are gone -- there is one function now, so that
    # identity check would compare it against itself. What it was really
    # pinning (artist names and title bases share one pipeline) is now true
    # by construction; the behaviour below is what remains worth asserting.
    assert normalize.base_string("Tyler, The Creator") == "tyler the creator"
    assert normalize.base_string("half\u2022alive") == "halfalive"
    assert detect.normalize_suffix("half\u2022alive") == "half alive"
    # The accent case is what makes the "NFKD, strip combining marks" half of
    # that comment checkable rather than merely stated -- without it, deleting
    # strip_accents() from base_string() passes this whole file (P3-003). The
    # pair is real: symr.db carries a merged "Jerome Ducros" / "Jerome Ducros"
    # with the accents, which bucketed together only because of this call.
    assert normalize.base_string("J\u00e9r\u00f4me Ducros") == "jerome ducros"
