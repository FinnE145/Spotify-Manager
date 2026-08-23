"""`config.py` -- environment-sourced settings (docs/specs/host-on-fe-pro-Q.md
§5).

Both settings under test are reloaded live rather than asserted once at
import time: `config` is already imported by the time any test runs, and the
only way to exercise "what happens when the environment differs" is to
re-execute the module against a changed environment and put it back
afterward. `load_dotenv()` is neutralised for every reload here because the
real `.env` now carries a real `SYMR_SECRET_KEY` -- without neutralising it,
a reload that deletes the var from `os.environ` would have it silently
refilled from the file, defeating the "absent" case entirely.
"""

import importlib
import os

import dotenv
import pytest

import config
import history_import


@pytest.fixture(autouse=True)
def _no_real_dotenv(monkeypatch):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)


def test_secret_key_has_no_fallback(monkeypatch):
    # source: host-on-fe-pro-Q.md §5.1 / Tests clause 1 -- the clause being
    # *replaced* is the random fallback
    # (`os.environ.get("SYMR_SECRET_KEY", secrets.token_hex(32))`); a test
    # that only checks "reads the var when set" would pass against the old
    # code too, since the old code also read it when present. The assertion
    # that can actually fail is that config refuses to import at all without
    # the variable.
    original = os.environ["SYMR_SECRET_KEY"]
    monkeypatch.delenv("SYMR_SECRET_KEY", raising=False)
    try:
        with pytest.raises(KeyError):
            importlib.reload(config)
    finally:
        monkeypatch.setenv("SYMR_SECRET_KEY", original)
        importlib.reload(config)


def test_upload_root_follows_the_env_var_and_defaults_when_unset(monkeypatch):
    # source: host-on-fe-pro-Q.md Tests clause 3 -- SYMR_UPLOAD_ROOT is
    # honoured, and its default is unchanged. The second half is what stops
    # the laptop default silently changing.
    try:
        monkeypatch.setenv("SYMR_UPLOAD_ROOT", "/custom/upload/root")
        importlib.reload(config)
        assert config.UPLOAD_ROOT == "/custom/upload/root"

        monkeypatch.delenv("SYMR_UPLOAD_ROOT", raising=False)
        importlib.reload(config)
        assert config.UPLOAD_ROOT == os.path.join("data", "streaming_history")
    finally:
        monkeypatch.delenv("SYMR_UPLOAD_ROOT", raising=False)
        importlib.reload(config)


def test_history_import_upload_root_follows_configs_binding(monkeypatch):
    # source: host-on-fe-pro-Q.md Tests clause 4 -- history_import.UPLOAD_ROOT
    # must stay a patchable module attribute bound to config.UPLOAD_ROOT
    # (`UPLOAD_ROOT = config.UPLOAD_ROOT`), not inlined at each use site.
    # conftest.py redirects it by name after import, which only works because
    # the name exists and is a plain rebind -- an inlined refactor would
    # leave the suite pointed at Finn's real streaming-history exports with
    # nothing complaining.
    safe_upload_root = history_import.UPLOAD_ROOT  # conftest's tmp redirect
    original_status = history_import._status  # conftest's tracked singleton
    try:
        monkeypatch.setenv("SYMR_UPLOAD_ROOT", "/custom/from/env")
        importlib.reload(config)
        importlib.reload(history_import)
        assert history_import.UPLOAD_ROOT == config.UPLOAD_ROOT == "/custom/from/env"
    finally:
        monkeypatch.delenv("SYMR_UPLOAD_ROOT", raising=False)
        importlib.reload(config)
        # Reloading history_import above minted a fresh _status JobStatus,
        # orphaning the reference conftest's module-state reset holds --
        # restore both by hand rather than by a second reload, which would
        # also re-run `UPLOAD_ROOT = config.UPLOAD_ROOT` against the
        # now-restored (real, non-test) default.
        history_import.UPLOAD_ROOT = safe_upload_root
        history_import._status = original_status
