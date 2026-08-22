"""`entity_link` centralization -- entity-pages-K.md and
`codebase-health-P.md` §6: "zero url_for bypasses today; keep it that way."

A static scan over `templates/*.html` rather than a runtime test: the
invariant is about what the templates *say*, and a page-render test could
never distinguish "used entity_link" from "happened to build the identical
href another way."
"""

import os
import re

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")

_ENTITY_ENDPOINTS = (
    "track_page",
    "album_page",
    "artist_page",
    "playlist_page",
    "song_page",
    "version_page",
    "recording_page",
    "release_page",
)

_URL_FOR_ENTITY_RE = re.compile(
    r"url_for\(\s*['\"](" + "|".join(_ENTITY_ENDPOINTS) + r")['\"]"
)


def test_no_template_outside_macros_bypasses_entity_link():
    # source: entity-pages-K.md's entity_link macro description -- "the one
    # way to link any entity, so links can't drift page to page" -- and
    # codebase-health-P.md §6's standing invariant. _macros.html itself is
    # exempt: it's where entity_link's own implementation legitimately calls
    # url_for for each of the eight entity endpoints.
    violations = []
    for name in os.listdir(TEMPLATES_DIR):
        if not name.endswith(".html") or name == "_macros.html":
            continue
        path = os.path.join(TEMPLATES_DIR, name)
        with open(path) as f:
            for lineno, line in enumerate(f, start=1):
                if _URL_FOR_ENTITY_RE.search(line):
                    violations.append(f"{name}:{lineno}: {line.strip()}")

    assert violations == [], "url_for bypass(es) of entity_link found:\n" + "\n".join(violations)


def test_macros_file_is_the_only_place_entity_link_urls_are_built():
    # characterization -- confirms the exemption above is doing real work:
    # _macros.html actually builds these hrefs, so the scan above isn't
    # vacuously passing because nothing anywhere uses url_for for an entity
    # route. entity_link spells out four endpoints literally (track/album/
    # artist/playlist) and builds the other four dynamically as
    # `url_for(kind ~ '_page', ...)` for the four canonical tiers -- so the
    # literal-name regex only ever finds the first set.
    path = os.path.join(TEMPLATES_DIR, "_macros.html")
    with open(path) as f:
        content = f.read()

    found = set(_URL_FOR_ENTITY_RE.findall(content))
    assert found == {"track_page", "album_page", "artist_page", "playlist_page"}
    assert "url_for(kind ~ '_page'" in content
