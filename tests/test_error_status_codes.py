"""Error paths assert their **exact** status code, not merely non-5xx.

`test_routes.py`'s permanent sweep asserts every route returns non-5xx, which
is the right shape for a completeness sweep and the wrong shape for an error
path: **a 400 mutated to a 401 is still non-5xx**, so the sweep passes and the
code goes unasserted. The S sweep found 26 such survivors in `app.py`
(`S_sweep.md` §3.3) -- the P2-010 shape the spec predicted for that module.

The sweep cannot be widened to cover them, because these paths are not in
`routes_catalog.py` at all: the catalog issues *valid* requests, and every one
of these aborts needs a deliberately malformed one. So this file supplements
rather than replaces it.

Each case asserts the code **and** the description, following the precedent
`CLAUDE.md` sets for `/callback`: where several distinct refusals share one
status, the code alone cannot tell a working guard from a deleted one.
"""

import pytest


@pytest.mark.parametrize(
    "method, path, kwargs, status, fragment",
    [
        # source: app.py canonical_group_deep_link -- an id with no
        # canonical_group row is a 404, distinct from the "exists but empty"
        # 404 below it, which is why the description is asserted too.
        ("get", "/dev/canonical/group/98765", {}, 404, "No such canonical group."),
        # source: app.py canonical_review -- an ad-hoc selection needs at least
        # two ids to be a grouping at all.
        ("get", "/dev/canonical/review?tracks=onlyone", {}, 400,
         "tracks= needs at least 2 track ids"),
        # source: app.py dev_generations_confirm -- the decision is a closed
        # yes/no set; anything else is a malformed submission, not a "no".
        ("post", "/dev/generations/confirm", {"data": {"playlist_id": "p1"}}, 400,
         "playlist_id and a yes/no decision are required"),
        ("post", "/dev/generations/confirm",
         {"data": {"playlist_id": "p1", "decision": "maybe"}}, 400,
         "playlist_id and a yes/no decision are required"),
        # source: app.py exclude_snapshot_playlists -- an empty list is
        # rejected rather than silently excluding nothing.
        ("post", "/api/snapshot/exclude", {"json": {"playlist_ids": []}}, 400,
         "playlist_ids required"),
    ],
)
def test_error_paths_return_their_exact_status_and_reason(
    client, method, path, kwargs, status, fragment
):
    # source: S_sweep.md §3.3 -- every one of these abort() codes survived
    # mutation because the only assertion covering the route was "non-5xx".
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code == status
    assert fragment in resp.get_data(as_text=True)
