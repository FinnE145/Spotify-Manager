"""The shared string normalizer (docs/codebase-health/P3_refactor.md §4.2).

**This module exists to break a cycle, and that is its whole job.**
`artists.py` needed one function from `canonical_detect.py`
(`normalize_name`, one call site) while `canonical_detect.py` needed
`artists.artist_sets` (two call sites), so the two imported each other --
the only cycle in the project's import graph. The normalizer is the piece
both sides actually shared, so it moves here and both import this instead.

**It imports nothing project-level, and must not start.** An edge out of
this module is an edge into whatever it points at from every module that
normalizes a string, which is how the cycle it replaces came about in the
first place.

Everything here is pure: same string in, same string out, no I/O and no
state. `canonical_detect.normalize_title` / `normalize_suffix` stay where
they are and call in -- they are detection-specific (suffix splitting, the
casefold-and-keep-digits variant) and belong beside the rules that read them.
"""

import re
import unicodedata


def strip_accents(s):
    """NFKD, then drop the combining marks -- "Beyoncé" -> "Beyonce"."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def strip_punct_collapse(s):
    """Drops punctuation outright and collapses runs of whitespace.

    Note that punctuation is *deleted*, not spaced: "half•alive" becomes
    "halfalive". `canonical_detect.normalize_suffix` deliberately does the
    opposite for suffixes, and the difference is load-bearing there -- see
    its docstring.
    """
    s = "".join(c for c in s if c.isalnum() or c.isspace())
    return re.sub(r"\s+", " ", s).strip()


def base_string(s):
    """The comparison form for a title base, an album name or an artist name.

    Artist names normalize through this same pipeline as titles, but only so
    `artists.py` can spot duplicate-id candidates -- detection itself never
    matches on names. It was called `normalize_name` when it lived in
    `canonical_detect.py` and was an alias for this same function.
    """
    return strip_punct_collapse(strip_accents(s or "").lower())
