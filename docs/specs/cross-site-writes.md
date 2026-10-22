# Cross-site writes — refusing state-changing requests another site's page makes Finn's browser send

Not a roadmap step — standalone.

---

## 1. The hole

`app.py`'s `require_login` hook asks one question: does the *server* hold a cached Spotify token
(`spotify_client.get_spotify_client()`)? It asks nothing about the browser making the request.
No session cookie takes part in auth, and nothing anywhere checks where a request came from.

So any web page open in Finn's browser, on any device inside the tailnet, can make that browser
send a request to Symr — for example

```js
fetch("https://symr.fmje.dev/api/roundtrip/start", {method: "POST", mode: "no-cors"})
```

That is a CORS *simple request*: a body-less (or form-encoded) `POST` is sent **without a
preflight**, so the handler runs and the side effect happens even though the page can never read
the response. `/api/roundtrip/start` takes no body at all and starts the one job that **writes to
Finn's real Spotify library** (`roundtrip.py`). Every other state-changing endpoint — every
`POST` / `PATCH` / `DELETE` route in `app.py`, including the plain HTML form `POST`
`/dev/generations/confirm` — is exposed the same way. A plain cross-site `<form>` needs no
JavaScript at all.

Production is `https://symr.fmje.dev` behind Caddy, which terminates TLS: the container sees plain
`http` on port 45660, with the browser's `Host` header preserved. The laptop dev loop is
`http://127.0.0.1:45660`, sometimes reached as `http://localhost:45660`.

---

## 2. The rule

One app-wide `before_request` hook, implementing the same algorithm as Go 1.25's
`net/http.CrossOriginProtection`. Modern browsers attach `Sec-Fetch-Site` to every request, and
browsers old enough to lack it still send `Origin` on a cross-origin `POST`; neither can be set
or suppressed by the page making the request.

**2.1 Safe methods always pass.** `GET`, `HEAD` and `OPTIONS` are never checked.

**2.2 `Sec-Fetch-Site`, when present, decides alone.** The request passes only if its value is
`same-origin` or `none` (`none` is a user-initiated navigation: a typed URL, a bookmark). Every
other value is rejected — `cross-site`, **and `same-site`**: another `*.fmje.dev` host is a
different origin and is not trusted. As in Go, a present-but-empty header counts as absent and
falls through to §2.3.

**2.3 Otherwise `Origin`, when present, decides.** The request passes only if the `Origin`'s
host-and-port (its netloc) equals the request's `Host` header, compared case-insensitively.
**The scheme is never compared** — behind Caddy the browser's `Origin` is
`https://symr.fmje.dev` while Flask sees `http`, so a scheme-sensitive comparison would refuse
every write in production. Anything else is rejected: a different host, the same host on a
different port, `Origin: null` (an opaque origin — a sandboxed iframe, a `data:` URL — whose
netloc is empty), and an `Origin` that does not parse at all (a 403, not a 500 — as in Go, only
a parsed, matching `Origin` passes). **An empty netloc is refused outright, not by the equality
test:** Werkzeug's `request.host` is `""` for a missing Host or one with invalid characters, so
equality alone would pass `null` against it. (Go would pass that pair, comparing `"" == ""`; no
browser sends an empty Host, so this is a stricter corner, not a divergence that matters.
Found at Verify.)

Scheme-blindness is safe here for the reason Go accepts it: the only thing it could let through
is an `http://` page on Symr's own host and port, and there is no such page that is not Symr
itself. Every browser Finn uses sends `Sec-Fetch-Site` anyway, so §2.3 is the fallback, not the
path.

**2.4 Neither header: pass.** A request with no `Sec-Fetch-Site` and no `Origin` is not from a
browser acting on another page's behalf — curl, a script, a future API client. Cross-site request
forgery is a browser-only threat, and passing these is the documented behaviour of the algorithm.

**2.5 A rejection is a 403 through the existing error path.** `/api/*` gets the project's one JSON
error shape, `api_error("cross_origin_request_rejected", 403, detail=...)` →
`{"error": "cross_origin_request_rejected", "detail": "<why>"}`. Every other path gets
`error.html` via `render_error(403, "Forbidden", detail=...)`. The detail names the method and
says the request did not come from a Symr page.

**2.6 No exemptions, and no CORS.** The hook applies to every endpoint, including
`_PUBLIC_ENDPOINTS` (`login`, `callback`, `static` — all `GET` today, so §2.1 passes them; not
exempting them means a future public write is covered by default). No `Access-Control-*` header is
added anywhere: Symr has no legitimate cross-origin caller.

**Not covered, deliberately.** §2.1 leaves `GET`s unchecked, as the algorithm does, and some Symr
`GET`s write: `canonical.ensure_track_groups()`, `scoring.ensure_fresh()`, and the album page's
`queue_wanted_uris()` plus a first-view detail fetch. All of those are idempotent derived-state
writes or a single *read* request per already-known album or artist; none starts a job or touches
the Spotify library. The one visible effect a cross-site `GET` can have is re-queueing an album's
unowned uris after a `[Clear]` — which revisiting the page does anyway (M §4.4).

---

## 3. What it replaces

The previous rule was **any request reaching the port is trusted**. `host-on-fe-pro-Q.md` §12
justified leaving Symr without app-level authentication on *reachability*: Tailscale is the
boundary, and only devices already in the tailnet can reach the port.

That argument is sound for who can **reach** Symr and says nothing about who can **drive** it. A
cross-site request is not sent by an outsider reaching the port — it is sent by Finn's own browser,
from inside the tailnet, on behalf of whatever page that browser happens to have open. §12's
boundary is exactly what such a request crosses legitimately. This spec closes that gap; it does
not revisit §12's authentication decision or its tripwire.

---

## 4. Where the hook sits and why

`app.py` now has **four** `before_request` hooks, in this order:

1. `set_api_context` — unchanged, still first and unconditional. It only labels a contextvar and
   does no work on the request's behalf, so running it for a request about to be refused costs
   nothing; keeping it first keeps partial-pulls-J §4.3's invariant intact, and means that if
   anything on the rejection path ever did make a Spotify request, it would be logged under this
   request's endpoint rather than a label left behind on the thread by the previous one.
2. **`reject_cross_origin_writes`** — new.
3. `require_login` — calls `get_spotify_client()`, which can fire a **token-refresh `POST`** to
   Spotify and rewrite the token cache. That is work done on the request's behalf, so the check
   has to come before it: a forged request must not spend a Spotify request or touch the token.
   (It also means a refused request is a 403, never a 401 or a redirect to `/login`.)
4. `refresh_scores` — `scoring.ensure_fresh()` can enqueue a recompute; same reason.

Flask runs `before_request` hooks before URL dispatch raises its 404/405, so a cross-site write to
a route that does not exist is a 403, not a 404. That is the algorithm's behaviour too (Go's check
wraps the whole handler) and costs nothing.

---

## Tests

Route-level, through the real `client` fixture, in `tests/test_cross_site_writes.py`. A status
code alone cannot tell a working check from a missing one on the pass side (the handler's own 200)
or from an unrelated refusal on the reject side, so:

- **Every rejection asserts the side effect did not happen** — a spy on the job-start or write
  function (`roundtrip.start`, `generations.confirm_generation`) records no call — and asserts the
  exact error body (`/api/*`) or the rendered detail (pages).
- **Every pass asserts the handler ran** — the same spy recorded exactly one call.

The clauses a plausible wrong implementation would get wrong, each pinned by its own test:

- `same-site` is rejected (§2.2) — a "reject only `cross-site`" implementation passes it.
- `https` `Origin` against an `http` request on the same host:port passes (§2.3) — the Caddy
  case, which a scheme-sensitive comparison refuses.
- Same host, different port is rejected (§2.3) — a hostname-only comparison passes it.
- `Origin: null` is rejected (§2.3).
- A cross-site `GET` passes (§2.1) — an implementation checking every method refuses it.
- `Sec-Fetch-Site: same-origin` passes even with a foreign `Origin` beside it (§2.2) — an
  implementation consulting `Origin` whenever it is present refuses it.
- A cross-site `POST` to `/dev/generations/confirm` is rejected with `error.html`, not JSON (§2.5).
- Added at Verify, each for a mutant that survived the original file: an empty `Sec-Fetch-Site`
  falls through to `Origin` (§2.2); the `Origin`/`Host` comparison ignores case (§2.3);
  `Origin: null` is refused against an empty `request.host` (§2.3); a cross-site write to an
  unknown or `GET`-only route is a 403, not a 404/405 (§4).
- Neither header passes (§2.4) — also what keeps `test_routes.py`'s sweep green, since the Flask
  test client sends neither.
