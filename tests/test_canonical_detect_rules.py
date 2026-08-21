"""Detection's pure rules: normalization, suffix classification, the prefill,
and the deterministic auto-group pair rule.

Authority is **`docs/specs/grouping-catch-up-E.md`** §2.1 (classes and their
precedence order), §2.2 (prefill) and §3.1 (the auto-group rule), all three
rewritten 2026-08-17 under P1-013 and stamped Audited.
`docs/canonical-tracks/detection.md` is deliberately **not** cited anywhere
here: it was read during P1's blind audit but produced no findings of its own,
so `P2_tests.md` §1 flags it unverified and forbids deriving specification
tests from it.

**The `neutral` exclusion is the point of this file.** P1-013 found the spec
said `shares_base_version` was true for `base`, `recording` *and* `neutral`,
while the code excluded `neutral` -- the largest behavioural divergence in the
whole audit. Finn ruled the code right and the spec was rewritten to match, so
the tests below are the executable record of that decision.

`shares_base_version` is a closure inside `_prefill_labels`, so it is
exercised through `_prefill_labels` rather than called directly -- which is the
better route anyway: it runs the real classifier over real rows, so a fixture
that mis-classifies shows up as a failure rather than as a silent pass.
"""

import pytest

import builders
import canonical
import canonical_detect as detect

ARTIST = "ar-main"
OTHER_ARTIST = "ar-other"


def make(
    conn,
    track_id,
    name,
    *,
    artists=(ARTIST,),
    album="Album One",
    isrc=None,
    duration_ms=210_000,
    explicit=0,
):
    """One track with everything detection actually reads under test control.

    The track's artists are also its album's artists, so every credit
    classifies `primary` -- `track_artist_role` calls an artist featured only
    when *some other* credit holds the album credit. A test that wants a
    featured credit passes a different `album` artist list explicitly.
    """
    album_id = "al-" + album.lower().replace(" ", "-")
    builders.make_album(conn, album_id=album_id, name=album, artists=list(artists))
    return builders.make_track(
        conn,
        track_id,
        name=name,
        album_id=album_id,
        artists=list(artists),
        isrc=isrc,
        duration_ms=duration_ms,
        explicit=explicit,
    )


def prefill(conn, *track_ids):
    """`_prefill_labels` over real rows, via the real `_fetch_tracks`."""
    tracks = detect._fetch_tracks(conn)
    return detect._prefill_labels(sorted(track_ids), tracks)


def shares(labels, a, b, tier):
    return labels[a][tier] == labels[b][tier]


def pair_rule(conn, rule, a, b):
    """A two-argument `tracks`-based rule (`_clean_explicit_pair`,
    `_auto_group_pair`, `_same_recording`, ...) against real rows."""
    return rule(detect._fetch_tracks(conn), a, b)


# -- Normalization ----------------------------------------------------------


@pytest.mark.parametrize(
    "title, base, suffix",
    [
        # source: E §1 / detection's _split_suffix -- the delimiter set is
        # " (", " [", " - ", " - " (en/em dash) and " /", and the *earliest*
        # one in the string wins.
        ("Cornelia Street", "cornelia street", ""),
        ("Cornelia Street (Live)", "cornelia street", "(live)"),
        ("Cornelia Street [Live]", "cornelia street", "[live]"),
        ("Cornelia Street - Live", "cornelia street", "- live"),
        ("Cornelia Street – Live", "cornelia street", "– live"),
        # Punctuation is dropped from the base and whitespace collapsed, so
        # apostrophes and commas never split two spellings of one title.
        ("Don't Blame Me", "dont blame me", ""),
        ("Tyler, The Creator's Song", "tyler the creators song", ""),
        # Accents fold: NFKD then drop combining marks.
        ("Café", "cafe", ""),
    ],
)
def test_normalize_title_splits_base_from_suffix(title, base, suffix):
    assert detect.normalize_title(title) == (base, suffix)


def test_normalize_suffix_turns_punctuation_into_a_space(self=None):
    # source: E §1 and normalize_suffix's own docstring -- "NFKD doesn't fold
    # U+2019, so '(taylor's version)' and '(taylor's version)' only agree once
    # both collapse to 'taylor s version'". Deleting punctuation instead would
    # give "taylors version" for one and the same for the other -- equal, but
    # then no keyword matches, which is the bug that left all 14 unclassified.
    straight = detect.normalize_suffix("(Taylor's Version)")
    curly = detect.normalize_suffix("(Taylor’s Version)")

    assert straight == curly == "taylor s version"


def test_normalize_suffix_keeps_digits():
    # source: E §1 / normalize_suffix's docstring -- "Digits are kept: '1947
    # version', 'remastered 1999' and '99 luftballons' all need them."
    assert detect.normalize_suffix("(Remastered 1999)") == "remastered 1999"


def test_keywords_match_whole_tokens_not_substrings():
    # source: canonical_detect.py's suffix-class comment -- keywords are
    # "matched as whole token sequences, never as bare substrings: 'feat don
    # toliver' contains 'live', and substring matching classified all 16 of
    # those as version." The negative case is the one worth pinning.
    assert detect._has_keyword("demo live", ("live",)) is True
    assert detect._has_keyword("feat don toliver", ("live",)) is False


# -- Suffix classes and their precedence (E §2.1) ---------------------------


@pytest.mark.parametrize(
    "suffix, expected",
    [
        # 0. no suffix at all
        ("", "base"),
        # 1. version keywords
        ("(Live)", "version"),
        ("(Acoustic)", "version"),
        ("(Instrumental)", "version"),
        ("(Unplugged)", "version"),
        # 2. recording keywords
        ("(Remastered)", "recording"),
        ("(Taylor's Version)", "recording"),
        ("(Deluxe)", "recording"),
        # 4. the generic "... version" catch-all
        ("(Jazz Version)", "version"),
        ("(1947 Version)", "version"),
        # 5. the generic "... mix" catch-all, added to the table by P1-013
        ("(Vocal Up Mix)", "version"),
        ("(Country Mix)", "version"),
        # 6. credit keywords, and the fallback
        ("(feat. NAV)", "neutral"),
        ("(with Phoebe Bridgers)", "neutral"),
        ("(Part 2)", "neutral"),
        ("(Full)", "neutral"),
    ],
)
def test_classify_suffix_by_family(suffix, expected):
    # source: E §2.1's class table.
    assert detect.classify_suffix(suffix) == expected


def test_version_outranks_recording():
    # source: E §2.1 precedence step 1 -- "'- Live (Remastered)' sounds
    # different, whatever the master, so version outranks recording."
    assert detect.classify_suffix("- Live (Remastered)") == "version"


def test_radio_edit_stays_recording_despite_containing_edit():
    # source: E §2.1 precedence step 2 -- recording keywords are checked
    # before the structural neutral markers, so "radio edit" is not swept up
    # by the bare "edit" marker. E §2.1 also states this directly: "Note
    # radio edit is still recording via its own keyword."
    assert detect.classify_suffix("(Radio Edit)") == "recording"
    assert detect.classify_suffix("- Edit") == "neutral"


def test_a_named_recording_mix_is_not_caught_by_the_generic_mix_rule():
    # source: E §2.1 precedence step 2 -- "'40th Anniversary Mono Mix' stays
    # recording despite containing 'mix'", i.e. the recording check runs
    # before the generic catch-all.
    assert detect.classify_suffix("(40th Anniversary Mono Mix)") == "recording"


@pytest.mark.parametrize("suffix", ["(Bonus Track Version)", "(Arr. Jazz Version)"])
def test_structural_markers_are_checked_before_the_version_catch_all(suffix):
    # source: E §2.1 precedence step 3 -- "these are structural markers, so
    # '(Bonus Track Version)' and '(Arr. Jazz Version)' must not be swept up
    # as version."
    assert detect.classify_suffix(suffix) == "neutral"


def test_a_credit_keyword_never_vetoes_a_version_keyword():
    """E §2.1's documented no-op, and the reason the neutral list is split.

    The credit keywords sit *after* the version/mix catch-all, so a suffix
    carrying both classifies `version`. Written as a test because the whole
    `_NEUTRAL_CREDIT_KEYWORDS` list is inert by construction -- its only
    observable consequence is this ordering.
    """
    # source: E §2.1 -- "a hypothetical '(feat. X) [Remix]' classifies
    # version, not neutral, because the version check runs first... a credit
    # says nothing about the audio, so it must never *veto* something that
    # does."
    assert detect.classify_suffix("(feat. X) [Remix]") == "version"
    assert detect.classify_suffix("(feat. X) [Vocal Up Mix]") == "version"


# -- Song tier (E §2.2) -----------------------------------------------------


def test_a_shared_primary_artist_merges_at_song_tier(conn):
    # source: E §2.2 -- "two tracks in a candidate group merge at song tier
    # whenever they share a **primary** artist id, whatever their suffix
    # class."
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow (Live)")

    assert shares(prefill(conn, "ta", "tb"), "ta", "tb", "song")


def test_disjoint_artists_never_merge_at_song_tier(conn):
    # source: E §2.2 -- "disjoint primary artists still never merge -- those
    # two rules are unchanged and are what keeps covers apart."
    make(conn, "ta", "Willow")
    make(conn, "tb", "Willow", artists=[OTHER_ARTIST], album="Album Two")

    assert not shares(prefill(conn, "ta", "tb"), "ta", "tb", "song")


def test_a_shared_featured_credit_alone_does_not_merge_at_song_tier(conn):
    """"Song by B" and "Song by A feat. B" are surfaced together but
    pre-filled apart."""
    # source: E §2.2 -- "A shared *featured* credit is still not enough".
    # tb credits both artists but its album is OTHER_ARTIST's, so ARTIST is
    # featured there and primary on ta -- the primary sets are disjoint.
    make(conn, "ta", "Willow")
    builders.make_album(conn, album_id="al-two", name="Album Two", artists=[OTHER_ARTIST])
    builders.make_track(
        conn, "tb", name="Willow", album_id="al-two", artists=[OTHER_ARTIST, ARTIST]
    )

    tracks = detect._fetch_tracks(conn)
    assert tracks["ta"]["primary_ids"].isdisjoint(tracks["tb"]["primary_ids"])
    assert tracks["ta"]["artist_ids"] & tracks["tb"]["artist_ids"]  # shared, but featured
    assert not shares(detect._prefill_labels(["ta", "tb"], tracks), "ta", "tb", "song")


# -- Version tier: shares_base_version, P1-013's ruling ---------------------


def test_base_and_recording_share_one_version(conn):
    # source: E §2.2 -- "`canonical_detect.py`'s `shares_base_version` is true
    # only for `base` and `recording`", because "a remaster sounds the same".
    # Distinct ISRCs so nothing but shares_base_version can merge them.
    make(conn, "ta", "Willow", isrc="ISRC-A")
    make(conn, "tb", "Willow (Remastered)", isrc="ISRC-B", album="Album Two")

    assert shares(prefill(conn, "ta", "tb"), "ta", "tb", "version")


def test_a_version_classified_track_stands_alone(conn):
    # source: E §2.2 -- "version-classified ones (acoustic, live, remix) each
    # stand alone -- two different live cuts are two different-sounding
    # things."
    make(conn, "ta", "Willow", isrc="ISRC-A")
    make(conn, "tb", "Willow (Live)", isrc="ISRC-B", album="Album Two")

    labels = prefill(conn, "ta", "tb")
    assert shares(labels, "ta", "tb", "song")
    assert not shares(labels, "ta", "tb", "version")


def test_a_neutral_suffix_does_not_share_the_base_version(conn):
    """P1-013's ruling, made executable.

    The spec used to say `neutral` joined `base` and `recording` in
    `shares_base_version`. It does not, and Finn ruled that correct: a
    neutral suffix is the one the classifier understands least, so assuming
    "sounds the same" from the class alone would be a guess made with no
    evidence.
    """
    # source: E §2.2 (rewritten 2026-08-17, P1-013, ruled code-is-right) --
    # "`neutral` stands alone, on purpose: a neutral suffix... is precisely
    # the one the classifier understands *least*, so assuming 'sounds the
    # same' from the suffix class alone is a guess made with no evidence."
    make(conn, "ta", "Willow", isrc="ISRC-A")
    make(conn, "tb", "Willow (feat. NAV)", isrc="ISRC-B", album="Album Two")

    labels = prefill(conn, "ta", "tb")
    assert detect._fetch_tracks(conn)["tb"]["suffix_class"] == "neutral"
    assert shares(labels, "ta", "tb", "song")
    assert not shares(labels, "ta", "tb", "version")


def test_a_neutral_track_still_joins_a_version_it_earns_on_evidence(conn):
    """E's own worked example: the Lemonade case.

    Excluding `neutral` from `shares_base_version` is not the same as
    isolating neutrals -- recording identity still merges them at version
    tier through the nesting branch.
    """
    # source: E §2.2 -- "'Lemonade' and 'Lemonade (feat. NAV)', sharing an
    # ISRC and duration, merge on that evidence".
    make(conn, "ta", "Lemonade", isrc="ISRC-SAME", duration_ms=195_000)
    make(
        conn,
        "tb",
        "Lemonade (feat. NAV)",
        isrc="ISRC-SAME",
        duration_ms=195_000,
        album="Album Two",
    )

    assert shares(prefill(conn, "ta", "tb"), "ta", "tb", "version")


def test_two_neutral_tracks_of_different_lengths_stay_apart(conn):
    # source: E §2.2 -- the other half of the same worked example:
    # "'Speechless (Full)' and 'Speechless (Part 2)', with different
    # durations, don't [merge]."
    make(conn, "ta", "Speechless (Full)", isrc="ISRC-SAME", duration_ms=208_000)
    make(
        conn,
        "tb",
        "Speechless (Part 2)",
        isrc="ISRC-SAME",
        duration_ms=144_000,
        album="Album Two",
    )

    assert not shares(prefill(conn, "ta", "tb"), "ta", "tb", "version")


# -- Version tier: the nesting fix ------------------------------------------


def test_two_rows_of_one_live_take_reach_a_single_version(conn):
    """The nesting fix, and why it had to exist.

    Both sides are `version`-classified, so `shares_base_version` refuses
    both. Without `same_version_group` also consulting `_same_recording`,
    they land in different version components -- and because
    `assign_recording_release` runs *scoped inside* a version component, they
    could then never merge at recording either.
    """
    # source: E §2.2 "Version tier gains a nesting fix" -- "Add:
    # same_version_group also returns true when _same_recording or
    # _same_release holds. Same recording implies same version by nesting."
    make(conn, "ta", "Willow (Live)", isrc="ISRC-SAME", duration_ms=200_000)
    make(
        conn,
        "tb",
        "Willow (Live)",
        isrc="ISRC-SAME",
        duration_ms=200_000,
        album="Album Two",
    )

    labels = prefill(conn, "ta", "tb")
    assert shares(labels, "ta", "tb", "version")
    assert shares(labels, "ta", "tb", "recording")


# -- Version tier: _clean_explicit_pair --------------------------------------


def test_a_clean_edit_shares_a_version_with_its_explicit_original(conn):
    """The rule's own reason for existing, so the fixture has to remove every
    other route to a version merge.

    **One side must be `neutral`.** A `(Clean)` / `(Explicit Ver.)` pair both
    classify `recording` and would merge through `shares_base_version`
    regardless -- deleting the `_clean_explicit_pair` call entirely leaves
    that fixture green, which is the mutation this version exists to catch.
    A `feat.` clause is exactly the case the docstring names.
    """
    # source: E §2.2 -- "_clean_explicit_pair... exists *because of* the
    # `neutral`-exclusion rule above: without it, a clean/explicit pair
    # sharing only a `neutral` or mismatched suffix class would have no other
    # path to a version merge."
    make(conn, "ta", "Willow (feat. NAV)", isrc="ISRC-A", explicit=1)
    make(conn, "tb", "Willow", isrc="ISRC-B", explicit=0, album="Album Two")

    # The fixture's premise: neither shares_base_version nor recording
    # identity can merge these, so only _clean_explicit_pair can.
    tracks = detect._fetch_tracks(conn)
    assert tracks["ta"]["suffix_class"] == "neutral"
    assert not detect._same_recording_identity(tracks, "ta", "tb")

    assert shares(prefill(conn, "ta", "tb"), "ta", "tb", "version")


def test_a_clean_edit_never_shares_a_recording_with_its_explicit_original(conn):
    """The tier distinction the deleted predecessor rule got wrong.

    They sound near-identical but are not the same audio, so version merges
    and recording must not -- even when Spotify reports the *same* ISRC for
    both, which it does for 15 groups in the real library. The `feat.` side
    again keeps `shares_base_version` out of the version answer.
    """
    # source: E §2.2 -- "never recording -- the `explicit` guard in
    # `_same_recording_identity` keeps them apart there deliberately, since
    # they're not literally the same audio"; and _same_recording_identity's
    # own docstring: "this holds even when Spotify reports the *same* ISRC for
    # the clean and explicit rows... the differing flag wins."
    make(conn, "ta", "Willow (feat. NAV)", isrc="ISRC-SAME", explicit=1)
    make(conn, "tb", "Willow", isrc="ISRC-SAME", explicit=0, album="Album Two")

    labels = prefill(conn, "ta", "tb")
    assert shares(labels, "ta", "tb", "version")
    assert not shares(labels, "ta", "tb", "recording")


def test_a_version_classified_side_vetoes_the_clean_explicit_rule(conn):
    # source: _clean_explicit_pair's docstring, per E §2.2 -- "A
    # version-classified side vetoes it: an instrumental or acoustic cut
    # genuinely sounds different, whatever its explicit flag says."
    make(conn, "ta", "Willow", isrc="ISRC-A", explicit=1)
    make(conn, "tb", "Willow (Instrumental)", isrc="ISRC-B", explicit=0, album="Album Two")

    assert not pair_rule(conn, detect._clean_explicit_pair, "ta", "tb")


def test_the_clean_explicit_rule_matches_on_the_base_title(conn):
    # source: _clean_explicit_pair's docstring -- "Matched on the base title,
    # not the full one... the suffixes are often what differ", specifically
    # "the '(Explicit Ver.)' marker that announces the very thing being
    # matched on."
    make(conn, "ta", "Willow (Explicit Ver.)", explicit=1)
    make(conn, "tb", "Willow (Clean Ver.)", explicit=0, album="Album Two")

    assert pair_rule(conn, detect._clean_explicit_pair, "ta", "tb")


def test_the_clean_explicit_rule_needs_a_shared_artist(conn):
    # source: _clean_explicit_pair's docstring -- "same base title, shared
    # artist, near-identical length, differing explicit". Without the artist
    # term it would merge two unrelated songs that happen to share a title.
    make(conn, "ta", "Willow", explicit=1)
    make(conn, "tb", "Willow", explicit=0, artists=[OTHER_ARTIST], album="Album Two")

    assert not pair_rule(conn, detect._clean_explicit_pair, "ta", "tb")


def test_the_clean_explicit_rule_needs_the_flags_to_differ(conn):
    # source: _clean_explicit_pair's docstring -- "differing `explicit`". Two
    # same-flag tracks are not a clean/explicit pair at all, and must reach a
    # version group (or not) by the ordinary rules.
    make(conn, "ta", "Willow", explicit=1)
    make(conn, "tb", "Willow", explicit=1, album="Album Two")

    assert not pair_rule(conn, detect._clean_explicit_pair, "ta", "tb")


# -- Recording and release identity -----------------------------------------


def test_recording_identity_needs_isrc_duration_and_explicit_to_agree(conn):
    # source: _same_recording_identity's docstring, per E §2.2 -- "same ISRC +
    # same duration + same explicit flag. Any of the three differing means a
    # different recording."
    make(conn, "base", "Willow", isrc="ISRC-SAME", duration_ms=200_000, explicit=0)
    make(conn, "same", "Willow", isrc="ISRC-SAME", duration_ms=200_000, explicit=0,
         album="Album Two")
    make(conn, "other-isrc", "Willow", isrc="ISRC-B", duration_ms=200_000, explicit=0,
         album="Album Three")
    make(conn, "other-len", "Willow", isrc="ISRC-SAME", duration_ms=250_000, explicit=0,
         album="Album Four")
    make(conn, "other-flag", "Willow", isrc="ISRC-SAME", duration_ms=200_000, explicit=1,
         album="Album Five")

    tracks = detect._fetch_tracks(conn)
    assert detect._same_recording_identity(tracks, "base", "same")
    assert not detect._same_recording_identity(tracks, "base", "other-isrc")
    assert not detect._same_recording_identity(tracks, "base", "other-len")
    assert not detect._same_recording_identity(tracks, "base", "other-flag")


def test_a_null_isrc_is_never_recording_identity(conn):
    # source: E §3.1 -- "both ISRCs are non-null and equal". Two NULLs are not
    # evidence of anything, and SQL's NULL semantics would otherwise make
    # every unstamped track identical to every other.
    make(conn, "ta", "Willow", isrc=None)
    make(conn, "tb", "Willow", isrc=None, album="Album Two")

    assert not pair_rule(conn, detect._same_recording_identity, "ta", "tb")


def test_recording_and_release_split_on_the_album(conn):
    """`_same_recording` is the across-releases rule; `_same_release` the
    same-album one. The album name is the only thing separating them."""
    # source: _same_recording's docstring ("One recording across two releases
    # -- the AAA-on-four-releases case") and _same_release's ("Same recording,
    # same album -- the duplicate-album-upload case").
    make(conn, "ta", "Willow", isrc="ISRC-SAME")
    make(conn, "cross", "Willow", isrc="ISRC-SAME", album="Album Two")
    make(conn, "same-album", "Willow", isrc="ISRC-SAME", album="Album One")

    tracks = detect._fetch_tracks(conn)
    assert detect._same_recording(tracks, "ta", "cross")
    assert not detect._same_release(tracks, "ta", "cross")
    assert detect._same_release(tracks, "ta", "same-album")
    assert not detect._same_recording(tracks, "ta", "same-album")


def test_two_uploads_of_one_album_share_a_release_group(conn):
    # source: E §2.2's "Recording and release tiers otherwise unchanged" plus
    # same_recording_group's comment -- "A release-tier match... must also
    # merge at recording tier, since release <= recording nesting requires
    # it."
    make(conn, "ta", "Willow", isrc="ISRC-SAME")
    make(conn, "tb", "Willow", isrc="ISRC-SAME")

    labels = prefill(conn, "ta", "tb")
    assert shares(labels, "ta", "tb", "release")
    assert shares(labels, "ta", "tb", "recording")


# -- An existing decision outranks the heuristics ---------------------------


def test_a_saved_version_grouping_survives_a_version_classified_suffix(conn):
    # source: E §2.2 / same_version_group's comment -- "An existing real match
    # always wins, including across the base/version-classified boundary: if
    # these two were once decided to be the same version, that decision is
    # never silently proposed as undone just because one of them carries a
    # '(Live)'-style suffix."
    make(conn, "ta", "Willow", isrc="ISRC-A")
    make(conn, "tb", "Willow (Live)", isrc="ISRC-B", album="Album Two")
    builders.make_group(conn, ["ta", "tb"])

    assert shares(prefill(conn, "ta", "tb"), "ta", "tb", "version")


def test_without_the_saved_grouping_the_same_pair_splits(conn):
    # The control for the test above: same rows, no track_group decision, so
    # the heuristics decide and a version-classified suffix stands alone.
    # Without this pair, the test above would pass for a prefill that ignored
    # suffix class entirely.
    make(conn, "ta", "Willow", isrc="ISRC-A")
    make(conn, "tb", "Willow (Live)", isrc="ISRC-B", album="Album Two")

    assert not shares(prefill(conn, "ta", "tb"), "ta", "tb", "version")


# -- The deterministic auto-group rule (E §3.1) -----------------------------


def test_the_auto_group_rule_matches_a_true_duplicate(conn):
    # source: E §3.1 -- ISRCs equal and non-null, normalized base *and*
    # suffix equal, durations within 2,000 ms, same explicit flag.
    make(conn, "ta", "Willow (Remastered)", isrc="ISRC-SAME", duration_ms=200_000)
    make(
        conn,
        "tb",
        "Willow (Remastered)",
        isrc="ISRC-SAME",
        duration_ms=201_500,
        album="Album Two",
    )

    assert pair_rule(conn, detect._auto_group_pair, "ta", "tb")


def test_the_auto_group_rule_does_not_strip_a_feat_clause(conn):
    # source: _auto_group_pair's docstring, per E §3.1 -- "It also
    # deliberately does *not* strip a trailing feat. clause: the rule asserts
    # certainty and stays maximally strict, and feature-neutrality belongs in
    # the prefill, which only suggests." Same ISRC and duration, so only the
    # suffix comparison can refuse this.
    make(conn, "ta", "Lemonade", isrc="ISRC-SAME", duration_ms=195_000)
    make(
        conn,
        "tb",
        "Lemonade (feat. NAV)",
        isrc="ISRC-SAME",
        duration_ms=195_000,
        album="Album Two",
    )

    assert not pair_rule(conn, detect._auto_group_pair, "ta", "tb")
    # ...and the prefill, which only suggests, still merges them at version.
    assert shares(prefill(conn, "ta", "tb"), "ta", "tb", "version")


def test_the_auto_group_rule_refuses_a_differing_explicit_flag(conn):
    """E §3.1's guard, added by P1-013 -- and the one that matters most.

    A run writes **one shared recording** per group, so a group whose rows
    disagree on `explicit` must not close at all.
    """
    # source: E §3.1 -- "both share the same `explicit` flag -- added
    # 2026-08-17 (P1-013)... A clean edit and its explicit original do **not**
    # auto-group by this rule (they still merge at version tier via
    # _clean_explicit_pair, §2.2)."
    make(conn, "ta", "Willow", isrc="ISRC-SAME", duration_ms=200_000, explicit=1)
    make(
        conn,
        "tb",
        "Willow",
        isrc="ISRC-SAME",
        duration_ms=200_000,
        explicit=0,
        album="Album Two",
    )

    assert not pair_rule(conn, detect._auto_group_pair, "ta", "tb")
    assert shares(prefill(conn, "ta", "tb"), "ta", "tb", "version")


@pytest.mark.parametrize(
    "duration_ms, matches",
    [(202_000, True), (202_001, False)],
)
def test_the_auto_group_rules_duration_tolerance_is_two_seconds(conn, duration_ms, matches):
    # source: E §3.1 -- "both durations are non-null and differ by <= 2,000
    # ms". Both sides of the boundary, because a `<` written for a `<=` is
    # invisible on any other input.
    make(conn, "ta", "Willow", isrc="ISRC-SAME", duration_ms=200_000)
    make(conn, "tb", "Willow", isrc="ISRC-SAME", duration_ms=duration_ms, album="Album Two")

    assert pair_rule(conn, detect._auto_group_pair, "ta", "tb") is matches


def test_the_auto_group_rule_refuses_a_differing_base_title(conn):
    # source: E §3.1 -- "normalized base titles are equal **and** normalized
    # suffixes are equal". A shared ISRC alone is not enough; the docstring
    # records that loosening it to bare ISRC equality "produces 7
    # recording-tier disagreements".
    make(conn, "ta", "Willow", isrc="ISRC-SAME")
    make(conn, "tb", "Wildest Dreams", isrc="ISRC-SAME", album="Album Two")

    assert not pair_rule(conn, detect._auto_group_pair, "ta", "tb")


def test_the_auto_group_rule_ignores_punctuation_and_case(conn):
    # source: E §3.1 -- the comparison is on the *normalized* base and suffix,
    # so two spellings of one title still match.
    make(conn, "ta", "Don't Blame Me", isrc="ISRC-SAME")
    make(conn, "tb", "Dont Blame Me", isrc="ISRC-SAME", album="Album Two")

    assert pair_rule(conn, detect._auto_group_pair, "ta", "tb")


# -- Detection writes nothing (codebase-health-P.md §6) ---------------------


def test_detection_writes_nothing(conn):
    # source: canonical_detect.py's module docstring -- "Proposes candidate
    # groups and pre-filled tier labels; decides nothing and writes nothing.
    # Pure computation over track/membership -- no Spotify calls." Asserted
    # against the SQL it issues, because P3 moves code between modules.
    from test_canonical_engine import WRITE_VERBS, executed_sql

    make(conn, "ta", "Willow", isrc="ISRC-SAME")
    make(conn, "tb", "Willow", isrc="ISRC-SAME", album="Album Two")
    canonical.ensure_track_groups(conn)
    conn.commit()

    statements = executed_sql(
        conn,
        lambda: (
            detect.candidate_groups(conn),
            detect.cross_buckets(conn),
            detect.auto_group_candidates(conn),
            detect.stale_recording_groups(conn),
        ),
    )

    assert statements
    for statement in statements:
        collapsed = " ".join(statement.lower().split())
        assert not any(collapsed.startswith(verb) for verb in WRITE_VERBS), statement
