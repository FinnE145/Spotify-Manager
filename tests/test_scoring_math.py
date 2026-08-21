"""scoring.py's pure functions (docs/specs/scoring-H.md §4.4, §5, §8) -- no DB,
no fixtures. `_sat`, `_raw`, `combine`, `_display`, `_undisplay`, `group_score`.

Every expected literal here was derived by hand from the spec's formulas, not
read off a run of this module -- see docs/codebase-health/P2_tests.md §2.
"""

import pytest

import scoring


def test_the_saturating_transform_puts_the_half_value_at_one_half():
    # source: scoring-H.md §4.4 -- "**`K` is the half-value** -- the input
    # level scoring 0.5 on that term"
    assert scoring._sat(0.5, scoring.K_RATE) == pytest.approx(0.5)
    assert scoring._sat(4.0, scoring.K_MEM) == pytest.approx(0.5)
    assert scoring._sat(2.0, scoring.K_TEN) == pytest.approx(0.5)
    assert scoring._sat(0, 1.0) == 0.0


def test_the_transform_stays_rankable_in_the_tail():
    """§4.4: "`x/(x+K)` decays as `1 − K/x`, giving 0.909 and 0.952 at those
    points" (x=10 and x=20 at K=1) -- the property exp/tanh lose ("differ by
    one part in a thousand")."""
    # source: scoring-H.md §4.4
    a = scoring._sat(10, 1.0)
    b = scoring._sat(20, 1.0)
    assert a == pytest.approx(0.9091, abs=1e-4)
    assert b == pytest.approx(0.9524, abs=1e-4)
    assert (b - a) > 0.01


def test_each_term_carries_its_own_weight():
    """One term saturated to exactly 0.5 at a time, so a swapped weight is
    visible. Deliberately not R=0.5/M=4/T=2 together -- that gives 0.5 under
    *any* set of weights summing to 1 and discriminates nothing."""
    # source: scoring-H.md §4.4 -- "raw = W_RATE·g(R, K_RATE) + W_MEM·g(M,
    # K_MEM) + W_TEN·g(T, K_TEN)", weights 0.40/0.35/0.25 (§10.1)
    assert scoring._raw({"R": 0.5, "M": 0, "T": 0}) == pytest.approx(0.20)
    assert scoring._raw({"R": 0, "M": 4, "T": 0}) == pytest.approx(0.175)
    assert scoring._raw({"R": 0, "M": 0, "T": 2}) == pytest.approx(0.125)


def test_the_weights_sum_to_one_so_raw_is_bounded_below_one():
    # source: scoring-H.md §4.4 -- "with `W_RATE + W_MEM + W_TEN = 1`, so
    # `raw ∈ [0, 1)`"
    assert (scoring.W_RATE + scoring.W_MEM + scoring.W_TEN) == pytest.approx(1.0)
    assert scoring._raw({"R": 1e9, "M": 1e9, "T": 1e9}) < 1.0


def test_the_combiner_reproduces_the_published_power_mean_table(monkeypatch):
    """The exact numbers scoring-H.md §5.2 tabulates for p=1 and p=3, on the
    ATG/banger/dead example (1.0, 0.8, 0.1). P_AGG is monkeypatched only
    because §5.2's table uses p=1 and p=3 to illustrate the tradeoff -- the
    shipped exponent (2.5) is covered separately below."""
    # source: scoring-H.md §5.2's worked table
    playlist = [1.0] + [0.8] * 4
    album_a = [1.0] + [0.8] * 4 + [0.1] * 5
    album_b = [1.0, 0.8] + [0.1] * 8

    monkeypatch.setattr(scoring, "P_AGG", 3)
    assert scoring.combine(playlist) == pytest.approx(0.848, abs=1e-3)
    assert scoring.combine(album_a) == pytest.approx(0.673, abs=1e-3)
    assert scoring.combine(album_b) == pytest.approx(0.534, abs=1e-3)

    monkeypatch.setattr(scoring, "P_AGG", 1)
    assert scoring.combine(playlist) == pytest.approx(0.840, abs=1e-3)
    assert scoring.combine(album_a) == pytest.approx(0.470, abs=1e-3)


def test_at_the_shipped_exponent_a_tight_playlist_beats_a_padded_album_beats_a_thin_one():
    """Same three collections, at the real P_AGG = 2.5 (§10.1)."""
    # source: scoring-H.md §5.2 -- "Tail must barely hurt ... But the
    # proportion of great tracks must matter."
    playlist = [1.0] + [0.8] * 4
    album_a = [1.0] + [0.8] * 4 + [0.1] * 5
    album_b = [1.0, 0.8] + [0.1] * 8

    p = scoring.combine(playlist)
    a = scoring.combine(album_a)
    b = scoring.combine(album_b)
    assert p == pytest.approx(0.845817, abs=1e-6)
    assert a == pytest.approx(0.642240, abs=1e-6)
    assert b == pytest.approx(0.480177, abs=1e-6)
    assert p > a > b


def test_the_combiner_is_size_independent():
    # source: scoring-H.md §5.1 -- "Size independence is structural -- it
    # comes from the division inside, not from any normalization step
    # afterwards."
    assert scoring.combine([0.8] * 3) == pytest.approx(0.8)
    assert scoring.combine([0.8] * 30) == pytest.approx(0.8)


def test_an_empty_collection_scores_zero():
    # source: scoring-H.md §5.1; the `peak = max(scores) or 1.0` guard
    assert scoring.combine([]) == 0.0
    assert scoring.combine([0.0, 0.0]) == 0.0


def test_the_tail_floor_is_inert_at_one_and_bites_below_it(monkeypatch):
    """TAIL_FLOOR=1.0 (shipped) gives "full proportion sensitivity (harsh
    tail)"; TAIL_FLOOR=0.0 "weights purely by score (dead members nearly
    ignored)" -- which raises the collection's score, since the dead member
    now barely counts against it."""
    # source: scoring-H.md §5.2
    harsh = scoring.combine([1.0, 0.1])
    assert harsh == pytest.approx(0.758816, abs=1e-6)

    monkeypatch.setattr(scoring, "TAIL_FLOOR", 0.0)
    lenient = scoring.combine([1.0, 0.1])
    assert lenient == pytest.approx(0.962715, abs=1e-6)
    assert lenient > harsh


def test_a_membership_weight_below_one_discounts_that_member():
    # source: scoring-H.md §5.3 -- "`uᵢ` is how strongly a member belongs to
    # the collection ... **`FEATURED_WEIGHT` (< 1)** for a version that
    # reaches an artist only through a featured credit."
    flat = scoring.combine([1.0, 0.5])
    discounted = scoring.combine([1.0, 0.5], [1.0, scoring.FEATURED_WEIGHT])
    assert flat == pytest.approx(0.808846, abs=1e-6)
    assert discounted == pytest.approx(0.862709, abs=1e-6)
    assert discounted > flat


def test_the_display_transform_is_a_scaled_square_root():
    # source: scoring-H.md §8 -- "display = SCALE · score ^ GAMMA"
    assert scoring._display(0.25) == pytest.approx(50.0)
    assert scoring._display(1.0) == pytest.approx(100.0)
    assert scoring._display(0.0) == 0.0


def test_display_is_unbounded_above():
    # source: scoring-H.md §8 -- "**Unbounded above.** Nothing is pinned at
    # 100, and a new all-time favourite does not push everything else down."
    assert scoring._display(1.44) == pytest.approx(120.0)


def test_display_floors_a_negative_score_at_zero():
    """The clamp is a guard against a future term, not a live path -- §8:
    "do not read its presence as evidence that negatives occur today.\""""
    # source: scoring-H.md §8 -- "Floored at 0 ... Keep the `max(s, 0)`
    # anyway, as a guard against a future term that can go negative"
    assert scoring._display(-0.5) == 0.0


def test_undisplay_inverts_display():
    # source: scoring-H.md §8 / _undisplay's docstring -- "Monotonic, so this
    # round-trip changes no ordering"
    assert scoring._undisplay(scoring._display(0.37)) == pytest.approx(0.37)
    assert scoring._undisplay(50.0) == pytest.approx(0.25)
    assert scoring._undisplay(-5.0) == 0.0


def test_group_score_combines_in_normalized_space_not_display_space():
    """The fixture (100.0, 25.0) is chosen so the plausible wrong
    implementations all disagree with the right one: combining directly in
    display space gives 76.7244, and a plain mean (p=1) in normalized space
    gives 72.8869. Neither is the answer below."""
    # source: scoring-H.md §8 -- "Callers holding materialized (display-
    # space) scores must _undisplay() them first -- this function only ever
    # operates in normalized space", via group_score's docstring
    assert scoring.group_score([100.0, 25.0]) == pytest.approx(87.0721, abs=1e-3)
    assert scoring.group_score([]) == 0.0
