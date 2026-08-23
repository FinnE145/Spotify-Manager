#!/bin/sh
set -eu

# docs/specs/host-on-fe-pro-Q.md §7. Runs on the HOST via a systemd timer
# (symr-backup.timer + symr-backup.service), not in the container: it uses
# the host's own stdlib Python 3.12 (SQLite 3.45.1) to run VACUUM INTO. The
# sqlite3 CLI is not installed on fe-pro and this script does not need it.
#
# VACUUM INTO writes a fresh, compacted, transactionally-consistent copy
# while the app keeps running. A plain `cp` against symr.db would be wrong:
# the database is in WAL mode, so live data is split across symr.db and
# symr.db-wal, and a bare copy mid-write would silently produce a torn file.

DB_PATH="/srv/symr/data/symr.db"
BACKUP_DIR="/var/backups/symr"
RETENTION_DAYS=30

DATE="$(date -u +%Y-%m-%d)"
OUT="${BACKUP_DIR}/symr-${DATE}.db"

mkdir -p "$BACKUP_DIR"

# sqlite3.connect() silently creates a new, empty database at a path that
# doesn't exist rather than erroring -- without this check, a missing or
# misconfigured $DB_PATH would "succeed" with an empty backup instead of
# failing loudly, and nobody finds out until the day it's needed.
if [ ! -f "$DB_PATH" ]; then
    echo "backup.sh: $DB_PATH does not exist" >&2
    exit 1
fi

# VACUUM INTO refuses to write over an existing file, so a same-day re-run
# (manual test, or a systemd retry after a transient failure) would
# otherwise hard-fail against its own earlier output.
rm -f "$OUT"

python3 - "$DB_PATH" "$OUT" <<'PYEOF'
import sqlite3
import sys

db_path, out_path = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db_path)
try:
    conn.execute("VACUUM INTO ?", (out_path,))
finally:
    conn.close()
PYEOF

# Only reached if the dump above succeeded (set -e propagates a Python
# traceback's non-zero exit). Belt-and-braces check that it actually wrote
# something: pruning after a failed/empty dump is how you end up with thirty
# days of nothing to restore from.
if [ ! -s "$OUT" ]; then
    echo "backup.sh: $OUT was not written" >&2
    exit 1
fi

find "$BACKUP_DIR" -name 'symr-*.db' -mtime "+${RETENTION_DAYS}" -delete

echo "backup.sh: wrote $OUT"
