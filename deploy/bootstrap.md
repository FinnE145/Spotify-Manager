# Bootstrap: hosting Symr on `fe-pro`

One-time setup, run by hand (`docs/specs/host-on-fe-pro-Q.md` §8). Deliberately not a
script: most of this is Finn's to do, and a repeatable script that silently re-did it
would be dangerous — step 6 in particular stops the laptop app and moves its database.

Machine facts this assumes (measured 2026-08-23, don't re-derive): `fe-pro` is reachable
over Tailscale at `fe-pro.tail78f5ec.ts.net`, `finne` is uid:gid `1000:1000`, `/srv` has
864 G free and is where Symr lives, and port 45660 is free on the host.

## 1. Enable Tailscale HTTPS certificates

In the Tailscale admin console, under **DNS → HTTPS Certificates**, enable certificates
for the tailnet. Confirm it took:

```bash
ssh fe-pro "tailscale status --json | grep -A2 CertDomains"
```

`fe-pro.tail78f5ec.ts.net` should appear instead of `None`. `tailscale serve` (step 9)
cannot get a certificate until this is done.

## 2. Register the server's redirect URI with Spotify

In the Spotify developer dashboard, add `https://fe-pro.tail78f5ec.ts.net/callback` as a
second redirect URI on the app — **keep the existing loopback one**
(`http://127.0.0.1:45660/callback`), which the laptop still uses.

## 3. Create `/srv/stacks/symr`

The one privileged step — `/srv/stacks` is root-owned. Use `ssh -t`, since `sudo` prompts.

```bash
ssh -t fe-pro "sudo mkdir -p /srv/stacks/symr && sudo chown 1000:1000 /srv/stacks/symr"
```

**`/srv/stacks/<service>/` is the machine's convention, not Symr's choice** — one directory per
hosted service, each with a `data/` subdirectory bind-mounted to `/data` in its container. The
Minecraft stack at `/srv/stacks/minecraft/` is the same shape, and Docker's own `data-root` is
moved to `/srv/docker` by `/etc/docker/daemon.json`. `~/SERVER.md` on the machine is the full
description. Symr diverges in two ways on purpose: its stack directory is `finne`-owned rather
than root-owned, so `deploy.sh` needs no `sudo`; and its compose file stays inside `repo/deploy/`
rather than at the stack root, because Symr is built from source and the compose belongs with the
commit it builds.

## 4. Clone the repo

```bash
ssh fe-pro "git clone https://github.com/FinnE145/Spotify-Manager /srv/stacks/symr/repo"
```

## 5. Write `symr.env`

Copy `deploy/symr.env.example` to `/srv/stacks/symr/symr.env` on the server (mode 600), and fill
in the real values — same Spotify client id/secret as the laptop, the server's own
redirect URI (already in the example), and a **fresh** `SYMR_SECRET_KEY`:

```bash
ssh fe-pro "python3 -c 'import secrets; print(secrets.token_hex(32))'"
```

Do not reuse the laptop's `SYMR_SECRET_KEY`.

```bash
scp deploy/symr.env.example fe-pro:/srv/stacks/symr/symr.env
ssh fe-pro "chmod 600 /srv/stacks/symr/symr.env"
# then edit /srv/stacks/symr/symr.env on the server by hand
```

## 6. Copy the database across

**Stop the laptop app first.** Then send the database through `VACUUM INTO` rather than
rsyncing `symr.db` directly — stopping the app is *not* on its own enough to make a bare file
copy safe. The database is in WAL mode, and a stopped app routinely leaves a populated
`symr.db-wal` beside it (there was 3.1 MB of it on 2026-08-23); copying only the `.db` silently
drops every transaction still sitting in that WAL. `VACUUM INTO` writes one consistent,
compacted file with no `-wal`/`-shm` to carry across, which is the same reason §7.1 uses it for
the nightly backup.

```bash
ssh fe-pro "mkdir -p /srv/stacks/symr/data"

python3 -c "import sqlite3; c = sqlite3.connect('symr.db'); c.execute('VACUUM INTO ?', ('/tmp/symr-transfer.db',)); c.close()"
rsync -az /tmp/symr-transfer.db fe-pro:/srv/stacks/symr/data/symr.db
rm /tmp/symr-transfer.db

rsync -az .spotipy_cache fe-pro:/srv/stacks/symr/data/.spotipy_cache
rsync -az data/streaming_history fe-pro:/srv/stacks/symr/data/
```

Check the row counts match on both sides before going further — `track`, `play`,
`reviewed_pair`, `generation`, `membership`, `snapshot` and `canonical_group` are a good
spread, and a truncated transfer shows up immediately.

No trailing slash on `data/streaming_history` — with one, rsync copies the folder's
*contents* into `/srv/stacks/symr/data/` and the timestamped export folders land loose there
instead of under `/srv/stacks/symr/data/streaming_history/`, which is where `SYMR_UPLOAD_ROOT`
points.

Copying `.spotipy_cache` means no re-login is needed on first boot — see step 10.

Then repoint the existing `play_import.folder` values, which are laptop-relative
(`data/streaming_history/<ts>`) and would resolve against the container's `/app` working
directory — where there is no `data/`, since `.dockerignore` excludes it. Without this the
re-import button on `/dev/import` fails for every export copied across; fresh uploads are
unaffected, as they store the container path.

Run this **on the server** (`ssh fe-pro` first), so the quoting stays readable:

```bash
python3 - <<'EOF'
import sqlite3

conn = sqlite3.connect("/srv/stacks/symr/data/symr.db")
n = conn.execute(
    "UPDATE play_import SET folder = '/data/streaming_history/' || "
    "substr(folder, length('data/streaming_history/') + 1) "
    "WHERE folder LIKE 'data/streaming_history/%'"
).rowcount
conn.commit()
conn.close()
print("repointed", n, "rows")
EOF
```

## 7. Set up the backup timer

The repo is already on the server from step 4, so the units are copied from there — no `scp`
from the laptop. Use `ssh -t`: every `sudo` here prompts for a password.

```bash
ssh -t fe-pro "sudo mkdir -p /var/backups/symr && sudo chown 1000:1000 /var/backups/symr"
ssh -t fe-pro "sudo cp /srv/stacks/symr/repo/deploy/symr-backup.{service,timer} /etc/systemd/system/ && \
  sudo systemctl daemon-reload && \
  sudo systemctl enable --now symr-backup.timer"
```

Confirm with `systemctl list-timers symr-backup.timer` — it should show the next run at
00:00 UTC.

## 8. Build and start the container

```bash
ssh fe-pro "cd /srv/stacks/symr/repo/deploy && docker compose up -d --build"
```

## 9. Start `tailscale serve`

Persists across reboots, so this is a one-time command — but it needs root, and refuses with
`Access denied: serve config denied` without it. Setting the operator once means this is the
last `tailscale` command that needs `sudo`:

```bash
ssh -t fe-pro "sudo tailscale set --operator=finne && tailscale serve --bg 45660"
```

Confirm with `tailscale serve status`. It must say **`(tailnet only)`** — anything mentioning
Funnel means Symr is exposed to the open internet, which trips the spec's §12 tripwire and
makes app-level authentication a prerequisite rather than a follow-up.

## 10. Verify

From a tailnet device, browse to `https://fe-pro.tail78f5ec.ts.net/`. Confirm:

- it loads already authenticated (the copied token cache) — no redirect to `/login`;
- `/dev/snapshot` shows the expected playlist counts, matching the laptop's before step 6;
- the scoring-failure banner is absent.

The new redirect URI still has to be exercised once for future logins to work — click
through `/login` manually to confirm it round-trips, even though the copied cache means
today's session doesn't need it.

---

Bootstrap is done. From here on, redeploys go through `deploy/deploy.sh`
(`CLAUDE.md`'s `## Commands` → Deploy).
