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

## 3. Create `/srv/symr`

The one privileged step — `/srv` is root-owned.

```bash
ssh fe-pro "sudo mkdir -p /srv/symr && sudo chown 1000:1000 /srv/symr"
```

## 4. Clone the repo

```bash
ssh fe-pro "git clone https://github.com/FinnE145/Spotify-Manager /srv/symr/repo"
```

## 5. Write `symr.env`

Copy `deploy/symr.env.example` to `/srv/symr/symr.env` on the server (mode 600), and fill
in the real values — same Spotify client id/secret as the laptop, the server's own
redirect URI (already in the example), and a **fresh** `SYMR_SECRET_KEY`:

```bash
ssh fe-pro "python3 -c 'import secrets; print(secrets.token_hex(32))'"
```

Do not reuse the laptop's `SYMR_SECRET_KEY`.

```bash
scp deploy/symr.env.example fe-pro:/srv/symr/symr.env
ssh fe-pro "chmod 600 /srv/symr/symr.env"
# then edit /srv/symr/symr.env on the server by hand
```

## 6. Copy the database across

**Stop the laptop app first** — this is the one transfer not protected by `VACUUM INTO`,
so stopping is what makes the copy consistent.

```bash
ssh fe-pro "mkdir -p /srv/symr/data"
rsync -az symr.db .spotipy_cache data/streaming_history fe-pro:/srv/symr/data/
```

No trailing slash on `data/streaming_history` — with one, rsync copies the folder's
*contents* into `/srv/symr/data/` and the timestamped export folders land loose there
instead of under `/srv/symr/data/streaming_history/`, which is where `SYMR_UPLOAD_ROOT`
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

conn = sqlite3.connect("/srv/symr/data/symr.db")
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

```bash
ssh fe-pro "sudo mkdir -p /var/backups/symr && sudo chown 1000:1000 /var/backups/symr"
scp deploy/symr-backup.service deploy/symr-backup.timer fe-pro:/tmp/
ssh fe-pro "sudo mv /tmp/symr-backup.service /tmp/symr-backup.timer /etc/systemd/system/ && \
  sudo systemctl daemon-reload && \
  sudo systemctl enable --now symr-backup.timer"
```

## 8. Build and start the container

```bash
ssh fe-pro "cd /srv/symr/repo/deploy && docker compose up -d --build"
```

## 9. Start `tailscale serve`

Persists across reboots, so this is a one-time command:

```bash
ssh fe-pro "tailscale serve --bg 45660"
```

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
