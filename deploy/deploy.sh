#!/bin/sh
set -eu

# docs/specs/host-on-fe-pro-Q.md §9. Run from the laptop, after the branch
# has been merged to main and pushed -- that's the Verify finish-up's job,
# not this script's.

HOST="fe-pro"
REPO="/srv/symr/repo"
COMPOSE="$REPO/deploy/docker-compose.yml"

echo "deploy.sh: pulling latest on $HOST"
ssh "$HOST" "git -C '$REPO' pull --ff-only"

echo "deploy.sh: rebuilding and restarting the container"
# Safe to run while a job is active and needs no guard: `up -d --build`
# replaces the old container, which delivers SIGTERM -- serve.py's handler,
# which stops the job at its own safe point (§6).
ssh "$HOST" "docker compose -f '$COMPOSE' up -d --build"

echo "deploy.sh: waiting for the health check"
# `up -d` returns as soon as the container is *created*, not when waitress
# is listening, so this retries for up to 30s. /login is the health target
# because it's in app.py's _PUBLIC_ENDPOINTS: it runs neither the login
# guard nor scoring.ensure_fresh(), and makes no outbound Spotify request
# (it builds the authorize URL locally and redirects) -- a health check
# that spent a Spotify request or triggered a recompute every time would be
# worse than none. Run over ssh, not from the laptop directly: the
# container's port is published to fe-pro's own loopback only (§3.3).
healthy=0
i=1
while [ "$i" -le 30 ]; do
    code="$(ssh "$HOST" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:45660/login" 2>/dev/null || true)"
    if [ "$code" = "302" ]; then
        healthy=1
        break
    fi
    sleep 1
    i=$((i + 1))
done

if [ "$healthy" != "1" ]; then
    echo "deploy.sh: health check failed -- /login did not return 302 within 30s" >&2
    exit 1
fi

echo "deploy.sh: healthy"

# §7.4's off-machine backup copy: pull the newest backup down to
# ~/Symr-backups/ on the laptop. Gated on being Finn's laptop specifically,
# so an agent running on a cloud machine or a second laptop doesn't quietly
# drag a 93 MB library down. This is a mistake-catcher, not a security
# control -- scutil is macOS-only, so it fails closed on Linux without
# depending on that being deliberate.
if [ "$(scutil --get LocalHostName 2>/dev/null || true)" != "Finns-MacBook-Pro" ]; then
    echo "deploy.sh: not Finn's laptop, skipping the backup pull"
    exit 0
fi

# Failure past this point never fails the deploy -- it already succeeded
# above. No backup yet, no route to the server, no disk space: print a
# clear warning and exit 0, the copy is a bonus.
set +e

mkdir -p "$HOME/Symr-backups"
if [ $? -ne 0 ]; then
    echo "deploy.sh: could not create $HOME/Symr-backups, skipping the backup pull" >&2
    exit 0
fi

latest="$(ssh "$HOST" "ls -t /var/backups/symr/symr-*.db 2>/dev/null | head -1" 2>/dev/null)"
if [ -z "$latest" ]; then
    echo "deploy.sh: no backup found on $HOST yet, skipping the pull"
    exit 0
fi

echo "deploy.sh: pulling $latest down to $HOME/Symr-backups/"
rsync -az "$HOST:$latest" "$HOME/Symr-backups/"
if [ $? -ne 0 ]; then
    echo "deploy.sh: backup pull failed -- deploy already succeeded, this is a bonus" >&2
    exit 0
fi

echo "deploy.sh: done"
exit 0
