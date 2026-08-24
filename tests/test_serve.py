"""serve.py -- the container entrypoint and its SIGTERM handler.

The whole module scored **0%** in the S sweep (`S_sweep.md` §3.2): both its
mutants survived, because nothing in the suite imported it at all. That is a
statement about coverage rather than about either line, and these tests are the
answer to it.
"""

import importlib
import logging
import signal
import sys

import pytest
import waitress

import jobs
import scrobble


def _load_serve(monkeypatch):
    """Import serve.py with its entrypoint neutered, and report what ran.

    **serve is imported here rather than at module scope, and that is
    load-bearing.** Under the `__name__ != "__main__"` mutant the guarded block
    executes on import, and a top-level import runs it at *collection* time --
    before any monkeypatch exists to stop waitress.serve() blocking forever. A
    hanging suite is not a kill: the sweep bills it as a 300-second timeout
    instead of a failure. Patching first and importing inside the test turns
    that mutant into a clean, immediate assertion failure.
    """
    started = []
    monkeypatch.setattr(waitress, "serve", lambda *a, **k: started.append("waitress"))
    monkeypatch.setattr(scrobble, "start", lambda *a, **k: started.append("scrobble"))
    if "serve" in sys.modules:
        module = importlib.reload(sys.modules["serve"])
    else:
        module = importlib.import_module("serve")
    return module, started


def test_importing_serve_does_not_start_the_server(monkeypatch):
    # source: S_sweep.md §3.2 -- mutant `if __name__ == "__main__":` -> `!=`
    # survived. Under that mutation the guarded block runs on *import*, so a
    # plain `import serve` would call create_app(), scrobble.start() and
    # waitress.serve(). Nothing imported it, so nothing noticed.
    _module, started = _load_serve(monkeypatch)
    assert started == []


def test_sigterm_drains_the_active_job(monkeypatch):
    # source: host-on-fe-pro-Q.md §6.5 -- the handler's whole purpose is to
    # call jobs.drain() before exiting, so an in-flight job stops cooperatively
    # rather than being cut off by Docker's SIGKILL.
    called = []

    def fake_drain():
        called.append(True)
        return True

    serve, _started = _load_serve(monkeypatch)
    monkeypatch.setattr(jobs, "drain", fake_drain)
    with pytest.raises(SystemExit):
        serve._handle_sigterm(signal.SIGTERM, None)
    assert called == [True]


def test_sigterm_exits_zero_so_a_graceful_stop_does_not_look_like_a_crash(monkeypatch):
    # source: S_sweep.md §3.2 -- mutant `sys.exit(0)` -> `sys.exit(1)` survived.
    # A non-zero exit from a *graceful* shutdown reads as a crash to Docker and
    # systemd, which is what decides whether a restart policy fires.
    serve, _started = _load_serve(monkeypatch)
    monkeypatch.setattr(jobs, "drain", lambda: True)
    with pytest.raises(SystemExit) as excinfo:
        serve._handle_sigterm(signal.SIGTERM, None)
    assert excinfo.value.code == 0


@pytest.mark.parametrize(
    "drained, expected, absent",
    [(True, "completed", "timed out"), (False, "timed out", "completed")],
)
def test_sigterm_logs_whether_the_drain_finished(monkeypatch, caplog, drained, expected, absent):
    # source: host-on-fe-pro-Q.md §6.5 via jobs.drain's contract -- drain
    # returns True once the slot is clear and False if it did not clear in
    # time, and the log line is the only place that distinction is visible.
    # Both directions are asserted: a handler hardcoding either word would
    # otherwise pass whichever single case was written.
    serve, _started = _load_serve(monkeypatch)
    monkeypatch.setattr(jobs, "drain", lambda: drained)
    with caplog.at_level(logging.INFO, logger="serve"):
        with pytest.raises(SystemExit):
            serve._handle_sigterm(signal.SIGTERM, None)
    assert expected in caplog.text
    assert absent not in caplog.text
