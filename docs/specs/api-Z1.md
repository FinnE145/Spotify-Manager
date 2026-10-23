# Z1 — The v1 JSON API

Step **Z1** of `docs/Planning/roadmap.md` (added to the roadmap in this step's plan session, together
with **Z2**, **AB** and **AA**). Planning took over a decision Y's roadmap section had left open:
**Y's query engine (filters, multi-key sort, pagination over every entity type) is built here**, as
the list endpoints in §7, and Y shrinks to the browse UI over them. Y's roadmap section has been
rewritten to match.

Branch: `feat/api-Z`, the branch for every Z sub-step (Z2 is planned, built and verified on it too).

---

## 1. What this is, and who uses it

A read-mostly JSON API over everything Symr already stores and derives about the library: entities,
search, filterable/sortable lists, generations, play history, and data freshness. **It spends no
Spotify requests unless a caller explicitly asks for one** (§4.4).

Its consumers, in the order they arrive:

1. **Y** (the entity browser): its pages fetch the §7 list endpoints from JS, so changing a filter or
   a sort does not reload the page.
2. **Z2**: converts existing pages onto the API wherever incremental loading helps. Pages that are
   better rendered at once keep server-rendering, but from the **same builders** this step writes
   (§2.2), in-process, so no page and no endpoint compute the same thing twice.
3. **Finn's desk dashboard**: a separate project running on `fe-pro`, inside the tailnet. It needs
   to search songs and get info **without spending Spotify requests**, which is exactly the default
   here.
4. **AA** (the MCP server), after **AB** (real authentication) makes it safe to reach Symr from
   outside the tailnet.

**Out of scope:** converting any existing page (Z2), the browse UI (Y), authentication (AB), the MCP
server (AA), and any change to the internal `/api/*` endpoints (§3.3).

---

## 2. Shape of the code

### 2.1 `api.py`: a Flask Blueprint at `/api/v1`

A new module `api.py` holds one `flask.Blueprint` named `api`, registered in `create_app()` with
`url_prefix="/api/v1"`. **This deliberately departs from P3's "no blueprints" decision**
(`docs/codebase-health/P3_refactor.md` §2 and §5) for this one surface. A versioned API is a
self-contained unit with its own conventions (param parsing, its own docs index, the auth exemption
in §4.1), and a Blueprint is the standard Flask way to carry those. Every existing route stays in
`app.py` exactly as it is.

`api.py` holds route plumbing only: parse and validate params (§5.2), call a builder, `jsonify`.

### 2.2 `resources.py`: the builders, with no Flask in them

A new module `resources.py` holds every function that produces a v1 representation: the batched
summary builders (§6), the detail builders, the list query (§7), the plays and timeline queries
(§9), and status (§10). It follows the rule `entities.py` follows: **no Flask** (no `request`, no
`abort`, no `jsonify`), so every builder is callable against a fixture DB with no request context.
Each takes a `conn` and returns plain JSON-ready Python (dicts, lists, str, int, float, bool, None,
and never a `sqlite3.Row`).

This module is what makes "pages use the API, without duplicated code" true. Z2's server-rendered
pages call these builders in-process; Z2's JS-driven pages fetch the endpoints that wrap them.

It **reuses** what already exists rather than re-deriving it:
- scores through `scoring.scores_for_tier` / `song_scores` / `album_scores` / `artist_scores` /
  `playlist_scores` (all already batched);
- generations through `generations.generation_spans` / `runs` / `pending_new_generation` /
  `confirm_generation` / `decline_generation` and the `generation_presence` view;
- the matcher through `search.rank`;
- the guarded detail fetches through `entities.fetch_album_tracklist` / `fetch_artist_image`, and
  queueing through `entities.queue_wanted_uris`;
- normalization through `normalize.base_string`.

Where an existing helper is per-entity and the list needs it for a whole set (`play_stats` is the
obvious one), `resources.py` writes the set-based query **once** and the per-entity case calls it
with one id. The per-entity helper in `entities.py` stays untouched until Z2 moves its callers.

**Import direction:** `resources` may import `canonical`, `canonical_detect`, `scoring`,
`generations`, `entities`, `search`, `normalize`, `api_log`, `jobs`, `scrobble` and `db`. Nothing
imports `resources` except `api.py` (and, from Z2, `app.py`). It must not introduce an import cycle.
`tests/test_codebase_map.py` will require both new modules in `CLAUDE.md`'s map.

### 2.3 A once-per-request grouping catch-up

Pages call `canonical.ensure_track_groups(conn); conn.commit()` before reading. The Blueprint does
the same in one `api.before_request` hook, for every v1 request, so no endpoint can forget it. This
is the same reasoning `app.py` gives for `refresh_scores` being one hook. It is an idempotent
SELECT on the common case.

---

## 3. What is in v1 and what is not

### 3.1 v1 is the library-data API

v1 is what the main pages and the MCP read, plus curation of that data. In Z1:

- **Reads:** the index (§5.4), status (§10), search (§8), detail for all eight entity types plus
  playlist items (§6), the eight list endpoints (§7), generations (§9.3), and plays and the timeline
  (§9.1, §9.2).
- **Writes, exactly three** (§11): the playlist exclude toggle, generation confirm/decline, and
  pinning a song group's representative.

### 3.2 Never in v1: the round-trip

**No v1 endpoint may start, reconcile, stop or otherwise drive the round-trip.** `roundtrip.py` is
the only module that writes to the Spotify library, and v1 is exactly the surface **AB** will expose
beyond the tailnet. `api.py` and `resources.py` must not import `roundtrip` at all. This is a rule,
not a Z1 scoping choice; a later step that wants it must change this spec, not just add a route.

### 3.3 The internal `/api/*` endpoints stay where they are

The existing 45 unversioned `/api/*` endpoints (job start/stop/status, imports, queue housekeeping,
review-queue applies, artist merges, the canvas store, search's HTML fragments) are page plumbing.
They are already JSON and already driven by their own page's JS, so moving them would only rename
them. They are untouched in Z1. Z2 may pull individual ones into v1 when it converts a page. AA
decides which, if any, the MCP may trigger (never a full pull, because of quota; never the
round-trip, per §3.2).

---

## 4. Security

### 4.1 The login guard exempts the whole v1 Blueprint

`require_login` 401s every non-public endpoint when there is no valid cached Spotify token. **The
rule being replaced:** every non-public request requires a valid cached Spotify token. **The new
rule:** a request whose endpoint belongs to the `api` Blueprint is exempt, **reads and writes
alike**. Every non-v1 route is unchanged.

Why:
- **A dashboard or an MCP can't sign in again.** Before the API, a lapsed token was fixed by signing
  in again in a browser.
- **Nothing in v1 needs the token**, except §4.4, which handles its own absence explicitly. Every v1
  endpoint, the three writes included (§11), touches only the database.
- **It costs no security.** The guard checks that a token exists, not who is asking. Who can reach
  Symr is still decided by the tailnet (Q §12), and real caller authentication is AB's.

The exemption is expressed in `require_login` itself (checking `request.blueprint == "api"`), next
to `_PUBLIC_ENDPOINTS`. It is not a second mechanism elsewhere.

### 4.2 Everything else that applies, unchanged

- **Reachability:** tailnet-only (Q §3.3 / §12). Z1 adds no authentication. **AB** is the step that
  does, and it is a prerequisite for anything reachable from outside the tailnet.
- **Cross-site writes:** the `reject_cross_origin_writes` hook (`docs/specs/cross-site-writes.md`)
  already covers every non-safe method on every route, v1 included. Nothing to add.
- **No CORS headers, ever,** on any v1 response. The same-origin policy is what stops another
  site's page from reading v1 data through Finn's browser. The dashboard and the MCP are not
  browsers and do not need CORS.
- **Never exposed:** `play.ip_addr`, and every `raw_json` column. These are not serialized by any
  builder, not even behind a flag.

### 4.3 What the API spends

By default, **a v1 request makes zero Spotify API requests, and no token refresh either.** Its own
logic never needs one, and since §4.1 the login guard's `get_spotify_client()` call, which can fire
a token-refresh POST, no longer runs for v1. The exceptions are `spotify_request=1` (§4.4) and
`/status`'s `spotify_authenticated` check (§10), which may refresh the token. Neither ever calls the
Spotify Web API itself, apart from §4.4's one fetch.

### 4.4 `spotify_request=1`: the one opt-in that spends

Album detail and artist detail accept `spotify_request=1`. With it, and only with it, they make
**exactly one** Spotify request through the same fetch functions the page uses
(`entities.fetch_album_tracklist` / `entities.fetch_artist_image`). The name is deliberately loud,
so an agent with an "ask before hitting Spotify" rule sees it and asks.

**It always fetches, even if the entity was fetched or attempted before.** This **replaces** the
pages' first-view-only rule (`entity-pages-K.md` §5.3/§7.1, P1-016) *for this parameter only*. The
page must not re-fetch on every view, because nobody asked it to spend anything. A caller passing
`spotify_request=1` is asking on purpose, and that includes retrying a fetch that failed earlier,
which the first-view rule would otherwise make impossible forever. A successful re-fetch overwrites
the stored tracklist or image and re-stamps the `*_pulled_at` column. The pages keep first-view-only
exactly as today. When Z2 moves the album/artist pages onto these builders, the page makes its own
first-view check and passes the flag only on a first view.

- **Without it:** anything not yet fetched is **`null`, never an empty value**. That means an
  album's `tracklist` (and `tracklist_count` / `total_duration_ms`, which depend on it) and an
  artist's `image_url`. `null` reads as "not known yet"; `[]` would read as an empty album or an
  error. What `null` means and what the parameter costs are documented in the endpoint's docstring
  (§5.4), not in the response.
- **With it, a failure is an error, never a silent fallback.** A caller that asked to spend a
  request has to be able to tell it didn't get what it paid for. A 200 with `null` would look like
  success. So:

  | situation | response |
  |---|---|
  | no valid Spotify token (`get_spotify_client()` is `None`) | **503** `spotify_not_authenticated`, detail saying the server's Spotify sign-in has lapsed and needs a browser sign-in at `/login`. Nothing is stamped, as today: not a real attempt. |
  | the request was made and failed (429, network, 404) | **502** `spotify_request_failed`, detail naming the cause. The attempt is still stamped (`tracklist_pulled_at` / `detail_pulled_at`), exactly as today (P1-016). |
  | fetched successfully (first time or a re-fetch) | **200** with the fresh data |

  Without the parameter, whether an earlier attempt failed is visible as `tracklist_fetched_at` set
  with `tracklist` still `null` (or `image_fetched_at` set with `image_url` still `null`), and
  `spotify_request=1` is the way to retry it.

  **Not 401,** although "not authenticated" suggests it: 401 means *the caller's* credentials, and
  **AB** will need exactly that status for "your API token is wrong". The missing credential here
  is the server's own Spotify sign-in, which is an upstream dependency being unavailable, hence 503.
  A failed upstream call is the textbook 502.

  For this, `entities.fetch_album_tracklist` and `entities.fetch_artist_image` **return an outcome**
  instead of `None`: `"no_client"`, `"failed"` (with the exception available to the caller for the
  detail), or `"fetched"`. As today, the "already attempted" check is the caller's: the entity
  pages and `backfill.py` keep skipping the call when the stamp is set, and `resources.py` simply
  never checks it. This is a small signature change. The existing callers ignore the return value
  and are unaffected, and their behaviour, including the stamping, does not change.
- **Queueing unowned uris is opt-in too: `queue_unowned=1`** on album detail. With it, the response
  is built after `entities.queue_wanted_uris(conn, album_id, 'album')` runs off the stored tracklist
  (zero Spotify cost), exactly as the album page does on every view (`grouping-fixes-backfill-M.md`
  §4.4). **Without it, a v1 album read writes nothing.** This **replaces** the page's
  "every album view queues" *for the API only*: a dashboard or an MCP reading album details must not
  silently undo a `[Clear]` on `/dev/roundtrip`. When Z2 moves the album page onto these builders,
  the page passes it on every view and keeps its current behaviour. It combines with
  `spotify_request=1`: the fetch runs first, then the queueing reads the fresh tracklist.

---

## 5. Conventions

### 5.1 Values

- **Raw values only:** `duration_ms` as an integer; timestamps as ISO-8601 UTC strings with `Z`,
  exactly as stored; booleans as JSON booleans (SQLite's 0/1 converted); counts as integers. **No
  formatted strings** (`"4:32"`, `"10 mo."`, thousands separators): formatting is the page's job.
- **Scores:** `{"all_time": float, "recent": float}` in display space, exactly the numbers the pages
  show, unrounded. **An entity with no materialized score has `score: null`.** This was Finn's call during
  Implement, recorded here at Verify. It replaces "the same fallback its page uses today": the album
  page falls back to `0.0`, but a zero is a real, computed score and "no score" is not. For song, album, artist and playlist, which are
  aggregated at query time, that means none of the entity's versions has a materialized score. A
  `score` / `recent_score` sort treats `null` as lower than every number, so it comes last in a
  descending sort and first in an ascending one.
- **Plays:** `{"total": int, "past_30d": int|null, "past_7d": int|null, "window": int|null,
  "data_through": str|null}`.
  - The two rolling windows keep `entities.play_stats`' semantics: `null` when the play data ends
    before the window starts (the pages' `—`), and they stay **rolling from now**.
  - `window` is the count inside `from`/`to` (§5.3), and `null` when neither is given.
- **Keys are always present.** Unknown means `null`, never an omitted key, so JS and an MCP can rely
  on the shape.
- **Every entity object carries `type`, `id`, `name` and `url`.**
  - `type` is one of `track`, `album`, `artist`, `playlist`, `song`, `version`, `recording`,
    `release`.
  - `id` keeps its native form: a Spotify id string for track/album/artist/playlist, the integer
    `canonical_group.id` for the four tiers.
  - `url` is the site path (`/album/<id>`, `/song/<id>`, …), relative so it is right on both
    machines. It is generated with `url_for` against the same endpoints `entity_link` uses, never
    hand-built.
  - The four Spotify-backed types also carry `spotify_url` (the stored `external_url`).
- **Artist ids are alias-resolved.** A request for an artist id that `artist_alias` maps onto
  another returns that canonical artist, and the response's `id` is the canonical one. Every
  `artists` list in every summary uses canonical ids.

### 5.2 Params: declared once, validated strictly

Each v1 endpoint declares its accepted query params **as data**: name, type, default, allowed values
and a one-line description. One shared parser in `api.py` reads that declaration, and the index
(§5.4) is built from the same declaration. Parsing and documentation therefore cannot disagree.

- **An unknown param is a 400** (`{"error": "unknown_parameter", "detail": "<name>"}`). This
  catches a mistyped filter rather than silently ignoring it.
- **A filter or sort key that exists but doesn't apply to this type is a 400**
  (`unsupported_filter` / `unsupported_sort`). It is never silently ignored. **The rule this
  replaces:** the existing pages ignore query args they don't read.
- **A malformed value is a 400** (`invalid_parameter`). That covers a non-integer page, an unknown
  tz, an unparseable date, a negative `min_ms`, and so on.
- **Errors keep the existing shape:** `{"error": <slug>, "detail": <string or null>}` through
  `app.py`'s `api_error`. The app-level error handlers already JSON-ify anything under `/api/`, and
  `/api/v1/` falls under that.
- **Lists** return `{"items": [...], "total": int, "page": int, "per_page": int}`. `page` is
  1-based, `per_page` defaults to **50** with a max of **200**, and a page past the end returns
  `"items": []`, not a 404. **Detail** endpoints return the bare object. **A miss** is a 404
  (`not_found`).

### 5.3 Dates, time zones and play counting

These params apply to every endpoint that counts plays: the lists, entity details, plays, and the
timeline.

- **`tz`**: an IANA zone name, default **`America/Toronto`** (a module constant
  `DEFAULT_TZ` in `resources.py`, not in `config.py`, for the reason `scoring.py` gives for its
  constants: one meaning everywhere). It decides calendar boundaries only: where a bare date starts
  and ends, and where day/week/month buckets fall. It never changes a returned timestamp, which stays
  UTC. Use stdlib `zoneinfo`. **Implement must confirm `zoneinfo` resolves `America/Toronto`
  inside the production container** (`python:3.14.5-slim`). If it doesn't, stop and ask rather than
  adding the `tzdata` package unasked.
- **`from` / `to`**: each is a bare date (`2025-03-01`) or a full ISO-8601 datetime with an offset.
  - A bare `from` starts at 00:00 of that day in `tz`.
  - A bare `to` is **inclusive of that whole day**: `to=2025-03-31` includes every play on March 31
    in `tz`.
  - A datetime `to` is exclusive.
  - Either may be given alone. `from` after `to` is a 400.
- **Weeks start on Monday.**
- **A play is a row**, as every page counts it today. **`min_played_ms`** (optional, integer)
  excludes plays shorter than that from **everything that response counts**: plays metrics, the
  `window`, the plays listing and timeline buckets. Without it, nothing is filtered. The name avoids
  colliding with the list filter `min_ms`, which is track duration.

### 5.4 Self-describing: `GET /api/v1/`

The index is the API's whole reference. It is enough to hand to another repository: "the base URL
plus `GET /api/v1/` describes everything."

```json
{
  "version": "v1",
  "defaults": {"tz": "America/Toronto", "per_page": 50, "max_per_page": 200},
  "shapes": {"track": "<doc>", "album": "<doc>", "...": "..."},
  "endpoints": [
    {"method": "GET", "path": "/api/v1/albums/<album_id>",
     "doc": "<the view's docstring>",
     "params": [{"name": "spotify_request", "type": "bool", "default": false, "doc": "..."}]}
  ]
}
```

- `endpoints` is built from the Blueprint's own rules in `app.url_map`, so a route cannot exist
  without appearing.
- Every view's docstring is its full prose reference: what it returns, what each section means,
  what `null` means where it can occur, and the cost of `spotify_request` where it applies.
- `shapes` documents each summary shape once (§6.1), from docstrings on the summary builders.
- **No OpenAPI document.** Finn's call: the payoff that would matter (auto-generated MCP tools) is
  worse than AA's hand-designed tools, and either way of producing one costs a thousand-line YAML
  or a schema library.

### 5.5 Stable ordering

Every list sort appends the type's unique key (`id`) as a final ascending tie-breaker, so
pagination never repeats or drops a row between pages. **Sorting is applied to the whole filtered
set before the page is cut.**

---

## 6. Entities

### 6.1 Summaries: one shape per type, everywhere

Each type has exactly one **summary** shape. It is used in every list, in search results, and
wherever the entity appears nested inside another (a track inside an album, a playlist inside a
track's memberships). It is built by **one batched builder per type** taking a list of ids
(`track_summaries(conn, ids) -> {id: dict}` and so on): one query per type per call, never one call
per row. This is the API's `entity_link`: a track has the same shape inside an album, a playlist and
a search result by construction.

| type | summary fields (beyond `type`, `id`, `name`, `url`) |
|---|---|
| track | `spotify_url`, `artists` `[{id, name}]` in credit order, `album` `{id, name, url}`, `duration_ms`, `explicit`, `cover_url`, `score` |
| song / version / recording / release | the representative track's `artists`, `album`, `duration_ms`, `cover_url`; `representative_track_id`, `member_count`, `score` |
| album | `spotify_url`, `artists`, `album_type`, `release_date`, `release_date_precision`, `total_tracks`, `owned_count`, `cover_url`, `score` |
| artist | `spotify_url`, `image_url` (`null` until fetched), `score` |
| playlist | `spotify_url`, `owner`, `cover_url`, `live_count`, `excluded`, `is_liked`, `generation` (ordinal, or `null`), `score` |

- A group's `name` is its representative track's name. The representative is
  `canonical.representative()`, the same election the pages use.
- **`is_liked`** marks Liked Songs. Like `snapshot.index_data`, it tells the caller what kind of row
  it has rather than handing out an id to recognise. Liked Songs is a `snapshot` row with no
  `snapshot_id` (see memory `liked-songs-is-half-an-exception`). Check every playlist rule here
  against it.

### 6.2 Details: summary plus facts

`GET /api/v1/<plural>/<id>` for all eight types (`tracks`, `albums`, `artists`, `playlists`,
`songs`, `versions`, `recordings`, `releases`). Each returns the summary's fields plus the sections
below.

**Facts only.** Template-only structures (the breadcrumb's dropdown labels, formatted durations,
the `spans` lookup dict, `tracks_by_id`) are not in it. A page that needs one derives it from these
facts (Z2's job).

Sections shared across types:
- **`plays`**: §5.1's shape, over every track the entity covers.
- **`playlists`**: summaries of the playlists holding any of its tracks live, each with that
  playlist's `added_at`. Same rule as `entities.playlists_for_tracks`, including that unfollowed
  playlists are excluded entirely (W §9.14b).
- **`generations`**:
  - `ordinals`: the sorted list of generations the entity was present in;
  - `total`: how many generations that is;
  - `tenure`: the longest consecutive run, in generations;
  - `run_count`;
  - `first_ordinal` / `last_ordinal`.

  These are derived from `generation_presence` through the entity's tracks with `generations.runs`,
  the same rule the tenure page and the entity pages' strip use. Playlists don't carry it.

Per type:

| type | sections beyond the shared ones |
|---|---|
| track | `isrc`, `uri`, `track_number`, `disc_number`, `groups` (`{song, version, recording, release}` summaries), `memberships` (`[{playlist, position, added_at, removed_at}]`, unfollowed playlists excluded, removed rows included, the same rows `entities.track_detail` reads), `aliases` (requested uris relinked onto it) |
| song / version / recording / release | `pinned` (song only; `null` at other tiers), `parents` (summaries of the tiers above, e.g. a recording's version and song), `children` (summaries of the next tier down; `null` at release tier), `members` (track summaries of every member, ordered by track score descending then name, as the page orders them) |
| album | `tracklist` (`[{disc_number, track_number, name, uri, duration_ms, track}]`, where `track` is the owned track's summary or `null`; or the whole list is `null` if never fetched, per §4.4), `tracklist_fetched_at` (`tracklist_pulled_at`; `null` = never attempted), `tracklist_count`, `appended` (summaries of owned tracks on this album that aren't in the stored tracklist), `total_duration_ms` (only when every tracklist entry has a length, else `null`, same rule as `album_detail`) |
| artist | `image_fetched_at` (`detail_pulled_at`; `null` = never attempted), `merged_ids` (the alias ids folded into it), `version_count`, `album_count`, `albums` (album summaries). **Its tracks are not inlined:** they come from `/api/v1/versions?artist=<id>` (§7) |
| playlist | `description`, `last_changed_at`, `tracks_pulled_at`, `unfollowed_at`, `generation` (`{ordinal, started_at, ended_at}` or `null`). **Its items are not inlined** (§6.3) |

Detail endpoints take the §5.3 params (they count plays). Album and artist take
`spotify_request`, and album also takes `queue_unowned` (§4.4).

### 6.3 Playlist items: `GET /api/v1/playlists/<id>/items`

Paginated (Finn All alone is 2,356 rows). Each item is
`{position, added_at, removed_at, track: <track summary>}`, ordered by position. By default the
listing holds live rows only; `include_removed=1` adds removed rows. Uses §5.2's list envelope.

---

## 7. Lists: `GET /api/v1/<plural>`

All eight types. Each item is the type's **summary plus a `metrics` object** holding every metric
that type supports. A Y table therefore gets its columns from the one call, and the cost is bounded
by the page size, since metrics are computed only for the page's rows after the sort.

### 7.1 Metrics

| metric | meaning | types |
|---|---|---|
| `plays` | §5.1's shape, over the entity's tracks | all |
| `memberships` | live membership rows across its tracks (unfollowed playlists excluded) | all except playlist |
| `generations` | number of generations present in | all except playlist |
| `tenure` | longest consecutive run of generations | all except playlist |

- An artist covers tracks credited to it in **any** role, alias-resolved: the set
  `entities.artist_detail` already counts.
- An album covers its owned tracks.
- A group covers its members.
- A playlist covers its live tracks.

### 7.2 Name filters

Three mutually exclusive params. Passing more than one is a 400.

- **`q`**: the normalized name **starts with** the normalized query.
- **`q_contains`**: it contains the query anywhere.
- **`q_full`**: it equals the query, with nothing either side.

All three compare `normalize.base_string` of both sides **with every space then removed**. Accents,
punctuation and spaces are folded, so `half alive` matches **half•alive** under any of the three,
which is why it is `q_full` and not "exact". The docstring says so. (Amended at Verify: removing
the spaces was Finn's call during Implement. `base_string` alone *deletes* punctuation without
spacing it, `half•alive` → `halfalive`, so a spaced query could never have matched and this
section's own example was unreachable.)

What name they match:
- a track's name;
- for a group, **any member track's** name;
- an album's name;
- an artist's name (the canonical one or any alias id's);
- a playlist's name.

### 7.3 Filters

| filter | meaning | types |
|---|---|---|
| `artist` | credited to this artist in any role (alias-resolved) | track, the four tiers, album |
| `artist_role` | `primary` or `featured`, narrowing `artist` (400 without it) | track, the four tiers |
| `album` | on this album | track, the four tiers |
| `playlist` | live in this playlist | track, the four tiers |
| `generation` | present in this generation (ordinal) | track, the four tiers, album, artist |
| `min_ms` / `max_ms` | duration in ms (a group's is its representative's) | track, the four tiers |
| `year_from` / `year_to` | release year, inclusive (a group's is its earliest member's) | track, the four tiers, album |
| `explicit` | `1` / `0` | track, the four tiers |
| `min_memberships` | as §7.1 | all except playlist |
| `min_plays` | as §7.1's `plays`: `window` if `from`/`to` are given, else `total` | all |
| `from` / `to` / `tz` / `min_played_ms` | §5.3 | all |

For the four group tiers, a filter on a track property matches if **any member track** satisfies it,
except where the table names the representative or the earliest member. Unfollowed playlists are
never listed and never match a `playlist` filter (W §9.14b). The playlist list itself excludes them.

**Filters run in SQL, before the sort.**

### 7.4 Sort

`sort` is an ordered, comma-separated list of keys, each optionally prefixed `-` for descending.
Example: `sort=-memberships,-plays`. The default is `-score`. Every sort gets the §5.5 tie-breaker.

| key | meaning | types |
|---|---|---|
| `score` / `recent_score` | the `all_time` / `recent` horizon | all |
| `plays` | `window` if `from`/`to` are given, else `total` | all |
| `memberships`, `generations`, `tenure` | §7.1 | all except playlist |
| `duration` | as `min_ms` | track, the four tiers |
| `release_date` | album's `release_date_sortable`; a group's earliest member's | track, the four tiers, album |
| `name` | case-insensitive | all |

**A date range applies to plays only.** Scores exist only for the two materialized horizons (all-time
and the last 90 days). Any other window would mean a whole-library recompute (~1.8s) per request.
`memberships` always means live now.

"Top songs by plays in March" is therefore `/api/v1/songs?sort=-plays&from=2025-03-01&to=2025-03-31`.
There is no separate "top" endpoint.

### 7.5 Performance

Sorting a whole type by a derived number knowingly breaks "work proportional to what's rendered",
exactly as `/dev/canonical`'s ranking already does. Song-tier scoring of all ~12k groups costs
~160–280ms today. Album, artist and playlist query-time scores, and the plays/memberships/tenure
aggregates over a whole type, are **unmeasured**.

- Implement measures each sort key on each type against the real library's size (13.6k tracks, ~12k
  songs, 6.3k albums, 4.4k artists, 94k plays as of 2026-09-15).
- **Any unfiltered whole-type request over 500ms is a stop-and-ask,** not something to cache or
  optimise silently. Report the measurement.
- Filters must cut the set in SQL before any per-entity Python runs.
- Metrics and summaries are built for the page slice only.

**Measured at Verify, 2026-10-23.** Implement recorded only the worst case, ~410ms, and only in
`CLAUDE.md`'s map. These are medians of three, in-process
through the test client, against a `VACUUM INTO` copy of the laptop's `symr.db`: 13,607 tracks,
11,936 songs, 12,243 versions, 12,448 recordings, 13,241 releases, 6,300 albums, 4,355 artists,
153 playlists, 94,418 plays. Every unfiltered whole-type request on every sort key is **under
500ms**, so nothing hit the stop-and-ask:

| type | slowest key | ms | other keys |
|---|---|---|---|
| artists | `-plays` | 387 | `score` / `recent_score` 345–364, `memberships` / `generations` / `tenure` ~295, `name` 115 |
| playlists | `-plays` | 291 | `score` / `recent_score` ~200, `name` 141 (the 50-row page's playlist scores dominate) |
| albums | `-plays` | 178 | `score` / `recent_score` ~172, the rest 33–84 |
| songs | `-plays` | 159 | `score` 113, `name` 82, the rest 65–79 |
| versions / recordings / releases | `-plays` | 142 | the rest 47–74 |
| tracks | `-plays` | 133 | the rest 30–47 |

`/search?q=taylor` took 240ms, `/plays/timeline?bucket=week` over the whole history 130ms and
`/status` 16ms.

The 900-id cutover in `resources._IN_LIMIT` matters here: above it a query drops its `IN` list and
filters in Python, and every whole-type list takes that path. A test forces it on the small fixture
and checks it returns exactly what the normal path does.

§5.3's container check also passed at Verify. `python:3.14.5-slim` (Debian 13.5) on `fe-pro` ships
`/usr/share/zoneinfo`, and `ZoneInfo("America/Toronto")` resolves there with the right DST offsets.
No `tzdata` package was added.

---

## 8. Search: `GET /api/v1/search`

The same matcher and ranking as `/search` (`search.rank`). The JSON replaces the HTML fragments.

- **`q`** (required): below `search.MIN_QUERY_LEN` after normalizing, every list is empty.
  That is not a 400.
- **Without `type`:** `{"q", "most_relevant": [...], "songs": {...}, "albums": {...},
  "artists": {...}, "playlists": {...}}`.
  - `most_relevant` is the mixed list (`search.COMBINED_LIMIT` rows), each item a summary of its
    own `type`.
  - Each type section is `{"items": [...], "total": int}` with `search.SECTION_LIMIT` items.
  - Songs rank at version tier (L2), so song results are **version** summaries.
- **With `type=songs|albums|artists|playlists`** plus `page` / `per_page`: one section in the §5.2
  list envelope, paging through the full ranked list. This is See more's job.
- Item order is exactly `rank()`'s. Relevance numbers are not exposed.
- Like `/search`, the grouping catch-up's write is fine here because it is the Blueprint hook
  (§2.3). The `is_searchable` gating `/search` does is not needed.

---

## 9. Plays and generations

### 9.1 `GET /api/v1/plays`

Individual plays, newest first (`sort=ts` reverses).

**Filters:**
- at most one entity filter: `track`, `song`, `version`, `recording`, `release`, `album`, `artist`
  (any role, alias-resolved), or `playlist` (tracks live in it now);
- `source=export|scrobble`;
- §5.3's `from` / `to` / `tz` / `min_played_ms`.

Each play:

```json
{"id": 1, "ts": "...Z", "ms_played": 0, "source": "export",
 "track": <track summary or null>,
 "spotify_track_uri": "...",
 "reported": {"track_name": "...", "artist_name": "...", "album_name": "..."},
 "reason_start": "...", "reason_end": "...", "shuffle": true, "skipped": false,
 "platform": "...", "conn_country": "..", "offline": false, "offline_ts": "...",
 "incognito_mode": false}
```

- `track` resolves through `played_uri_track`, so relinked uris count. An unresolved play has
  `track: null`.
- `reported` is what the export claimed at play time. Its `artist_name` is the *album* artist
  (`db.py`'s note), and the docstring says so.
- **`ip_addr` is never included** (§4.2).
- With an entity filter, unresolved plays can't match and don't appear.

### 9.2 `GET /api/v1/plays/timeline`

Takes the same filters as §9.1, plus `bucket=day|week|month` (default `month`).

```json
{"bucket": "week", "tz": "America/Toronto", "from": "...", "to": "...",
 "buckets": [{"start": "2025-03-03", "plays": 12, "ms_played": 2400000}]}
```

- Each `start` is the bucket's first calendar day in `tz` (Mondays for weeks).
- **Empty buckets are included as zeros**, so a chart has no gaps.
- When `from` / `to` are absent, the range runs from the first matching play to `data_through`.
- **Bucket boundaries must be correct across DST transitions** in `tz`. That rules out bucketing on a
  fixed UTC offset.

### 9.3 Generations

- **`GET /api/v1/generations?tier=version|song`** (default `version`): `{items, total}`, every
  generation in ordinal order, each one `{ordinal, name, playlist: <summary>, started_at, ended_at,
  version_count, song_count, carried, new}`. `ended_at` is `null` for the active generation. The
  counts are the ones `generations.generations()` derives: `version_count` and `song_count` are
  fixed, while `carried` / `new` are counted at `tier`. (Amended at Verify: the `{items, total}`
  envelope and the `tier` param were added in Implement. The confirm/decline writes in §11 return
  this same shape.)
- **`GET /api/v1/generations/<ordinal>?tier=version|song`** (default `version`): that row plus
  `tier` (echoed) and `items`: one entry per group present, each `{group: <version or song summary>, label}`, where
  `label` is `carried` or `new`, ordered by score descending. Also `survived_out`, which is `null`
  for the newest generation (as `generation_view` has it). Same carried/new rule as
  `generations.generation_view`. This is the only unpaginated list in the API, because a generation
  is bounded (a single playlist).
- **`GET /api/v1/generations/pending`**: `generations.pending_new_generation()` as
  `{playlist: <summary>, name}`, or `null`.
- **Tenure needs no endpoint of its own:** it is `/api/v1/versions?sort=-tenure` (or `songs`), and
  every entity detail carries its `generations` section.

---

## 10. Status: `GET /api/v1/status`

Data freshness, so a dashboard or an MCP answer can say how current it is. None of this is
sensitive. It holds no tokens, no ids beyond what other endpoints expose, and no error tracebacks.

```json
{
  "spotify_authenticated": true,
  "active_job": "snapshot" | null,
  "last_pull_at": "...Z",
  "scrobble": {"enabled": true, "last_poll_at": "...Z", "next_poll_at": "...Z" | null},
  "plays": {"data_through": "...Z", "export_through": "...Z", "last_import_at": "...Z"},
  "scoring": {"last_recompute_at": "...Z", "outcome": "ok" | "error"},
  "pending_generation": <§9.3 pending shape or null>,
  "spotify_requests": {"past_24h": 0, "past_7d": 0},
  "library": {"tracks": 0, "albums": 0, "artists": 0, "playlists": 0, "songs": 0,
              "versions": 0, "recordings": 0, "releases": 0, "plays": 0, "generations": 0}
}
```

Where each field comes from:
- `spotify_authenticated`: whether `get_spotify_client()` would return a client. It is computed
  **without** the login guard (§4.1), so this endpoint answers even when the token has lapsed, and
  that answer is the point.
- `active_job`: `jobs.active()`.
- `last_pull_at`: the latest successful playlist item read (`snapshot.tracks_pulled_at`).
- `scrobble.next_poll_at`: `scrobble._next_wake_at` through a small public accessor. `null` means
  no poller in this process (always so on the laptop), as `/dev/scrobble` already says.
- `export_through`: `MAX(ts)` over `source = 'export'`.
- `last_import_at`: the newest successful `play_import`.
- `scoring`: `scoring.recompute_status()`, without its error text.
- `spotify_requests`: `api_log.request_counts()`, already filtered to `api.spotify.com`.
- `library.playlists`: followed playlists only. The tier counts come from `canonical.tier_counts`.

---

## 11. The three writes

All three take a JSON body and use the §5.2 error shape. They are exempt from the login guard like
the rest of v1 (§4.1), since none needs Spotify, but sit behind the cross-site hook (§4.2). They
follow `async-recompute-N.md` §4.1's rule on blocking: async where you
work a queue, synchronous where you clicked once and are waiting for the outcome anyway.

| endpoint | body | does | recompute |
|---|---|---|---|
| `PATCH /api/v1/playlists/<id>` | `{"excluded": bool}` (the only accepted field) | `snapshot.set_excluded(conn, [id], excluded)`; returns the playlist's summary | none (exclusion affects pulls, not scores) |
| `POST /api/v1/generations/pending/confirm` / `.../decline` | `{"playlist_id": str}` | `generations.confirm_generation` / `decline_generation`; returns the §9.3 generations list | confirm: `scoring.recompute(conn)` inline, as `/dev/generations/confirm` does; decline: none |
| `PUT /api/v1/songs/<song_id>/representative` | `{"track_id": str}` | `canonical.pin_representative`; returns the song's summary | `scoring.request_recompute()` after commit, as `/api/canonical/pin` does |

- **Pin:** a `track_id` that isn't a member of `<song_id>` is a 400. `pin_representative` derives
  the song from the track, so the route must check membership itself, or a pin aimed at one song
  would silently land on another. Song is the only tier with a representative anyone consumes
  (`canonical.pin_representative`'s docstring).
- **Confirm/decline:** a `playlist_id` that isn't the pending one is a 400, and so is a call with
  nothing pending.
- **Ordering at every write:** commit first, then request the recompute, as N §4.1 requires.
- The existing `/api/snapshot/exclude`, `/api/canonical/pin` and `/dev/generations/confirm` stay;
  Z2 moves their callers.

---

## 12. Docs and map changes this step owes

- `CLAUDE.md` Codebase Map: entries for `api.py` and `resources.py` (required by
  `test_codebase_map.py`), and the `app.py` entry's `require_login` description updated for the
  §4.1 exemption.
- `CLAUDE.md`'s Tech Stack line ("server-rendered Jinja templates + vanilla JS") is **not** changed
  in Z1. It becomes untrue only when Z2 converts a page, so Z2 updates it.
- The roadmap's spec index gets this spec's row (done in the plan session's roadmap commit).

---

## Tests

Clauses worth pinning. Where a clause replaces an existing rule, the old rule is named, and the
fixture must be one the old rule would fail.

1. **The v1 Blueprint skips the login guard; nothing else does** (§4.1). This replaces "every
   non-public request requires a valid cached Spotify token". With `get_spotify_client` returning
   `None`:
   - a v1 GET is 200;
   - a v1 write succeeds and writes its row;
   - an internal `/api/*` GET and an internal `/api/*` POST are still 401;
   - a page still redirects to `/login`.

   All in one test, so an exemption keyed on the `/api/` prefix rather than the Blueprint fails it.
   Also: a v1 request without `spotify_request` never calls `get_spotify_client` at all (spy). That
   is the "no token refresh" half of §4.3, and an exemption placed after the client call would fail
   it.
2. **No Spotify spend by default** (§4.3, §4.4).
   - Album and artist detail without the param never call the guarded fetch functions (spy on
     them).
   - The never-fetched tracklist is `null`, **not `[]`**, and the never-fetched `image_url` is
     `null`. This is the clause an implementation returning the stored empty value would fail.
   - With `spotify_request=1` on an unattempted album, the fetch is called exactly once.
   - With `spotify_request=1` on an album **already fetched successfully**, and on one whose earlier
     attempt failed, the fetch is still called exactly once and the stored tracklist is replaced.
     This replaces the pages' first-view-only rule, so a fixture with the stamp set is what an
     implementation that kept that rule would fail.
3. **`spotify_request=1` never fails silently** (§4.4). This replaces the pages' "a failed or
   impossible fetch renders from the DB as if nothing happened".
   - No client → 503 `spotify_not_authenticated`, and nothing stamped.
   - A fake client whose call raises → 502 `spotify_request_failed`, **and** the attempt stamped
     (both halves, so neither "error without stamping" nor "stamp without error" passes).
   - Neither case is a 200, and neither is a 401.
4. **Queueing is opt-in** (§4.4). This replaces the page's "every album view queues". Use an album
   with a stored tracklist holding unowned uris:
   - without `queue_unowned`, a v1 album read inserts **no** `wanted_uri` row;
   - with `queue_unowned=1`, it inserts them with `source = 'album'`;
   - the album page itself still queues on a plain view (unchanged).
5. **Never exposed** (§4.2). A fixture play with a populated `ip_addr`: neither the key nor the
   value appears in `/plays` output. No response from any v1 endpoint in the route sweep contains
   any `raw_json` content.
6. **One summary shape** (§6.1). The same track's summary is identical (same keys, same values)
   in a list, in album detail's `tracklist`, in a search result and in a playlist's items.
7. **Strict params** (§5.2). This replaces the pages' "unread args are ignored".
   - An unknown param → 400 `unknown_parameter`.
   - A real filter on a type it doesn't apply to (e.g. `min_ms` on `/artists`) → 400
     `unsupported_filter`.
   - A real sort key on a type it doesn't apply to (`tenure` on `/playlists`) → 400
     `unsupported_sort`.
   - `artist_role` without `artist` → 400.
   - More than one of `q` / `q_contains` / `q_full` → 400.
8. **Sort before paging, stable** (§5.5, §7.4). Use a fixture where the highest-scoring entity is
   alphabetically last and `per_page=1`: page 1 is that entity. A multi-key sort's second key
   decides a first-key tie. Equal keys page without repeats or gaps.
9. **The three name filters** (§7.2): `q` matches a prefix but not an infix; `q_contains` matches
   the infix; `q_full` matches only the whole name. `half alive` matches **half•alive** under all
   three (the normalization clause). A group matches through a non-representative member's name.
10. **Day boundaries in `tz`** (§5.3), each with fixtures the UTC rule would fail:
   - a play at `2025-03-02T03:00:00Z` counts toward `to=2025-03-01` under the default tz (it is
     March 1 in Toronto) and not under `tz=UTC`;
   - a bare `to` includes that whole day;
   - week buckets start on Monday;
   - a week spanning the March DST change has the right boundaries;
   - an unknown tz → 400.
11. **`min_played_ms`** (§5.3) changes the plays metrics, the `window`, the plays listing and the
    timeline buckets, and without it every row counts.
12. **Rolling windows unchanged** (§5.1): `past_30d` / `past_7d` are `null` when `data_through`
    predates the window, matching `entities.play_stats` on the same fixture.
13. **Timeline zero-fills** (§9.2): a gap month appears with `plays: 0`.
14. **Artist alias** (§5.1): detail for an alias id returns the canonical artist with the canonical
    `id`, and the `artist` filter given an alias id matches the canonical artist's tracks.
15. **Unfollowed playlists** (§6.2, §7.3): an unfollowed playlist is absent from `/playlists` and
    from a track detail's `playlists`, and a `playlist` filter naming it matches nothing.
16. **Writes** (§11).
    - Exclude flips `snapshot.excluded`.
    - Pin with a track from another song → 400, and **no row changed**.
    - Confirm runs `scoring.recompute` inline; decline doesn't (spy).
    - Pin commits before `request_recompute` is called.
17. **Never the round-trip** (§3.2): `api.py` and `resources.py` do not import `roundtrip`, and no
    rule in the `api` Blueprint names it. This is deliberately static, because the clause *is* a
    statement about the code's reach.
18. **No CORS** (§4.2): no v1 response carries `Access-Control-Allow-Origin`, including when the
    request sends an `Origin`.
19. **The index is complete both ways** (§5.4). Every `api` rule in `app.url_map` appears in
    `GET /api/v1/` and vice versa. Every entry has a non-empty `doc`. Every declared param appears
    in its entry. Mirror `test_routes`' both-ways catalog check.
20. **Route catalog** (`tests/routes_catalog.py`): every v1 route is added, plus query-string
    variants for `queue_unowned=1`, a filter, a multi-key sort, `from`/`to` and `tz`. **Not** for
    `spotify_request=1`: with the suite's blocked sockets it is a deliberate 503, which the
    catalog's non-5xx sweep would read as a failure. Clause 3 covers it instead. A variant
    proves only that the branch responds, so the clauses above carry the semantics (P2-010).
