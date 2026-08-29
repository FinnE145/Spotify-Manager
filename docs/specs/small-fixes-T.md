# T — Small fixes: OAuth host mismatch, round-trip request clarity, queue colours, review-queue exit

Step T of `docs/Planning/roadmap.md`.

Planning contradicted that step's section in two places, both found by reading the code rather
than assuming:

- **T2's combined figure is derivable at zero request cost**, which the roadmap did not know.
  `backfill._settled_map` already computes, per album, exactly the number of URIs an Add would
  queue (§2.2). The roadmap's "worth solving alongside **O**" therefore does not apply — nothing
  here waits on O's measured ceiling, and T2 lands now.
- **T3's palette cannot be "a bigger version of the same six" with white text**, which is what
  the roadmap implies. White text at 12px needs a 4.5:1 contrast ratio, which caps a chip's
  relative luminance at about 18% — and two of today's six already fail it. Varying lightness
  and keeping white text are mutually exclusive; §3.1 records the measurement and the choice.

The roadmap's other claims all survive. T1's mechanism is exactly as described there, and T4's
is too.

---

## 0. What this is

Four unrelated papercuts, each too small for its own step. They share a branch and nothing else.
Read each section independently; there is no cross-cutting design here and no shared code.

---

## 1. T1 — the `localhost` / `127.0.0.1` OAuth state mismatch

### 1.1 The mechanism

`/login` (`app.py:739`) sets `session["oauth_state"]`. Flask scopes that cookie to whichever host
served the request. `get_auth_manager()` builds its `redirect_uri` from
`config.SPOTIFY_REDIRECT_URI` — a fixed string — regardless of how `/login` was reached. Spotify
then redirects the browser to *that* host.

Browsers treat `localhost` and `127.0.0.1` as unrelated hostnames and never share cookies between
them. So reaching `/login` via `localhost:45660` guarantees `session.pop("oauth_state", None)` in
`/callback` returns `None`, and the request 400s. Deterministically, every time. This has been
misread as user error for months.

### 1.2 The fix

`/login` redirects onto the canonical host **before** it touches the session.

`config.py` gains two derived constants, parsed once at import from `SPOTIFY_REDIRECT_URI`:

- `SPOTIFY_CANONICAL_HOST` — its `hostname`, lowercased (`127.0.0.1` on the laptop,
  `fe-pro.tail78f5ec.ts.net` on the server).
- `SPOTIFY_CANONICAL_ORIGIN` — its `scheme` + `netloc`, i.e. everything before the path.

They live in `config.py` rather than `app.py` because that file is where every setting lives, and
because a module-level constant is testable with no request context — which §5.1 needs.

At the top of `/login`, before `session["oauth_state"] = state`:

1. If `SPOTIFY_CANONICAL_HOST` is empty or `None`, do nothing. A misconfigured redirect URI must
   not make login unreachable.
2. If `request.args.get("canonical")` is present, do nothing (§1.3).
3. If `request.host`'s hostname — port stripped, lowercased — equals `SPOTIFY_CANONICAL_HOST`, do
   nothing.
4. Otherwise `redirect(SPOTIFY_CANONICAL_ORIGIN + "/login?canonical=1")`.

**The comparison is hostname-only, and that is load-bearing rather than lazy.** Cookies are scoped
to a host: they ignore the port entirely and ignore the scheme except for the `Secure` attribute.
Hostname is therefore the exact axis the bug lives on. Comparing scheme or port instead would
**guarantee a redirect loop on fe-pro**, where `tailscale serve` terminates TLS on the host and
the container sees `http` on port 45660 while the canonical URI is `https` on 443. Anyone
"tightening" this into a full-origin comparison reintroduces that; §5.1 pins it with a test.

`/callback` needs no host handling. Spotify delivers it to the redirect URI's host by
construction, so it always arrives canonically.

### 1.3 The `?canonical=1` marker, and the fe-pro unknown

Step 2's marker exists for one scenario that could not be tested: whether `tailscale serve`
forwards the original `Host` header to the container, or rewrites it to `127.0.0.1:45660`. fe-pro
was offline for the whole of planning (in transit until ~2026-09-05). If it rewrites, the app
never sees its own canonical hostname, step 3 never matches, and step 4 fires forever.

The marker bounds that. The redirect is one-shot: the second request carries `canonical=1` and
proceeds regardless. The worst case degrades to **"T1 is unfixed on fe-pro"** — which is exactly
today's behaviour there — rather than to an unusable app.

**If that turns out to be the case**, the fix is to read the proxy's forwarded host
(`X-Forwarded-Host`, or Werkzeug's `ProxyFix`) instead of the raw `Host` header. It is *not* to
widen the comparison; see §1.2. Verify by browsing to
`https://fe-pro.tail78f5ec.ts.net/login` once the server is back and checking whether the URL
lands carrying `?canonical=1`.

The marker is read and never propagated — it is not forwarded to Spotify and nothing else reads it.

### 1.4 `/callback`'s two refusals stop sharing one message

`/callback` currently aborts with `"Invalid OAuth state."` for two different conditions. Split
them, so each states only what the app actually knows:

- `not expected` → **"This session carried no OAuth state."**
- `request.args.get("state") != expected` → **"The OAuth state did not match."**

Both stay **400**. Neither message names a cause: "your cookie wasn't sent because you are on the
wrong host" is an inference the app cannot make, and it must not be written as though it can.
These two conditions, by contrast, are facts the code has already established.

`session.pop(...)` stays **before** both checks, unchanged, so the state remains single-use and
a captured callback stays unreplayable.

**The order of the two checks is a security property, not a style choice.** They are one `or`
today; splitting them into two `if` statements makes their order visible and therefore
reversible. `not expected` **must** be tested first. If the mismatch check went first, an
unsolicited callback carrying no `state` argument against a session holding no state would
evaluate `None != None` → `False`, fall through both guards, and reach the token exchange. The
existing `test_a_callback_carrying_no_state_at_all_is_refused` (`test_routes.py:892`) is the test
that catches this, and its docstring already spells the reasoning out — it must keep passing, and
its `auth_spy == []` assertion is the half that matters here.

---

## 2. T2 — `/dev/roundtrip`'s two request estimates start combining

### 2.1 The problem

Two estimates sit on that page with nothing tying them together:

- The Status panel's `~N requests` — `roundtrip.counts()`, `2 * batches + 3` — the cost of
  round-tripping what is queued *now*.
- Each Album backfill **Add** button's `~M requests` — `backfill._requests_estimate`, one request
  per album — spent *immediately on click*, fetching tracklists.

The second causes the first to rise, and the page says so nowhere. "Add for 7 generations, then
run the round-trip — what does that cost?" is unanswerable from the page as it stands.

### 2.2 The projection, which is free

`_settled_map` already computes `missing = total_tracks - owned - queued` per album, and throws it
away after reducing it to a boolean. **That number is precisely how many URIs an Add would
queue.** So:

- `_settled_map` returns the missing *count* per album alongside (or instead of) the boolean;
  settled remains `missing <= 0` and every existing caller keeps its current meaning.
- `_derive` sums `max(0, missing)` over its chosen unsettled albums into a new `queued_uris` key.
- `previews()` carries `queued_uris` through to each row.

No Spotify calls, no new query — it is the arithmetic already being run.

It is an estimate to the same degree the settled/unsettled figures on that page already are: a
tracklist fetch can reveal a URI Symr owns under a different `album_id`, and `total_tracks` can
disagree with the tracklist's real length. That is acceptable and is not to be "fixed" by
fetching anything.

### 2.3 The combined figure

`roundtrip.py` gains a module-level `requests_estimate(n_uris)` returning `2 * ceil(n_uris / 100) + 3`.
`counts()` calls it instead of inlining the formula. This is the only reason the formula moves:
**there must not be a second copy** that can drift from the Status panel's.

`backfill.previews()` gains a `remaining_uris` parameter and calls
`roundtrip.requests_estimate(remaining_uris + row_queued_uris)` per row. `backfill.py` gaining an
import of `roundtrip.py` adds no cycle — `roundtrip.py` imports neither `backfill` nor anything
that reaches it, and only `app.py` imports both today.

Each preview row therefore carries: `album_count`, `requests_estimate` (unchanged, the Add's own
spend), `range_label` (unchanged), `queued_uris`, and `roundtrip_estimate`.

### 2.4 What the page says

Each Add row renders all three numbers and their sum, e.g.:

> Next 7 generations (31–37): 42 albums · ~42 requests now, ~318 on the next round-trip · **~360 total**

and the panel's existing `<p class="meta">` gains a sentence saying the two spends happen at
different times — the Add's on click, the round-trip's when you next run it. That sentence is the
whole point of T2; the roadmap's complaint was that the adjacency reads as one cost.

Exact wording is not specified beyond that. Function over form.

**Page-load only.** These figures are server-rendered on load exactly as `album_count` and
`requests_estimate` already are, and get **no** `data-field` wiring in `roundtrip.js`. The Status
panel's own `requests_estimate` continues to update live, so after a backfill run the two halves
of the page disagree until reload. That is already true today of `album_count`, it is accepted,
and it is not a bug to fix here.

---

## 3. T3 — the review queue's group colours

### 3.1 The measurement, and why white text goes

`static/js/canonical_review.js`'s six-colour `COLORS` array is cycled per distinct group. Two of
the six fail white text at 12px outright: `#059669` at 3.77:1 and `#d97706` at 3.19:1, against the
4.5:1 needed. And the ceiling is structural — 4.5:1 against white caps relative luminance at about
18%, so **every** white-text-safe colour is dark, and a "bigger version of the same six" collapses
into one narrow lightness band. A fourteen-colour white-text set was built and measured during
planning: nine of the fourteen landed between 13% and 18%, which is the reported bug with more
hues on top.

So chips carry **per-colour text**, black or white, whichever passes. That is what buys the
lightness variation the roadmap asked for.

### 3.2 The palette

Twelve colours, in cycle order. Every entry passes 4.5:1 against its own text colour.

| # | Name | Background | Text | Ratio | Lightness |
|---|------|-----------|------|-------|-----------|
| 1 | Blue | `#2563eb` | white | 5.2:1 | 15% |
| 2 | Red | `#dc2626` | white | 4.8:1 | 17% |
| 3 | Green | `#047857` | white | 5.5:1 | 14% |
| 4 | Gold | `#facc15` | black | 13.7:1 | 64% |
| 5 | Pink | `#ec4899` | black | 6.0:1 | 25% |
| 6 | Navy | `#1e3a8a` | white | 10.4:1 | 5% |
| 7 | Lavender | `#ddd6fe` | black | 15.1:1 | 71% |
| 8 | Orange | `#fb923c` | black | 9.3:1 | 41% |
| 9 | Teal | `#14b8a6` | black | 8.4:1 | 37% |
| 10 | Brown | `#78350f` | white | 9.1:1 | 7% |
| 11 | Sky | `#7dd3fc` | black | 12.6:1 | 58% |
| 12 | Lime | `#84cc16` | black | 10.6:1 | 48% |

Sorted, the lightness ladder is 5, 7, 14, 15, 17, 25, 37, 41, 48, 58, 64, 71 — no two adjacent
values share a hue family. The order above is the **cycle** order, chosen so the first few slots,
which co-occur on nearly every item, are maximally separated.

`COLORS` becomes twelve `[background, text]` pairs. `chipCell` sets `chip.style.color` alongside
`chip.style.background`. The cycle still repeats past the twelfth (`% length`); twelve distinct
groups on one item is already far past what is readable, and generating more is not worth it.

`.tier-chip { color: #fff }` in `style.css` stays as it is — the inline colour overrides it, and
the rule is still doing work for the fixed tier chips.

### 3.3 The ISRC stripes are a separate, smaller palette

`isrcColorMap` shares `COLORS` today, but it does not render chips: `canonical_review.js:550`
paints a `4px solid` **left border** on the ISRC cell. Lightness that reads well as a filled pill
disappears as a hairline — lavender at 71% is invisible against white — and the two darkest read
as plain black.

So it gets its own six, the saturated mid-tones, as bare hex strings with no text colour:

`#2563eb` blue, `#dc2626` red, `#047857` green, `#ec4899` pink, `#14b8a6` teal, `#fb923c` orange.

### 3.4 Out of scope here

`.tier-chip.version` / `.recording` / `.release` (`style.css:799-812`) are **not** touched. Those
are fixed *tier* colours on `/dev/canonical` and the entity pages — colour means "which tier"
there, not "which group" — and that three of their hexes coincide with old `COLORS` members is
coincidence with no consequence.

---

## 4. T4 — the review queue's done screen gets a way out

### 4.1 The problem

`#save-btn`'s click handler and the `Enter` keydown branch both guard on `itemSection.hidden` and
no-op once `finishQueue()` reveals `#review-done`. The only exit is clicking a small
"← Canonical Tracks" link. `#review-empty` — shown when the queue is empty on arrival — is the
same dead end.

### 4.2 The fix

Both screens enter an **exit state**. In it:

- `#save-btn` is relabelled **"Done"** and keeps `title="Enter"`. Not "Save & exit" and not
  "Exit": by that point there is nothing left to save, and "exit" alone reads as though it might
  discard something. "Done" says the state is committed.
- `#save-btn`'s click navigates to the canonical-tracks page instead of calling `commit()`.
- `Enter` does the same.
- `#clear-btn` and `#back-btn` are **disabled** — they already no-op, and a live-looking control
  that does nothing is worse than a greyed one.

The destination is read off the visible screen's existing `<a>` href, which `url_for('dev_canonical')`
already renders. Do not hardcode `/dev/canonical` in JS.

The keydown handler's `if (itemSection.hidden) return;` early return currently kills every branch,
so the exit check has to sit **above** it.

### 4.3 The held-Enter guard

Enter is held down through items. `keydown` auto-repeats, so a naive re-wire would navigate on the
very next repeat after the final commit and the "Got through N items" screen would never be seen.

So entering the exit state disarms Enter, and a **`keyup`** re-arms it. Not a timeout: a keyup is
the actual event being waited for, and a delay would be an invented number that is either too
short to work or long enough to feel broken.

This applies to `Enter` only. The click path needs no guard.

---

## 5. Tests

### 5.1 T1

**The trap, which has to be designed around rather than discovered.** `tests/conftest.py:53` sets
`SPOTIFY_REDIRECT_URI = "http://localhost:45660/callback"`, and the Flask test client's default
host is also `localhost`. They agree. So the redirect branch fires in **no** existing test, and
every current `/login` test passes identically with or without T1. A test of the redirect must
issue the request from a deliberately non-canonical host (`base_url=` or `environ_overrides`).

Assert:

- **The redirect fires.** `/login` from a non-canonical host returns 302 to
  `SPOTIFY_CANONICAL_ORIGIN`'s `/login`, **and sets no `oauth_state`**. Both halves: a test that
  only checks the 302 passes against an implementation that redirects *after* setting the cookie,
  which fixes nothing.
- **The marker suppresses it.** `/login?canonical=1` from a non-canonical host proceeds and sets
  `oauth_state`. Without this, §1.3's whole loop bound is untested.
- **The comparison is hostname-only.** A request from the canonical hostname on a different port
  does **not** redirect. This is the clause that stops the comparison being "tightened" into a
  full-origin one and looping fe-pro.
- **The two `/callback` descriptions are distinct**, and each fires on its own branch.

  *This replaces an existing rule.* `test_routes.py:892` and `:871` both assert against today's
  single `"Invalid OAuth state."`. Updating them to the new strings is necessary but not
  sufficient — the point of the split is that the two branches now say *different* things, so
  something must assert they differ. Two tests that each check a 400 and a message, where both
  messages happen to be equal, would pass against an implementation that split the branches and
  gave them identical text.

  This is `CLAUDE.md`'s `/callback` carve-out still applying: every refusal there is a 400, so a
  status code distinguishes nothing. The `auth_spy` fixture stays in use — it is what stops a
  wrongly-passing guard surfacing as a blocked-socket error that reads like a refusal.

- The existing `/login` tests (`test_routes.py:1325`, `:1374`) stay valid and unchanged: their
  default host *is* the canonical one, so they exercise the pass-through path deliberately rather
  than accidentally. Say so in a comment, or the next reader will assume they were missed.

### 5.2 T2

- **`previews()`'s `queued_uris` equals the sum of `missing` over the chosen unsettled albums.**
  The fixture must contain an album that is *partly* owned and one that *already has unresolved
  `wanted_uri` rows*, so that a naive `sum(total_tracks)` and a naive `total_tracks - owned` both
  give a different answer from the right one. Without that, the test passes against two wrong
  implementations.
- **`roundtrip.requests_estimate` is the single source.** Assert `counts()["requests_estimate"]`
  and a preview row's `roundtrip_estimate` both come from it — and choose a fixture where
  `remaining + queued_uris` crosses a multiple of 100, so an off-by-one in the `ceil` is caught.
  A round number tests nothing.

  *This replaces an existing rule.* `previews()` today returns exactly three keys; a test that
  asserts the row *shape* would pass on a `roundtrip_estimate` of `0`. Assert the value.

### 5.3 T3 and T4

**None — JavaScript and templates only.** Both are entirely `static/js/canonical_review.js` and
`templates/canonical_review.html`. The suite is pytest over Python and has no browser-JS runner;
`test_template_conventions.py` covers `entity_link` usage, which neither touches. `test_routes.py`'s
sweep already asserts `/dev/canonical/review` responds, and that remains true.

Verifying T3 and T4 is Verify's job, in the browser, by hand.

---

## 6. Out of scope

- **O's "remaining today" figure.** T2 combines two estimates the page already computes; it does
  not introduce a budget, a ceiling, or anything to subtract from. That stays O's, still gated on
  the `api_request` log catching a real lockout.
- **Live-updating the Add rows** (§2.4).
- **The fixed tier chips** (§3.4).
- **`#review-error`'s exit.** It is a genuine dead end too, but it is an error state, not a
  completion state, and re-wiring a control on it was not asked for.
- **Any change to `/callback`'s status codes or to the OAuth flow itself.** T1 changes where the
  cookie is set and what two refusals say, and nothing else.
