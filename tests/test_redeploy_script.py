"""infra/redeploy_smoke.sh: what leaves the workstation, and what the remote side may do.

ssh is replaced by a stub that records its arguments and stdin, so nothing here touches a VM.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "infra" / "redeploy_smoke.sh"

SSH_STUB = """#!/bin/sh
n=$(( $(cat "$STUB_LOG/n" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_LOG/n"
printf '%s\\n' "$@" > "$STUB_LOG/call$n.args"
cat > "$STUB_LOG/call$n.stdin"
"""

ALLOWED_PREFIXES = (
    "pyproject.toml",
    "uv.lock",
    "backend",
    "prompts",
    "frontend",
    "infra/caddy",
    "compose.yaml",
    "compose.demo.yaml",
)
FORBIDDEN_FRAGMENTS = (".env", ".git/", ".pem", "node_modules", "data/", "terraform.tfvars", "test-tokens")
DESTRUCTIVE = (
    "down -v",
    "--volumes",
    "volume rm",
    "docker volume",
    "system prune",
    "image prune",
    "rm -rf /opt/minsky",
)


@pytest.fixture
def stub(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(SSH_STUB)
    ssh.chmod(0o755)
    log = tmp_path / "log"
    log.mkdir()
    key = tmp_path / "key.pem"
    key.write_text("not a real key")
    # a minimal environment: a failing test prints this fixture, and it must not dump the caller's variables
    env = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(tmp_path),
        "STUB_LOG": str(log),
    }
    return env, log, key


def _run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)


def _calls(log: Path) -> int:
    marker = log / "n"
    return int(marker.read_text()) if marker.exists() else 0


def test_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_host_and_key_are_required(stub):
    env, log, _key = stub
    result = _run(env)
    assert result.returncode == 2
    assert "--host" in result.stderr
    assert _calls(log) == 0


def test_dry_run_prints_the_plan_and_runs_nothing(stub):
    env, log, _key = stub
    result = _run(env, "--dry-run", "--host", "ubuntu@vm.invalid", "--key", "/does/not/exist")
    assert result.returncode == 0, result.stderr
    assert "plan: deploy" in result.stdout and "tracked files only" in result.stdout
    assert "never uploaded" in result.stdout
    assert _calls(log) == 0


def test_deploy_runs_preflight_uploads_then_deploys(stub):
    env, log, key = stub
    result = _run(env, "--host", "ubuntu@vm.invalid", "--key", str(key))
    assert result.returncode == 0, result.stderr
    assert _calls(log) == 3
    first = (log / "call1.args").read_text()
    assert "ubuntu@vm.invalid" in first and "preflight" in first
    assert str(key) in first and "IdentitiesOnly=yes" in first
    assert "tar -xf -" in (log / "call2.args").read_text()
    assert "deploy" in (log / "call3.args").read_text().splitlines()


def test_only_tracked_release_paths_leave_the_workstation(stub):
    env, log, key = stub
    _run(env, "--host", "ubuntu@vm.invalid", "--key", str(key))
    tar = tarfile.open(fileobj=io.BytesIO((log / "call2.stdin").read_bytes()))
    names = tar.getnames()
    assert names, "empty upload"
    parents = {"infra"}  # directory entries leading to an allowed path
    for name in names:
        assert name in parents or name.startswith(ALLOWED_PREFIXES), name
        assert not any(fragment in name for fragment in FORBIDDEN_FRAGMENTS), name


def test_remote_side_never_destroys_data_or_config(stub):
    env, log, key = stub
    _run(env, "--host", "ubuntu@vm.invalid", "--key", str(key))
    remote = (log / "call3.stdin").read_text()
    for fragment in DESTRUCTIVE:
        assert fragment not in remote, fragment
    # compose always runs as the same project, with absolute env files, so volumes and names are stable
    assert "docker compose -p minsky" in remote
    assert '--env-file "$ROOT/runtime.env"' in remote
    # the only config file it writes is images.env
    assert 'runtime.env"' in remote and '> "$ROOT/runtime.env' not in remote
    assert '> "$ROOT/db.env' not in remote


def test_rollback_uses_the_rollback_phase(stub):
    env, log, key = stub
    result = _run(env, "--host", "ubuntu@vm.invalid", "--key", str(key), "--rollback")
    assert result.returncode == 0, result.stderr
    assert _calls(log) == 1
    assert "rollback" in (log / "call1.args").read_text().splitlines()


def test_an_unknown_ref_is_rejected_before_any_ssh(stub):
    env, log, key = stub
    result = _run(env, "--host", "ubuntu@vm.invalid", "--key", str(key), "--ref", "no-such-ref-anywhere")
    assert result.returncode != 0
    assert _calls(log) == 0


def test_remote_script_is_valid_bash(stub, tmp_path):
    env, log, key = stub
    _run(env, "--host", "ubuntu@vm.invalid", "--key", str(key))
    remote = tmp_path / "remote.sh"
    remote.write_bytes((log / "call1.stdin").read_bytes())
    assert subprocess.run(["bash", "-n", str(remote)]).returncode == 0


def test_print_remote_needs_no_host_and_matches_what_is_sent(stub):
    env, log, key = stub
    printed = _run(env, "--print-remote")
    assert printed.returncode == 0 and 'case "$PHASE" in' in printed.stdout
    _run(env, "--host", "ubuntu@vm.invalid", "--key", str(key))
    assert (log / "call1.stdin").read_text() == printed.stdout
