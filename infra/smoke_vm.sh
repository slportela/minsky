#!/usr/bin/env bash
# Operate the hand-built Lightsail smoke VM from a workstation: deploy a commit of main, roll back, and look at it
# without changing anything. Read infra/README.md, "Redeploy from main", before running it.
#
# What it does for you: asks Lightsail for temporary SSH credentials (valid about a minute, kept in a private temp
# dir that is deleted on exit, never printed), finds the VM's address and the public URL through the AWS API, checks
# the server's host key against a fingerprint YOU pinned (it refuses to connect otherwise), and calls the reviewed
# scripts. Nothing about the VM's address, account or domain is stored in the repository.
#
# Usage:
#   infra/smoke_vm.sh status                       # read-only: release, containers, schemas, API log errors
#   infra/smoke_vm.sh verify [<ref>]               # read-only checks, exit 1 if one fails; with a ref, it must be the running release
#   infra/smoke_vm.sh deploy <ref> [--dry-run]     # ship the commit (it must be on main), then run verify
#   infra/smoke_vm.sh rollback                     # back to the previous release, then run verify
#   infra/smoke_vm.sh sql "<SELECT ...>"           # one read-only query against the VM's Postgres
#   infra/smoke_vm.sh cases-report                 # rows per cases.* table (changes nothing)
#
# Not here on purpose: deleting cases (infra/reset_cases_smoke.sh --apply) and writing credentials into runtime.env
# are done by a person, not through this script.
#
# Configuration (environment; the defaults are this project's demo):
#   SMOKE_AWS_PROFILE=personal  SMOKE_REGION=us-east-2  SMOKE_INSTANCE=minsky-smoke  SMOKE_STATIC_IP=minsky-1
#   SMOKE_CDN=minsky-smoke-cdn  SMOKE_CDN_REGION=us-east-1
#   SMOKE_HOST_FP        the VM's ED25519 host key fingerprint (SHA256:...), or put it in ~/.config/minsky/smoke_host_fp
#   SMOKE_VM_IP / SMOKE_PUBLIC_URL   override the lookups through the AWS API
#   SMOKE_MAIN_REF=origin/main       the ref a deployed commit must be reachable from
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PROFILE="${SMOKE_AWS_PROFILE:-personal}"
REGION="${SMOKE_REGION:-us-east-2}"
INSTANCE="${SMOKE_INSTANCE:-minsky-smoke}"
STATIC_IP="${SMOKE_STATIC_IP:-minsky-1}"
CDN="${SMOKE_CDN:-minsky-smoke-cdn}"
CDN_REGION="${SMOKE_CDN_REGION:-us-east-1}"
FP_FILE="${SMOKE_HOST_FP_FILE:-$HOME/.config/minsky/smoke_host_fp}"
MAIN_REF="${SMOKE_MAIN_REF:-origin/main}"

usage() { sed -n '2,/^set -euo/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//'; }
die() { printf 'error: %s\n' "$*" >&2; exit "${2:-2}"; }

CMD="${1:-}"; [ -n "$CMD" ] || { usage >&2; exit 2; }
shift || true
case "$CMD" in
  status | verify | deploy | rollback | sql | cases-report) ;;
  -h | --help | help) usage; exit 0 ;;
  *) usage >&2; die "unknown command: $CMD" ;;
esac

T=""
cleanup() { [ -z "$T" ] || rm -rf "$T"; }
trap cleanup EXIT

aws_ls() { AWS_PAGER="" aws lightsail "$@" --profile "$PROFILE" --output text; }

resolve_targets() {  # sets VM_IP and PUBLIC_URL
  VM_IP="${SMOKE_VM_IP:-}"
  if [ -z "$VM_IP" ]; then
    VM_IP="$(aws_ls get-static-ip --region "$REGION" --static-ip-name "$STATIC_IP" --query 'staticIp.ipAddress')" \
      || die "could not read the static IP $STATIC_IP (profile $PROFILE, region $REGION)"
  fi
  PUBLIC_URL="${SMOKE_PUBLIC_URL:-}"
  if [ -z "$PUBLIC_URL" ]; then
    domain="$(aws_ls get-distributions --region "$CDN_REGION" --distribution-name "$CDN" --query 'distributions[0].domainName' 2> /dev/null || true)"
    [ -z "$domain" ] || [ "$domain" = None ] || PUBLIC_URL="https://$domain"
  fi
}

expected_fp() {
  if [ -n "${SMOKE_HOST_FP:-}" ]; then printf '%s' "$SMOKE_HOST_FP"; return; fi
  [ -f "$FP_FILE" ] && tr -d ' \n' < "$FP_FILE" || true
}

connect() {  # temporary credentials + a host key checked against the pinned fingerprint; leaves $T/bin/ssh and $T/key
  resolve_targets
  T="$(mktemp -d)"; mkdir "$T/bin"
  AWS_PAGER="" aws lightsail get-instance-access-details --region "$REGION" --instance-name "$INSTANCE" --protocol ssh \
    --profile "$PROFILE" --output json > "$T/details.json" \
    || die "could not get temporary SSH credentials (needs lightsail:GetInstanceAccessDetails on $INSTANCE, profile $PROFILE)"
  python3 - "$T" "$VM_IP" << 'PY'
import json, pathlib, sys
t, ip = pathlib.Path(sys.argv[1]), sys.argv[2]
d = json.loads((t / "details.json").read_text())["accessDetails"]
if d["ipAddress"] != ip:
    raise SystemExit(f"error: the instance answers on {d['ipAddress']}, not on the expected address")
if d["username"] != "ubuntu":
    raise SystemExit(f"error: unexpected SSH user {d['username']}")
(t / "key").write_text(d["privateKey"].rstrip("\n") + "\n"); (t / "key").chmod(0o600)
(t / "key-cert.pub").write_text(d["certKey"].rstrip("\n") + "\n")
fps = sorted({h["fingerprintSHA256"].removeprefix("SHA256:").rstrip("=") for h in d.get("hostKeys", [])})
(t / "api_fps").write_text("\n".join(fps) + "\n")
PY
  rm -f "$T/details.json"

  local want bare fp line ok=0
  want="$(expected_fp)"
  /usr/bin/ssh-keyscan -T 10 -t ed25519,ecdsa,rsa "$VM_IP" 2> /dev/null | grep -v '^#' > "$T/scan" || true
  : > "$T/known_hosts"
  while read -r line; do
    fp="$(printf '%s\n' "$line" | /usr/bin/ssh-keygen -lf - 2> /dev/null | awk '{print $2}' || true)"
    [ -n "$fp" ] || continue
    bare="$(printf '%s' "$fp" | sed 's/^SHA256://; s/=*$//')"
    if grep -qxF "$bare" "$T/api_fps" 2> /dev/null || { [ -n "$want" ] && [ "$bare" = "$(printf '%s' "$want" | sed 's/^SHA256://; s/=*$//')" ]; }; then
      printf '%s\n' "$line" >> "$T/known_hosts"; ok=1
    fi
  done < "$T/scan"
  if [ "$ok" != 1 ]; then
    seen=""
    while read -r line; do
      fp="$(printf '%s\n' "$line" | /usr/bin/ssh-keygen -lf - 2> /dev/null | awk '{print $2}' || true)"
      [ -z "$fp" ] || seen="$seen $fp"
    done < "$T/scan"
    {
      echo "error: no host key of the server matches a pinned fingerprint, so nothing was sent."
      echo "Observed:${seen:- (none: the server did not answer)}"
      echo "Pinned:   ${want:-(none: set SMOKE_HOST_FP or write the fingerprint to $FP_FILE)}"
      echo "Ask the person who owns the VM to confirm the fingerprint from a machine that already trusts it"
      echo "(ssh-keygen -lF <VM_IP>) before pinning it. Do not pin what the network shows you without that check."
    } >&2
    exit 6
  fi
  cat > "$T/bin/ssh" << SHIM
#!/bin/bash
exec /usr/bin/ssh -o UserKnownHostsFile="$T/known_hosts" -o GlobalKnownHostsFile=/dev/null -o StrictHostKeyChecking=yes "\$@"
SHIM
  chmod 700 "$T/bin/ssh"
}

vm_ssh() { PATH="$T/bin:$PATH" ssh -o BatchMode=yes -o IdentitiesOnly=yes -i "$T/key" "ubuntu@$VM_IP" "$@"; }

remote_status() {
  vm_ssh bash -s << 'REMOTE'
echo "release:            $(readlink /opt/minsky/src 2> /dev/null | sed 's#releases/##')"
echo "previous release:   $(cat /opt/minsky/previous-release 2> /dev/null || echo none)  (the rollback target)"
echo "images:             $(grep -E '^MINSKY_(API|WEB)_IMAGE=' /opt/minsky/images.env | cut -d= -f2 | tr '\n' ' ')"
echo "containers:"; docker ps --format '  {{.Names}}  {{.Image}}  {{.Status}}' < /dev/null
echo "schemas:            $(docker exec minsky-postgres-1 psql -U minsky -d minsky -Atc "select string_agg(nspname, ', ' order by nspname) from pg_namespace where nspname in ('bank','cases','ops')" < /dev/null 2>&1 | head -1)"
echo "API log, error lines (last 300 lines):"
docker logs --tail 300 minsky-api-1 < /dev/null 2>&1 | grep -iE 'error|traceback|exception|critical' | tail -10 | sed 's/^/  /' || true
echo "  (end)"
echo "disk free in /opt/minsky: $(df -BG --output=avail /opt/minsky | tail -1 | tr -d ' ')  swap: $(free -m | awk '/^Swap:/ {print $2}') MB"
REMOTE
}

verify() {  # $1 = optional ref the running release must equal
  local fail=0 want="" run code body
  [ -z "${1:-}" ] || want="$(git -C "$REPO" rev-parse --verify --short=12 "${1}^{commit}")"
  run="$(vm_ssh 'readlink /opt/minsky/src | sed "s#releases/##"')"
  if [ -n "$want" ]; then
    if [ "$run" = "$want" ]; then echo "ok    running release $run is the expected commit"; else echo "FAIL  running release $run, expected $want"; fail=1; fi
  else echo "info  running release $run"; fi
  if vm_ssh 'docker ps --format "{{.Names}} {{.Status}}" | grep -E "minsky-(api|web|postgres|caddy)-1"' | tee "$T/ps" | grep -qE "minsky-api-1 Up.*healthy|minsky-api-1 Up"; then
    for c in api web postgres caddy; do
      grep -q "minsky-$c-1 Up" "$T/ps" && echo "ok    container $c is up" || { echo "FAIL  container $c is not up"; fail=1; }
    done
    grep -q "minsky-api-1 Up.*unhealthy" "$T/ps" && { echo "FAIL  the API container is unhealthy"; fail=1; } || true
  else
    echo "FAIL  the API container is not up"; fail=1
  fi
  errs="$(vm_ssh 'docker logs --tail 300 minsky-api-1 2>&1 | grep -ciE "traceback|exception|critical" || true')"
  [ "${errs:-0}" = 0 ] && echo "ok    no traceback, exception or critical lines in the last 300 API log lines" || { echo "FAIL  $errs error lines in the API log"; fail=1; }
  if [ -n "${PUBLIC_URL:-}" ]; then
    body="$(/usr/bin/curl -fsS -m 20 "$PUBLIC_URL/api/health" 2> /dev/null || true)"
    case "$body" in *'"status":"ok"'*) echo "ok    $PUBLIC_URL/api/health answers ok" ;; *) echo "FAIL  public health check: ${body:-no answer}"; fail=1 ;; esac
    for path in /chat /console; do
      code="$(/usr/bin/curl -s -o /dev/null -m 20 -w '%{http_code}' "$PUBLIC_URL$path" || true)"
      [ "$code" = 200 ] && echo "ok    $path answers 200" || { echo "FAIL  $path answers $code"; fail=1; }
    done
    code="$(/usr/bin/curl -s -o /dev/null -m 20 -w '%{http_code}' -X POST "$PUBLIC_URL/api/chat/turn" -H 'Content-Type: application/json' -d '{"messages":[{"user":"hola"}]}' || true)"
    [ "$code" = 401 ] && echo "ok    a chat turn without a credential is refused (401)" || { echo "FAIL  a chat turn without a credential answers $code, expected 401"; fail=1; }
  else
    echo "info  no public URL known (set SMOKE_PUBLIC_URL): the public checks were skipped"
  fi
  [ "$fail" = 0 ] && echo "verify: all checks passed" || echo "verify: FAILED"
  return "$fail"
}

case "$CMD" in
  status)
    connect; remote_status
    ;;

  verify)
    connect; verify "${1:-}"
    ;;

  deploy)
    REF="${1:-}"; [ -n "$REF" ] || die "deploy needs a git ref (a commit of main)"; shift
    DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
    cd "$REPO"
    SHA="$(git rev-parse --verify --short=12 "${REF}^{commit}")" || die "not a commit: $REF"
    git rev-parse --verify -q "$MAIN_REF" > /dev/null || die "$MAIN_REF does not exist here: git fetch origin main"
    git merge-base --is-ancestor "$SHA" "$MAIN_REF" \
      || die "$SHA is not on $MAIN_REF. Only commits of main are deployed: merge first, then deploy."
    if [ "$DRY" = 1 ]; then
      "$HERE/redeploy_smoke.sh" --host ubuntu@dry-run.invalid --key /dev/null --ref "$SHA" --dry-run
      exit 0
    fi
    connect
    PATH="$T/bin:$PATH" "$HERE/redeploy_smoke.sh" --host "ubuntu@$VM_IP" --key "$T/key" --ref "$SHA"
    echo
    verify "$SHA"
    ;;

  rollback)
    cd "$REPO"
    connect
    PATH="$T/bin:$PATH" "$HERE/redeploy_smoke.sh" --host "ubuntu@$VM_IP" --key "$T/key" --rollback
    echo
    verify
    ;;

  sql)
    SQL="${1:-}"; [ -n "$SQL" ] || die "sql needs a SELECT statement"
    case "$SQL" in [Ss][Ee][Ll][Ee][Cc][Tt]*) ;; *) die "only SELECT statements" ;; esac
    case "$SQL" in *";"*[!\ ]*) die "one statement only (no ; followed by more text)" ;; esac
    connect
    printf '%s' "$SQL" | base64 | tr -d '\n' > "$T/sql.b64"
    vm_ssh "echo $(cat "$T/sql.b64") | base64 -d | docker exec -i minsky-postgres-1 psql -U minsky -d minsky -At -F ' | ' -q -v ON_ERROR_STOP=1 --single-transaction -c 'set transaction read only' -f -"
    ;;

  cases-report)
    cd "$REPO"
    connect
    PATH="$T/bin:$PATH" "$HERE/reset_cases_smoke.sh" --host "ubuntu@$VM_IP" --key "$T/key"
    ;;
esac
