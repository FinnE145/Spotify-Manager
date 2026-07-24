# Site Shell (Multi-Page Navigation) — Feature Spec

Status: **ready to implement**. This spec is the standalone implementation prompt — an implementation session can start from just this file. Follow the implement-phase workflow in `CLAUDE.md`: ask implementation questions live/one-at-a-time, don't decide undecided things yourself. Open implementation questions are at the bottom.

> **Branch:** work in the main checkout on the current `feat/*` branch (currently `feat/canvas`). Do **not** create a git worktree or work off `main` (see project memory `feedback-no-worktrees`). Check with `git branch --show-current` first.

## Read first
- `CLAUDE.md` — conventions, workflow, KISS, frontend "function over form" rule.
- `docs/specs/org-canvas.md` — the canvas feature being moved onto its own route.
- `docs/Planning/feature_ideas.md` — the backlog these nav entries point toward.

## What this is
Symr currently serves the org canvas directly at `/` with no shared layout. This turns Symr into a **multi-page site**: a shared base template + navbar, a home page at `/`, the canvas moved to its own route, and stub routes for the planned feature pages so the navbar is complete and every link works.

This session builds **only the shell** — no new feature logic. Each real feature page (Audit, Cover Library, etc.) gets its own spec later; here they are "coming soon" placeholders.

## Scope

### Build now
- Shared `templates/base.html` (common `<head>`, navbar, content block).
- Navbar with primary feature links + a right-aligned utility slot.
- Home page at `/` — structured-but-blank landing/dashboard placeholder.
- Canvas moved from `/` to `/canvas`, refactored to extend `base.html`.
- Stub pages for the unbuilt routes, all rendering a shared "coming soon" placeholder.
- A global auth guard so **every** page (except OAuth routes + static) requires Spotify login.

### Not now (out of scope)
- Any real content for Audit / Cover Library / Folder Structure / Analytics / Snapshot beyond the placeholder.
- A design system / `style_guide.md` (still TBD). Keep styling minimal — "generally the right shape," no polish.
- Version engine as its own page — it is a **sub-section of Audit**, not a nav item.

## Routes
| Path | Name | Type | Notes |
|------|------|------|-------|
| `/` | Home | Real (structured stub) | Landing/dashboard hub. |
| `/canvas` | Canvas | Real (existing feature) | The org canvas, moved here from `/`. |
| `/audit` | Audit | Stub | Verification / problems detail. Version-engine checks live here later. |
| `/covers` | Cover Library | Stub | |
| `/folders` | Folder Structure | Stub | |
| `/analytics` | Analytics | Stub | |
| `/snapshot` | Snapshot | Stub (utility) | Raw library snapshot browser — diagnostic, reached via the navbar utility slot, not the primary nav. |
| `/login`, `/callback` | — | Existing OAuth | Unchanged. Exempt from the auth guard. |

Keep the existing `/api/*` routes exactly as they are — only the page routes change.

## Templates & layout
- **`templates/base.html`** — the one layout every page extends. Contains `<head>` (title, stylesheet link), the navbar, and a content block (e.g. `{% block content %}`). Provide a `{% block title %}` so pages can set their own `<title>`, and a hook (e.g. a `body` class or `{% block body_class %}`) so a page can opt into the immersive full-viewport layout — see Canvas below.
- **`templates/canvas.html`** — refactor to `{% extends "base.html" %}`; move its toolbar + `#main`/`#tray`/`#viewport`/`#world` markup into the content block. Its `<script>`/logic is unchanged.
- **`templates/home.html`** — new; see Home page.
- **`templates/coming_soon.html`** — new; shared placeholder for all stub routes. Takes a page title/name and renders a simple "‹Page› — coming soon" heading. All stub routes render this same template with a different name passed in.

## Navbar
Horizontal bar across the top, full width, sits **above** all page content on every page.

- **Left:** app title/wordmark **"Symr"** (links to `/`), then the primary feature links: **Home · Canvas · Audit · Cover Library · Folder Structure · Analytics**.
- **Right (utility slot):** visually separated from the primary links (pushed to the far right). Holds the **Snapshot** link, shown as a small icon (a database/🗄-style glyph is fine — plain text/emoji, no icon library). This slot is the future home for other diagnostic/backend tools; structure it so more can be added without touching the primary nav.
- **Active state:** the current page's link is visually marked (e.g. an `active` class). Pass the active page name from each route to the template.
- Basic HTML + minimal CSS only. Right shape, not pretty.

## Home page (`/`)
A structured-but-blank landing hub — not literally empty, but no real data yet.
- A page heading (Symr / home).
- A **"Problems at a glance"** panel: a placeholder box (empty/"nothing to show yet") that will later summarize verification problems, with a link through to **`/audit`** for detail.
- Optionally a few quick-link cards/links to the main pages (Canvas, etc.).
- Plain HTML, right shape, no fancy CSS.

## Stub pages
`/audit`, `/covers`, `/folders`, `/analytics`, `/snapshot` each render `coming_soon.html` with their display name. That's the whole page for now — they exist so the navbar is complete and every link resolves.

## Auth guard
Replace the per-route login check in `index` with a **single app-wide `before_request` guard**:
- If `get_spotify_client()` is `None`, redirect to `login`.
- **Exempt:** the `login` and `callback` endpoints and static files (`request.endpoint == "static"`). Guard by endpoint name so it's robust.
- Result: every page (home, canvas, all stubs) is gated identically. Remove the now-redundant check inside `index`.

Security note (per CLAUDE.md): do this thoroughly — exempt by a known allowlist of endpoints, not by URL-prefix string matching.

## CSS
- Keep the single `static/css/style.css`. Add navbar + shared/base styles there. (There won't be many; a separate file isn't worth it yet.)
- **Layout fix:** `overflow: hidden` on `html, body` and the `height: calc(100% - 45px)` math are currently global and assume the canvas is the whole page. The canvas must still fill the viewport **below the navbar** (navbar height + its own toolbar accounted for), while ordinary pages (home, stubs) should **scroll normally**. Move the immersive/no-scroll behavior onto the canvas page only (via the `base.html` body-class hook), so home/stub pages get default document flow.

## Files touched (expected)
- `app.py` — move `/` canvas → `/canvas`; add `/` home + stub routes; add `before_request` auth guard; render templates with active-page + stub-name context.
- `templates/base.html` — new.
- `templates/home.html` — new.
- `templates/coming_soon.html` — new.
- `templates/canvas.html` — refactor to extend base.
- `static/css/style.css` — navbar/base styles + the overflow/height layout fix.
- `docs/Planning/feature_ideas.md` — mark version engine as an Audit sub-section (the shell itself is infrastructure, not a backlog feature — don't list it here).
- `CLAUDE.md` Codebase Map — add the new routes/templates once created.
- `.gitignore` — already edited in the working tree (adds `.claude/`, local Claude Code tooling config). Not yet committed; fold it into this session's commits.

## Open implementation questions
Ask these live if they come up; don't assume:
1. Exact wording/heading text for the home page and the "coming soon" placeholder.
2. Whether the navbar title "Symr" should also show the "simmer" tagline anywhere (default: no).
3. Any preferred URL slug changes (defaults above: `/audit`, `/covers`, `/folders`, `/analytics`, `/snapshot`).
