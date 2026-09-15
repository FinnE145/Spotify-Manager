# UI clean-up: adopt Bootstrap — Feature Spec

**Step W of `docs/Planning/roadmap.md`.**

> **DRAFT — incomplete.** §9, the per-page brief, is written from a live walkthrough with Finn and
> is the section the implement session is actually driven by. Everything above it is decided and
> settled. **Do not commit this file and do not start implementing until §9 is filled in.**

---

## 1. What this is

Wrap the existing site in a CSS framework so it stops looking hand-rolled. **Not a custom design.**
Finn's ask, verbatim from the roadmap: *"i dont want a super fancy custom ui, just maybe wrap it all
with bootstrap or something."* That is the explicit request `CLAUDE.md`'s *Function over form* rule
defers to, and it licenses **adopting a framework's defaults** — a session that starts inventing
components has misread the step.

Layout changes *are* in scope (Finn, planning session): this is not a pure restyle. But they are
layout changes expressed in the framework's own vocabulary, not new components.

## 2. Framework: Bootstrap 5.3

**Bootstrap 5.3.x**, vendored, CSS **and** its JS bundle. Pin the exact patch at vendoring time and
record it here and in a header comment in each vendored file.

### 2.1 Why not a classless framework

Pico.css, Simple.css and Water.css were all considered and priced. The measured argument for
classless was strong: the templates carry **0 inline `style=` attributes**, 54 class names of which
only five are frequent (`meta` 70, `panel` 59, `empty` 40, `data-table` 38, `page` 25), and the JS
keys off `data-*` attributes and ids rather than classes. A classless sheet would have restyled all
21 pages from one `<link>`, making the conversion a matter of *deleting* CSS rather than editing
templates.

It was rejected on two grounds, both Finn's, both decisive:

- **Layout changes are in scope.** Classless frameworks ship one look and no components. Changing
  layout under one means hand-rolling cards, badges and button variants — precisely the
  "inventing components" this step forbids. Bootstrap has them off the shelf.
- **Finn prefers Bootstrap's look**, having looked at all three.

Of the three, Water.css was the closest to Symr's current appearance — which is the argument
against it, not for it: the whole conversion for almost no visible change. Pico was the strongest
classless option and is the fallback if Bootstrap is ever backed out.

### 2.2 The JS bundle ships even though nothing uses it

`bootstrap.bundle.min.js` is vendored and loaded even though **no Symr page currently uses a
Bootstrap JS component** — the navbar has no collapse, the search dropdown is hand-rolled in
`search.js`, and the theme toggle in §4 is ten lines of vanilla JS.

It ships anyway, on Finn's call, for one reason worth recording so a later session does not "clean
up" an unused asset: **a session that sees no framework JS available will hand-roll a component
rather than reach for one.** The bundle's presence is what makes the framework's own dropdown,
modal and collapse the obvious choice at the moment one is first needed. It is essentially free.

## 3. What ships

- `static/css/vendor/bootstrap.min.css`
- `static/js/vendor/bootstrap.bundle.min.js`

Both **vendored, never a CDN.** The reason is availability, not pinning (a CDN URL carries a version
too): the server sits behind `tailscale serve` on `fe-pro`, and a CDN makes every page render depend
on the box having outbound internet. Vendored always works, and costs ~230KB in the repo once.
Secondary benefit: no third-party request per page load.

`templates/base.html` loads them in this order, and the order is load-bearing:

1. `bootstrap.min.css`
2. `style.css` — **second, so Symr's own rules win every specificity tie.**

## 4. Colour modes: auto, plus a manual toggle

Bootstrap 5.3's colour modes are used as-is: `data-bs-theme="light|dark"` on `<html>`, with every
component's colours as variables that respond to it.

- **Initial value from `prefers-color-scheme`**, overridden by a stored choice.
- **The toggle lives in the navbar utility slot**, beside the gear. (Confirm during §9's walkthrough
  when the navbar is on screen.)
- Stored in `localStorage` under `symr_theme`; values `light` / `dark`; **absent means follow the
  system**, so "auto" is the absence of a value rather than a third stored string.
- **The attribute is set by a small inline `<script>` in `<head>`, not by an external file.**
  An external script — even one loaded synchronously — leaves a window in which the document paints
  light before the dark attribute lands, and the flash is on every page load. The toggle *button's*
  click handler is ordinary and belongs in a normal file.

Every surviving Symr rule in `style.css` must work in both modes. There are ~50 hardcoded hex values
today; they become Bootstrap CSS variables wherever a variable means the right thing, and a
`[data-bs-theme="dark"]` override only where it does not.

## 5. The review-queue palette

`canonical_review.js`'s twelve `[background, text]` chip pairs and six ISRC stripe tones are a
deliberate, contrast-checked design decision (`docs/specs/small-fixes-T.md` §3.1) and are **not**
surrendered to the framework. Light mode is unchanged. Dark mode gets the palette below, approved by
Finn in the planning session.

T §3.1's rule was that per-chip black-or-white text is what buys the lightness range. **In dark mode
that constraint inverts**: contrast against the chip's own text is still required, but the binding
constraint becomes not vanishing into the page ground. Navy `#1e3a8a` and Brown `#78350f` cannot
survive it — they are darker than the background allows — so they become a light indigo and a tan.
Everything else moves one or two steps up its own ramp and takes black text, which makes **dark mode
uniformly black-on-light**, simpler than light mode's mixed rule. Sky is unchanged; it was already
in the band.

| Chip | Light (bg / text) | Dark (bg / text) |
|---|---|---|
| Blue | `#2563eb` / `#fff` | `#60a5fa` / `#000` |
| Red | `#dc2626` / `#fff` | `#f87171` / `#000` |
| Green | `#047857` / `#fff` | `#34d399` / `#000` |
| Gold | `#facc15` / `#000` | `#fcd34d` / `#000` |
| Pink | `#ec4899` / `#000` | `#f472b6` / `#000` |
| Navy | `#1e3a8a` / `#fff` | `#818cf8` / `#000` |
| Lavender | `#ddd6fe` / `#000` | `#c4b5fd` / `#000` |
| Orange | `#fb923c` / `#000` | `#fdba74` / `#000` |
| Teal | `#14b8a6` / `#000` | `#2dd4bf` / `#000` |
| Brown | `#78350f` / `#fff` | `#d4a373` / `#000` |
| Sky | `#7dd3fc` / `#000` | `#7dd3fc` / `#000` |
| Lime | `#84cc16` / `#000` | `#a3e635` / `#000` |

ISRC stripes — a 4px left border, so they need saturation rather than lightness:

| | Light | Dark |
|---|---|---|
| 1 | `#2563eb` | `#3b82f6` |
| 2 | `#dc2626` | `#f87171` |
| 3 | `#047857` | `#10b981` |
| 4 | `#ec4899` | `#f472b6` |
| 5 | `#14b8a6` | `#2dd4bf` |
| 6 | `#fb923c` | `#fb923c` |

**The score chip is explicitly not protected.** `_macros.html`'s `score_display` may be restyled;
its CSS rule was written hurriedly during L2's verify pass and was never properly designed.

## 6. Class-name collisions — three, and one is dangerous

Symr's 54 class names were checked against Bootstrap 5.3's components. Three collide, and they are
not equivalent risks. **All three must be resolved before any page is converted**, because the
collision fires the moment `bootstrap.min.css` is linked in `base.html` — it does not wait for a
page to be converted.

### 6.1 `.progress-bar` — inverted semantics

The dangerous one. Symr's `.progress-bar` is the **track** and `.progress-bar-fill` is the fill.
Bootstrap's `.progress` is the track and `.progress-bar` is the **fill**. Linking Bootstrap gives
every Symr progress *track* the styling of a Bootstrap *fill*.

Six templates: `snapshot.html`, `history_import.html`, `roundtrip.html` (×2 — the round-trip and the
backfill panels), `canonical_review.html`, `canonical_cross.html`. Five JS files write `.style.width`
on the fill element. Resolve by adopting Bootstrap's own structure — `.progress` outside,
`.progress-bar` inside — and updating the JS to write width to the element that is now named
`.progress-bar`.

### 6.2 `.card` — and it is on the page we are *not* converting

`canvas.js` sets `el.className = "card"` at three sites plus `"card tray-card"` and a drag ghost, and
reads `closest(".card")`. Bootstrap's `.card` is a component carrying `background-color`, `border`,
`border-radius` and `display: flex; flex-direction: column`.

**This is the mechanism by which "leave the canvas alone" fails.** `canvas.html` extends
`base.html`, so it inherits the stylesheet link whether or not the page is converted — and a changed
card border or padding changes `el.offsetWidth`/`offsetHeight`, which `canvas.js:66-79` snaps to a
grid and stores as card geometry. `grouping.py`'s `group_cards` then measures distances between those
stored coordinates. **The damage lands in saved data, not just on screen.**

Resolve by renaming Symr's canvas class — `.canvas-card` — across `canvas.js` and `style.css`. A
rename is correct rather than an override here precisely because the canvas is out of scope: it must
be provably untouched, and a rename makes that structural instead of a matter of getting an override
right.

### 6.3 `.badge` — benign, but not automatic

`_macros.html`'s `explicit_badge` and `not_in_library`, `canonical.html`'s `auto_badge`, and
`canonical_review.js`. Bootstrap's `.badge` is a component, so this collision is closer to helpful
than harmful — Symr's badge can simply *become* Bootstrap's. But a bare Bootstrap `.badge` sets a
light text colour and **no background**, so `class="badge muted"` (Bootstrap has no `.muted`) would
render near-invisible. Each of the three variants gets an explicit Bootstrap background utility.

### 6.4 `.active` — no action

Symr puts a bare `.active` on plain navbar `<a>` elements. Bootstrap scopes `.active` under
`.nav-link` / `.list-group-item`, so a bare one is never matched. It resolves itself if the navbar
becomes a Bootstrap `.navbar`, where `.active` is then the correct class.

## 7. Scope: 21 pages, canvas excluded

22 page templates exist. **`canvas.html` is excluded** (Finn's call) — see §6.2 for why exclusion
still requires work. The remaining 21, in walkthrough order:

| # | Route | Template |
|---|---|---|
| 1 | `/` | `home.html` |
| 2 | `/dev` | `dev.html` |
| 3 | `/dev/snapshot` | `snapshot.html` |
| 4 | `/dev/canonical` | `canonical.html` |
| 5 | `/dev/canonical/review` | `canonical_review.html` · immersive |
| 6 | `/dev/canonical/cross` | `canonical_cross.html` · immersive |
| 7 | `/dev/artists` | `artists.html` |
| 8 | `/dev/import` | `history_import.html` |
| 9 | `/dev/roundtrip` | `roundtrip.html` |
| 10 | `/dev/scrobble` | `scrobble.html` |
| 11 | `/dev/generations` | `generations.html` |
| 12 | `/dev/generations/tenure` | `generations_tenure.html` |
| 13 | `/dev/scoring` | `scoring.html` |
| 14 | `/search` | `search.html` |
| 15 | `/song`\|`/version`\|`/recording`\|`/release/<id>` | `entity_group.html` |
| 16 | `/track/<id>` | `entity_track.html` |
| 17 | `/album/<id>` | `entity_album.html` |
| 18 | `/artist/<id>` | `entity_artist.html` |
| 19 | `/playlist/<id>` | `entity_playlist.html` |
| 20 | `/audit` (and `/covers`, `/folders`, `/analytics`) | `coming_soon.html` |
| 21 | any 404 | `error.html` |

Eight partials render inside those and get no stop of their own: `base.html`, `_macros.html`,
`_canonical_cross.html`, `_search_combined.html`, and the four `_search_*_rows.html`.

Three variants share a template but are worth a separate look: `/playlist/<id>?generation=1`,
`entity_group` at a second tier, and `/dev/canonical` with a filter applied.

**The two immersive queue pages (5, 6) convert last**, after the 19 ordinary ones — they opt out of
the normal shell via `body_class` and carry the palette in §5.

## 8. What happens to `style.css`

1,065 lines today. Roughly half — lines ~285–850 — is the three immersive pages (`#toolbar`,
`#viewport`, `#world`, `.card`, `#review-header`, `#help-popover`, `.cross-*`) and is not something a
framework replaces. What dissolves into Bootstrap is the ordinary-page half: `.page`, `.panel`,
`.data-table`, `.badge`, `.checkbox-label`, `.inline-form`, the navbar and the form inputs.

It stays **one file**, loaded second so it wins. It is not split, and no per-page stylesheet is
introduced.

## 9. Per-page brief

> **IN PROGRESS.** Written live, page by page, with Finn at the browser. Pages done so far: the
> shell (`base.html`), the navbar, site-wide icons, the gear dropdown, and pages 1, 2, 3, 4, 7, 8
> and 9 of §7's list. **Remaining: `/dev/scrobble` onward, with the two immersive queues last.**
>
> Note that §1–§8 were written *before* the walkthrough and one of their assumptions did not
> survive it: §7 said panels would be decided case by case, and at page 7 Finn made it a site-wide
> rule that there are no cards or panels at all, structure carried by headings, dividers and
> collapses. The one deliberate exception is a stats table, which stays in a card.

### 1. `/` — `home.html`
- "Jump to" bullet list becomes a **row of Bootstrap cards**.
- **Delete** the `Search your library →` line entirely (the navbar box is the only search).
- Otherwise a **light touch-up only**. Finn: the page gets filled out properly later, by pulling in
  whichever elements he ends up using most across the main pages.
- **Converted in normal order.** What changes later is the page's *content*, not its conversion:
  once F/G-era steps exist, Finn pulls their elements onto home. That is well past step W and is
  not a reason to defer the conversion.

### 2. `/dev` — `dev.html`
- The eight dev-tool links become a Bootstrap **list-group**, descriptions as muted text. Cards were
  considered and rejected: eight items with descriptions is too heavy for a card row, and the
  list-group still reads as a sibling of home's cards.
- **Keep** J's `Requests: N in 24h · N in 7d` line as-is — Finn explicitly likes it. It stays a line,
  not a panel; step **O** will add budget figures beside it, so leave it room and don't over-build.
- **Delete** the `Developer and inspection tools.` subtitle under the h1.
- The stale *"the 36 current-favs playlists"* count is **step V's**, not W's. Left alone.

### 3. `/dev/snapshot` — `snapshot.html`
Sets the site-wide vocabulary; the pages after it mostly reuse these.
- **Status**: no panel, no heading. It *is* the top of the page.
- **Controls**: no panel, no heading — a `.button-row` of buttons announces itself.
  Variants: Refresh `btn-primary`, Full pull / Backfill `btn-outline-secondary`,
  Stop `btn-outline-danger`, all `btn-sm`.
- **Find a track: deleted.** `/search` supersedes it. This is W's one reach into Python —
  `snapshot.index_data(conn, q)` loses its argument, `app.py` loses the `?q=` parsing, and the
  `?q=` case at `tests/routes_catalog.py` is removed.
- **Playlists**: a Bootstrap **collapse**, collapsed by default, heading carries the count.
  Collapse rather than an inner scroll region — Finn: scroll regions trap the wheel on the way
  down a page. Candidate general rule; the three existing ones get flagged as we reach them.
- **Playlists sort**: `last_changed_at` **descending**, name ascending within a date, never-changed
  last. **Replaces** scoring-H.md §11.1's score-descending order, which read as arbitrary.
- **Recent changes**: plain section, standard table, stays at the bottom.
- **Tables**: `table table-sm table-hover align-middle`, **no striping** (it fights the cover
  thumbnails). Site-wide `.page .table { font-size: 13px }` keeps `.data-table`'s density.
- General: **a panel is not automatically a card** — case by case, and move away from cards.

### Shell — `base.html` (converted alongside page 3, out of §7's order)
Done early because the navbar is on every page and its hand-rolled search box looked broken
the moment Bootstrap landed.
- `<nav>` becomes `navbar navbar-expand bg-body-tertiary border-bottom py-0`. `navbar-expand`
  with **no breakpoint** — it never collapses to a burger; this is a desktop tool.
- Nav links become `.nav-link`, which is what makes **§6.4 come true**: `.active` was inert on a
  bare `<a>` and is now the correct Bootstrap class.
- **The dev gear needed its own `.nav-icon.active` rule.** It is not a `.nav-link`, so stripping
  `#navbar a.active` silently removed every /dev page's navbar indicator. Caught in the browser,
  not by a test.
- Search box becomes an `input-group input-group-sm`. `.nav-search` survives **only** as the
  positioning anchor for `.search-dropdown` (better-search-L.md §7) plus its 220px width; the
  input and button rules are deleted.
- `#navbar` keeps `height: 45px` and `flex: none` — the immersive pages' flex column is built
  around that number.
- Removed entirely: `.nav-primary`, `.nav-wordmark`, `#navbar a`/`:hover`/`.active`,
  `.nav-search input`, `.nav-search button`.

### Icons — Bootstrap Icons 1.13.1, vendored (site-wide, out of §7's order)
Finn's call: vendor the whole package rather than paste individual paths. The repo already had
**one** hand-pasted Bootstrap Icons path (`person-fill`, inlined in `_macros.html`'s `cover_cell`)
and no package at all; that one is converted too, so there is a single mechanism rather than two.
- `static/css/vendor/bootstrap-icons.css` + `fonts/bootstrap-icons.woff2` / `.woff`. The font
  directory must sit **next to** the CSS — the stylesheet references `./fonts/` relatively.
- Converted: navbar gear (`bi-gear`), theme toggle (`bi-moon-stars` / `bi-sun`, class swapped by
  `theme.js` rather than the button's text), the pin-star in `canonical.html`, `entity_group.html`
  and `canonical_review.js` (`bi-star-fill` / `bi-star`), `cover_cell`'s artist placeholder
  (`bi-person-fill`), and the collapse chevron.
- **The collapse chevron uses codepoints (`\f282` / `\f286`), not `.bi-` classes** — it flips on
  `aria-expanded`, which only CSS can observe, and CSS cannot swap a class.
- **`canonical_review.js` needed a real change, not a substitution**: it concatenated `" ★"` onto
  a title *string*. An icon is an element, so it now appends an `<i>` to the cell.
- **`.cover-placeholder.cover-person svg` matched nothing after the swap** — Bootstrap Icons size
  by `font-size`, the pasted SVG was sized by `width`/`height`. Caught in the browser.
- Left as text, deliberately: the review queues' keyboard hints (`↑ / ↓, j / k` are key *names*),
  trailing link arrows, the date-range `→`, the `›` breadcrumb separator, `·` `—` `±` `≠` `§`.

### Shared chips — `.badge` and `.score-display`
- **§6.3 resolved by keeping Symr's `.badge` rule and theming it**, not by adopting Bootstrap's.
  style.css loads second so it wins on purpose: Bootstrap's badge is a bold filled pill, and these
  are meant to stay quieter than `.tier-chip`, which carries real colour meaning.
- **`.score-display` restyled** — Finn: "it was never properly done". Now on Bootstrap variables,
  so it works in both modes; it rendered as a white pill on dark before.

### Navbar gear — hover dropdown of the dev pages
- **Bootstrap's dropdown component cannot do this.** Hover triggers were removed in v4 on purpose,
  and `data-bs-toggle="dropdown"` intercepts the click — which is exactly the click that must still
  navigate to `/dev`. The menu's whole appearance is Bootstrap's `.dropdown-menu`; only the trigger
  is Symr's, and it is four lines of CSS.
- `:focus-within` sits beside `:hover` because `:hover` alone leaves the menu unreachable by
  keyboard. The menu carries **no top margin** — a gap there and the pointer crosses dead space on
  its way down, closing the menu under itself.
- **The dev page list moved into one place**: `app.py`'s `_DEV_PAGES` + an `inject_dev_pages`
  context processor, read by both `dev.html`'s list-group and the gear menu. Two copies would have
  drifted the first time a dev page was added. The dropdown marks the current page `.active`.

### 4. `/dev/canonical` — `canonical.html`
**The whole page is unpanelled.** Headings and dividers carry the structure instead.

New order, top to bottom:
1. Two **queue buttons** (main / cross-artist), counts inside them, no longer links inline after
   the word "Unreviewed:". The pending-tier-review link joins them as an outline button.
2. The `Reviewed: N pairs · most recent …` line directly beneath them.
3. **Auto-group** button left, **Undo** right-aligned as `btn-sm btn-outline-danger`.
4. Auto-group's own stat line (`Last run … · N groups, M tracks`) beneath its buttons — the same
   button-then-stats shape as (1) and (2).
5. The undo warning below that.
6. **Deleted**: the "Closes every queue item whose tracks share an ISRC…" copy that used to sit
   *above* the stats.
7. Stats — track count and the tier table — moved **below** all of the above.
8. **Search & group**, moved above the two listings.
9. `<hr>`, **Groups** collapse, `<hr>`, **Cross-artist** collapse.

Dividers, after Finn's correction: **below** auto-group (not above it — `#autogroup`'s
`border-top` is deleted), below Stats, below Search & group, and between the two collapses.

- **`.group-scroll` is deleted entirely**, both here and in `_canonical_cross.html`. Both listings
  are collapses now. The one inner scroll region left on the site is
  `.search-section.scrollable` on `/search` — flag it at page 14.
- Filter forms become `input-group input-group-sm` inside a `.filter-form` flex row.
- A matching `<hr>` was added after Snapshot's Playlists collapse.

### Site-wide bug found here: `[hidden]` loses to Bootstrap display utilities
`class="d-flex" hidden` and `class="progress" hidden` both render **visible** — an author-declared
`display` outranks the UA stylesheet's `[hidden] { display: none }`. Found because the auto-group
Confirm/Cancel and Yes-restore steps were showing on page load. It silently affects **every**
progress bar (all four carry `class="progress" hidden`) and every two-step confirm on the site.
Fixed once, globally, with `[hidden] { display: none !important; }` near the top of style.css.
Nothing in the suite could have caught it — it is pure CSS cascade.

### SITE-WIDE RULE (Finn, at page 7) — supersedes the "case by case" note at page 3
**No cards or panels on any page.** Structure is carried by headings, `<hr>` dividers, and
collapses. `.panel` is retired as pages convert; a long or growable listing gets a collapse with
its count in the heading, a short fixed one does not.

### 7. `/dev/artists` — `artists.html`
- Both panels dropped; `<hr>` between the two sections.
- **Merged gets a collapse** even at 14 rows — it only ever grows. **Duplicate candidates does
  not**: it is the actionable half and is usually empty.
- Intro copy at the top deleted.
- Buttons: "Same artist" `btn-primary`, "Not the same" `btn-outline-secondary`, "Unmerge"
  `btn-outline-secondary` — unmerge is reversible curation, so no danger styling, unlike
  /dev/canonical's Undo which discards review decisions.

### 8. `/dev/import` — `history_import.html`
- Intro copy deleted, but **the link inside it survives as a control**: `Add new imports to db →`
  (with `bi-arrow-right`) now sits below the upload row rather than buried in prose.
- "Upload" heading dropped. File input + `Upload & import` become one `input-group`;
  **`Re-import newest upload` moves out of the `<form>`** to right-align opposite it — safe because
  it is `type="button"` and bound by id, so nothing depended on it being inside.
- **Coverage stays in a card** — the one card left on the site, kept deliberately as a trial:
  a stats table reads as a bounded object in a way a listing does not. **No divider after it.**
- Import history becomes the usual collapse, count in the heading.
- OPEN: whether to retrofit the card to `/dev/snapshot`'s status block and `/dev/canonical`'s
  Stats table.

### Second test touched by W
`test_the_import_page_toggles_the_reimport_button_on_has_upload` asserted
`type="button" disabled` as an **exact adjacent substring**, so adding a `class` broke a test that
is really about behaviour. Rewritten to match the button by id and look for `disabled` in the tag —
and it now asserts the tag was found, without which a page that stopped rendering the button at all
would satisfy the "not disabled" half. Verified to still fail by forcing `has_upload=True`.

### 9. `/dev/roundtrip` — `roundtrip.html` + `roundtrip.js`
The page was reordered around the actual flow, not the kind of thing. Finn's diagnosis: he'd start
a run and the progress appeared ~600px above the button he'd just pressed. The backfill half
already worked, because *its* progress sat under *its* buttons — one page, two conventions.

**The sticky action bar** (`.action-bar`, `position: sticky; top: 0`) is now the only place a live
run appears. Three states:
- **idle** — `N batches · ~N requests`, Start round-trip, Reconcile unresolved.
- **live** — progress bar; beneath it the newest feed line with **Stop** hard right. The counts and
  both start buttons are hidden.
- **done** — the same line carrying the run summary, **Stop replaced by Reload**. Reloading returns
  the bar to idle, which is the only way out of this state by design.

**Both jobs drive the one bar.** The backfill's own progress bar, feed, Stop button, busy note and
error line are all deleted; `backfillError` is now an alias for the shared `errorEl`. The single
Stop asks `liveJob` which endpoint to hit.

**One feed, merged by timestamp**, each row tagged `round-trip` or `backfill` with a `.badge`. The
page carried two feeds in two places before, and neither was where you were looking.

Order: h1 → write-warning → sticky bar → queue → album backfill → Stats (card) → manual aliases →
**Live feed** → Known-failed URIs → Run history (collapse). Live feed is third from last, per Finn.

- **Album backfill restructured** from two rows of prose into a real table: Scope / Albums /
  Requests now / On next round-trip / Total / Add. The explanatory paragraph is deleted because the
  column headers now say what it said.
- Copy deleted: the intro paragraph, the "Every clear is one click…" note, the backfill paragraph.
  The ISRC note is shortened to two clauses.
- Leftover counts (remaining, known-but-unplaylisted, aliases, failed, reconcilable, review) move
  into a **Stats card**.

**Bug fixed (pre-existing, found live).** The poll loop rescheduled on `status.active_job` from the
*round-trip's* payload only, while also driving the backfill's display. `active_job` is stamped on
each response as it is served, and the two statuses are fetched as concurrent requests — so when
the backfill released the slot between them, the round-trip payload said `active_job: null` while
the backfill payload still read running. The loop painted that stale frame and **stopped forever**,
freezing the UI mid-run with Stop still enabled. Finn hit exactly this at 44/46 albums; the job had
in fact completed all 46. Now reschedules on `status.active_job || status.running ||
backfillStatus.running`.

#### Page 9, second pass (Finn's follow-ups)
- **`Upload new plays →`** moved below the sticky bar, matching `/dev/import`'s pattern for the
  same kind of cross-page link. The intro copy that used to carry it is deleted.
- **The write warning is shortened to one sentence and moved from danger to warning colour**
  (`.warn`, not `.error`) — it is a standing property of the page, not an error.
- **Stats rows flag themselves** when their value points at work further down: known-failed →
  danger, worth-reconciling and awaiting-manual-alias → warning. **Text colour, not row
  background** (Finn's correction), fed through **`--bs-table-color`** rather than a `color`
  declaration: Bootstrap's cell rule is `.table > :not(caption) > * > *`, which outranks any
  selector worth writing here, so a plain `color` on the row would silently lose. Feeding the
  variable wins with no specificity fight and stays theme-aware.
- **The sticky bar's border spans the window**, not the 900px column: negative margins pull each
  edge out to the viewport and matching padding puts the contents back on the column's grid.
  Checked for horizontal overflow — `scrollWidth == clientWidth`, so the usual `100vw`-vs-scrollbar
  trap is not triggered here.

#### Page 9, third pass
- `Upload new plays →` sits **between the warning and the sticky bar** — it is a page link, not a
  run control, so it must not be inside the part that sticks.
- ISRC copy reworded: the previous version rhymed ("none" / "undone").

### SITE-WIDE: no trailing border on the last row of any repeated list
`.table > tbody > tr:last-child > *` and the same for `.data-table` get
`border-bottom-width: 0`. That border sat directly above whatever followed the table — an `<hr>`,
a card edge, a section heading — and read as a doubled line every time. Both classes are covered
because pages are still mid-conversion. **`.event-log li:last-child` needed the same fix** and was
missed on the first pass — the rule is about any repeated row, not about tables.

Two borders that look similar and are deliberately left alone: `.artist-pair`'s is a **leading**
border separating one pair from the next, not a trailing one; `.action-bar`'s is the sticky bar's
own bottom edge, which is what the divider rule above defers to.

### Bug: the listening row's Clear was never disabled
`/dev/roundtrip`'s three other queue rows each carried `{{ 'disabled' if not counts.X }}` and a
matching `btn.disabled = !status[field]` in `setQueueControls`. **The listening row had neither** —
only a `hidden` toggle for the muted state — so its Clear stayed live at 0 tracks, where muting an
empty row does nothing. Fixed in both the template and the JS. Pre-existing, spotted by Finn.

### SITE-WIDE RULE — dividers (Finn: "pay attention to every divider and what's above and below it")
A divider separates two sections of content. **It must not sit directly below something that
already draws its own bottom edge** — that reads as a doubled line. Two cases on the site:
- **The sticky action bar**, which has a full-width `border-bottom` of its own.
- **A card**, which is bordered on all four sides. (Finn made this call first on `/dev/import`.)

Where a section is conditional, the divider belongs **inside** the condition, below the section, so
it disappears with it — `/dev/roundtrip`'s Manual aliases block is the case: its divider used to sit
above it, which left a stray rule under the Stats card whenever there was nothing to review.

### SITE-WIDE: headings need air above them
Bootstrap's reboot zeroes every heading's top margin, so an `<h2>` sits flush against whatever came
before it — **measured at exactly 0px** against the bottom edge of a card. `.page h2` now carries
`margin-top: 28px`, with `hr + h2` and `hr + section > h2:first-child` reset to 0: the divider has
already separated them, and stacking both makes one gap twice the size of every other on the page.

### Dead bindings cleaned on `/dev/roundtrip`
A scan of template ids against `getElementById` calls, and of `COUNT_FIELDS` against `data-field`
attributes, found three leftovers from the rework: `id="roundtrip-progress"` and `id="rt-bar"`
(read by neither the JS nor the CSS) and `"requests"` in `COUNT_FIELDS`, whose element was removed
with the old status block. No id was read that the template did not render, so nothing was null.

### Colour audit (Finn asked for a sweep of hardcoded values)
86 hardcoded colour declarations at the start. After the pass: **12 remain on converted pages, and
all 12 are deliberate** — the four `.tier-chip` tier colours and the gold pin-star. Those are a
semantic palette in the same sense as T §3.1's review chips: the colour *is* the meaning, and all
of them are saturated mid-tones under white text, so they read on either ground. The rest became
Bootstrap variables (`--bs-border-color`, `--bs-secondary-color`, `--bs-emphasis-color`,
`--bs-secondary-bg`, `--bs-body-bg`, `--bs-primary`).

**Real bug found by the sweep: `.generation-confirm` set a near-white background and never set a
`color`.** In dark mode that rendered the theme's light body text onto `#fffbeb` — invisible, not
merely off-palette. It now uses the warning subtle/emphasis trio. The banner is shared by
`/dev/snapshot` and `/dev/generations`, so it was broken on both.

**47 hardcoded colours remain on the two immersive queue pages, which are broken in dark mode
right now** — verified in the browser: `/dev/canonical/review` paints `#f5f5f5` / `#fff` surfaces
while the text inherits the dark theme's light body colour, so every track name, artist, album and
ISRC is near-white on white. The canvas has the same class of hardcoding but is **excluded from
conversion**, so it will stay self-consistently light under a dark navbar — worth a decision.

#### Page 4, second pass
- **No sticky bar here.** Finn: every control you'd use on this page is within one screen, so the
  round-trip's bar solves a problem `/dev/canonical` does not have.
- **Stats moves below Search & group and into a card**, matching `/dev/import`. No divider under it.
- The undo warning is `.small`.
- **The grouping tree's tier labels stop being coloured pills** and become plain muted text
  (`.tree-tier`). The colour carried nothing the label did not already say, and four pills a row
  made the tree harder to read than its own indentation. `.tier-chip` keeps its colours for
  `entity_group.html`'s tier badge and for `canonical_review.js`'s group palette — both of those
  *are* carrying meaning in colour.
- **`edit_link` right-aligns** (`margin-left: auto`) and gains `bi-arrow-right`. Safe at its other
  call site in `_canonical_cross.html`, where it sits in a plain block and the auto margin is inert.

### SITE-WIDE: link treatment
Links were raw blue underlined text, which on a listing page where nearly every cell is a link read
as a wall of blue. They now use Bootstrap's `.link-body-emphasis` + `.link-underline-opacity-25`
look — body-coloured, faint underline, solid underline and link colour on hover — applied as **one
CSS rule** rather than four utility classes on every anchor across thirty templates.

Anchors that are already something else are excluded rather than fought with: `.btn`,
`.list-group-item`, `.dropdown-item`, and home's `.card` tiles all carry their own colour. The
navbar is untouched because the rule is scoped to `.page` and `.search-dropdown`.

## 10. Tests

**None.** Finn's call, and it is defensible rather than a shortcut: this step changes templates, CSS
and a small amount of JS, and the defects it can introduce are visual — a collision from §6, a
mis-tuned scroll region, a colour that fails in dark mode. None of those are assertable without a
rendering test, which this suite does not have and which this step does not justify building.

Two compensating controls already exist and are the real safety net:

- **`tests/routes_catalog.py`'s non-5xx sweep renders every route**, so any Jinja breakage during
  the conversion fails the suite with no new test written.
- **Per-page browser sign-off** is built into the implement session's structure (§9): each page is
  shown to Finn before the next is started.

The one clause a test *could* pin — that the canvas's class is `.canvas-card` and not `.card`
(§6.2) — is better served by the rename itself, since the collision is with a vendored file the
suite does not parse.

## 11. Not in scope

- **`docs/style_guide.md` is not written, now or later.** `CLAUDE.md`'s Frontend section currently
  says to follow it "once it exists"; that line is **deleted** in this step's plan-phase second
  commit. Bootstrap is the design system, and a second document describing it would only drift.
- The canvas page's own layout (§7).
- Site copy — that is step **V**, deliberately sequenced after this one so the words are written
  against pages that have stopped moving.

---

## 9b. Second review pass (session 2)

Finn walked back through the pages already converted, checking them against rules that were only
settled *after* they were built. **Step V (site copy) is folded into W from here** — his call: we
are on every page anyway and had already been editing copy. V's roadmap entry gets marked
*absorbed into W*, not deleted.

### Copy rules (Finn, session 2) — these are V's, and apply site-wide
- **Em dashes almost never.** An em dash is usually punctuation standing in for structure the
  layout should carry. When one is tempting, look for the structural fix instead: split a column,
  or let weight and colour separate the parts. Worked example on `/dev`, where `Name — blurb`
  became an emphasis-weight name beside muted text with no punctuation at all.
- **Middle dots are fine** where they genuinely separate peers (`64 in 24h · 66 in 7d`).
- **One voice per set.** A list of blurbs is either all descriptions or all instructions, never
  mixed. `/dev`'s eight are all instructions, because it is an index of tools and the useful thing
  is what you would go there to do.
- **A count in prose is rendered from the data or it isn't stated.** `/dev`'s stale "36
  current-favs playlists" was fixed by dropping the number, which is the rule's preferred branch —
  the figure was decoration on a nav row and would have needed a live query to stay honest. This
  rule belongs in `CLAUDE.md`'s Frontend section in the plan-phase second commit.

### CORRECTION to §9's divider rule
The rule was stated one-sidedly as "no divider directly below something with its own bottom edge".
Finn's correction on `/dev/snapshot`: **a missing divider between two consecutive headings is its
own failure**, because nothing then signals that they are separate sections. The rule is
**a divider separates content; it neither decorates an edge nor is omitted where two headings
would otherwise collide.**

### Navbar, second pass
- Theme icon is `bi-moon` (not `bi-moon-stars`), and **both icons fill on hover** —
  `bi-moon-fill` / `bi-sun-fill` / `bi-terminal-fill`. Done through **codepoints in CSS**, because
  the class is set in markup for one icon and by `theme.js` for the other, and CSS cannot swap a
  class from a hover state.
- Dev icon is **`bi-terminal`**, not the originally-chosen `bi-code-slash`: Bootstrap Icons ships
  no `code-slash-fill`, so it could not match the other two. Comment left in the CSS so nobody
  switches it back.
- **Icon alignment took two fixes.** Making both `inline-flex` matched their boxes but left them
  1.6px apart; the cause was `.nav-dev`, the wrapper the hover menu needs, being a block that the
  utility bar centred *instead of* the anchor inside it. Making that wrapper a flex box closed it.
  Verified at delta 0.00.
- The vertical rule beside the search box is removed. The `.nav-utility` **class stays** — it
  carries Bootstrap's flex/gap utilities and is the anchor `.search-dropdown` positions against.

### `/dev`, second pass
- All eight blurbs shortened and rewritten as instructions; em dashes removed entirely.
- `.item-name` (emphasis colour, weight 500) does the separating the dash used to.
- **Row hover underlines the title only**, using the same faint rule as the site-wide link
  treatment, so the affordance reads identically everywhere.

### `/dev/snapshot`, second pass
- `Full pull` becomes a filled `btn-secondary` beside Refresh's primary blue.
- **Recent changes splits `Track — Artist` into two columns** (the em-dash structural fix), and
  **supporting columns are muted**: When, Change, Artist. The two link columns carry the eye.
- **Playlists table muted the same way**: Owner, Last changed, Captured. `Name` and `Tracks` stay
  full weight — `Tracks` is the number that differentiates rows, so it is data, not metadata.
- **The status block stays as dotted `.meta` lines, deliberately not a card or table** (Finn): its
  three rows mix units — playlist counts, track/membership counts, and dates — so a table would
  imply a shared column meaning that does not exist.
- The divider under Playlists **stays**, per the corrected rule above.

### Trap: `.muted` does nothing inside a `.table`
A bare `.muted` is one class and loses to Bootstrap's `.table > :not(caption) > * > *` cell rule,
so it silently has no effect in a table. Needs `.table td.muted` (higher specificity) **and**
`--bs-table-color`, which is the variable that rule actually reads. Same trap as `.stat-row-warn`
on `/dev/roundtrip`; it will recur on every remaining page that mutes a column.

## 9d. Second review pass, continued

### Unfollowed / deleted playlists (`/dev/snapshot`)
Spotify cannot distinguish "you deleted it" from "you unfollowed it" — both simply vanish from
`current_user_playlists` — so `unfollowed_at` honestly covers both.

- **A regression this step caused**: `.data-table tr.unfollowed` stopped matching the moment the
  table became `class="table"`, so the dimming silently died. **Converting a table's class orphans
  every rule written against the old one** — sweep for those before converting each remaining page.
- Even corrected, `color` on a `<tr>` cannot work inside a Bootstrap table: the cell rule
  *declares* a colour, and a declaration beats inheritance at any specificity. It must go through
  **`--bs-table-color`**.
- The row now **dims and strikes through**, links included. Finn rejected a badge.
- **Dimming the link needed `--bs-emphasis-color` overridden on the row**, not a more specific
  selector: the site-wide link rule carries four `:not()` clauses and scores (0,5,1), so beating it
  by specificity would mean repeating all four at every override site. Feeding the variable it
  already reads wins with no fight. **This is the general technique for overriding that rule.**
- Unfollowed playlists now count as **uncapturable** (they can never be captured again), and
  **Hide uncapturable is on by default** — rendered `hidden` server-side, because
  `applyUncapturableFilter` is only bound to `change` and never runs on load.

### Cover placeholders — `cover_cell` is now the site's whole answer
Five kinds: `artist` → `bi-person-fill`, `album` → `bi-vinyl`, `playlist` → `bi-music-note-beamed`,
`song` → `bi-music-note`, `liked` → `bi-heart-fill`; anything else stays a bare, size-reserving
placeholder. `.cover-person` was renamed `.cover-glyph`, since the container is now shared and the
specific icon is the `<i>`'s class.

**The mechanism is one-time and done; only call sites are per-page.** Most pages still hand-roll
`<img class="cover">`, which is why their coverless rows render blank — adopting `cover_cell` is
part of converting each page.

- **Liked Songs is identified by a flag, not an id.** `index_data` computes
  `playlist_id = ? AS is_liked` where the constant already lives. Finn's challenge was right, though
  the real reason is stronger than "the id might change" (it is synthetic and won't): **a template
  should be told what kind of row it is rendering, never handed an id to recognise.**
- **A trap removed in `search.py`**: one `kind` value fed both `entity_link` (which builds the href)
  and `cover_cell`. They overlapped for three of four types and disagreed on songs, whose link tier
  is `"version"` and which therefore matched no glyph branch. There is now a separate
  `_COMBINED_COVER_KIND`.
- **Vinyl and the song note are defensive only** — the library has 0 coverless albums and 0
  coverless tracks, so the tests are the only place they are ever exercised.

### `.btn-outline-emphasis` — a new button variant
Bootstrap has no outline button that works in both themes: `btn-outline-light` is white in *both*
so it vanishes on light, and `btn-outline-secondary` reads as *disabled* on dark. This variant is
driven entirely through Bootstrap's own `--bs-btn-*` variables, so hover/active/disabled stay
consistent. Used for `Full pull`, `Unmerge`, `Re-import newest upload` — the deliberate,
expensive-but-not-destructive actions.

### `<code>` greyed site-wide
Bootstrap styles `<code>` in a magenta accent, which made ids and URIs the loudest thing in a row.
`.page code` is now muted. **One exception**: `.generation-confirm code`, where the `<code>` is the
playlist *name* — the subject of the sentence, not metadata beside it.

### `/dev/artists`
Ids greyed, `Decided` muted, `Unmerge` on the new emphasis variant. Deliberately **no** cover
column: 4,332 of 4,344 artists have no image, so it would add a column of identical person glyphs.

### `/dev/import`
- Explanations that were glued to values with em dashes move to **`bi-info-circle` info hints on the
  label**, carrying their text in `title`. Native tooltips, not Bootstrap's component, which needs
  per-element JS init — and `title` is already the site's convention.
- `(13,599 track rows in total)` deleted, and `tracks_total` removed from `history_import.js`'s
  field list with it.

### Tests touched this pass
- `test_every_search_row_reserves_a_cover_cell_image_or_not` now asserts on the **icon** class
  (`bi-person-fill`, `bi-vinyl`) rather than the container, since the container is shared by every
  glyph kind. Stronger than before: it pins each type drawing its *own* glyph, and it caught the
  Albums section and Most Relevant rendering the same album two different ways.
- `test_index_data_selects_every_snapshot_column_and_no_others` excludes the derived `is_liked`
  before comparing against `PRAGMA table_info`, and asserts it is present separately — or the
  exclusion would hide its removal.

## 9.10 `/dev/scrobble` (page 10)

Cards gone, controls in the middle, and the plays table behind the standard collapse.

**Order on the page is the order you use it in.** Status first as bare lines (no card, no
`Status` heading), then the controls, then the copy explaining them. The intro paragraph that
used to sit above everything is deleted outright — it described the feature to someone who had
already navigated to it.

- **The poller note is two facts, not a paragraph**: "Polls every 100 minutes on the deployed
  server; Poll now calls the same function." The interval is still rendered from
  `interval_seconds`, so the kwarg keeps a reader and the number cannot go stale.
- The **Play History** cross-link survives the deleted paragraph, moved below the controls with
  the `bi-arrow-right` the other two dev pages use.
- **`Poll now` is `btn-primary`, Pause/Resume `btn-outline-emphasis`** — the everyday action
  filled, the state toggle outlined, matching Refresh vs Full pull on `/dev/snapshot`.

### Warning colour, not danger
`N polls with a gap warning` and the last poll's own `gap warning:` detail both move from
`.error` to `.warn`, following the `/dev/roundtrip` decision that a caution is not a failure.
Rate-limited backoff moves too: it is self-healing, and leaving a transient 429 in red beside a
data-loss warning in orange had the severities backwards. **`failed:` stays `.error`** — that one
really did fail.

The count is coloured **only while it is non-zero** (`id="gap-warning-stat"`, class stamped by the
template and toggled by `scrobble.js`). An orange `0 polls with a gap warning` is a false alarm.

### The plays table refreshes in place
`Last 50 plays` is a collapse, closed by default, and **a completed poll opens it** — seeing what
came in is the whole point of clicking Poll now. That only works if the table is fresh, so the
rows became a fragment:

- **`templates/_scrobble_plays_rows.html`** holds the `<tr>`s and no wrapper. `scrobble.html`
  includes it; `/api/scrobble/*` renders the same fragment standalone. Page load and poll cannot
  disagree about a row's markup, and the entity links in it stay `entity_link` — the
  `_search_*_rows.html` convention, for the reason `/api/canonical/cross/listing` gives.
- **`app._scrobble_payload(conn)`** is `index_data` plus `plays_html`, and **both** endpoints
  return it. One identical shape is what lets `scrobble.js` re-render everything from either
  response, which `scrobbling-R.md` §7 already relied on; 50 rows of already-fetched data is not
  worth a second shape.
- **The swapped-in rows must be re-formatted**: `format.js` fills `data-datetime` spans on
  `DOMContentLoaded`, which has long since fired, and `datetime_span` renders the raw ISO as its
  fallback text — so a poll returned a `When` column of bare `2026-09-01T02:04:47Z` strings until
  `renderPlays` called `applyRelativeTimes(playsBodyEl)`. That function's `root` argument existed
  for exactly this and had no caller before now. **Any future fragment carrying a date has the
  same trap**; `_scrobble_plays_rows.html` is currently the only injected one that does.
- The old **"Reload to see the N new play(s)"** link is deleted — the table updates in place, and
  `read 50, stored 3` already says how many arrived.
- Opening uses `bootstrap.Collapse.getOrCreateInstance(...).show()` rather than adding a `show`
  class, so Bootstrap's own state stays in step with the heading's `aria-expanded`.

### Cover cells
`_recent_plays` gains `al.image_url AS album_image_url` (a `LEFT JOIN album`), and each row draws
`cover_cell(p.album_image_url, 'song')`. `Source` and `When` are muted; `Track` is not.

### The export divider
It said `— export data begins here —`: em dashes doing a border's job. Now `Export data begins
here` over a `.divider-row > td { border-top: 2px }` rule, which reads as a division across the
table instead of decoration inside one cell. `colspan` 3 → 4 for the cover column.

**This is a path that cannot be seen live** — all 50 recent plays on the real library are
scrobbles, so the cutover is off the bottom of the table. It is covered by a render test instead.

### Tests added
Three, each checked against a mutant that a passing-but-blind test would have missed:
- `test_the_export_divider_is_rendered_once_between_the_two_sources` — `index_data` only supplies
  a cutover timestamp; *which* row the divider lands above, and that it lands once rather than
  above every export row, is decided entirely by the template's namespace flag. Removing the flag
  fails it.
- `test_a_play_renders_its_album_cover_and_falls_back_to_the_song_glyph` — nothing else reads the
  new join, so dropping it would leave every row on the glyph and still look plausible. Replacing
  it with `NULL` fails it.
- `test_a_poll_hands_back_the_rendered_plays_rows` — an empty `plays_html` satisfies the shape
  test beside it while blanking the table on every poll. Returning `""` fails it.

`test_the_toggle_returns_the_full_status_payload` now compares the toggle against **the poll
endpoint** rather than against `index_data`: the shared shape is no longer index_data alone, and
comparing against it would have passed while only one of the two endpoints carried the fragment —
exactly the drift that test exists to catch.

## 9.11 `/dev/generations` (page 11)

The intro paragraph is deleted. It claimed the generations were *"numbered 1–36 and counting"*
while the table under it rendered **37 rows** — the same failure as `dev.html`'s "the 36
current-favs playlists", still sitting on the page that the rule was written for. Deleting it was
cheaper than rendering it, since nothing else in the sentence was load-bearing.

### The tenure link is a button, not a `Name →` link
**This is the only page in the site that reaches `/dev/generations/tenure`.** The `→` convention
the other dev pages use is for one route among several; this is the page's single outbound action,
so it sits beside the `<h1>` as a `btn-outline-emphasis` — page title left, action right.

### Tabs for the tier
`Version · Song` was a `.toggle-links` paragraph: body text with one word bolded, saying nothing
about the two being alternatives. Now `nav nav-tabs`.

**Real links, not Bootstrap's JS tab component** — the tier decides what the *server* counts, so
each is a page load at its own `?tier=`. The tabs' own bottom rule is the divider above the table,
so the page needs no `<hr>` (rule 6, the never-doubled half).

`.nav-link` joins `.btn` / `.list-group-item` / `.dropdown-item` / `.card` in the site-wide link
rule's `:not()` chain. **That is not a breach of standing rule 3**: narrowing a rule's own scope is
not out-specifying it, and that chain is exactly where "Bootstrap already styles this component"
gets said once.

`.toggle-links` still has two users — `generations_tenure.html` (page 12) and
`entity_playlist.html` (page 19). The same tabs go there when those pages come up; the CSS rule
stays until they do.

### The table
- **Cover + name first, the ordinal second** and as a bare number, not `Generation N`. Both still
  link: the name to the playlist, the number to the same playlist's `?generation=1` view.
- **`Carried in / new` split into two numeric columns.** It was one cell holding the string
  `"133 carried / 55 new"`, which is two numbers pretending to be prose.
- **Newest first**, reversed in the template rather than in `generations()` — that function's
  carried / new / survived figures are computed from each generation's *neighbours*, and its
  docstring promises ordinal order.
- `generation_spans` picks up `s.image_url` (it already joins `snapshot` for the name) and
  `generations()` passes it through, for `cover_cell(g.image_url, 'playlist')`.
- **No collapse here**, unlike `/dev/snapshot`'s playlist list: the table *is* the page, and
  collapsing a page's only content by default hides everything behind a click.

The empty state's em dash becomes a full stop.

## 9.12 `/dev/generations/tenure` (page 12)

Same tabs as page 11, top copy deleted, backlink left as the plain `← Generations` link it was.

### The table lost three columns and gained a cover
`Runs`, `First` and `Last` are gone — five columns of small integers where the strip beside them
already shows first, last and every run visually. **`Tenure` and `Total` both stay**: they diverge
whenever a group leaves and comes back (visible on the real page — *Hot Tea*, 6 and 7).

The freed width goes to `Track`, which was the only column that wrapped, at three lines. The
artist credit now sits on its own line: `.leaf-meta` is inline in its six other uses, so the rule
is scoped `#tenure-table .leaf-meta`, not changed globally. Album covers via `cover_cell`.

The page's total moved from the deleted intro copy into the **pager line**, where it belongs
anyway — it is what the pager is paging through. Deleting it outright would have dropped a
rendered count the page genuinely needs.

### `score_display(…, label=false)`
A new keyword, defaulting to `true`, so every other caller is untouched. H spells the word out
because *"an unlabelled unbounded number beside a track name reads as a play count or a duration"* —
that reasoning is about a chip sitting **in prose**. In a column under a `Score` header the header
is the label and repeating it 50 times is noise.

### `entities.format_span(days)`
`598 days` is a number you divide in your head before it means anything. Now: days under a month,
months above it, one decimal under ten months and none at or above.

**The cut is made on the rounded value, not the raw one.** 303 days is 9.955 months, which
`months < 10` would format as `10.0 mo.` — the two digits the decimal exists to avoid.

It lives in `entities.py` rather than in a macro **because these edges are worth a unit test**, and
a macro can only be tested through a page render, which would mean constructing a group with a
tenure of exactly 303 days to observe one branch.

### The strip went invisible, and this is standing rule 1
Every `.gen-cell` rule was written `.data-table td.gen-cell`. Converting this table to `.table`
orphaned all of them and **the entire 37-column strip rendered as blank space** — no background, no
width, nothing. It is the page's whole point, and it was gone.

That is the exact rule §9c lists first, broken on the very next page after it was written. Two
lessons, and the second is the useful one:

1. Sweeping means grepping `\.data-table` in `style.css` **before** converting, not after.
2. The fix is not to re-prefix with `.table`. `generation_strip` renders into the tenure table
   (`.table`) *and* into `entity_group.html` / `entity_artist.html` (still `.data-table`), so a rule
   keyed on either one is silently blank on the other. **`td.gen-cell` with no table prefix** is
   what is actually correct, and the plain `background` is what makes it work in both — inside a
   `.table` it beats Bootstrap's own cell rule on source order at equal specificity, and inside a
   `.data-table` there is no `--bs-table-bg` for a variable to feed.

### Tests
- Five on `format_span`, covering the day/month boundary at 29 and 30, both decimal branches, the
  rounded-cut edge at 303 days, and `None` rendering as nothing rather than `0 days`.
- **`tests/test_macros.py` is new**: `_macros.html` holds the site's display decisions and had no
  direct tests at all, so `score_display` — "the whole design system for scores" — was observable
  only through whatever arguments a page happened to pass. Both label directions are asserted, since
  ignoring the flag and dropping the word for everyone are the two ways it goes wrong. A mutant of
  each was checked; before this file existed, both survived the entire suite.
- `test_the_tenure_tier_toggle_rolls_two_versions_into_one_song` follows the count into the pager
  line, and now reads it back **by regex rather than substring** — `"1 group"` is a substring of
  `"21 groups"`.

### Numbering the strip cells
Every cell carries its ordinal, and **the width for it was bought, not found.** At the original
9px a two-digit number needed a 7px font, which is not readable.

Finn's rule settled the shape: **number every cell, not just each run's ends** — numbering the ends
would need those cells wider than the rest, and an uneven grid is harder to read across than small
text is. So the cells had to grow uniformly, and the only source of width was the data columns:
`Total` dropped (it and `Tenure` differ only when a group leaves and returns, which `Span` and the
strip both already show), and `Track` narrowed from 320px to 196px.

That funds **13px cells**: 10px of tabular digits at 8px, the 1px separator, and a hair either
side. Measured on the real page — 37 cells, no horizontal scroll on the strip *or* the document,
and `"37"` is not clipped in its box.

- **Top-aligned**, so the number labels the column and the bar under it stays the thing you scan.
- Filled cells take **`#fff`, not `var(--bs-body-bg)`** — `--bs-primary` is a mid blue in both
  themes, so the contrasting colour is white in both; the body background would go light in light
  mode and vanish into the bar. Verified in both themes.
### The cell tooltip says when the generation began
`Generation 37: v37.2.1` became `Aug 11, 2026 · v37.2.1` — the ordinal is already the cell's
visible text, so repeating it in the tooltip said nothing the hover didn't already show.

The date goes through **`format.js`, the site's one date formatter**, so it lands in the viewer's
timezone and in the same phrasing as every other date on the site. That needs a new member of the
`data-datetime` family: **`data-datetime-title`** formats into an element's `title` rather than its
text, for an element whose visible content is something else, with `data-title-suffix` appended
after a `·`.

- **Built from the two attributes every pass, never appended to the existing title**, so a second
  `applyRelativeTimes()` over the same element cannot double it up. Verified live.
- Two named attributes rather than one packed string: at 3,700 cells the ISO dominates the weight
  either way, so the packing bought nothing and cost legibility.
- `s.started_at or ''` matters — `generation_spans` returns NULL for a generation with zero live
  members (P1-015), and Jinja renders `None` as the string `"None"`, which `new Date()` would
  happily title the cell with.

### The page-weight investigation, and what it actually found
The tooltip takes the page from 293KB → 442KB, since a ~20-char ISO replaces a shorter title on
every one of 3,700 cells. The obvious fix — emit the 37 spans once as JSON and let JS apply them,
taking the page to 167KB — **was measured before being built, and does not save time.**

| | 442KB (per-cell attributes) | 167KB (no attributes) |
|---|---|---|
| server, curl, 12 runs | median **143ms** | median **146ms** |
| DOM parse, 3 loads | 71 / 56 / 60ms | 33 / 60 / 26ms |
| DOMContentLoaded | median 247ms | median 231ms |

Server cost of the attributes is **zero within noise**, and the parse difference is ~25ms with
samples that overlap. **An earlier reading of 212ms vs 134ms was contamination** — a parallel
session on the same machine — which is exactly what `timings-contaminated-by-parallel-chats`
warns about, and it was quoted to Finn as a 78ms saving before the twelve-run re-measurement
withdrew it. One-shot `curl` timings are not evidence here.

**The real cost was in the formatter, not the bytes.** `applyRelativeTimes` called
`formatRelativeTime` once per cell — 3,700 calls for **37 distinct dates**:

| title pass over 3,700 cells | |
|---|---|
| recomputing per cell (what it did) | ~130ms |
| DOM assignment alone, no formatting | 0.5ms |
| 37 strings precomputed into an array, then assigned | 1.7ms |
| **shipped: per-pass cache keyed on the ISO** | **~7ms** |

The middle two rows are the diagnostic benchmark, not the implementation — they isolate the cost
to `formatRelativeTime` rather than to touching the DOM. **What ships is the last row**: a `Map`
in `applyRelativeTimes`, which still walks the document, reads two dataset properties per cell and
builds each string, and so lands at ~7ms rather than the array's 1.7ms.

Measured over three cold loads: title pass **~133ms → ~7ms** (5.1–11.4ms), DOMContentLoaded
**~383ms → ~216ms** (209 / 216 / 262), parse ~53ms, server ~143ms. This is a site-wide win, not a
tenure one — any page rendering the same timestamp repeatedly gets it.

**Per-pass, not module-level, and this is the load-bearing part**: relative phrasing goes stale —
"just now" does not stay true — and a long-lived page re-runs this after a fragment swap
(`/dev/scrobble` polls). Within a single pass there is nothing to go stale against.
`formatRelativeTime` itself stays pure, since `makeDateSpan` and the job progress labels call it
directly.

Verified on `/dev/scrobble` as well as the strip: 51 distinct timestamps, all rendered, none left
as raw ISO — a cache keyed wrongly would have shown one date 51 times.

**Conclusion: the JSON-blob rewrite is not worth doing.** It would trade a shared macro, a new
JSON emitter and changes to two unreviewed templates for ~25ms of parse, in noise.


**This changes two pages that have not been reviewed yet**: `entity_group.html` and
`entity_artist.html` render the same macro, and their single-row strips are now numbered too. That
follows from the macro being shared on purpose, and it reads as an improvement — an unlabelled
lone strip is worse than a labelled one — but it is a change made outside the page being worked on.

Also this pass: the sort line gets `mt-3` (it sat flush against the tabs), and the `Span` cell gets
`white-space: nowrap`, since `"8.8 mo."` was the one value narrow enough to look like it fits and
wide enough to wrap, making one row taller than its neighbours.

## 9.13 `/dev/scoring` (page 13)

The smallest page left, and the one **V's brief named** for implementation vocabulary leaking into
the UI. Both instances are gone.

- The intro paragraph cited its own spec path — `(docs/specs/scoring-H.md)` — and then explained
  the materialization model, which is a design note rather than something you act on. Deleted.
- The never-run status said *"the read-time backstop (docs/specs/scoring-H.md §9.3) runs one on the
  very next page load regardless."* A section number in the UI, which will outlive the section, and
  an em dash. It is now *"No recompute has run in this process yet. One is queued automatically
  when anything it depends on changes."*

**That rewording is a correctness fix, not just a copy one.** `ensure_fresh()` enqueues *only when
the fingerprint has actually moved*, so "runs one on the very next page load" was a promise the
code does not make.

Layout follows the settled pattern: controls to the top and de-carded (status line, then the
button), a divider, then the counts table kept in a card — the `/dev/import` stats-card shape, with
`<th>` labels and no `thead`, since `Tier | Scores` said nothing the rows didn't. `Recompute
scores` is `btn-primary`, like `Poll now`: the page's one action.

The `.data-table` → `.table` sweep was run **before** converting this time (§9.12's lesson). This
table uses none of the special-case rules, so nothing was orphaned.

### Tests
`/dev/scoring` had **only the non-5xx route sweep** — neither branch of the page was asserted, and
neither were the four counts. That is P2-010's shape exactly: a route case proves the page responds
and nothing more. Both branches now have a test, checked against two mutants: inverting the
finished/never-run condition, and dropping a tier from the loop. Both were caught; before, both
would have passed the whole suite.

## 9.14 `/search` (page 14)

All five sections become collapses. **Most Relevant is `show` by default** — it is the answer to
the query, and the four type sections are the drill-down. They render collapsed with their counts
in the heading, which is most of what you wanted from them anyway.

One Jinja `{% macro section(id, title, open) %}` with a `{% call %}` block per section, rather than
the same fifteen lines of collapse scaffolding written out five times.

### The four type tables take the bare score chip
Same argument as §9.12: each has a `Score` header, so the word on every row is noise.
**`_search_combined.html` keeps the label** — it is shared with the navbar dropdown, which has no
header to carry it.

### `See more` is borderless, but not blue
`.btn-link` is Bootstrap's borderless button and its border really is transparent (verified). It
also ships raw link blue and underlined, which would have been **the only blue text on the page**
and exactly the treatment the site-wide link rule exists to avoid. `--bs-btn-color` is fed the
emphasis colour, through the button's own variables so hover and focus still work.

### The last inner scroll region is gone
`See more` used to add `.scrollable` to its section — a 420px `overflow-y` box, described in the
CSS as "the one inner scroll region left on the site". That is the thing §9.3 removed everywhere
else, on Finn's note that scroll sections "get annoying when you scroll down the page and get stuck
in each one". The collapse is now the bound, so the class, both its rules and the JS line that
added it are deleted. Verified: 10 → 79 rows on See more, no sideways scroll.

### The orphan this page nearly created, and it was in the navbar
`_search_combined.html` is rendered by **both** this page and the navbar dropdown, so converting it
to `.table` reached outside the page being worked on. Two rules were keyed to the old class:

- `.search-dropdown .data-table { font-size: 12px }` — renamed.
- `.search-dropdown tr.highlighted { background: … }` — **the dropdown's keyboard-navigation
  highlight**, and a plain `background` on a `<tr>`, which is standing rule 2.

The second was **proven rather than assumed**: injecting the old rule's exact shape live paints the
row red and leaves the cell `rgba(0,0,0,0)` — the cell's own declared background covers the row
entirely, so the highlight would have been invisible while the class was still being applied
correctly. Nothing on the page would have looked broken; Up/Down would just have stopped showing
where you were. Fed as `--bs-table-bg`, it works: highlighted cell `rgb(52,58,64)` against
`rgb(33,37,41)`, verified by driving the dropdown with synthetic key events.

## 9.14b Deleted playlists, everywhere (folded in at page 14)

`/search` surfaced it: `/dev/snapshot` hides unfollowed playlists, and **nothing else in the site
knew they existed.** This is a data change, not a UI one, folded into W because the search page is
where it became visible.

**Finn's rule, and the reason the fix goes where it does:** *"the app already doesn't know about
any deleted playlists that came before it, so we shouldn't treat new deleted playlists
differently."* Membership and tenure must exclude them; every page except `/dev/snapshot` must not
show them at all.

### What was actually wrong
`unfollowed_at` was read in **exactly two places** — `snapshot.html`'s row class and
`entity_playlist.html`'s note. Unfollowing stamped the `snapshot` row and stopped, so every one of
that playlist's membership rows still had `removed_at IS NULL`, which is what the whole codebase
means by *live*.

Measured on the real library before the fix:

| | |
|---|---|
| unfollowed playlists | 1 (`Indie Rock Mix (test)`, deleted 2026-08-23) |
| live memberships it still held | 50, of 12,709 |
| tracks whose **only** live membership was that playlist | 32 |
| generation playlists affected | 0 — tenure was clean |

Live memberships are a **scoring input**, so those 50 were being scored as if the playlist existed;
`live_count` counted them, and `playlists_for_tracks` listed a deleted playlist on 50 tracks' pages.

### The fix is one write at the point the fact becomes true
`snapshot.py` ends the memberships in the same loop that stamps `unfollowed_at`. The alternative —
`AND s.unfollowed_at IS NULL` on every live-membership query — is **six queries across four
modules** (`scoring`, `canonical`, `entities`, `generations`), each one a chance to forget, and
every future query too. Ending the rows means every reader is correct without being touched,
because they already agree on what `removed_at` means.

- **Guarded on `removed_at IS NULL`.** Without it, a track taken out of the playlist months ago
  would have its removal date rewritten to the day the playlist was deleted, quietly falsifying an
  append-only log. Tested, and the mutant is caught.
- **The rows themselves survive** — the log is append-only; only `removed_at` is stamped.
- **Re-following still works**: `_diff_playlist_tracks` compares against live rows only, finds
  none, and inserts fresh ones — a truthful gap rather than pretending the playlist never left.

`search.py` needs its **own** filter, because it reads `snapshot` directly and never touches
`membership` — ending memberships does not reach it.

### The history tables needed the same rule, and this is the sharper half
Ending the memberships fixed every *count*, but the membership **history** tables show removed rows
on purpose, so the deleted playlist kept appearing on them — struck through, marked removed, and
looking like an ordinary past membership.

Finn's objection is the original argument turned back on itself: that table now *"shows the song
present in some deleted playlists but doesn't show other deleted playlists."* Symr has no rows at
all for the playlists deleted before it existed, so a table that lists the ones it happened to
watch being deleted **reads as a complete history and is not one**. A partial history that looks
complete is worse than a shorter honest one.

So `entities.track_detail`'s memberships query and `entities.playlists_for_tracks` (the rollup the
group, album and artist pages share) both exclude unfollowed playlists outright — removed rows
included. They are two separate queries and can regress independently, so both have their own test
and both mutants were checked.

`/track/<id>` for one of the 32 now reads *"Not in any captured playlist"* — exactly what it would
say for a track whose only playlist was deleted before Symr existed, which is the point.

`/playlist/<id>` deliberately still renders for an unfollowed playlist: `playlist_detail` selects
*all* memberships including removed ones, so the page stays a truthful record, and `/dev/snapshot`
links to it. That is the one deliberate exception, and it is reachable only from the page that
still lists deleted playlists.

**Not changed:** `generations.py` still joins `snapshot` without the filter. No generation playlist
is unfollowed (checked), and a generation is a numbered era that happened — deleting its playlist
does not undo that. Flagged rather than decided.

### Tests
Three, each checked against its mutant: memberships not ended, the `removed_at IS NULL` guard
dropped, and the search filter removed. All three caught; **the whole suite was green before any of
them existed**, which is what let this sit unnoticed.

### The existing rows
`scripts/end_unfollowed_memberships.py` backfills playlists unfollowed before the change — the next
pull will not, since a deleted playlist is no longer in the target list. It sets `removed_at` to
that playlist's own `unfollowed_at` (when Symr observed it gone, which is what the new code path
would have written), reports without writing unless `--apply`, and recomputes scores afterwards
because it has just changed a scoring input.

## 9.15 `entity_group.html` (page 15, four routes)

De-carded throughout, `Playlists` / `Subtree` / `Member tracks` on collapses, the Edit link
promoted to a `btn-outline-emphasis` at the top with the other controls, covers added to the
Playlists table's Track column, and the **tier chip is now plain text** — settling §9c's open
question the same way the canonical tree's chips were settled.

**Section order (Finn, at page 17):** `Plays` → `Generations` → `Subtree` → `Member tracks` →
`Playlists`. What the page *is* comes first — where this group sits in the library and what it
contains — and the playlist rollup goes last as reference. `Playlists` had been second, which put
a table of every membership between the score and the group's own structure.

### The breadcrumb became a navigator
It was five words, each linking to the one path through `track_group` that `track_ids[0]` happened
to take. Now it carries **all five tiers including Track**, and:

- A tier **above** the current one has exactly one group — every member track shares it — so it is
  always a plain link.
- A tier **below** can have several. **One is a link; several is a dropdown.**

Each option shows a cover plus whatever actually tells that tier's members apart, which is the
whole point — `Release 14757` identifies nothing:

| tier | shown |
|---|---|
| release | album cover + **album name** |
| recording | cover + track name + **length** |
| version / song | cover + track name + **artists** |
| track | cover + track name + **middle-truncated id** |

The truncation earns its place on the real page: `/song/4296`'s three tracks are all called
*Forgotten Souls*, and the id is the only thing separating them.

**`_breadcrumb` costs no extra track queries.** Every descendant group's tracks are a subset of
this group's, so its representative is already in `tracks_by_id`; only `canonical.representative`
is called per descendant, which is the site's one rule for which track stands for a group.

### `entity_link` gained a `{% call %}` body
A dropdown option is a link with rich content — cover, name, second line. Building its `href`
anywhere else would have duplicated the routing `entity_link` exists to centralize (CLAUDE.md: *no
`url_for` to an entity route survives outside this file*). The macro now renders `caller()` when
invoked with `{% call %}` and its `text` argument otherwise, so every existing call site is
untouched.

### We had been shadowing a Bootstrap component by accident
The separators were written as `" › "` and rendered with **no spaces at all**. The HTML was
correct; the cause was that `<p class="breadcrumb">` silently picked up **Bootstrap's own
`.breadcrumb` component**, which is `display: flex` — and flex trims whitespace inside an anonymous
text item, so no amount of rewriting the separator string would have fixed it.

The fix is to stop shadowing and use the real component: `<ol class="breadcrumb">` with
`breadcrumb-item`, and the divider fed through **`--bs-breadcrumb-divider`** rather than written
between the crumbs at all. `.active` replaces the hand-rolled `<strong>`.

**This is worth generalising**: Symr had a `.breadcrumb`, a `.card`, a `.badge` and a `.table`
before Bootstrap arrived, and a class Bootstrap owns will silently apply its component's layout to
markup that was never shaped for it. A collision does not error — it just quietly lays out wrong.

### Tests
Five, replacing the single old breadcrumb test (which asserted the retired `["version_id"]` shape).
They cover: scoping to this group's own tracks, all five tiers present with exactly one marked
current, a tier below with three groups offering three options, each tier's own display field, and
`_short_id` keeping both ends while leaving a short id alone. Three mutants checked — building the
breadcrumb from one track (the old behaviour), showing the id instead of the album, and never
marking the current tier. All caught.

## 9.16 `entity_track.html` (page 16)

Built to match the tier template, which is what Finn asked for: same header shape, same breadcrumb,
same Edit button at the top, same de-carded sections with a divider between each, same collapse on
the listy ones.

### `Canonical` becomes the breadcrumb
That section was a four-row table reading `Song 4296 / Version 4295 / Recording 4294 / Release
14757` — the same four links §9.15 had just turned into a named, covered breadcrumb, still rendered
as bare ids. It is gone, replaced by `breadcrumb_nav`, with **Track** as the current tier.

So `crumb` and `breadcrumb_nav` moved out of `entity_group.html` and into **`_macros.html`**, which
is where "the one way to render X" lives on this site.

**A track page's breadcrumb is all ancestors**, which exposed a gap in `_breadcrumb`: it looked
representatives up in `tracks_by_id`, which on a group page covers every descendant for free (their
tracks are a subset of this group's) but on a track page covers nothing above the track itself.
Every crumb would have fallen back to rendering a bare group id — the exact thing this replaced. It
now falls back to `canonical.track_display`, paying at most four lookups on the one page that needs
them, and none on the group pages.

The track page **never produces a dropdown** — a track has exactly one group per tier — but it goes
through the same builder rather than a second, simpler one that would drift.

### The rest
- `Spotify identity` keeps its card, being the same label/value stats table as §9.8's, with `<th>`
  labels now.
- `Relink aliases` is conditional and absent on most tracks, so it was checked on a track that has
  one (`/track/03auLpFLdCv4HozP4pQseu`) rather than assumed.
- The Spotify link's `→` becomes `bi-box-arrow-up-right`, since it leaves the site.
- The Edit button points at `groups.song`; the deep-link route resolves any group id to its song
  anyway.

Section order and dividers were verified from the DOM rather than the screenshot — the 1px `<hr>`
at Bootstrap's 0.25 opacity is genuinely hard to see against the dark background at screenshot
resolution, and "I cannot see it" is not the same finding as "it is not there".

## 9.17 `entity_album.html` (page 17)

De-carded, dividers, `Tracklist` and `Playlists` on collapses, `Plays` left open, external-link
icon.

**Section order:** `Plays` first, like every entity page, then
**Tracklist → Playlists → Spotify identity**.

Strict consistency would have put `Tracklist` last, where the group page puts `Member tracks` and
the track page puts its extras — and it was built that way first. Finn moved it up, correctly: an
album's tracklist is the thing you came to the page for, and burying it under an identity table to
match a structural rule is the rule outranking the content. Identity stays last, which is also
where it is most defensible: it is reference detail on every page that has one. **No cover column in the tracklist** — every row would show the same album art. No
breadcrumb: an album is not in the canonical hierarchy.

### The header now reads like the other entity pages
It had drifted into its own order. Aligned to the track and group pages: **who and what** on the
first line, **what this is in Symr** on the second.

| | track page | album page |
|---|---|---|
| line 1 | artists · album · duration | artists · release date · **runtime** |
| line 2 | `Track` · score | `Album` · N of M known · score |

`album_type` takes the slot `Track` / `Song` / `Version` occupies, which is what it actually is.

### Album runtime, or nothing
New `entities.format_duration(ms)`, beside `format_span` and there for the same reason — the edges
are worth a test, and a macro can only be tested through a render. It grows an hours field **only
when there is one**: a fixed `h:mm:ss` puts `0:` in front of every track length, a fixed `m:ss`
renders a long album as `97:14`.

**The total is shown only when every track is accounted for.** A sum over the tracks that happen to
be known reads as the album's runtime and is not one. `Hot Fuss` is complete at 11 of 11 even
though Symr owns two, because the stored tracklist supplies the rest; the 460-track Vivaldi
compilation Spotify paged past shows nothing rather than a third of its length. The guard is both
`len == total_tracks` **and** `all(d is not None)` — the count check alone would happily sum a
missing duration as zero.

### The new Spotify identity section
Finn's question, and yes: the `album` row carries fields the page never showed. `album_id`, type,
release date **with its precision** (a year-precision date reads very differently from a day one),
total tracks, **when the tracklist was last fetched**, and the Spotify link. `release_date_precision`
had to be added to `album_detail`'s SELECT — it was not loaded before.

No URI row, unlike the track page: `album` stores no `uri` column, and deriving
`spotify:album:<id>` would be inventing a value rather than showing one.

### Both standing traps, on one page
This is the first page carrying rule 1 and rule 2 in the same rule:

`.data-table tr.unowned` is the greying on tracks Symr does not own — 9 of the 11 here. Converting
naively would have orphaned it *and*, once re-keyed, a plain `color` on a `<tr>` still loses to
Bootstrap's declared cell colour. Either failure renders all 11 rows identically, which reads as
owning the whole album. It is now `.table tr.unowned` fed `--bs-table-color`, verified live:
`rgb(255,255,255)` against `rgba(222,226,230,0.75)`, 9 rows greyed.

`.tracklist-divider` was re-keyed with it. Both classes are used only by this page, so unlike
`.gen-cell` they need no unprefixed form.

### Tests
Four, three mutants checked (always sum, drop the `None` guard, never render hours) — all caught.
The always-sum mutant also failed the complete-album case, which is the half that stops "return
None forever" passing.

## 9.18 `entity_artist.html` (page 18)

Order, per Finn: **Plays → Generations → Albums → Tracks → Features → Playlists → Spotify
identity**. Everything de-carded, the four lists on collapses with counts, covers throughout.

### `Primary` / `Featured` become `Tracks` / `Features`
They were two `<h3>` sub-tables inside one `Tracks` section, and the labels did not say what they
meant. **`track_artist_role` decides it**: *primary* is an artist who is also credited on the
album, *featured* is one credited on the track while somebody else holds the album. There is a
fallback — when no credit on a track is an album artist, all of them count as primary — which
exists so the 63 tracks on Various Artists compilations do not classify their real artist as a
guest.

Finn had never seen `Featured` populated, so it was measured rather than explained: **2,352
featured credits against 14,639 primary, across 1,214 of 4,271 artists.** It fires, just not for
the artists he visits — Travis Scott 51, Future 35, 21 Savage 29. half•alive has 103 primary and
**0** featured, being a band that releases its own records and does not guest. The empty state
now says so in words (*"Never credited on someone else's release"*) rather than `None.`

### Spotify identity, at the bottom
Thin but real: `artist_id`, **when the image was last fetched**, the Spotify link, and the
**merged ids** — moved out of the header, where they had been a stray line of prose. That row also
carries the `/dev/artists` link, so the whole alias story is in one place.

Verified on an alias: `/artist/7sOR7gk6XUlGnxj3p9F54k` redirects to the canonical id and the
identity table reports the requested id as the merged one.

### Playlist covers, retroactively (Finn, at page 18)
The playlist lists on the pages already converted had no covers. `playlists_for_tracks` and
`track_detail`'s memberships query now both carry `s.image_url AS playlist_image_url`, and all four
entity pages render `cover_cell(..., 'playlist')`.

Verified live rather than assumed: artist page **61 covers + 1 `bi-music-note-beamed` glyph over 62
rows** — the glyph is Liked Songs, which stores no image. Group page 8 rows × 2 covers, album page
6, track page 6.

**Liked Songs gets the generic playlist glyph here, not the heart it gets on `/dev/snapshot`.** The
heart needs an `is_liked` flag, and `LIKED_PLAYLIST_ID` lives in `snapshot.py`, so entity pages
would take a new `entities → snapshot` edge for one glyph. That edge is cycle-free (nothing
`snapshot.py` imports reaches `entities.py`, checked) but it is an architectural change for a
cosmetic detail, so it is flagged rather than taken. Only **2 of 153** playlists have no cover at
all, and one of them is Liked Songs.

## 9.19 `entity_playlist.html` (page 19)

The last entity page and the one with the most render paths. `Totals` folded into the header meta
(it was two facts), `Tracks` a collapse that is **open by default** — it is the point of the page —
covers added, `Exclude from pulls` on Bootstrap's `.form-check`.

### The generation view stops being a second page
It was a whole `<section>` re-listing the same tracks as two `<h3>` lists, `Carried forward` and
`New in this generation`, with a `.toggle-links` tier switch. Finn's reshape: **a control at the
top that turns one column on in the tracklist that is already there.**

So `generations.generation_view(conn, ordinal, tier, track_ids)` now returns **`label_by_track`**
rather than two lists of group summaries, plus `carried_count` / `new_count` for the header line.
That also removes real work: the old `_summaries` built a `representative()` + `track_display()`
for every carried and new group — ~100 lookups per view — for rows nobody draws any more.

The toggle and the tier tabs go through **`entity_link`**, not `url_for`. The first draft used
`url_for` and `test_no_template_outside_macros_bypasses_entity_link` caught it, which is exactly
what that test is for.

### Sorting removed, and owed back
`/playlist/<id>` had the site's only click-to-sort table. Finn's call: drop it here and do it
properly site-wide. The markup, the ~35 lines in `snapshot.js` and both CSS rules are deleted, and
**roadmap step X** now exists to reinstate it as part of one shared way to render an entity table.

### The strikethrough had never worked
Removed rows were meant to be greyed *and* struck through. Measured on the real page:
`textDecorationLine` came back **`"none"`**.

**`text-decoration` does not propagate from a table-row box into its cells.** The rule declared it
on the `<tr>`, so the strike silently did nothing — on this page, and on `/dev/snapshot`'s
unfollowed playlists, for as long as the rule has existed. The dimming beside it worked, which is
what made the rule look live: a half-working rule reads as a working one.

Now `.table tr.removed > td`. Verified: `line-through` on removed rows, `none` on live ones.

### Also found
`playlist_detail`'s row query never selected the album image, so every cover fell back to the
music-note glyph the moment `cover_cell` was added. 131 covers, 0 glyphs after.

### Tests
The six `generation_view` tests were rewritten for the per-track shape. One was rewritten twice:
the tier test used to compare group counts (2 versions vs 1 song), which per-track labels cannot
express — both tiers label both tracks. My first rewrite asserted `new_count == 2` on both sides,
**which no longer proved the tier did anything**. It now pins the real semantic difference: a new
version of a song that was already there is `new` at version tier and `carried` at song tier —
same track, same generation, opposite answers.

The route test needed a second generation in its fixture for the same reason. Three mutants
checked — tier ignored, previous generation ignored, out-of-generation tracks labelled — all caught
at both unit and route level.

## 9.20 `coming_soon.html` (page 20, four routes)

One line. `Audit — coming soon` becomes the page name as the `<h1>` and *"Coming soon."* as the
meta line under it — the structural fix for the em dash, and the shape every other page has: the
name in the heading, the status beneath.

## 9.21 `error.html` (page 21)

`404 — Not Found` → `404 Not Found`. The two `.panel` boxes were the last cards on the site
outside the stats tables, and they become **Bootstrap alerts**, the one component we had not yet
used that actually fits: `detail` (*Album not found.*) is information and gets `alert-secondary`;
`exc`, present only on a 500, is a failure and gets `alert-danger` with the text in `<code>`.

### The 500 branch is tested through the real handler
It cannot be reached from any route without breaking something, so the test registers a throwaway
`/__boom` on the fixture app that raises. This works **only because `conftest.py`'s `app` fixture
deliberately leaves `TESTING` off** — Flask would otherwise propagate the exception past the
handler and into the test, and `app.py`'s whole centralised error path would be unreachable from
the suite. The fixture is function-scoped, so the extra route does not leak into
`test_catalog_covers_every_registered_route`.

Two mutants checked: rendering the exception box unconditionally, and swapping the two alert
classes. Both caught.

## 9c. Resume here

**Done:** shell, navbar (twice), icons and cover placeholders, gear/terminal hover menu, and pages
1, 2, 3, 4, 7, 8, 9. **The second review pass is complete** — pages 1, 2, 3, 7 and 8 have all been
re-checked against the rules that were settled after they were first built.

**Next: `/dev/scrobble`** (new territory, not a re-review), then generations, tenure, scoring,
search, the five entity pages, `coming_soon`, `error`. **The two immersive queues convert last.**

**Standing rules to apply to every remaining page**, learned the hard way above:
1. Converting `.data-table` → `.table` **orphans any rule written against the old class**. Sweep
   for them before converting a page, rather than finding them by eye afterwards.
2. `.muted` and any row colouring need `--bs-table-color` inside a `.table`, not a bare `color`.
3. Overriding the site-wide link colour is done by feeding `--bs-emphasis-color`, never by
   out-specifying its four `:not()` clauses.
4. Adopt `cover_cell` wherever a page hand-rolls `<img class="cover">`.
5. Em dashes get a structural fix, not a substitution. Counts in prose are rendered or dropped.
6. A divider separates content: never directly under a card or the sticky bar, but never omitted
   where two headings would otherwise collide.

**Open decisions:**
1. The two immersive queues are **broken in dark mode right now** — light surfaces, light text.
   Converting them last is Finn's call; the state is known.
2. The canvas is excluded from conversion but still inherits dark mode. To be reviewed separately.
3. `entity_group.html`'s `.tier-chip` badge still carries tier colour, while the canonical tree's
   chips were made plain text. A page-15 question.
4. `/dev/snapshot`'s `Playlists (154)` and `146 / 154 pulled` still count the unfollowed playlist.
   Finn: "leave for now."

**Playlist deletion, verified session 2:** handled correctly. `_sync_playlists_and_get_targets`
diffs stored playlists against the fully-paged `_fetch_all_playlists` and stamps `unfollowed_at`;
re-following clears it. A partial pull cannot mis-mark anything, because the diff runs against a
complete list before any item read — the resumable part is the loop that comes after.
