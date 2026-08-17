# Error Pages (HTTP Error Handling) — Feature Spec

Status: **ready to implement**. This spec is the standalone implementation prompt — an implementation session can start from just this file. Follow the implement-phase workflow in `CLAUDE.md`: ask implementation questions live/one-at-a-time, don't decide undecided things yourself.

**Audited 2026-08-17** against the code, as part of P1 (`docs/codebase-health/P1_spec_audit.md`).

> **Branch:** work in the main checkout on the current `feat/*` branch (currently `feat/snapshot`). Do **not** create a git worktree or work off `main` (see project memory `feedback-no-worktrees`). Check with `git branch --show-current` first.

## Read first
- `CLAUDE.md` — conventions, workflow, KISS, the "function over form" frontend rule, and the security-is-the-exception rule.
- `docs/specs/site-shell.md` — the base-template/navbar shell these pages plug into.
- `app.py` — current routes and the ad-hoc error returns this replaces.

## What this is
Symr has **no error-handling infrastructure**. Errors today are ad-hoc: page routes return bare strings (`return "Playlist not found.", 404`), OAuth failures return plain 400 strings, `/api/*` routes return JSON, and anything uncaught falls through to Flask's default 500. This feature adds **centralized HTTP error handling**: one styled HTML error page for browser-facing routes, JSON for API routes, and a hardened fallback for when the error page itself can't render.

The point is **diagnostic usefulness, not looks** — when something errors, the page should make it easy to see *what* errored and *why*. Nothing fancy.

## Scope

### Build now
- Central Flask error handlers registered in `create_app()`: one for `HTTPException` (all `abort()`/HTTP errors, any code) and one for uncaught `Exception` (→ 500).
- A single generic HTML error template `templates/error.html` (extends `base.html`), parametrized by code/name/detail.
- **Content negotiation:** `/api/*` requests get a JSON error body; all other (page) routes get the HTML error page.
- **Full diagnostics** on the HTML page: HTTP code + name, the request method + path, and — for the 500/exception case — the exception type + message.
- A **hardened fallback**: if rendering `error.html` itself raises, return a minimal self-contained HTML string instead (no template, no static assets).
- Convert the five existing inline error returns to route through this system.

### Not now (out of scope)
- A design system / `style_guide.md` (still TBD). Error page styling is minimal — right shape, no polish.
- Client-side/JS error surfacing on the canvas/snapshot pages (how the fetch() callers *display* an API error is each feature's concern, not this spec's). This spec only guarantees API errors stay JSON.
- Custom pages for every HTTP code individually — one generic template serves all codes.

## Design

### Error handlers (in `app.py`, inside `create_app()`)
Register two handlers plus one shared helper:

- `@app.errorhandler(HTTPException)` — catches every `abort(...)` and HTTP error (404 for unmatched URLs, 400, 405, etc.). Reads `e.code`, `e.name`, and `e.description` (the message passed to `abort`). Renders with that code; **no exception-type/traceback section** (these are expected, controlled errors).
- `@app.errorhandler(Exception)` — catches uncaught non-HTTP exceptions → **500**. Passes the exception **type name + message** through to the page (see Diagnostics below).

Both delegate to a shared helper, e.g. `render_error(code, name, detail=None, exc=None)`, which does content negotiation, template rendering, and the fallback.

### Content negotiation
The helper decides JSON vs HTML by request path:

```python
if request.path.startswith("/api/"):
    return jsonify({"error": <machine_slug>, "detail": <message>}), code
```

- `/api/*` → JSON. Keeps the canvas/snapshot `fetch()` callers getting machine-readable errors even on an uncaught 500 (the one case this matters — an HTML page handed to `fetch()` would break the JS). Match the existing shape (`{"error": "..."}`); add a `"detail"` field with the message.
- Everything else → the HTML page.

Use the `/api/` path prefix (the app's own convention), not Accept-header negotiation — simpler and exact.

**Standardized on `{"error": <slug>, "detail": <string-or-null>}` for every `/api/*` error
response, not just this handler's own** (settled during P1, P1-014). `render_error` still derives
its slug mechanically from the HTTP status name for generic/uncaught errors; hand-written routes
that need to signal a specific expected precondition (not authenticated, a job already running)
now call a small shared `api_error(slug, code, detail=None)` helper directly, with their own
hand-picked domain-specific slug, rather than building `jsonify(...)` inline — the two paths
previously diverged on shape (the hand-written ones often omitted `detail` entirely). No slug was
renamed; nothing anywhere compares a slug by exact string, so this was a shape fix, not a breaking
rename.

### Diagnostics shown on the HTML page
- Heading: `<code> — <name>` (e.g. `404 — Not Found`, `500 — Internal Server Error`).
- The request **method + path**, including the query string when present (e.g.
  `GET /callback?error=access_denied`) — verified during P1 (P1-014) that the query string was
  being silently dropped; fixed to include it, since the query parameter is frequently what
  actually caused the failure.
- For `HTTPException`: the `description`/message, if any.
- For the 500/exception case: the **exception type name + message** (e.g. `KeyError: 'placement'`). This shows even when debug is off — that's the normal run mode (`SYMR_DEBUG` defaults to `0`) and the whole point of the feature.
- **Traceback:** do **not** re-implement a traceback dump in the template. **The reason is not
  that Flask's interactive debugger gets there first** — corrected during P1 (P1-014), verified
  empirically by running the app with `debug=True`: once `@app.errorhandler(Exception)` is
  registered, Flask's `handle_user_exception` dispatches straight to *that* handler, and the
  debugger's own entry point (`handle_exception`/`log_exception`) is never reached. The custom
  500 page renders with no traceback dump **regardless of `APP_DEBUG`**, because the registered
  handler pre-empts the debugger entirely, not because the debugger already showed one. The
  practical outcome (no traceback in the template) is unchanged; only the stated reason was wrong.

Since this is a local single-user tool, showing the exception type+message in the debug-off page is intended and acceptable.

### The template (`templates/error.html`)
- `{% extends "base.html" %}` so the navbar is present and the page is consistent with the rest of the site.
- Set `{% block title %}` to something like `Symr — Error <code>`.
- Content block: the heading, the request line, and the message/exception info — plain HTML in the `.page` content area. No cleverness.

### Hardened fallback
Wrap the `render_template("error.html", ...)` call in `try/except`. If it raises (base template chain broken, missing static, etc.), return a **minimal self-contained HTML string** built inline in Python — no template, no stylesheet, no `url_for`. It states only that an error occurred *and* that the error page itself failed to render, plus the original status code. Example intent (wording flexible):

> **Error `<code>`.** An error occurred, and the templated error page could not be rendered.

Return it with the original status code. This is the last line of defense and must not depend on anything that could itself be broken.

### Convert the five inline errors
Reroute these through the new system so they render on the styled page (or JSON for API — none of these are API):
- `app.py` — `snapshot_playlist`: `return "Playlist not found.", 404` → `abort(404, description="Playlist not found.")`.
- `app.py` — `snapshot_track`: `return "Track not found.", 404` → `abort(404, description="Track not found.")`.
- `app.py` — `callback`: the three OAuth failures →
  - `abort(400, description=f"Spotify authorization failed: {error}")`
  - `abort(400, description="Invalid OAuth state.")`
  - `abort(400, description="Missing authorization code.")`

The `HTTPException` handler surfaces each `description` as the page's message. (`/callback` and `/login` are exempt from the auth guard, so rendering an error page there won't loop back to login.)

**Both named routes are dead** (found during P1, P1-014, same K-supersession pattern found
repeatedly elsewhere in this audit): `snapshot_playlist`/`snapshot_track` and their
`/dev/snapshot/{playlist,track}/<id>` routes no longer exist — `entity-pages-K.md` §12.1 replaced
them with `/playlist/<id>`/`/track/<id>`. Their `abort(404, description=...)` conversions survive
verbatim on the new routes; only the names above are stale pointers to where they used to live.

**"The five" describes the state at landing, not today.** The app now has **35** `abort()` call
sites (measured 2026-08-17, `grep -c 'abort(' app.py`), the overwhelming majority added since on
`/api/*` routes this spec's own closing parenthetical says don't apply ("none of these are API")
— each of those goes through the JSON path documented in Content negotiation above, not the HTML
template. Not wrong, just radically incomplete as a description of current error-handling surface
area; not worth enumerating precisely here, since the mechanism (route through `abort()`, let the
registered handlers do the rest) is what actually matters and hasn't changed.

### Auth-guard interaction
**Fixed during P1 (P1-014) — this used to be a real gap.** The `before_request` guard runs
*before* dispatch and used to unconditionally redirect an unauthenticated request to `login`,
including an `/api/*` request — invisible to a `fetch()` caller, which would see an opaque
redirect-then-HTML response instead of a JSON error. The guard now checks `request.path` first:
an unauthenticated `/api/*` request gets `api_error("not_authenticated", 401)`, matching every
other `/api/*` error path (see Content negotiation); an unauthenticated page request still
redirects to `login` exactly as before. (`/callback` and `/login` remain exempt from the auth
guard, so rendering an error page there still can't loop back to login.)

## Files touched (expected)
- `app.py` — import `abort`, `HTTPException` (from `werkzeug.exceptions`); add the two error handlers + the shared `render_error` helper inside `create_app()`; convert the five inline errors to `abort(...)`.
- `templates/error.html` — new; generic error page extending `base.html`.
- `static/css/style.css` — minimal error-page styling only if needed (reuse existing `.page` styles first; add nothing decorative).
- `CLAUDE.md` Codebase Map — note the error handlers in `app.py` and add `templates/error.html`.

## Verify (implementer)
Exercise each path in the running app (`SYMR_DEBUG` off, the default) and confirm the page/JSON shows the right code and diagnostics:
1. **Unmatched URL** (e.g. `/nope`) while logged in → 404 HTML page with `GET /nope`.
2. **Not-found record** — a bad id, e.g. `/track/999999999` → 404 page (the route and its 404
   message have moved since this was written — see the "Convert the five inline errors" section
   above — but the mechanism this item exercises is unchanged).
3. **OAuth failure** — hit `/callback?error=access_denied` → 400 page showing the message.
4. **Uncaught 500** — temporarily raise in a page route → 500 page showing the exception type + message; **revert the temporary raise**.
5. **API 500** — temporarily raise in an `/api/*` route, hit it → JSON error body (not HTML); **revert**.
6. **Fallback** — temporarily break `error.html` (or point it at a missing parent) → the minimal fallback string renders; **revert**.
