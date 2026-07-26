# Error Pages (HTTP Error Handling) — Feature Spec

Status: **ready to implement**. This spec is the standalone implementation prompt — an implementation session can start from just this file. Follow the implement-phase workflow in `CLAUDE.md`: ask implementation questions live/one-at-a-time, don't decide undecided things yourself.

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

### Diagnostics shown on the HTML page
- Heading: `<code> — <name>` (e.g. `404 — Not Found`, `500 — Internal Server Error`).
- The request **method + path** that errored (e.g. `GET /snapshot/track/xyz`).
- For `HTTPException`: the `description`/message, if any.
- For the 500/exception case: the **exception type name + message** (e.g. `KeyError: 'placement'`). This shows even when debug is off — that's the normal run mode (`SYMR_DEBUG` defaults to `0`) and the whole point of the feature.
- **Traceback:** when `APP_DEBUG` is on, Flask's built-in interactive debugger already intercepts uncaught exceptions with a full traceback *before* our handler runs, so the custom page primarily serves the **debug-off** case. Do **not** re-implement a traceback dump in the template — rely on the built-in debugger when debug is on, and on the type+message when it's off. (This satisfies "traceback only when debug is on" without duplicating Werkzeug.)

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

### Auth-guard interaction (no change needed, but verify)
The `before_request` guard runs *before* dispatch, so an unauthenticated user is redirected to `login` before reaching most errors (including a 404 for an unknown URL while logged out). Error handlers fire for logged-in requests, or during `/login`/`/callback` (which are exempt). No changes to the guard — just confirm error pages don't trigger a redirect loop.

## Files touched (expected)
- `app.py` — import `abort`, `HTTPException` (from `werkzeug.exceptions`); add the two error handlers + the shared `render_error` helper inside `create_app()`; convert the five inline errors to `abort(...)`.
- `templates/error.html` — new; generic error page extending `base.html`.
- `static/css/style.css` — minimal error-page styling only if needed (reuse existing `.page` styles first; add nothing decorative).
- `CLAUDE.md` Codebase Map — note the error handlers in `app.py` and add `templates/error.html`.

## Verify (implementer)
Exercise each path in the running app (`SYMR_DEBUG` off, the default) and confirm the page/JSON shows the right code and diagnostics:
1. **Unmatched URL** (e.g. `/nope`) while logged in → 404 HTML page with `GET /nope`.
2. **Not-found record** — a bad `/snapshot/track/<id>` → 404 page showing "Track not found."
3. **OAuth failure** — hit `/callback?error=access_denied` → 400 page showing the message.
4. **Uncaught 500** — temporarily raise in a page route → 500 page showing the exception type + message; **revert the temporary raise**.
5. **API 500** — temporarily raise in an `/api/*` route, hit it → JSON error body (not HTML); **revert**.
6. **Fallback** — temporarily break `error.html` (or point it at a missing parent) → the minimal fallback string renders; **revert**.
