#!/usr/bin/env bash
# Delete every case on the hand-built smoke VM (infra/README.md, "Reset the demo's cases"), so the demo can be replayed.
#
# "Case" means the cases.* schema in the VM's Postgres: disputes, handoffs, card blocks, the case queue and the
# audit log. The bank read models (bank.*), the traces (ops.*), the config files, the images and the Compose volumes
# are not touched. Without --apply it only reports what would be deleted.
#
# With --apply, on the VM: a dump of cases.* goes to /opt/minsky/backups first, then the schema is dropped, the API is
# restarted (it recreates the empty schema on startup and forgets its in-memory conversations), and the result is
# checked: health, empty cases tables, bank.* row counts unchanged. If any check fails the dump is restored.
#
# Usage:
#   infra/reset_cases_smoke.sh --host ubuntu@<VM_IP> --key <KEY_FILE>            # report only, changes nothing
#   infra/reset_cases_smoke.sh --host ubuntu@<VM_IP> --key <KEY_FILE> --apply    # back up, delete, verify
#   infra/reset_cases_smoke.sh --print-remote                                    # print the script that runs on the VM
# The same values can come from SMOKE_VM_HOST and SMOKE_KEY_FILE.
set -euo pipefail

VM_HOST="${SMOKE_VM_HOST:-}"
KEY_FILE="${SMOKE_KEY_FILE:-}"
APPLY=0
PRINT_REMOTE=0

usage() { sed -n '2,/^set -euo/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//'; }
die() { printf 'error: %s\n' "$*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --host) VM_HOST="${2:-}"; shift 2 ;;
    --key) KEY_FILE="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --print-remote) PRINT_REMOTE=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

[ "$PRINT_REMOTE" = 1 ] || [ -n "$VM_HOST" ] || { usage >&2; die "--host (or SMOKE_VM_HOST) is required"; }
[ "$PRINT_REMOTE" = 1 ] || [ -n "$KEY_FILE" ] || { usage >&2; die "--key (or SMOKE_KEY_FILE) is required"; }
[ "$PRINT_REMOTE" = 1 ] || [ -f "$KEY_FILE" ] || die "key file not found: $KEY_FILE"

SSH=(ssh -o BatchMode=yes -o IdentitiesOnly=yes -i "$KEY_FILE" "$VM_HOST")

# --- the part that runs on the VM -------------------------------------------------------------------
# Printed here and piped to `bash -s`, so --print-remote can show it and the tests can read it.
remote_script() {
  cat <<'REMOTE'
set -euo pipefail
PHASE="$1"
ROOT="${RESET_ROOT:-/opt/minsky}"  # only the tests set RESET_ROOT
PG=minsky-postgres-1
API=minsky-api-1

pg_value() {  # $1 = key in db.env, $2 = default
  local v
  v=$(grep -E "^$1=" "$ROOT/db.env" 2> /dev/null | tail -1 | cut -d= -f2- || true)
  echo "${v:-$2}"
}
PGUSER_NAME=$(pg_value POSTGRES_USER minsky)
PGDB_NAME=$(pg_value POSTGRES_DB minsky)

psql_q() {  # read-only or schema statements; stops at the first error
  # No -i and stdin from /dev/null: this script arrives on stdin (`bash -s`), and a docker that reads it would eat
  # the rest of the script and end it silently.
  docker exec "$PG" psql -U "$PGUSER_NAME" -d "$PGDB_NAME" -v ON_ERROR_STOP=1 -At -F ' | ' "$@" < /dev/null
}

healthy() {
  local i
  for i in $(seq 1 24); do
    if curl -fsS -m 5 http://localhost/api/health > /dev/null 2>&1; then return 0; fi
    sleep 5
  done
  return 1
}

case_counts() {  # one "table | rows" line per table of the cases schema; nothing if the schema does not exist
  local t
  for t in $(psql_q -c "select table_name from information_schema.tables where table_schema = 'cases' order by 1"); do
    echo "$t | $(psql_q -c "select count(*) from cases.\"$t\"")"
  done
}

bank_counts() {  # the read models must come out of a reset exactly as they went in
  echo "customers | $(psql_q -c 'select count(*) from bank.customers')"
  echo "transactions | $(psql_q -c 'select count(*) from bank.transactions')"
}

docker info > /dev/null 2>&1 < /dev/null || { echo "docker is not running or not usable by this user" >&2; exit 3; }
[ -f "$ROOT/db.env" ] || { echo "missing $ROOT/db.env (see the rebuild steps)" >&2; exit 3; }
docker ps --format '{{.Names}}' < /dev/null | grep -qx "$PG" || { echo "container $PG is not running" >&2; exit 3; }
[ "$(bank_counts | cut -d' ' -f3 | head -1)" -gt 0 ] || { echo "bank.customers is empty: wrong database?" >&2; exit 3; }

case "$PHASE" in
  report)
    echo "cases.* rows that --apply would delete:"
    case_counts | sed 's/^/  /'
    echo "bank.* (not touched):"
    bank_counts | sed 's/^/  /'
    ;;

  apply)
    before_bank=$(bank_counts)
    echo "before:"; case_counts | sed 's/^/  /'
    stamp=$(date +%Y%m%d%H%M%S)
    mkdir -p "$ROOT/backups"
    backup="$ROOT/backups/cases-$stamp.sql"
    umask 077
    docker exec "$PG" pg_dump -U "$PGUSER_NAME" -d "$PGDB_NAME" --schema=cases --no-owner < /dev/null > "$backup"
    grep -q 'CREATE TABLE' "$backup" || { echo "the backup has no tables: nothing was deleted" >&2; rm -f "$backup"; exit 4; }
    echo "backup: $backup ($(wc -c < "$backup") bytes)"

    psql_q -c 'DROP SCHEMA cases CASCADE'
    docker restart "$API" > /dev/null < /dev/null

    fail=""
    healthy || fail="the API did not become healthy"
    if [ -z "$fail" ]; then
      rows=$(case_counts)
      [ -n "$rows" ] || fail="the API did not recreate the cases schema"
      echo "$rows" | awk -F' [|] ' '$2 != 0 {bad=1} END {exit bad}' || fail="cases tables are not empty after the reset"
      [ "$(bank_counts)" = "$before_bank" ] || fail="bank.* row counts changed"
    fi
    if [ -n "$fail" ]; then
      echo "reset failed ($fail): restoring $backup" >&2
      psql_q -c 'DROP SCHEMA IF EXISTS cases CASCADE'
      docker exec -i "$PG" psql -U "$PGUSER_NAME" -d "$PGDB_NAME" -v ON_ERROR_STOP=1 -q < "$backup" > /dev/null
      docker restart "$API" > /dev/null < /dev/null
      healthy && echo "restored and healthy" >&2 || echo "restored but the API is NOT healthy" >&2
      exit 4
    fi
    echo "after:"; case_counts | sed 's/^/  /'
    echo "bank.* unchanged:"; bank_counts | sed 's/^/  /'
    echo "done: the cases are deleted, the API is healthy. To undo: psql < $backup (after dropping the empty cases schema)."
    ;;

  *) echo "unknown phase: $PHASE" >&2; exit 2 ;;
esac
REMOTE
}

run_remote() {  # $1 = phase
  remote_script | "${SSH[@]}" bash -s -- "$1"
}

# --- local flow --------------------------------------------------------------------------------------
if [ "$PRINT_REMOTE" = 1 ]; then remote_script; exit 0; fi

if [ "$APPLY" = 1 ]; then
  printf 'plan: delete every case on %s (cases.* only; dump first, verify after, restore on failure)\n' "$VM_HOST"
  run_remote apply
else
  printf 'report only (nothing is changed) for %s; rerun with --apply to delete\n' "$VM_HOST"
  run_remote report
fi
