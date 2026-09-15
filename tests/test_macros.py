"""Direct renders of _macros.html's shared macros.

These are the site's display decisions -- score_display is described in
CLAUDE.md as "the whole design system for scores" -- and a macro is only
reachable through a render, so a page test can only observe one indirectly
and only for the arguments that page happens to pass.
"""

import pytest
from flask import render_template_string


def render(source, **kwargs):
    return render_template_string(source, **kwargs)


def test_the_score_chip_drops_its_label_only_when_asked(app):
    # source: ui-framework-W.md 9.12 -- `label=false` is for a chip sitting in
    # a column under a Score header, where repeating the word on every row is
    # noise. Every other caller keeps H's spelled-out label, so both
    # directions are asserted: a macro that ignored the flag, or one that
    # dropped the word for everyone, are the two ways this goes wrong.
    with app.app_context():
        out = render(
            '{% from "_macros.html" import score_display %}'
            "{{ score_display(92.0) }}|{{ score_display(92.0, label=false) }}"
        )

    labelled, bare = out.split("|")
    assert labelled == '<span class="score-display">score 92</span>'
    assert bare == '<span class="score-display">92</span>'


def test_the_score_chip_renders_nothing_at_all_for_a_missing_score(app):
    # source: scoring-H.md -- a score of None is "not materialized", not zero.
    # An empty chip would claim the entity had been scored.
    with app.app_context():
        out = render(
            '{% from "_macros.html" import score_display %}[{{ score_display(none) }}]'
        )

    assert out == "[]"


def test_the_generation_strip_hands_the_date_formatter_a_raw_iso(app):
    # source: ui-framework-W.md 9.12 -- the cell's tooltip is built by
    # format.js from data-datetime-title, so what the server emits must be the
    # untouched ISO. A server-formatted date here would render fine and be
    # silently wrong: the wrong timezone, and never relative.
    spans = [
        {"ordinal": 1, "name": "First", "started_at": "2021-02-09T20:10:25Z"},
        {"ordinal": 2, "name": "Second", "started_at": "2021-04-24T09:00:00Z"},
    ]
    with app.app_context():
        out = render(
            '{% from "_macros.html" import generation_strip %}'
            "{{ generation_strip(present, spans) }}",
            spans=spans,
            present={2},
        )

    assert 'data-datetime-title="2021-02-09T20:10:25Z"' in out
    assert 'data-title-suffix="First"' in out
    # The ordinal is the cell's visible text, and only the present one is filled.
    assert out.count("gen-cell") == 2
    assert out.count("filled") == 1
    assert ">1</td>" in out and ">2</td>" in out


def test_a_generation_with_no_start_date_emits_an_empty_datetime_not_the_word_none(app):
    # source: ui-framework-W.md 9.12 / P1-015 -- generation_spans returns
    # started_at NULL for a generation with zero live members. Jinja renders
    # None as "None", which format.js would hand to new Date() and title the
    # cell with the literal string; the `or ''` is what stops that.
    with app.app_context():
        out = render(
            '{% from "_macros.html" import generation_strip %}'
            "{{ generation_strip(present, spans) }}",
            spans=[{"ordinal": 1, "name": "Empty", "started_at": None}],
            present=set(),
        )

    assert 'data-datetime-title=""' in out
    assert "None" not in out
