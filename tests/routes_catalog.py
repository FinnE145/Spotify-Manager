"""Every route rule as a concrete, issuable request (P2_tests.md §4.6).

**One catalog, two consumers**: the permanent non-5xx sweep
(`test_routes.py`) and the ephemeral golden-snapshot tooling (`golden.py`).
Deliberately shared rather than written twice -- two lists drift, and the
drift is invisible in the worst way, since a route missing from one list is
simply untested there and nothing says so.

Capture needs the routes as URLs **carrying real ids**, which is why this is
session 4's and not session 0's: the ids come from whatever database the
caller points it at. `discover()` reads them out of the DB rather than
hard-coding them, so the same catalog serves the builders' fixture corpus here
and P3's sampled DB later.

**`discover()` raises rather than substituting a placeholder id.** A missing
fixture would otherwise turn a page test into a 404 test -- still non-5xx,
still green, and asserting nothing about the page. That is exactly the
"returned 200" non-observation `P2_tests.md` §1 warns is this session's
cheapest failure mode, so it fails loudly instead.

**What this file does not decide:** whether a job-starting POST actually runs
its job. That is the caller's fixture choice (`run_jobs_inline` to run it, a
stubbed `jobs.try_start` to assert only that the route claimed the slot). The
catalog knows URLs and bodies, nothing about threads.
"""

from dataclasses import dataclass, field

# Flask adds these to every rule; neither is a route anyone wrote.
_IMPLICIT_METHODS = {"HEAD", "OPTIONS"}


@dataclass(frozen=True)
class Case:
    """One issuable request against one route rule.

    `endpoint` + `method` is the key back to `app.url_map`, which is what
    `catalog_rules()` compares against `app_rules()` to prove the catalog is
    complete.
    """

    endpoint: str
    method: str
    path: str
    json: dict | None = None
    form: dict | None = None
    #: False for the routes whose response is not a stable document -- see
    #: `golden_cases()`.
    golden: bool = True
    #: Set only where one endpoint+method carries two rules and so two cases
    #: (today: roundtrip start/reconcile, one view function under two
    #: @app.route decorators). Without it both cases produce the same slug,
    #: which is a snapshot filename -- so a capture covering POSTs would
    #: silently write one over the other and compare() would report no diff
    #: for a route it never actually compared (P2-008).
    variant: str | None = None

    @property
    def slug(self):
        """A filename-safe name for this case, unique across the catalog
        (asserted by test_routes.py)."""
        suffix = f"_{self.variant}" if self.variant else ""
        return f"{self.method.lower()}_{self.endpoint}{suffix}"


def discover(conn):
    """The ids the parameterised routes need, read out of `conn`.

    Raises `LookupError` naming what is missing -- see the module docstring for
    why this is not allowed to fall back to a placeholder.
    """

    def one(sql, what):
        row = conn.execute(sql).fetchone()
        if row is None:
            raise LookupError(
                f"routes_catalog.discover: no {what} in this database. "
                "The route catalog needs one to build a concrete URL; build the "
                "fixture corpus before calling."
            )
        return row[0]

    def group(tier):
        return one(
            f"SELECT id FROM canonical_group WHERE tier = '{tier}' ORDER BY id",
            f"{tier}-tier canonical_group",
        )

    return {
        "track": one("SELECT track_id FROM track ORDER BY track_id", "track"),
        # The ?tracks= variants need a *pair*, and an ad-hoc group of one id
        # is rejected with a 400 before it reaches the code being swept.
        "track2": one(
            "SELECT track_id FROM track ORDER BY track_id LIMIT 1 OFFSET 1", "second track"
        ),
        "album": one("SELECT album_id FROM album ORDER BY album_id", "album"),
        "artist": one("SELECT artist_id FROM artist ORDER BY artist_id", "artist"),
        "playlist": one("SELECT playlist_id FROM snapshot ORDER BY playlist_id", "snapshot"),
        "song": group("song"),
        "version": group("version"),
        "recording": group("recording"),
        "release": group("release"),
        "card": one("SELECT id FROM card ORDER BY id", "card"),
        "label": one("SELECT id FROM label ORDER BY id", "label"),
        "generation_playlist": one(
            "SELECT playlist_id FROM generation ORDER BY ordinal", "generation"
        ),
    }


# The catalog. Paths carry `{name}` placeholders filled from discover().
#
# Every rule in app.url_map appears here exactly once per (endpoint, method) --
# `test_routes.py` asserts that, so a route added later cannot escape the sweep
# by nobody remembering to list it.
CASES = (
    # -- Pages ------------------------------------------------------------
    Case("index", "GET", "/"),
    Case("canvas", "GET", "/canvas"),
    Case("audit", "GET", "/audit"),
    Case("covers", "GET", "/covers"),
    Case("folders", "GET", "/folders"),
    Case("analytics", "GET", "/analytics"),
    # -- Entity pages -----------------------------------------------------
    Case("song_page", "GET", "/song/{song}"),
    Case("version_page", "GET", "/version/{version}"),
    Case("recording_page", "GET", "/recording/{recording}"),
    Case("release_page", "GET", "/release/{release}"),
    Case("track_page", "GET", "/track/{track}"),
    Case("album_page", "GET", "/album/{album}"),
    Case("artist_page", "GET", "/artist/{artist}"),
    Case("playlist_page", "GET", "/playlist/{playlist}"),
    Case("search_page", "GET", "/search?q=a"),
    # -- Dev pages --------------------------------------------------------
    Case("dev_index", "GET", "/dev"),
    Case("dev_scoring", "GET", "/dev/scoring"),
    Case("dev_artists", "GET", "/dev/artists"),
    Case("dev_import", "GET", "/dev/import"),
    Case("dev_roundtrip", "GET", "/dev/roundtrip"),
    Case("dev_scrobble", "GET", "/dev/scrobble"),
    Case("dev_snapshot", "GET", "/dev/snapshot"),
    Case("dev_canonical", "GET", "/dev/canonical"),
    Case("dev_generations", "GET", "/dev/generations"),
    Case("dev_generations_tenure", "GET", "/dev/generations/tenure"),
    Case("canonical_review", "GET", "/dev/canonical/review"),
    Case("canonical_cross", "GET", "/dev/canonical/cross"),
    # A redirect by design (302 to the viewer, deep-linked) -- a stable
    # document, so it snapshots fine.
    Case("canonical_group_deep_link", "GET", "/dev/canonical/group/{song}"),
    # -- Auth -------------------------------------------------------------
    # Both are public (no login guard) and neither renders a stable document:
    # /login redirects into Spotify's authorize URL with a fresh random state
    # per request, and /callback errors differently depending on the session.
    Case("login", "GET", "/login", golden=False),
    Case("callback", "GET", "/callback", golden=False),
    # -- Canonical API ----------------------------------------------------
    Case("api_canonical_queue", "GET", "/api/canonical/queue"),
    Case("api_canonical_cross", "GET", "/api/canonical/cross"),
    Case("api_canonical_cross_listing", "GET", "/api/canonical/cross/listing"),
    Case("api_canonical_autogroup_preview", "GET", "/api/canonical/autogroup/preview"),
    Case(
        "api_canonical_apply",
        "POST",
        "/api/canonical/apply",
        json={"track_ids": ["{track}"], "labels": {}},
    ),
    Case(
        "api_canonical_cross_apply",
        "POST",
        "/api/canonical/cross/apply",
        json={"track_ids": ["{track}"], "assignments": {}},
    ),
    Case("api_canonical_autogroup", "POST", "/api/canonical/autogroup"),
    Case("api_canonical_autogroup_undo", "POST", "/api/canonical/autogroup/undo"),
    Case("api_canonical_pin", "POST", "/api/canonical/pin", json={"track_id": "{track}"}),
    # -- Snapshot API -----------------------------------------------------
    Case("snapshot_status", "GET", "/api/snapshot/status"),
    Case("pull_snapshot", "POST", "/api/snapshot/pull"),
    Case("refresh_snapshot", "POST", "/api/snapshot/refresh"),
    Case("backfill_snapshot", "POST", "/api/snapshot/backfill"),
    Case("stop_snapshot", "POST", "/api/snapshot/stop"),
    Case(
        "exclude_snapshot_playlists",
        "POST",
        "/api/snapshot/exclude",
        json={"playlist_ids": ["{playlist}"], "excluded": True},
    ),
    # -- History API ------------------------------------------------------
    Case("history_status", "GET", "/api/history/status"),
    # No file part: the upload path itself is history_import.py's subject
    # (session 1). What the sweep asserts here is that the missing-file
    # precondition is a clean 4xx, not a 500.
    Case("import_history", "POST", "/api/history/import"),
    Case("reimport_history", "POST", "/api/history/reimport"),
    # -- Round-trip API ---------------------------------------------------
    Case("roundtrip_status", "GET", "/api/roundtrip/status"),
    Case("start_roundtrip", "POST", "/api/roundtrip/start"),
    Case("start_roundtrip", "POST", "/api/roundtrip/reconcile", variant="reconcile"),
    Case("stop_roundtrip", "POST", "/api/roundtrip/stop"),
    Case("alias_roundtrip_uris", "POST", "/api/roundtrip/alias", json={"aliases": []}),
    Case("clear_roundtrip_failures", "POST", "/api/roundtrip/clear-failures"),
    Case("clear_wanted_uris", "POST", "/api/roundtrip/wanted/clear", json={"source": "album"}),
    Case(
        "mute_roundtrip_listening",
        "POST",
        "/api/roundtrip/listening/mute",
        json={"muted": True},
    ),
    Case("clear_incomplete_isrc", "POST", "/api/roundtrip/incomplete-isrc/clear"),
    # -- Scrobble API -------------------------------------------------------
    Case("api_scrobble_poll", "POST", "/api/scrobble/poll"),
    Case("api_scrobble_toggle", "POST", "/api/scrobble/toggle", json={"enabled": True}),
    # -- Backfill API -----------------------------------------------------
    Case("backfill_status", "GET", "/api/backfill/status"),
    Case("start_backfill_job", "POST", "/api/backfill/start", json={"generations": 2}),
    Case("stop_backfill_job", "POST", "/api/backfill/stop"),
    # -- Scoring API ------------------------------------------------------
    Case("api_scoring_recompute", "POST", "/api/scoring/recompute"),
    # -- Artists API ------------------------------------------------------
    Case(
        "alias_artists",
        "POST",
        "/api/artists/alias",
        json={"artist_id_a": "{artist}", "artist_id_b": "{artist}", "same": False},
    ),
    Case("unmerge_artist", "POST", "/api/artists/unmerge", json={"artist_id": "{artist}"}),
    # -- Generations ------------------------------------------------------
    Case(
        "dev_generations_confirm",
        "POST",
        "/dev/generations/confirm",
        form={"playlist_id": "{generation_playlist}", "decision": "no"},
    ),
    # -- Canvas API -------------------------------------------------------
    Case("get_board", "GET", "/api/board"),
    Case("export", "GET", "/api/export"),
    Case(
        "update_card",
        "POST",
        "/api/card/{card}",
        json={"placement": "placed", "x": 10, "y": 20},
    ),
    Case("patch_card", "PATCH", "/api/card/{card}", json={"note": "hello"}),
    Case("create_label", "POST", "/api/label", json={"text": "New", "x": 5, "y": 6}),
    Case("update_label", "PATCH", "/api/label/{label}", json={"text": "Renamed"}),
    Case("delete_label", "DELETE", "/api/label/{label}"),
    # -- Query-string variants --------------------------------------------
    #
    # Every case above issues a query-string-free path, so an alternate render
    # path behind a query param is invisible to the sweep -- not because it was
    # forgotten, but because `catalog_rules()` keys on (endpoint, method) and a
    # query string is neither. `test_catalog_covers_every_registered_route`
    # therefore cannot catch it: the route *is* covered, one of its two bodies
    # is not. Session 4 hit one instance of this (`?generation=1`) and filled it
    # as a one-off; session 5's consolidated pass found it was systematic, so
    # the variants live here, in the shared catalog, where the golden capture
    # gets them too.
    #
    # Values are deliberately **fixture-independent** -- a filter that matches
    # nothing still takes the branch, and baking corpus strings in here would
    # break against P3's sampled DB. What the *content* of a filtered page
    # should be is asserted in `test_routes.py`, not here.
    Case("playlist_page", "GET", "/playlist/{playlist}?generation=1", variant="generation"),
    Case(
        "playlist_page",
        "GET",
        "/playlist/{playlist}?generation=1&tier=song",
        variant="generation_song",
    ),
    Case("dev_canonical", "GET", "/dev/canonical?q=zzz", variant="q"),
    Case("dev_canonical", "GET", "/dev/canonical?cross=zzz", variant="cross"),
    Case("dev_canonical", "GET", "/dev/canonical?search=zzz", variant="search"),
    Case("dev_canonical", "GET", "/dev/canonical?singletons=1", variant="singletons"),
    Case("dev_canonical", "GET", "/dev/canonical?expand={song}", variant="expand"),
    Case(
        "canonical_review",
        "GET",
        "/dev/canonical/review?tracks={track},{track2}",
        variant="tracks",
    ),
    Case(
        "api_canonical_queue",
        "GET",
        "/api/canonical/queue?tracks={track},{track2}",
        variant="tracks",
    ),
    Case("api_canonical_queue", "GET", "/api/canonical/queue?queue=pending", variant="pending"),
    Case(
        "api_canonical_cross",
        "GET",
        "/api/canonical/cross?tracks={track},{track2}",
        variant="tracks",
    ),
    Case(
        "api_canonical_cross_listing",
        "GET",
        "/api/canonical/cross/listing?cross=zzz",
        variant="cross",
    ),
    Case("dev_snapshot", "GET", "/dev/snapshot?q=zzz", variant="q"),
    Case("dev_generations", "GET", "/dev/generations?tier=song", variant="song"),
    Case("dev_generations_tenure", "GET", "/dev/generations/tenure?tier=song", variant="song"),
    Case(
        "dev_generations_tenure",
        "GET",
        "/dev/generations/tenure?sort=score&page=2",
        variant="sorted",
    ),
    # An unrecognised sort falls back to "tenure" rather than reaching SQL --
    # the whitelist is what keeps `sort` out of an interpolated ORDER BY.
    Case(
        "dev_generations_tenure",
        "GET",
        "/dev/generations/tenure?sort=;drop",
        variant="bad_sort",
    ),
    Case("export", "GET", "/api/export?cutoff=100", variant="cutoff"),
    # /callback's two argument-driven refusals. Neither reaches Spotify: both
    # abort before the token exchange, which is why the third arm (a valid
    # state carrying a code) is deliberately absent.
    Case("callback", "GET", "/callback?error=access_denied", golden=False, variant="error"),
    Case("callback", "GET", "/callback?state=mismatched", golden=False, variant="bad_state"),
)


def _fill(value, ids):
    """Substitutes `{name}` placeholders anywhere in a path or a JSON body."""
    if isinstance(value, str):
        return value.format(**ids) if "{" in value else value
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    return value


def cases_for(conn):
    """Every case with its placeholders resolved against `conn`."""
    ids = discover(conn)
    return [
        Case(
            endpoint=case.endpoint,
            method=case.method,
            path=_fill(case.path, ids),
            json=_fill(case.json, ids),
            form=_fill(case.form, ids),
            golden=case.golden,
            variant=case.variant,
        )
        for case in CASES
    ]


def golden_cases(conn):
    """The subset a golden snapshot is meaningful for: GET only.

    A POST changes state, so capturing it would make the snapshot depend on
    the order the capture ran in -- and the request would have to be re-issued
    against a mutated database on compare. `golden=False` additionally drops
    the two responses that legitimately differ run to run (see the auth cases).
    """
    return [c for c in cases_for(conn) if c.method == "GET" and c.golden]


def issue(client, case):
    """Sends one case through a Flask test client."""
    kwargs = {}
    if case.json is not None:
        kwargs["json"] = case.json
    if case.form is not None:
        kwargs["data"] = case.form
    return client.open(case.path, method=case.method, **kwargs)


def app_rules(app):
    """`{(endpoint, method)}` for every route rule the app actually registers."""
    return {
        (rule.endpoint, method)
        for rule in app.url_map.iter_rules()
        if rule.endpoint != "static"
        for method in rule.methods - _IMPLICIT_METHODS
    }


def catalog_rules():
    """`{(endpoint, method)}` this catalog covers."""
    return {(case.endpoint, case.method) for case in CASES}
