"""infra/reset_cases_smoke.sh: what it may touch, and what it does when a step fails.

ssh is replaced by a stub that records its arguments and stdin; the remote half is also run for real against stand-ins
for docker, curl and sleep, so the dump-before-drop order, the checks and the restore are exercised without a VM.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "infra" / "reset_cases_smoke.sh"

SSH_STUB = """#!/bin/sh
n=$(( $(cat "$STUB_LOG/n" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_LOG/n"
printf '%s\\n' "$@" > "$STUB_LOG/call$n.args"
cat > "$STUB_LOG/call$n.stdin"
"""

# A stand-in for docker: the cases schema, its row counts and the dump live in files under $STATE.
DOCKER_STUB = r"""#!/bin/bash
echo "docker $*" >> "$STATE/calls"
case "$1" in
  info) exit 0 ;;
  ps) echo minsky-postgres-1; exit 0 ;;
  restart)
    # the API creates the schema if it is missing (IF NOT EXISTS): a restored schema keeps its rows
    if [ -f "$STATE/api_creates_schema" ] && [ ! -f "$STATE/schema" ]; then
      touch "$STATE/schema"; echo 0 > "$STATE/rows"
    fi
    exit 0 ;;
  exec)
    # like the real docker: with -i it reads the caller's stdin (the remote script itself, when run as `bash -s`)
    if printf '%s ' "$@" | grep -q -- ' -i '; then
      if printf '%s ' "$@" | grep -q -- ' -c '; then cat > /dev/null; fi
    fi
    if printf '%s ' "$@" | grep -q pg_dump; then
      [ -f "$STATE/empty_dump" ] || echo "CREATE TABLE cases.disputes ();"
      exit 0
    fi
    sql=""; prev=""
    for a in "$@"; do [ "$prev" = "-c" ] && sql="$a"; prev="$a"; done
    if [ -z "$sql" ]; then   # a restore from the dump, on stdin
      cat > /dev/null; touch "$STATE/schema"; cp "$STATE/rows_before" "$STATE/rows"; exit 0
    fi
    case "$sql" in
      *information_schema*) [ -f "$STATE/schema" ] && printf 'disputes\ncase_queue\n'; exit 0 ;;
      "select count(*) from cases."*) cat "$STATE/rows"; exit 0 ;;
      "select count(*) from bank.customers") echo 100; exit 0 ;;
      "select count(*) from bank.transactions") echo 5000; exit 0 ;;
      "DROP SCHEMA cases CASCADE"|"DROP SCHEMA IF EXISTS cases CASCADE") rm -f "$STATE/schema"; exit 0 ;;
    esac
    echo "unexpected sql: $sql" >&2; exit 9 ;;
esac
echo "unexpected docker call: $*" >&2; exit 9
"""
CURL_STUB = '#!/bin/sh\n[ -f "$STATE/health_ok" ]\n'
SLEEP_STUB = "#!/bin/sh\nexit 0\n"

FORBIDDEN = (
    "down -v",
    "--volumes",
    "volume rm",
    "docker volume",
    "system prune",
    "image prune",
    "rm -rf",
    "TRUNCATE",
    "DELETE ",
    "UPDATE ",
    "INSERT ",
    "DROP TABLE",
    "DROP DATABASE",
    "DROP SCHEMA bank",
    "DROP SCHEMA ops",
    "docker compose",
    "runtime.env",
)


def _stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def ssh_stub(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir / "ssh", SSH_STUB)
    log = tmp_path / "log"
    log.mkdir()
    key = tmp_path / "key.pem"
    key.write_text("not a real key")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(tmp_path),
        "STUB_LOG": str(log),
    }
    return env, log, key


def _local(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=60)


def _remote_script() -> str:
    return subprocess.run(["bash", str(SCRIPT), "--print-remote"], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def vm(tmp_path):
    """The remote half of the script, run against stand-ins. `state` is the fake VM's flags."""
    bin_dir = tmp_path / "vmbin"
    bin_dir.mkdir()
    _stub(bin_dir / "docker", DOCKER_STUB)
    _stub(bin_dir / "curl", CURL_STUB)
    _stub(bin_dir / "sleep", SLEEP_STUB)
    state = tmp_path / "state"
    state.mkdir()
    root = tmp_path / "opt-minsky"
    root.mkdir()
    (root / "db.env").write_text("POSTGRES_USER=minsky\nPOSTGRES_DB=minsky\n")
    for flag in ("schema", "health_ok", "api_creates_schema"):
        (state / flag).touch()
    (state / "rows").write_text("3\n")
    (state / "rows_before").write_text("3\n")
    env = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin",
        "STATE": str(state),
        "RESET_ROOT": str(root),
        "HOME": str(tmp_path),
    }
    script = _remote_script()

    def run(phase: str) -> subprocess.CompletedProcess[str]:
        # the way ssh runs it: the script arrives on stdin, so anything that reads stdin eats the rest of the script
        return subprocess.run(
            ["bash", "-s", "--", phase], input=script, env=env, capture_output=True, text=True, timeout=60
        )

    return run, state, root


def test_scripts_are_valid_bash(tmp_path):
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    remote = tmp_path / "remote.sh"
    remote.write_text(_remote_script())
    assert subprocess.run(["bash", "-n", str(remote)]).returncode == 0


def test_host_and_key_are_required(ssh_stub):
    env, log, _key = ssh_stub
    result = _local(env)
    assert result.returncode == 2 and "--host" in result.stderr
    assert not (log / "n").exists()


def test_by_default_it_only_reports(ssh_stub):
    env, log, key = ssh_stub
    result = _local(env, "--host", "ubuntu@vm.invalid", "--key", str(key))
    assert result.returncode == 0, result.stderr
    assert "report only" in result.stdout
    assert (log / "call1.args").read_text().splitlines()[-1] == "report"
    assert str(key) in (log / "call1.args").read_text()


def test_apply_runs_the_apply_phase_once(ssh_stub):
    env, log, key = ssh_stub
    result = _local(env, "--host", "ubuntu@vm.invalid", "--key", str(key), "--apply")
    assert result.returncode == 0, result.stderr
    assert (log / "n").read_text().strip() == "1"
    assert (log / "call1.args").read_text().splitlines()[-1] == "apply"
    assert "cases.* only" in result.stdout


def test_print_remote_matches_what_is_sent(ssh_stub):
    env, log, key = ssh_stub
    _local(env, "--host", "ubuntu@vm.invalid", "--key", str(key), "--apply")
    assert (log / "call1.stdin").read_text() == _remote_script()


def test_the_remote_side_only_ever_drops_the_cases_schema_and_dumps_it_first():
    remote = _remote_script()
    for fragment in FORBIDDEN:
        assert fragment not in remote, fragment
    drops = re.findall(r"DROP SCHEMA (?:IF EXISTS )?cases CASCADE", remote)
    assert len(drops) == remote.count("DROP ") == 2, drops
    assert remote.index("pg_dump") < remote.index("DROP SCHEMA cases CASCADE")
    assert "--schema=cases" in remote
    # the read models are only counted
    assert not re.search(r"(?i)\b(insert|update|delete|truncate|drop|alter|create)\b[^\n]*\bbank\.", remote)
    assert re.findall(r"select [^'\"]*bank\.\w+", remote) == [
        "select count(*) from bank.customers",
        "select count(*) from bank.transactions",
    ]


def test_report_changes_nothing(vm):
    run, state, _root = vm
    result = run("report")
    assert result.returncode == 0, result.stderr
    assert "disputes | 3" in result.stdout and "customers | 100" in result.stdout
    calls = (state / "calls").read_text()
    assert "DROP" not in calls and "pg_dump" not in calls and "restart" not in calls


def test_apply_dumps_then_drops_then_restarts_and_checks(vm):
    run, state, root = vm
    result = run("apply")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [line for line in (state / "calls").read_text().splitlines()]
    dump = next(i for i, c in enumerate(calls) if "pg_dump" in c)
    drop = next(i for i, c in enumerate(calls) if "DROP SCHEMA cases CASCADE" in c or "-c DROP" in c)
    restart = next(i for i, c in enumerate(calls) if c.startswith("docker restart minsky-api-1"))
    assert dump < drop < restart
    (backup,) = list((root / "backups").glob("cases-*.sql"))
    assert "CREATE TABLE" in backup.read_text() and oct(backup.stat().st_mode & 0o777) == "0o600"
    assert "disputes | 0" in result.stdout.split("after:")[1] and "bank.* unchanged" in result.stdout
    assert "restoring" not in result.stderr


def test_a_dump_with_no_tables_stops_before_deleting_anything(vm):
    run, state, root = vm
    (state / "empty_dump").touch()
    result = run("apply")
    assert result.returncode == 4 and "nothing was deleted" in result.stderr
    assert "DROP" not in (state / "calls").read_text()
    assert not list((root / "backups").glob("cases-*.sql"))


def test_if_the_api_does_not_recreate_the_schema_the_dump_is_restored(vm):
    run, state, _root = vm
    (state / "api_creates_schema").unlink()
    result = run("apply")
    assert result.returncode == 4
    assert "reset failed" in result.stderr and "restoring" in result.stderr
    assert (state / "schema").exists() and (state / "rows").read_text().strip() == "3"  # back as it was


def test_if_the_api_is_not_healthy_the_dump_is_restored(vm):
    run, state, _root = vm
    (state / "health_ok").unlink()
    result = run("apply")
    assert result.returncode == 4 and "did not become healthy" in result.stderr
    assert (state / "schema").exists() and (state / "rows").read_text().strip() == "3"


def test_a_wrong_database_is_refused_before_anything_happens(vm):
    run, state, _root = vm
    # bank.customers empty -> refuse; the stand-in always answers 100, so change its answer
    original = (state.parent / "vmbin" / "docker").read_text()
    (state.parent / "vmbin" / "docker").write_text(original.replace("echo 100", "echo 0"))
    result = run("apply")
    assert result.returncode == 3 and "wrong database" in result.stderr
    assert "DROP" not in (state / "calls").read_text()
