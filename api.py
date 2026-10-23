"""The v1 JSON API's routes (docs/specs/api-Z1.md §2.1): one Blueprint, mounted
at /api/v1 by create_app(). Route plumbing only -- parse and validate params,
call a resources.py builder, jsonify.

This deliberately departs from P3's "no blueprints" decision for this one
surface (§2.1): a versioned API is a self-contained unit with its own
conventions -- the param parser below, its own self-describing index, and the
login-guard exemption of §4.1 (keyed on `request.blueprint == "api"`).

**Params are declared once, as data** (`P`), and one parser reads that
declaration while the index (GET /api/v1/) is built from the same one, so
parsing and documentation cannot disagree (§5.2). **Nothing here may import
`roundtrip`** (§3.2): v1 is the surface AB will expose beyond the tailnet, and
roundtrip.py is the only module that writes to the Spotify library. No CORS
header is ever set (§4.2): the same-origin policy is what stops another site's
page reading this through Finn's browser.
"""

import inspect
import re

from flask import Blueprint, current_app, jsonify, request

import canonical
import db
import resources
from resources import ApiError

bp = Blueprint("api", __name__)


# -- Param declarations ---------------------------------------------------------------


class P:
    """One declared param: name, type, default, a one-line doc. `type` is one of
    str, int, bool, enum (with `choices`). `where` is "query" or "body"."""

    def __init__(self, name, type_, doc, default=None, choices=None, required=False,
                 minimum=None, maximum=None, where="query"):
        self.name, self.type, self.doc, self.default = name, type_, doc, default
        self.choices, self.required = choices, required
        self.minimum, self.maximum, self.where = minimum, maximum, where

    def describe(self):
        out = {"name": self.name, "type": self.type, "default": self.default,
               "doc": self.doc, "in": self.where}
        if self.choices:
            out["choices"] = list(self.choices)
        if self.required:
            out["required"] = True
        return out

    def parse(self, raw):
        name = self.name
        if self.type == "str":
            return raw
        if self.type == "bool":
            if raw in ("1", "true"):
                return True
            if raw in ("0", "false"):
                return False
            raise ApiError("invalid_parameter", 400, f"{name}: expected 1 or 0")
        if self.type == "enum":
            if raw not in self.choices:
                raise ApiError("invalid_parameter", 400,
                               f"{name}: expected one of {', '.join(self.choices)}")
            return raw
        try:
            value = int(raw)
        except ValueError:
            raise ApiError("invalid_parameter", 400, f"{name}: expected an integer") from None
        if self.minimum is not None and value < self.minimum:
            raise ApiError("invalid_parameter", 400, f"{name}: must be at least {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise ApiError("invalid_parameter", 400, f"{name}: must be at most {self.maximum}")
        return value


def parse_args(declared, inapplicable=()):
    """The request's query string against its endpoint's declaration. An
    unknown param is a 400; a real filter on a type it does not apply to is a
    400 of its own kind (`inapplicable`), never silently ignored (§5.2)."""
    by_name = {p.name: p for p in declared if p.where == "query"}
    for name in request.args.keys():
        if name in by_name:
            continue
        if name in inapplicable:
            raise ApiError("unsupported_filter", 400, name)
        raise ApiError("unknown_parameter", 400, name)
    out = {}
    for name, p in by_name.items():
        values = request.args.getlist(name)
        if len(values) > 1:
            raise ApiError("invalid_parameter", 400, f"{name}: given more than once")
        if values:
            out[name] = p.parse(values[0])
        elif p.required:
            raise ApiError("invalid_parameter", 400, f"{name} is required")
        else:
            out[name] = p.default
    return out


WINDOW = [
    P("tz", "str", f"IANA time zone for calendar boundaries (day starts, buckets); never changes a "
      f"returned timestamp.", default=resources.DEFAULT_TZ),
    P("from", "str", "Start of the play window: a bare date (00:00 in tz) or an ISO-8601 datetime with an offset."),
    P("to", "str", "End of the play window: a bare date (that whole day, in tz) or a datetime with an offset (exclusive)."),
    P("min_played_ms", "int", "Exclude plays shorter than this from everything this response counts.", minimum=0),
]
PAGING = [
    P("page", "int", "1-based page.", default=1, minimum=1),
    P("per_page", "int", f"Items per page (max {resources.MAX_PER_PAGE}).",
      default=resources.DEFAULT_PER_PAGE, minimum=1, maximum=resources.MAX_PER_PAGE),
]

_FILTER_PARAMS = {
    "q": P("q", "str", "Normalized name starts with this (accents/punctuation/spaces folded)."),
    "q_contains": P("q_contains", "str", "Normalized name contains this anywhere."),
    "q_full": P("q_full", "str", "Normalized name equals this, with nothing either side."),
    "artist": P("artist", "str", "Credited to this artist id in any role (alias-resolved)."),
    "artist_role": P("artist_role", "enum", "Narrow `artist` to this role (needs `artist`).",
                     choices=("primary", "featured")),
    "album": P("album", "str", "On this album id."),
    "playlist": P("playlist", "str", "Live in this playlist id."),
    "generation": P("generation", "int", "Present in this generation (ordinal)."),
    "min_ms": P("min_ms", "int", "Duration in ms at least this (a group's is its representative's).", minimum=0),
    "max_ms": P("max_ms", "int", "Duration in ms at most this.", minimum=0),
    "year_from": P("year_from", "int", "Release year at least this (a group's is its earliest member's)."),
    "year_to": P("year_to", "int", "Release year at most this."),
    "explicit": P("explicit", "bool", "1 for explicit tracks only, 0 for clean only."),
    "min_memberships": P("min_memberships", "int", "Live membership rows across its tracks at least this.", minimum=0),
    "min_plays": P("min_plays", "int", "Plays at least this: the window count if from/to are given, else the total.", minimum=0),
}
_SORT_DOC = (
    "Ordered, comma-separated sort keys, each optionally prefixed `-` for descending "
    "(e.g. `-memberships,-plays`). Keys for this type: {keys}. An id tie-break is always appended."
)

# endpoint name -> its declared params, for the index
_PARAMS = {}


def route(rule, params=(), methods=("GET",)):
    def decorate(fn):
        _PARAMS[fn.__name__] = list(params)
        bp.add_url_rule(rule, endpoint=fn.__name__, view_func=fn, methods=list(methods))
        return fn
    return decorate


def _window(a):
    return resources.make_window(a["tz"], a["from"], a["to"], a["min_played_ms"])


def _not_found(what):
    return ApiError("not_found", 404, f"No such {what}.")


def _body(*fields):
    """A write's JSON body: an object holding exactly the declared fields."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ApiError("invalid_parameter", 400, "a JSON object body is required")
    for name in body:
        if name not in fields:
            raise ApiError("unknown_parameter", 400, name)
    return body


# -- Hooks and errors ----------------------------------------------------------------


@bp.before_request
def ensure_groups():
    """§2.3: the grouping catch-up every page does, once for every v1 request so
    no endpoint can forget it. An idempotent SELECT on the common case."""
    conn = db.get_db()
    canonical.ensure_track_groups(conn)
    conn.commit()


@bp.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify({"error": e.slug, "detail": e.detail}), e.status


# -- The index (§5.4) ------------------------------------------------------------------


@bp.route("/")
def index():
    """The API's whole reference: base URL plus this document describes everything.

    `defaults` are the values every endpoint shares; `shapes` documents each
    entity summary once (the same shape wherever the entity appears -- in a
    list, a search result, or nested in another); `endpoints` has one entry per
    route, built from the Blueprint's own rules, with its full prose reference
    and the params it accepts. Nothing here is OpenAPI, deliberately."""
    endpoints = []
    for rule in sorted(current_app.url_map.iter_rules(), key=lambda r: (r.rule, sorted(r.methods))):
        if not rule.endpoint.startswith("api."):
            continue
        name = rule.endpoint.split(".", 1)[1]
        view = current_app.view_functions[rule.endpoint]
        path = re.sub(r"<(?:\w+:)?(\w+)>", r"<\1>", rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            endpoints.append({
                "method": method, "path": path, "doc": inspect.getdoc(view) or "",
                "params": [p.describe() for p in _PARAMS.get(name, [])],
            })
    return jsonify({
        "version": "v1",
        "defaults": {"tz": resources.DEFAULT_TZ, "per_page": resources.DEFAULT_PER_PAGE,
                     "max_per_page": resources.MAX_PER_PAGE},
        "shapes": {t: inspect.cleandoc(doc) for t, doc in resources.SHAPE_DOCS.items()},
        "endpoints": endpoints,
    })


_PARAMS["index"] = []


# -- Status and search -------------------------------------------------------------------


@route("/status")
def status():
    """Data freshness, so an answer can say how current it is.

    `spotify_authenticated` is whether the server holds a valid Spotify token
    (computed without the login guard, so this answers even when it has
    lapsed -- that answer is the point); `active_job` the running background
    job or null; `last_pull_at` the latest successful playlist item read;
    `scrobble.next_poll_at` is null when no poller runs in this process;
    `plays.data_through` / `export_through` / `last_import_at` how far the play
    data reaches; `scoring` the last recompute's time and outcome (no error
    text); `pending_generation` the unanswered new-generation prompt or null;
    `spotify_requests` the quota-counting calls made in the last 24h / 7d; and
    `library` row counts (followed playlists only). Holds no tokens and no
    tracebacks. Spends no Spotify request. May refresh the token when checking
    `spotify_authenticated`."""
    parse_args([])
    return jsonify(resources.status(db.get_db()))


@route("/search", params=[
    P("q", "str", "The query. Below the matcher's minimum length (after normalizing) every list is empty.", required=True),
    P("type", "enum", "Return one type's full ranked list, paginated, instead of the sections.",
      choices=("songs", "albums", "artists", "playlists")),
    *PAGING,
])
def search():
    """The same matcher and ranking as the site's /search, as JSON.

    Without `type`: `{q, most_relevant, songs, albums, artists, playlists}` --
    `most_relevant` the mixed top list (each item a summary of its own type),
    and each type section `{items, total}` holding its first few. With `type`:
    that one section in the list envelope, paging through the whole ranked
    list (what See more does). Songs rank at the version tier, so song results
    are version summaries. Item order is exactly the matcher's; relevance
    numbers are not exposed. Spends no Spotify request."""
    declared = _PARAMS["search"]
    a = parse_args(declared)
    if a["type"] is None and ("page" in request.args or "per_page" in request.args):
        raise ApiError("invalid_parameter", 400, "page and per_page need type")
    return jsonify(resources.search_results(db.get_db(), a["q"].strip(), a["type"], a["page"], a["per_page"]))


# -- Lists and details, one pair per entity type ---------------------------------------------

_LIST_DOC = """{label} list: filtered, sorted and paginated.

Returns `{{items, total, page, per_page}}`. Each item is the {type} summary plus a
`metrics` object: `plays` ({{total, past_30d, past_7d, window, data_through}} --
the rolling windows are null when the play data ends before they start, and
`window` is the count inside from/to, null when neither is given){extra_metrics}.
Name filters (`q`, `q_contains`, `q_full`, at most one) compare normalized names:
accents, punctuation and spaces are folded, so `half alive` matches `half•alive`.
Filters run in SQL before the sort; metrics are built for the returned page only.
Sorting is applied to the whole filtered set before the page is cut, and `score`
sorts treat an entity with no materialized score as lowest. A date range applies
to plays only: scores exist only for the all-time and last-90-days horizons, and
`memberships` always means live now. Spends no Spotify request."""

_DETAIL_DOC = """{label} detail: the {type} summary plus facts.

`plays` is {{total, past_30d, past_7d, window, data_through}} over every track it
covers (the rolling windows are null when the play data ends before they start;
`window` is the count inside from/to, null when neither is given).{sections}
{request_doc}"""

_SECTION_DOCS = {
    "track": (" `groups` holds its song/version/recording/release summaries; `memberships` every "
              "playlist row (live and removed; unfollowed playlists excluded) with position, "
              "added_at and removed_at; `aliases` the requested uris relinked onto it. "
              "`playlists` lists the playlists holding it live, each with its added_at, and "
              "`generations` is {ordinals, total, tenure, run_count, first_ordinal, last_ordinal}."),
    "group": (" `pinned` (song only; null at other tiers) says whether the representative is pinned; "
              "`parents` the summaries of the tiers above, keyed by tier; `children` the next tier "
              "down (null at release); `members` every member track, ordered by score then name. "
              "`playlists` lists the playlists holding any member live, each with the earliest live "
              "added_at; `generations` is {ordinals, total, tenure, run_count, first_ordinal, last_ordinal}."),
    "album": (" `tracklist` is the stored tracklist ({disc_number, track_number, name, uri, "
              "duration_ms, track}, where `track` is the owned track's summary or null) -- **null, not "
              "empty, if it was never fetched**, with `tracklist_count` and `total_duration_ms` null "
              "alongside it; `tracklist_fetched_at` set with `tracklist` still null means an earlier "
              "fetch failed. `appended` holds owned tracks not in the stored tracklist (all of them "
              "when it is null). `playlists` and `generations` cover the owned tracks."),
    "artist": (" `image_url` is null until fetched (`image_fetched_at` set with it still null means "
               "an earlier fetch failed). `merged_ids` are the alias ids folded into it; its tracks "
               "are not inlined -- list them with /api/v1/versions?artist=<id>. `playlists` and "
               "`generations` cover every track credited to it."),
    "playlist": (" `generation` is {ordinal, started_at, ended_at} or null. Its items are not "
                 "inlined: see /api/v1/playlists/<id>/items. A playlist has no `playlists` or "
                 "`generations` section."),
}
_REQUEST_DOC = {
    "album": ("`spotify_request=1` makes exactly one Spotify request (always -- even if the tracklist "
              "was fetched before) to fetch the tracklist; without it nothing is fetched. With it: "
              "no valid server-side Spotify sign-in is a 503 `spotify_not_authenticated` (nothing "
              "stamped); a failed request is a 502 `spotify_request_failed` (the attempt is "
              "stamped). `queue_unowned=1` queues the stored tracklist's unowned uris for the "
              "round-trip (zero Spotify cost); without it a read writes nothing."),
    "artist": ("`spotify_request=1` makes exactly one Spotify request (always) to fetch the image; "
               "without it nothing is fetched. With it: no valid server-side Spotify sign-in is a 503 "
               "`spotify_not_authenticated` (nothing stamped); a failed request is a 502 "
               "`spotify_request_failed` (the attempt is stamped). Spends no request otherwise."),
}


def _list_params(type_):
    names = [n for n, types in resources.FILTER_TYPES.items() if type_ in types]
    keys = [k for k, types in resources.SORT_TYPES.items() if type_ in types]
    return (
        [_FILTER_PARAMS[n] for n in names]
        + [P("sort", "str", _SORT_DOC.format(keys=", ".join(keys)), default="-score")]
        + WINDOW + PAGING
    )


def _parse_sort(raw, type_):
    sort, seen = [], set()
    for part in raw.split(","):
        part = part.strip()
        desc = part.startswith("-")
        key = part[1:] if desc else part
        if key not in resources.SORT_TYPES:
            raise ApiError("invalid_parameter", 400, f"sort: unknown key {key!r}")
        if type_ not in resources.SORT_TYPES[key]:
            raise ApiError("unsupported_sort", 400, key)
        if key in seen:
            raise ApiError("invalid_parameter", 400, f"sort: {key!r} given twice")
        seen.add(key)
        sort.append((key, desc))
    return sort


def _make_list_view(type_):
    plural = resources.PLURAL[type_]
    inapplicable = {n for n, types in resources.FILTER_TYPES.items() if type_ not in types}

    def view():
        a = parse_args(_PARAMS[f"list_{plural}"], inapplicable)
        modes = [m for m in ("q", "q_contains", "q_full") if a[m] is not None]
        if len(modes) > 1:
            raise ApiError("invalid_parameter", 400, "q, q_contains and q_full are mutually exclusive")
        if a.get("artist_role") is not None and a.get("artist") is None:
            raise ApiError("invalid_parameter", 400, "artist_role needs artist")
        filters = {n: a[n] for n in resources.FILTER_TYPES if n in a and a[n] is not None}
        return jsonify(resources.list_entities(
            db.get_db(), type_, filters, _parse_sort(a["sort"], type_), _window(a),
            a["page"], a["per_page"],
        ))

    view.__name__ = f"list_{plural}"
    extra = "" if type_ == "playlist" else (
        ", `memberships` (live membership rows across its tracks), `generations` (how many "
        "generations it was present in) and `tenure` (its longest consecutive run of them)")
    view.__doc__ = _LIST_DOC.format(label=plural.capitalize(), type=type_, extra_metrics=extra)
    route(f"/{plural}", params=_list_params(type_))(view)


def _make_detail_view(type_):
    plural = resources.PLURAL[type_]
    group = type_ in resources.GROUP_TIERS
    extra = []
    if type_ == "album":
        extra = [P("spotify_request", "bool", "Make exactly one Spotify request to (re)fetch the tracklist.", default=False),
                 P("queue_unowned", "bool", "Queue the stored tracklist's unowned uris for the round-trip.", default=False)]
    elif type_ == "artist":
        extra = [P("spotify_request", "bool", "Make exactly one Spotify request to (re)fetch the image.", default=False)]
    declared = WINDOW + extra
    arg = "group_id" if group else f"{type_}_id"

    def view(**kwargs):
        entity_id = kwargs[arg]
        a = parse_args(_PARAMS[f"{type_}_detail"])
        data = resources.detail(
            db.get_db(), type_, entity_id, _window(a),
            spotify_request=bool(a.get("spotify_request")), queue_unowned=bool(a.get("queue_unowned")),
        )
        if data is None:
            raise _not_found(type_)
        return jsonify(data)

    view.__name__ = f"{type_}_detail"
    section = _SECTION_DOCS["group" if group else type_]
    view.__doc__ = _DETAIL_DOC.format(
        label=type_.capitalize(), type=type_, sections=section,
        request_doc=_REQUEST_DOC.get(type_, "Spends no Spotify request."),
    )
    converter = "int:" if group else ""
    route(f"/{plural}/<{converter}{arg}>", params=declared)(view)


for _type in resources.TYPES:
    _make_list_view(_type)
    _make_detail_view(_type)


@route("/playlists/<playlist_id>/items", params=[
    P("include_removed", "bool", "Include removed rows as well as live ones.", default=False), *PAGING])
def playlist_items(playlist_id):
    """A playlist's items in position order, paginated (Finn All alone is thousands).

    Each item is `{position, added_at, removed_at, track}` with `track` the track
    summary. Live rows only by default; `include_removed=1` adds removed rows.
    List envelope `{items, total, page, per_page}`. Spends no Spotify request."""
    a = parse_args(_PARAMS["playlist_items"])
    data = resources.playlist_items(db.get_db(), playlist_id, a["page"], a["per_page"], bool(a["include_removed"]))
    if data is None:
        raise _not_found("playlist")
    return jsonify(data)


# -- Plays (§9.1, §9.2) -----------------------------------------------------------------------

_PLAY_FILTERS = [
    P(name, "str", f"Only plays resolving to this {name} id" +
      (" (any role, alias-resolved)." if name == "artist" else
       " (tracks live in it now)." if name == "playlist" else "."))
    for name in resources.PLAY_ENTITY_FILTERS
] + [
    P("source", "enum", "Only plays from this source.", choices=("export", "scrobble")),
]


def _play_entity(a):
    given = [(n, a[n]) for n in resources.PLAY_ENTITY_FILTERS if a[n] is not None]
    if len(given) > 1:
        raise ApiError("invalid_parameter", 400, "at most one entity filter")
    return given[0] if given else None


@route("/plays", params=[
    *_PLAY_FILTERS,
    P("sort", "enum", "`-ts` newest first, `ts` oldest first.", default="-ts", choices=("ts", "-ts")),
    *WINDOW, *PAGING])
def plays():
    """Individual plays, newest first (`sort=ts` reverses).

    List envelope. Each play: `{id, ts, ms_played, source, track, spotify_track_uri,
    reported, reason_start, reason_end, shuffle, skipped, platform, conn_country,
    offline, offline_ts, incognito_mode}`. `track` is the track summary, resolved
    through relinked uris, or null for a play that does not resolve (those cannot
    match an entity filter). `reported` is what the export claimed at play time;
    its `artist_name` is the *album* artist, so it misses featured credits. At most
    one entity filter. The IP address is never included. Spends no Spotify request."""
    a = parse_args(_PARAMS["plays"])
    return jsonify(resources.plays_list(
        db.get_db(), _play_entity(a), a["source"], _window(a), a["sort"] == "ts",
        a["page"], a["per_page"]))


@route("/plays/timeline", params=[
    *_PLAY_FILTERS,
    P("bucket", "enum", "Bucket size (weeks start on Monday).", default="month", choices=("day", "week", "month")),
    *WINDOW])
def plays_timeline():
    """Plays and listening time per day, week or month.

    Returns `{bucket, tz, from, to, buckets: [{start, plays, ms_played}]}`. `start`
    is the bucket's first calendar day in `tz`; `from`/`to` echo the resolved UTC
    bounds (null when not given). Empty buckets are included as zeros so a chart has
    no gaps. With no `from`/`to` the range runs from the first matching play to the
    end of the play data. Bucket boundaries are real local midnights, correct across
    DST changes. Same filters as /plays. Spends no Spotify request."""
    a = parse_args(_PARAMS["plays_timeline"])
    return jsonify(resources.plays_timeline(
        db.get_db(), _play_entity(a), a["source"], _window(a), a["bucket"]))


# -- Generations (§9.3) -----------------------------------------------------------------------

_TIER = P("tier", "enum", "Count groups at this tier.", default="version", choices=("version", "song"))


@route("/generations", params=[_TIER])
def generations_index():
    """Every generation in ordinal order: `{items, total}`.

    Each: `{ordinal, name, playlist, started_at, ended_at, version_count,
    song_count, carried, new}`. `ended_at` is null for the active generation;
    `carried`/`new` count groups at `tier` (default version) that did / did not
    also appear in the previous generation. Tenure has no endpoint of its own:
    it is /api/v1/versions?sort=-tenure, and every entity detail carries its
    `generations` section. Spends no Spotify request."""
    a = parse_args(_PARAMS["generations_index"])
    rows = resources.generations_list(db.get_db(), a["tier"])
    return jsonify({"items": rows, "total": len(rows)})


@route("/generations/pending")
def generations_pending():
    """The unanswered new-generation prompt as `{playlist, name}`, or null."""
    parse_args([])
    return jsonify(resources.pending_generation(db.get_db()))


@route("/generations/<int:ordinal>", params=[_TIER])
def generation_detail(ordinal):
    """One generation's row plus every group present in it.

    `items` is one `{group, label}` per version (or song, per `tier`) group
    present, `label` being `carried` (also in the previous generation) or `new`,
    ordered by score descending. `survived_out` is how many carry into the next
    generation, null for the newest. The only unpaginated list in the API, since a
    generation is bounded by one playlist. Spends no Spotify request."""
    a = parse_args(_PARAMS["generation_detail"])
    data = resources.generation_detail(db.get_db(), ordinal, a["tier"])
    if data is None:
        raise _not_found("generation")
    return jsonify(data)


# -- The three writes (§11) --------------------------------------------------------------------


@route("/playlists/<playlist_id>", methods=("PATCH",),
       params=[P("excluded", "bool", "Whether pulls skip this playlist.", required=True, where="body")])
def playlist_exclude(playlist_id):
    """Sets whether pulls skip a playlist. Body `{"excluded": bool}` (the only
    accepted field). Returns the playlist's summary. Exclusion affects pulls, not
    scores, so no recompute. Touches only the database."""
    parse_args([])
    body = _body("excluded")
    if not isinstance(body.get("excluded"), bool):
        raise ApiError("invalid_parameter", 400, "excluded: expected a boolean")
    data = resources.set_playlist_excluded(db.get_db(), playlist_id, body["excluded"])
    if data is None:
        raise _not_found("playlist")
    return jsonify(data)


_PLAYLIST_ID = P("playlist_id", "str", "The pending generation's playlist id.", required=True, where="body")


def _generations_response(rows):
    return jsonify({"items": rows, "total": len(rows)})


def _answer_pending(answer):
    parse_args([])
    body = _body("playlist_id")
    playlist_id = body.get("playlist_id")
    if not isinstance(playlist_id, str) or not playlist_id:
        raise ApiError("invalid_parameter", 400, "playlist_id: expected a string")
    return _generations_response(answer(db.get_db(), playlist_id))


@route("/generations/pending/confirm", methods=("POST",), params=[_PLAYLIST_ID])
def generations_confirm():
    """Confirms the pending new generation. Body `{"playlist_id": str}`, which must
    be the pending one (a mismatch, or nothing pending, is a 400). Recomputes
    scores inline, since a new generation is a scoring input and the caller is
    waiting for the outcome. Returns the generations list."""
    return _answer_pending(resources.confirm_pending_generation)


@route("/generations/pending/decline", methods=("POST",), params=[_PLAYLIST_ID])
def generations_decline():
    """Declines the pending new generation, so it stops asking about that
    playlist. Body `{"playlist_id": str}` (must be the pending one, else a 400).
    Touches nothing scoring reads, so no recompute. Returns the generations list."""
    return _answer_pending(resources.decline_pending_generation)


@route("/songs/<int:song_id>/representative", methods=("PUT",),
       params=[P("track_id", "str", "A member track of this song.", required=True, where="body")])
def song_pin(song_id):
    """Pins a track as a song group's representative. Body `{"track_id": str}`; a
    track that is not a member of this song is a 400 and nothing changes. Returns
    the song's summary. Recomputes scores asynchronously (after the commit), as
    working a queue does."""
    parse_args([])
    body = _body("track_id")
    track_id = body.get("track_id")
    if not isinstance(track_id, str) or not track_id:
        raise ApiError("invalid_parameter", 400, "track_id: expected a string")
    data = resources.pin_song_representative(db.get_db(), song_id, track_id)
    if data is None:
        raise _not_found("song")
    return jsonify(data)
