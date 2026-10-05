#!/usr/bin/env bash
# Redeploy a commit of the stack to the hand-built smoke VM (infra/README.md, "Smoke stack built by hand").
#
# From your workstation: uploads the tracked sources of one git ref, builds the two images ON the VM
# (the workstation cannot cross-build the Next.js frontend for linux/amd64), restarts Compose and waits
# for /api/health. If the new version does not become healthy it goes back to the previous one.
#
# It never uploads .env, keys or data (only the tracked paths listed below), never touches the config
# files in /opt/minsky (images.env is the only one it edits), and never removes Compose volumes.
#
# Usage:
#   infra/redeploy_smoke.sh --host ubuntu@<VM_IP> --key <KEY_FILE> [--ref <git-ref>] [--public-url <url>]
#   infra/redeploy_smoke.sh --host ubuntu@<VM_IP> --key <KEY_FILE> --rollback
#   infra/redeploy_smoke.sh ... --dry-run          # print the plan, run nothing
#   infra/redeploy_smoke.sh --print-remote         # print the script that runs on the VM, run nothing
# The same values can come from SMOKE_VM_HOST, SMOKE_KEY_FILE and SMOKE_PUBLIC_URL.
# Set ALLOW_NO_SWAP=1 to build on a VM with less than 1 GB of swap (the web build can run out of memory).
set -euo pipefail

# Tracked paths that make up a release. Keep this list explicit: it is what leaves the workstation.
RELEASE_PATHS=(pyproject.toml uv.lock backend prompts frontend infra/caddy compose.yaml compose.demo.yaml)

VM_HOST="${SMOKE_VM_HOST:-}"
KEY_FILE="${SMOKE_KEY_FILE:-}"
PUBLIC_URL="${SMOKE_PUBLIC_URL:-}"
REF="HEAD"
DRY_RUN=0
ROLLBACK=0
PRINT_REMOTE=0

usage() { sed -n '2,/^set -euo/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//'; }
die() { printf 'error: %s\n' "$*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --host) VM_HOST="${2:-}"; shift 2 ;;
    --key) KEY_FILE="${2:-}"; shift 2 ;;
    --ref) REF="${2:-}"; shift 2 ;;
    --public-url) PUBLIC_URL="${2:-}"; shift 2 ;;
    --rollback) ROLLBACK=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --print-remote) PRINT_REMOTE=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

[ "$PRINT_REMOTE" = 1 ] || [ -n "$VM_HOST" ] || { usage >&2; die "--host (or SMOKE_VM_HOST) is required"; }
[ "$PRINT_REMOTE" = 1 ] || [ -n "$KEY_FILE" ] || { usage >&2; die "--key (or SMOKE_KEY_FILE) is required"; }
[ "$PRINT_REMOTE" = 1 ] || [ "$DRY_RUN" = 1 ] || [ -f "$KEY_FILE" ] || die "key file not found: $KEY_FILE"

SSH=(ssh -o BatchMode=yes -o IdentitiesOnly=yes -i "$KEY_FILE" "$VM_HOST")

# --- the part that runs on the VM -------------------------------------------------------------------
# Printed here and piped to `bash -s`, so the dry run can show it and the tests can read it.
remote_script() {
  cat <<'REMOTE'
set -euo pipefail
PHASE="$1"; SHA="$2"; ALLOW_NO_SWAP="${3:-0}"
ROOT=/opt/minsky
cd "$ROOT"

compose() {
  docker compose -p minsky --env-file "$ROOT/images.env" --env-file "$ROOT/db.env" \
    --env-file "$ROOT/runtime.env" -f compose.yaml -f compose.demo.yaml "$@"
}

healthy() {
  local i
  for i in $(seq 1 24); do
    if curl -fsS -m 5 http://localhost/api/health > /dev/null 2>&1; then return 0; fi
    sleep 5
  done
  return 1
}

point_images_at() {  # $1 = tag
  { grep -v -E '^MINSKY_(API|WEB)_IMAGE=' "$ROOT/images.env" || true
    echo "MINSKY_API_IMAGE=minsky-api:$1"
    echo "MINSKY_WEB_IMAGE=minsky-web:$1"; } > "$ROOT/images.env.new"
  chmod 600 "$ROOT/images.env.new"
  mv "$ROOT/images.env.new" "$ROOT/images.env"
}

switch_src() {  # $1 = release directory name under releases/ (atomic symlink swap)
  ln -sfn "releases/$1" "$ROOT/src.new"
  mv -T "$ROOT/src.new" "$ROOT/src"
}

case "$PHASE" in
  preflight)
    for f in images.env db.env runtime.env; do
      [ -f "$ROOT/$f" ] || { echo "missing $ROOT/$f (see the rebuild steps)" >&2; exit 3; }
    done
    docker info > /dev/null 2>&1 || { echo "docker is not running or not usable by this user" >&2; exit 3; }
    for key in MINSKY_LLM_API_KEY MINSKY_TEST_SESSIONS; do
      grep -q "^$key=" "$ROOT/runtime.env" || { echo "runtime.env has no $key" >&2; exit 3; }
    done
    if grep -q '^MINSKY_STAFF_SESSIONS=' "$ROOT/runtime.env"; then
      echo "runtime.env: MINSKY_STAFF_SESSIONS is set"
    else
      echo "runtime.env: MINSKY_STAFF_SESSIONS is NOT set (only needed by versions that serve /console)"
    fi
    avail_gb=$(df -BG --output=avail "$ROOT" | tail -1 | tr -dc '0-9')
    [ "$avail_gb" -ge 5 ] || { echo "only ${avail_gb} GB free in $ROOT; need at least 5" >&2; exit 3; }
    swap_mb=$(free -m | awk '/^Swap:/ {print $2}')
    if [ "${swap_mb:-0}" -lt 1000 ] && [ "$ALLOW_NO_SWAP" != 1 ]; then
      echo "swap is ${swap_mb:-0} MB: the web build can run out of memory on this VM." >&2
      echo "Add a swapfile (infra/README.md, rebuild step 5) or rerun with ALLOW_NO_SWAP=1." >&2
      exit 3
    fi
    grep -q swapfile /etc/fstab || echo "note: the swapfile is not in /etc/fstab and will not survive a reboot"
    echo "preflight ok: compose $(docker compose version --short), ${avail_gb} GB free, swap ${swap_mb:-0} MB"
    ;;

  deploy)
    [ -d "$ROOT/releases/$SHA" ] || { echo "release $SHA was not uploaded" >&2; exit 3; }
    current=""
    if [ -L "$ROOT/src" ]; then
      current=$(basename "$(readlink "$ROOT/src")")
    elif [ -d "$ROOT/src" ]; then  # first redeploy: the hand-built tree becomes the first release
      current="manual-$(date +%Y%m%d%H%M%S)"
      mkdir -p "$ROOT/releases"
      mv "$ROOT/src" "$ROOT/releases/$current"
    fi
    if [ -n "$current" ] && [ "$current" != "$SHA" ]; then
      echo "$current" > "$ROOT/previous-release"
      cp -p "$ROOT/images.env" "$ROOT/images.env.prev"
    fi
    switch_src "$SHA"
    cd "$ROOT/src"
    docker build -f backend/Dockerfile -t "minsky-api:$SHA" .
    docker build -t "minsky-web:$SHA" frontend
    point_images_at "$SHA"
    compose up -d
    if healthy; then
      echo "healthy: release $SHA"
    else
      echo "release $SHA did not become healthy" >&2
      compose logs --tail 40 api >&2 || true
      if [ -f "$ROOT/previous-release" ] && [ -f "$ROOT/images.env.prev" ]; then
        echo "going back to $(cat "$ROOT/previous-release")" >&2
        switch_src "$(cat "$ROOT/previous-release")"
        cp -p "$ROOT/images.env.prev" "$ROOT/images.env"
        cd "$ROOT/src" && compose up -d
        healthy && echo "rolled back and healthy" >&2 || echo "rolled back but NOT healthy" >&2
      fi
      exit 4
    fi
    compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
    ;;

  rollback)
    [ -f "$ROOT/previous-release" ] && [ -f "$ROOT/images.env.prev" ] \
      || { echo "nothing to roll back to (no previous-release / images.env.prev)" >&2; exit 3; }
    target=$(cat "$ROOT/previous-release")
    [ -d "$ROOT/releases/$target" ] || { echo "release $target is gone" >&2; exit 3; }
    here=$(basename "$(readlink "$ROOT/src")")
    switch_src "$target"
    cp -p "$ROOT/images.env.prev" "$ROOT/images.env"
    echo "$here" > "$ROOT/previous-release"  # a second rollback goes forward again
    cd "$ROOT/src"
    compose up -d
    healthy || { echo "rolled back to $target but it is NOT healthy" >&2; exit 4; }
    echo "healthy: back on $target"
    compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
    ;;

  *) echo "unknown phase: $PHASE" >&2; exit 2 ;;
esac
REMOTE
}

run_remote() {  # $1 = phase, $2 = release id
  remote_script | "${SSH[@]}" bash -s -- "$1" "$2" "${ALLOW_NO_SWAP:-0}"
}

# --- local flow --------------------------------------------------------------------------------------
if [ "$PRINT_REMOTE" = 1 ]; then remote_script; exit 0; fi

if [ "$ROLLBACK" = 1 ]; then
  printf 'plan: roll %s back to its previous release\n' "$VM_HOST"
  [ "$DRY_RUN" = 1 ] && { echo "(dry run: nothing executed)"; exit 0; }
  run_remote rollback none
else
  SHA=$(git rev-parse --verify --short=12 "${REF}^{commit}") || die "not a commit: $REF"
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "warning: the working tree has uncommitted changes; only the committed $SHA is deployed" >&2
  fi
  printf 'plan: deploy %s (%s) to %s\n' "$SHA" "$REF" "$VM_HOST"
  printf '  upload (git archive, tracked files only): %s\n' "${RELEASE_PATHS[*]}"
  echo '  on the VM: preflight, extract to /opt/minsky/releases/<sha>, build both images, compose up -d, health check'
  echo '  never uploaded: .env, keys, data/, anything untracked'
  [ "$DRY_RUN" = 1 ] && { echo "(dry run: nothing executed)"; exit 0; }

  run_remote preflight "$SHA"
  git archive --format=tar "$SHA" -- "${RELEASE_PATHS[@]}" \
    | "${SSH[@]}" "mkdir -p /opt/minsky/releases/$SHA && tar -xf - -C /opt/minsky/releases/$SHA"
  run_remote deploy "$SHA"
fi

if [ -n "$PUBLIC_URL" ]; then
  echo "public check: $PUBLIC_URL"
  curl -fsS -m 20 "$PUBLIC_URL/api/health"; echo
  code=$(curl -s -o /dev/null -m 20 -w '%{http_code}' -X POST "$PUBLIC_URL/api/chat/turn" \
    -H 'Content-Type: application/json' -d '{"messages":[{"user":"hola"}]}')
  [ "$code" = 401 ] || { echo "expected 401 without a credential, got $code" >&2; exit 5; }
  echo "unauthenticated chat turn is refused (401)"
fi
