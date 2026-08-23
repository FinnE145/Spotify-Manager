# Q — Host Symr on `fe-pro`

Step Q of `docs/Planning/roadmap.md`.

Nothing in planning contradicted that step's section. Two of its predictions were confirmed by
measurement rather than assumed — the redirect-URI rules (§3.2) and the shutdown gap (§6) — and
one thing it does not mention was found and is treated as forced (§3.3).

---

## 0. What this is

Symr moves off the laptop and onto `fe-pro`, Finn's home server, reachable over Tailscale. It
runs as a Docker container behind `tailscale serve`, restarts on its own, backs itself up nightly,
and stops its background jobs cleanly when the service goes down.

**The laptop stays the development machine.** It keeps its own `symr.db`, its own loopback OAuth
redirect, and the existing `venv/bin/python app.py` loop on port 45660. Nothing about the Plan /
Implement / Verify workflow changes. What changes is that **the server's copy becomes the real
one** — see §11.

After this lands, `fe-pro` is the source of truth for `symr.db` and the laptop copy is dev scratch.

---

## 1. The target machine — measured 2026-08-23

All of this was read off the machine during planning. Don't re-derive it; don't trust a number
that contradicts it without re-measuring.

| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS, x86_64, 12 cores, 30 GB RAM |
| Docker | 29.6.1, Compose v5.3.0 |
| Host Python | 3.12.3 only — **no 3.14** |
| Host `sqlite3` CLI | **not installed** |
| Host Python's SQLite | 3.45.1 (`VACUUM INTO` needs ≥ 3.27 — supported) |
| `rsync`, `curl` | present |
| Tailscale | 1.102.2, node `fe-pro`, `100.64.132.111` |
| Tailnet DNS name | `fe-pro.tail78f5ec.ts.net` |
| `tailscale serve` config | none yet |
| Tailnet HTTPS certs | **not enabled** (`CertDomains: None`) |
| `finne` uid:gid | `1000:1000`, in the `docker` group |
| Already listening | `22` (ssh), `25565` (Minecraft, on `0.0.0.0`) — **45660 is free** |

### 1.1 Drives

| Device | Size | Mount | Role |
|---|---|---|---|
| `nvme0n1` | 477 G | `/` — 431 G free | OS. **Backups live here.** |
| `nvme1n1` | 932 G | `/srv` — 864 G free | hosting material. **Symr lives here.** |
| `nvme2n1` | 1.8 T | **not mounted** | old Windows (NTFS). **Never touch.** |

The Windows drive is not mounted and nothing in this spec mounts it. That is the whole of its
protection and it is sufficient — but no step here may add a mount for it.

Backups therefore land on a **different physical drive** from the live database, which is the
property that matters. Both are in one box, so §7.4 covers the off-machine copy.

### 1.2 Filesystem layout on the server

```
/srv/symr/
  repo/                      the git clone (code only; deploy replaces it wholesale)
  symr.env                   the environment file — NOT in git, mode 600
  data/                      the persistent volume, bind-mounted to /data in the container
    symr.db                  + -wal / -shm
    .spotipy_cache
    streaming_history/       one folder per uploaded export
/var/backups/symr/           nightly VACUUM INTO output, on the OS drive
```

`/srv` is root-owned, so creating `/srv/symr` is the one bootstrap step needing `sudo`; it is
then `chown`ed to `1000:1000` and everything after runs unprivileged.

---

## 2. Scope

**In:** the container and its image, TLS and the redirect URI, `SECRET_KEY` and path config, the
graceful-shutdown path, backups, the one-time bootstrap, the repeatable deploy, and the doc
changes those force.

**Out:** app-level authentication (§12), any change to how jobs, scoring, detection or the UI
behave, and any change to the laptop dev loop beyond adding `SYMR_SECRET_KEY` to its `.env`.

**No behavioural change to the app is in scope.** The only Python that changes is `config.py`
(two settings), `history_import.py` (one binding), `jobs.py` (one new function) and a new
`serve.py`. If an implement session finds itself editing `snapshot.py`, `scoring.py` or any
template, something has gone wrong.

---

## 3. Network path and TLS

### 3.1 The path

```
browser on the tailnet
  └─ https://fe-pro.tail78f5ec.ts.net       (443, Tailscale-issued cert)
      └─ tailscale serve  →  http://127.0.0.1:45660
          └─ Docker published port, loopback-only
              └─ waitress → Flask, port 45660 in the container
```

`tailscale serve` runs on the host, terminates TLS with a certificate Tailscale provisions and
renews itself, and is reachable only from the tailnet. No cert files, no renewal cron, no nginx.

**`tailscale funnel` is never used.** Funnel publishes to the open internet, which trips §12's
tripwire on the spot.

Command (host, one-time, persists across reboots):

```
tailscale serve --bg 45660
```

**Prerequisite, and it must be done first:** HTTPS Certificates are not currently enabled for the
tailnet, so `tailscale serve` cannot get a certificate. Finn enables it in the Tailscale admin
console under **DNS → HTTPS Certificates**. Confirm it took by checking that
`tailscale status --json` reports `fe-pro.tail78f5ec.ts.net` under `CertDomains` instead of
`None`.

### 3.2 The redirect URI

Verified against Spotify's current documentation on 2026-08-23 rather than assumed, because the
roadmap flagged it as a moving target. The rules:

- HTTPS is required **unless** the address is loopback, where HTTP is permitted.
- `localhost` is **not allowed at all** — loopback must be the literal `127.0.0.1` or `[::1]`.
- Enforcement began 2025-04-09 for new apps; all apps had to migrate by 2025-11.

Two consequences:

1. **The laptop is already compliant.** Its URI is `http://127.0.0.1:45660/callback`, which is
   loopback with an IP literal. It needs no change and keeps working.
2. **The server URI must be HTTPS**, because a `*.ts.net` name is not loopback. It is
   `https://fe-pro.tail78f5ec.ts.net/callback` — no port, since `tailscale serve` fronts it on 443.

Finn adds the server URI to the Spotify dashboard **as a second entry, keeping the loopback one**.
An app may register several; which is used is decided per-environment by `SPOTIFY_REDIRECT_URI`.

`docs/spotify_constraints.md` gains a section recording these rules, since they are exactly the
kind of hard external limit that file exists to hold.

### 3.3 The container publishes to loopback only — forced, not optional

The Minecraft container publishes on `0.0.0.0:25565`, i.e. the whole LAN. **Symr must not.**

`CLAUDE.md` makes security the exception to KISS, and §12 leaves Symr unauthenticated **only**
because Tailscale is the boundary. Publishing on `0.0.0.0` would make Symr reachable from every
device on the home network without crossing that boundary at all — and anyone reaching it gets a
live authed Spotify client carrying the round-trip's `playlist-modify-private` scope. The
unauthenticated design would go from justified to indefensible in one line of YAML.

So the port mapping is exactly:

```yaml
ports:
  - "127.0.0.1:45660:45660"
```

This also sidesteps Docker's habit of writing iptables rules that bypass UFW — a loopback-bound
publish is never exposed regardless of firewall state.

**A future session that widens this bind, adds a Funnel, or puts a reverse proxy in front has
changed Symr's security model and must read §12 first.**

---

## 4. The container

### 4.1 Image

`deploy/Dockerfile`, built from `python:3.14.5-slim`.

This is what makes the host's Python 3.12.3 irrelevant: the image pins **3.14.5**, matching the
laptop exactly, and **nothing is installed on the host**. Pin the exact patch tag, not `3.14-slim` —
a floating tag would silently move the production interpreter on an unrelated rebuild.

- `COPY` the repo in; do not bind-mount it. The image is the unit of deployment, so a deploy is a
  rebuild and the running code always matches a commit.
- `pip install -r requirements.txt`, plus **`waitress`** (§4.3), which is added to
  `requirements.txt`.
- Run as `1000:1000`, matching `finne` on the host, so bind-mounted files stay Finn-owned rather
  than becoming root-owned.
- `.dockerignore` excludes `venv/`, `data/`, `symr.db*`, `.env`, `.spotipy_cache*`, `tests/`,
  `__pycache__/`, `.git/`.

Nothing in the image writes to the image. Every writable path is under `/data` (§4.4).

### 4.2 Compose

`deploy/docker-compose.yml`, build context `..` (the repo root).

```yaml
services:
  symr:
    build: { context: .., dockerfile: deploy/Dockerfile }
    container_name: symr
    env_file: /srv/symr/symr.env
    volumes:
      - /srv/symr/data:/data
    ports:
      - "127.0.0.1:45660:45660"
    restart: unless-stopped
    stop_grace_period: 45s
```

- `restart: unless-stopped` — survives reboot and crash, but a deliberate `docker compose stop`
  stays stopped.
- `stop_grace_period: 45s` — **Docker's default is 10 s and that is too short.** See §6.
- `env_file` points at `symr.env`, deliberately **not** named `.env`: Compose reads a file called
  `.env` in its own directory for variable substitution, and having two files with one name and
  two meanings is a trap for whoever next edits this.

### 4.3 waitress, and why not the dev server or gunicorn

The container's entrypoint is `serve.py` (§6), which runs **waitress** — a pure-Python WSGI server
that is **one process with a bounded thread pool**. Compared to `app.run()`: request timeouts,
proper handling of slow or malformed clients, and no "do not use this in a production
environment" warning on every start.

**Multi-*process* servers are ruled out, and this is a correctness constraint rather than a
preference.** Symr's concurrency control is entirely in-process module state:

- `jobs._active` — the single job slot, guarded by a module lock (`jobs.py:22`)
- `scoring._worker_alive` / `_worker_pending` — the coalescing recompute worker
- `scoring.ensure_fresh()`'s dedicated module-level connection, whose `PRAGMA data_version` check
  is only meaningful relative to that one connection
- the four `JobStatus` singletons the status pollers read

A second worker process gets its **own** copy of all four. That means two job slots (two
concurrent pulls writing one SQLite file), two recompute workers, and a status poller reading a
different process's state than the one actually running the job. Threads share this state;
processes do not. **Any future change here must keep the server single-process.**

`app.py`'s `app.run()` stays exactly as it is — it remains the laptop dev loop's entrypoint, and
`serve.py` does not replace or import it.

### 4.4 Paths and the environment file

`/srv/symr/symr.env`, mode `600`, owned by `finne`. Never in git; `deploy/symr.env.example`
records the keys with placeholder values.

| Key | Server value | Note |
|---|---|---|
| `SPOTIFY_CLIENT_ID` / `_SECRET` | same as laptop | |
| `SPOTIFY_REDIRECT_URI` | `https://fe-pro.tail78f5ec.ts.net/callback` | §3.2 |
| `SYMR_DB_PATH` | `/data/symr.db` | |
| `SYMR_SPOTIPY_CACHE` | `/data/.spotipy_cache` | **must** be under `/data`, or every deploy logs Finn out |
| `SYMR_UPLOAD_ROOT` | `/data/streaming_history` | new setting, §5.2 |
| `SYMR_PORT` | `45660` | |
| `SYMR_DEBUG` | `0` | the reloader restarts on any `.py` write and would kill an in-flight pull mid-transaction |
| `SYMR_SECRET_KEY` | a fresh `secrets.token_hex(32)` | §5.1 |

All three data paths already read from the environment except the upload root, which §5.2 fixes.

---

## 5. Config changes

### 5.1 `SECRET_KEY` stops having a fallback

Today:

```python
SECRET_KEY = os.environ.get("SYMR_SECRET_KEY", secrets.token_hex(32))
```

The random fallback means every restart silently invalidates the session cookie that carries the
OAuth `state` token. On a laptop that is a nuisance; on a service that restarts on its own it
breaks the login flow.

**`config.py` refuses to start when `SYMR_SECRET_KEY` is unset** — unconditionally, not gated on
`SYMR_DEBUG`. It becomes a required key like the three Spotify credentials, i.e. a plain
`os.environ["SYMR_SECRET_KEY"]`, and the `secrets` import goes with it.

Unconditional was Finn's call: it removes a branch that would only ever be exercised in
production, and it brings dev to parity — the laptop stops losing its session on every reloader
restart.

Three places must gain the key, and **all three are part of this step**:

1. `/srv/symr/symr.env` — during bootstrap.
2. The laptop's `.env` — generated with `secrets.token_hex(32)`. **This has to land before or with
   the `config.py` change**, or the laptop app stops starting.
3. `tests/conftest.py` — in the block above the first project import, beside the three Spotify
   credentials it already sets. Miss this and the entire suite fails at import.

### 5.2 `SYMR_UPLOAD_ROOT`

`history_import.py:25` hardcodes `UPLOAD_ROOT = os.path.join("data", "streaming_history")`. It
moves to `config.py` as `UPLOAD_ROOT = os.environ.get("SYMR_UPLOAD_ROOT", os.path.join("data", "streaming_history"))`,
with `history_import.py` keeping the module-level name:

```python
UPLOAD_ROOT = config.UPLOAD_ROOT
```

**Keep that binding.** `tests/conftest.py` redirects `history_import.UPLOAD_ROOT` by name to stop
the suite finding and re-importing seven years of real history; rename or inline it and that
redirect silently stops working.

The default is unchanged, so the laptop needs no `.env` entry.

---

## 6. Graceful shutdown

### 6.1 The gap

All five background threads — the four jobs and `scoring._worker()` — are `daemon=True`.
`jobs.request_stop()` exists, and every job polls `jobs.stop_requested()` at its own safe points,
but **nothing calls it except the UI's Stop button**. A `docker stop` today kills whatever job was
running, wherever it had got to.

### 6.2 How bad that actually is — measured

Not very, and the spec is honest about it so nobody over-builds here.

From the `api_request` log (248 rows since 2026-08-16), the one recorded snapshot run was
**233 requests in 1.9 minutes**, ≈2 req/s; extrapolating over 143 pullable playlists puts a full
pull at roughly **3–5 minutes**. Jobs are minutes, not hours.

More importantly **all four jobs are already interruption-safe by design**: J made pulls entirely
derived and resumable with nothing checkpointed, the round-trip commits per batch, backfill
commits per album, and SQLite rolls back any transaction killed mid-write. An abrupt kill costs
re-work, never corruption.

So graceful shutdown buys **tidiness and avoided re-work**, not data safety. It should be simple.

### 6.3 The bound on the wait

`jobs.py:32` sets `_SHORT_WAIT_LIMIT_SECONDS = 30`: a rate-limit wait shorter than that is slept
through inside `jobs.call`, and anything longer raises `RateLimited` and aborts the run. So the
longest a job can be unresponsive to a stop request is **~30 s of sleep plus one in-flight
request**.

**45 s** covers it, which is `stop_grace_period` in §4.2. `drain()` itself waits **40 s**, leaving
margin to exit cleanly before Docker's SIGKILL.

### 6.4 `jobs.drain(timeout=40)`

New function in `jobs.py`, which is where it belongs — it owns `_active` and the stop flag, and
putting it here keeps it testable with no Flask and no container.

Behaviour:

1. Read `active()`. If `None`, return `True` immediately.
2. Call `request_stop(name)` **with that name** — `request_stop` no-ops when the name doesn't
   match `_active`, so a hardcoded name would silently do nothing.
3. Poll `active()` until it is `None` or `timeout` elapses.
4. Return `True` if the slot cleared, `False` on timeout.

It does **not** kill anything on timeout. Docker's SIGKILL is the backstop, and §6.2 says that is
survivable.

### 6.5 `serve.py`

New file at the repo root — the container's entrypoint, and **deliberately thin**, because
everything worth testing lives in `jobs.drain`.

- Builds the app via `create_app()`.
- Installs a `SIGTERM` handler that calls `jobs.drain()`, logs the outcome, and exits.
- Serves with `waitress.serve(app, host="0.0.0.0", port=APP_PORT)`.

`host="0.0.0.0"` is correct and safe **here specifically**: it is the address *inside* the
container's network namespace, and §3.3's `127.0.0.1:45660:45660` is what decides reachability
from outside.

**The scoring worker is not drained.** If a recompute is in flight it dies with the process, and
`scoring.ensure_fresh()` re-catches it on the next request — that backstop exists for exactly
this case, and the work is ~1.8 s. Finn's call.

**`serve.py` must be added to `CLAUDE.md`'s Codebase Map**, or `tests/test_codebase_map.py` fails:
it asserts every `*.py` at the repo root appears there.

---

## 7. Backups

The database holds every merge, reviewed pair, alias, pin, generation and confirmed grouping
decision — **human curation no re-pull can reconstruct**. Losing it is not a re-download.

### 7.1 Mechanism — `VACUUM INTO`

`db.py:644` puts the database in **WAL mode**, so live data is split across `symr.db` and
`symr.db-wal`. A plain `cp` of the `.db` file mid-write copies a torn, incomplete database — and
you find out only when you need it.

`VACUUM INTO '<path>'` is one SQLite statement that writes a fresh, compacted,
transactionally-consistent copy to a new file while the app keeps running.

It runs through the **host's stdlib Python 3.12**, whose SQLite is 3.45.1 (`VACUUM INTO` needs
≥ 3.27). The `sqlite3` CLI is not installed on `fe-pro` and **this step does not install it** —
nor anything else on the host.

### 7.2 Schedule and retention

`deploy/backup.sh`, driven by a systemd timer (`deploy/symr-backup.service` +
`deploy/symr-backup.timer`), installed to `/etc/systemd/system/`.

- **Daily.** Output `/var/backups/symr/symr-YYYY-MM-DD.db`.
- **Keep 30 days**, pruning by age. ~93 MB each, so ~2.8 GB against 431 GB free.

Thirty days is sized for the failure that actually threatens this database: not drive failure, but
a bad merge or an auto-group run whose damage isn't noticed for weeks.

The script must **exit non-zero on failure** and not prune when the new backup wasn't written —
a prune that runs after a failed dump is how you end up with thirty days of nothing.

### 7.3 What is not backed up

`data/streaming_history/` (~203 MB). It is re-derivable — the `play` rows it produced are in the
database, which *is* backed up. Only re-import needs the folders, and that re-reads what is
already there.

### 7.4 The off-machine copy

Both drives are in one box, so §7.2 covers drive failure but not fire, theft or total loss.

`deploy/deploy.sh` therefore ends by pulling the newest backup down to `~/Symr-backups/` on the
laptop — outside the repo entirely, so no `.gitignore` question arises.

**Gated on being Finn's laptop**, so an agent running on a cloud machine or a second laptop
doesn't quietly drag a 93 MB library down:

```sh
[ "$(scutil --get LocalHostName 2>/dev/null)" = "Finns-MacBook-Pro" ]
```

`scutil` is macOS-only and absent on Linux, so the check fails closed on a cloud box without
depending on that. This is **a mistake-catcher, not a security control** — it is not load-bearing
and must not be described as though it were.

**Failure here never fails the deploy.** No backup yet, no route to the server, no disk space:
print a clear warning and exit 0. The deploy already succeeded; the copy is a bonus.

---

## 8. Bootstrap — one-time

`deploy/bootstrap.md`, a checklist run once by hand. Deliberately **not** folded into `deploy.sh`:
it is a different operation, most of it is Finn's to do, and a repeatable script that silently
re-does it would be dangerous.

1. **Finn:** enable HTTPS Certificates in the Tailscale admin console (§3.1).
2. **Finn:** add `https://fe-pro.tail78f5ec.ts.net/callback` to the Spotify dashboard, keeping the
   loopback URI (§3.2).
3. `sudo mkdir -p /srv/symr && sudo chown 1000:1000 /srv/symr` — the one privileged step.
4. `git clone` into `/srv/symr/repo`.
5. Write `/srv/symr/symr.env` (mode 600) from `deploy/symr.env.example`, generating a fresh
   `SYMR_SECRET_KEY`.
6. **Stop the laptop app**, then `rsync` `symr.db`, `.spotipy_cache` and `data/streaming_history/`
   into `/srv/symr/data/`. Stopping first is what makes the copy consistent — this is the one
   transfer not protected by `VACUUM INTO`.
7. `sudo mkdir -p /var/backups/symr && sudo chown 1000:1000 /var/backups/symr`; install and enable
   the timer.
8. `docker compose … up -d --build`.
9. `tailscale serve --bg 45660`.
10. Browse to `https://fe-pro.tail78f5ec.ts.net/` from a tailnet device and confirm it loads
    already authenticated (the copied token cache), that `/dev/snapshot` shows the expected
    playlist counts, and that the scoring banner is absent.

Step 6 copies `.spotipy_cache`, so no re-login is needed; the new redirect URI still has to be
registered for future logins and is verified by clicking through `/login` once.

---

## 9. Deploy — repeatable

`deploy/deploy.sh`, run **from the laptop**. Assumes the branch has already been merged to `main`
and pushed, which is the Verify finish-up's job.

1. `ssh fe-pro` → `git -C /srv/symr/repo pull --ff-only`
2. `docker compose -f /srv/symr/repo/deploy/docker-compose.yml up -d --build`
3. Health check, retried for up to 30 s: `curl` `http://127.0.0.1:45660/login` on the host,
   expecting **302**.
4. §7.4's gated backup pull.

`/login` is the health target because it is in `app.py`'s `_PUBLIC_ENDPOINTS`, so it runs neither
the login guard nor `scoring.ensure_fresh()`, and it makes no outbound Spotify request — it builds
the authorize URL locally and redirects. A health check that triggered a recompute or spent a
Spotify request every time would be worse than none.

The retry loop matters: `up -d` returns as soon as the container is created, not when waitress is
listening.

**Deploying while a job is running is safe and needs no guard** — `up -d --build` stops the old
container, which is §6's SIGTERM path, which stops the job at a safe point.

`CLAUDE.md`'s `## Commands` gains a **Deploy** subsection pointing at this script, so a later
session asked to "deploy" knows what that means without rediscovering it.

---

## 10. Documentation changes

- **`CLAUDE.md` — the port rule, restated not deleted.** It currently reads *"Port 45660 is not
  negotiable — the Spotify OAuth redirect URI is registered against it."* After this step there
  are **two** registered URIs, so read literally that sentence is false, and a later session could
  reasonably conclude the port is now flexible or the rule is stale — then reassign it during a
  Verify run and lose a session to a broken OAuth loop. Reword to scope it: the **laptop dev
  loop's** URI is registered against `127.0.0.1:45660` and that port is still not negotiable
  locally; the server has its own HTTPS URI and does not affect this.
- **`CLAUDE.md` — Codebase Map.** Add `serve.py` (required by `test_codebase_map.py`) and a
  `deploy/` entry.
- **`CLAUDE.md` — Commands.** Add the Deploy subsection (§9).
- **`docs/spotify_constraints.md`.** Add the redirect-URI rules from §3.2 with the date verified.
- **`docs/Planning/roadmap.md`.** Two edits, both Verify's finish-up:
  - Mark Q ✅ DONE pointing at this spec.
  - Add a row to the **Spec index** table. Its intro prose says *"the 17 audited specs"* and the
    table has exactly 17 rows, so that number becomes 18 and needs changing with it — the count is
    a claim in English that drifts silently, which is precisely why `test_codebase_map.py`
    deliberately does not try to parse claims of this shape (`CLAUDE.md`, `tests/`). Nothing will
    fail if it is missed; someone has to look.

---

## 11. After this lands: two copies, one of them real

`fe-pro` is the source of truth. The laptop keeps its own `symr.db` for development, and the two
**will** diverge — curation done on the server is not on the laptop, and vice versa.

**Curation Finn actually wants to keep — merges, reviewed pairs, aliases, pins, generation
confirmations, groupings — must be done on the server, not the dev copy.** Work done against the
laptop database is scratch and will eventually be overwritten.

§7.4's backup pull is the sanctioned way to refresh the laptop copy: replace `symr.db` with the
newest file from `~/Symr-backups/` when the dev copy has drifted far enough to stop being a
useful test bed. There is no sync, no merge, and no way back — laptop → server is never a
supported direction.

---

## 12. Out of scope: app-level authentication, and the tripwire

Carried forward from the roadmap deliberately, because leaving Symr unauthenticated looks like
exactly the shortcut `CLAUDE.md`'s security rule forbids.

It is not, because **Tailscale is the boundary**: the app is reachable only from devices already
in the tailnet, which is a real authentication decision made one layer down, not an absent one.
§3.3's loopback-only publish is what keeps that true rather than nominal. A login form behind it
would add a password to protect against people who cannot reach the port.

**The tripwire, and it is a hard one:** the moment Symr is reachable from anything outside the
tailnet — a port forward, a public hostname, a reverse proxy, `tailscale funnel`, sharing it with
anyone — **real authentication becomes a prerequisite, not a follow-up**, and it becomes its own
roadmap step. Symr's login guard checks for a *Spotify token*, not for a *user*; there is no notion
of who is asking. Anyone who can reach the port is Finn, with a live authed Spotify client and the
round-trip's write scope.

---

## Tests

Four clauses are worth pinning. The rest of this step is a Dockerfile, a compose file, three shell
scripts and a systemd timer — **not unit-testable in any way proportionate to their value**, and
verified instead by §8.10 and §9.3 actually running. Saying so here is the point: a test that
shells out to `docker build` would be slow, would need a daemon, and would assert nothing the
deploy doesn't already prove.

**1. `SYMR_SECRET_KEY` has no fallback.**

The clause being **replaced** is `config.py`'s
`SECRET_KEY = os.environ.get("SYMR_SECRET_KEY", secrets.token_hex(32))` — the *random fallback*.
That matters more than the new rule: a test asserting only "when `SYMR_SECRET_KEY` is set,
`config.SECRET_KEY` equals it" **passes against the old code too**, because the old code also read
the variable when present. Such a test cannot fail and is worth nothing.

The assertion that can fail is that **`config` raises when the variable is absent**. Test by
reloading `config` with the key removed from the environment and `load_dotenv` neutralised (the
real `.env` will have it), expecting `KeyError`.

**2. `jobs.drain()` requests a stop and waits for the slot.**

Three assertions, each targeting a specific wrong implementation:

- With no job active, returns `True` **without** waiting — catches a `drain()` that just sleeps out
  its timeout.
- With a job active that polls `stop_requested()`, returns `True` once the slot clears, and the job
  observed the flag — catches a `drain()` that waits without ever calling `request_stop`.
- With a job that ignores the flag, returns `False` after the timeout — catches one that reports
  success unconditionally.

Use a **non-default job name** in the fixture. `request_stop(name)` no-ops when `name != _active`,
so a `drain()` that hardcodes `"snapshot"` would pass a test that happens to use `"snapshot"` while
being completely broken.

**3. `SYMR_UPLOAD_ROOT` is honoured, and its default is unchanged.**

Assert `config.UPLOAD_ROOT` follows the environment variable when set, and falls back to
`data/streaming_history` when not. The second half is what stops the laptop default silently
changing.

**4. `history_import.UPLOAD_ROOT` stays a patchable module attribute.**

`tests/conftest.py` redirects it by name, and every history test's isolation depends on that. A
test asserting `history_import.UPLOAD_ROOT` exists and equals `config.UPLOAD_ROOT` under the
suite's own redirect is trivial, but it is the thing that fails loudly if a later refactor inlines
`config.UPLOAD_ROOT` at each use site — which would leave the suite pointed at seven years of real
history with nothing complaining.

**Not new tests, but they must not break:** `tests/test_codebase_map.py` requires `serve.py` in the
map (§6.5), and `tests/conftest.py` must set `SYMR_SECRET_KEY` above the first project import
(§5.1) or the whole suite fails to collect.
