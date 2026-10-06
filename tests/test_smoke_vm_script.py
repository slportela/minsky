"""infra/smoke_vm.sh: what it refuses, what it never prints, and what it hands to the reviewed scripts.

The AWS CLI, ssh-keyscan, ssh-keygen, ssh and curl are replaced by stubs, so nothing here touches AWS or a VM.
The script runs from a throwaway git repo (it finds its repo from its own path), with a stand-in for
infra/redeploy_smoke.sh that only records its arguments.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "infra" / "smoke_vm.sh"

FINGERPRINT = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
SECRET_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nTHE-PRIVATE-KEY-MATERIAL\n-----END OPENSSH PRIVATE KEY-----"

AWS_STUB = """#!/bin/sh
echo "aws $*" >> "$STUB_LOG/aws.calls"
case "$*" in
  *get-static-ip*) echo 203.0.113.7 ;;
  *get-distributions*) echo dxxxxxxxxxxxxx.cloudfront.example ;;
  *get-instance-access-details*)
    cat "$STUB_LOG/details.json" ;;
  *) echo "unexpected aws call: $*" >&2; exit 9 ;;
esac
"""
# ssh-keyscan prints a host key line; ssh-keygen -lf - turns it into the fingerprint the test pins.
KEYSCAN_STUB = '#!/bin/sh\necho "203.0.113.7 ssh-ed25519 AAAAFAKEHOSTKEY"\n'
KEYGEN_STUB = '#!/bin/sh\ncat > /dev/null\necho "256 $FAKE_FP 203.0.113.7 (ED25519)"\n'
SSH_STUB = """#!/bin/sh
echo "ssh $*" >> "$STUB_LOG/ssh.calls"
cat > /dev/null
echo "ssh-stub-output"
"""
CURL_STUB = """#!/bin/sh
case "$*" in
  *"-w"*) echo 200 ;;
  *) echo '{"status":"ok"}' ;;
esac
"""
REDEPLOY_STUB = """#!/bin/sh
{ printf 'redeploy'; for a in "$@"; do printf ' [%s]' "$a"; done; echo; } >> "$STUB_LOG/redeploy.calls"
"""


def _stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _git(cwd: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(
        ["git", *args], cwd=cwd, env={**os.environ, **env}, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def box(tmp_path):
    repo = tmp_path / "repo"
    (repo / "infra").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "infra" / "smoke_vm.sh")
    _stub(repo / "infra" / "redeploy_smoke.sh", REDEPLOY_STUB)
    _stub(repo / "infra" / "reset_cases_smoke.sh", REDEPLOY_STUB.replace("redeploy", "reset"))
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "on main")
    on_main = _git(repo, "rev-parse", "--short=12", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side")
    (repo / "b.txt").write_text("b")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "not on main")
    off_main = _git(repo, "rev-parse", "--short=12", "HEAD")
    _git(repo, "checkout", "-q", "main")

    log = tmp_path / "log"
    log.mkdir()
    (log / "details.json").write_text(
        json.dumps(
            {
                "accessDetails": {
                    "ipAddress": "203.0.113.7",
                    "username": "ubuntu",
                    "privateKey": SECRET_KEY,
                    "certKey": "ssh-ed25519-cert-v01@openssh.com FAKECERT",
                    "hostKeys": [],
                }
            }
        )
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir / "aws", AWS_STUB)
    _stub(bin_dir / "ssh", SSH_STUB)
    _stub(bin_dir / "curl", CURL_STUB)
    # the script calls ssh-keyscan, ssh-keygen and curl by absolute path under /usr/bin: point it at the stubs
    script = (repo / "infra" / "smoke_vm.sh").read_text()
    for name in ("ssh-keyscan", "ssh-keygen", "curl"):
        script = script.replace(f"/usr/bin/{name}", str(bin_dir / name))
    script = script.replace("exec /usr/bin/ssh", f"exec {bin_dir / 'ssh'}")
    (repo / "infra" / "smoke_vm.sh").write_text(script)
    _stub(bin_dir / "ssh-keyscan", KEYSCAN_STUB)
    _stub(bin_dir / "ssh-keygen", KEYGEN_STUB)
    env = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(tmp_path),
        "STUB_LOG": str(log),
        "FAKE_FP": FINGERPRINT,
        "SMOKE_MAIN_REF": "main",
        "SMOKE_HOST_FP_FILE": str(tmp_path / "no-such-file"),
    }

    def run(*args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(repo / "infra" / "smoke_vm.sh"), *args],
            cwd=repo,
            env={**env, **extra},
            capture_output=True,
            text=True,
            timeout=60,
        )

    return run, log, on_main, off_main


def _calls(log: Path, name: str) -> list[str]:
    path = log / name
    return path.read_text().splitlines() if path.exists() else []


def test_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0


def test_a_command_is_required_and_an_unknown_one_is_refused(box):
    run, log, *_ = box
    assert run().returncode == 2
    result = run("destroy-everything")
    assert result.returncode == 2 and "unknown command" in result.stderr
    assert not _calls(log, "aws.calls")


def test_without_a_pinned_fingerprint_nothing_is_sent(box):
    run, log, *_ = box
    result = run("status")
    assert result.returncode == 6
    assert "no host key of the server matches a pinned fingerprint" in result.stderr
    assert FINGERPRINT in result.stderr  # what the network showed, so the owner can compare it
    assert "Do not pin what the network shows you" in result.stderr
    assert _calls(log, "ssh.calls") == []


def test_a_wrong_pinned_fingerprint_is_refused_too(box):
    run, log, *_ = box
    assert run("status", SMOKE_HOST_FP="SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB").returncode == 6
    assert _calls(log, "ssh.calls") == []


def test_the_pinned_fingerprint_can_come_from_the_file_and_the_private_key_is_never_printed(box, tmp_path):
    run, log, *_ = box
    pin = tmp_path / "pin"
    pin.write_text(FINGERPRINT + "\n")
    result = run("status", SMOKE_HOST_FP_FILE=str(pin))
    assert result.returncode == 0, result.stderr
    assert "ssh-stub-output" in result.stdout
    for text in (result.stdout, result.stderr):
        assert "PRIVATE-KEY-MATERIAL" not in text and "BEGIN OPENSSH" not in text
    assert any("get-instance-access-details" in call for call in _calls(log, "aws.calls"))


def test_the_temporary_credentials_are_deleted_when_it_ends(box, tmp_path):
    run, *_ = box
    before = set(Path("/tmp").glob("tmp.*")) | set(Path(os.environ.get("TMPDIR", "/tmp")).glob("tmp.*"))
    run("status", SMOKE_HOST_FP=FINGERPRINT)
    after = set(Path("/tmp").glob("tmp.*")) | set(Path(os.environ.get("TMPDIR", "/tmp")).glob("tmp.*"))
    leftovers = [p for p in after - before if (p / "key").exists()]
    assert not leftovers


def test_only_commits_of_main_are_deployed(box):
    run, log, on_main, off_main = box
    result = run("deploy", off_main, SMOKE_HOST_FP=FINGERPRINT)
    assert result.returncode == 2 and "is not on main" in result.stderr
    assert not _calls(log, "redeploy.calls") and not _calls(log, "aws.calls")  # refused before asking AWS for anything
    assert run("deploy", "no-such-ref", SMOKE_HOST_FP=FINGERPRINT).returncode != 0
    assert run("deploy").returncode == 2  # a ref is required: there is no default


def test_deploy_hands_the_commit_to_the_reviewed_script_then_verifies(box):
    run, log, on_main, _off = box
    result = run("deploy", on_main, SMOKE_HOST_FP=FINGERPRINT)
    assert result.returncode in (0, 1), result.stderr  # verify may fail against the stubs; the hand-off is what counts
    (call,) = _calls(log, "redeploy.calls")
    assert f"[--ref] [{on_main}]" in call and "[--host] [ubuntu@203.0.113.7]" in call
    assert "[--rollback]" not in call and "[--key]" in call
    assert "verify" in result.stdout


def test_a_dry_run_asks_aws_for_nothing(box):
    run, log, on_main, _off = box
    result = run("deploy", on_main, "--dry-run")
    assert result.returncode == 0, result.stderr
    (call,) = _calls(log, "redeploy.calls")
    assert "[--dry-run]" in call and f"[{on_main}]" in call
    assert not _calls(log, "aws.calls") and not _calls(log, "ssh.calls")


def test_rollback_uses_the_rollback_flag(box):
    run, log, *_ = box
    run("rollback", SMOKE_HOST_FP=FINGERPRINT)
    (call,) = _calls(log, "redeploy.calls")
    assert "[--rollback]" in call


def test_sql_is_select_only_and_one_statement(box):
    run, log, *_ = box
    for bad in (
        "delete from cases.case_queue",
        "update x set a = 1",
        "drop schema cases cascade",
        "select 1; drop schema cases",
    ):
        result = run("sql", bad, SMOKE_HOST_FP=FINGERPRINT)
        assert result.returncode == 2, bad
    assert not _calls(log, "ssh.calls")
    ok = run("sql", "select count(*) from cases.case_queue", SMOKE_HOST_FP=FINGERPRINT)
    assert ok.returncode == 0, ok.stderr
    assert "set transaction read only" in "\n".join(_calls(log, "ssh.calls"))  # the database refuses writes too


def test_cases_report_never_applies(box):
    run, log, *_ = box
    run("cases-report", SMOKE_HOST_FP=FINGERPRINT)
    (call,) = _calls(log, "reset.calls")
    assert "--apply" not in call


def test_the_script_has_no_way_to_delete_cases_or_write_credentials():
    text = SCRIPT.read_text()
    for fragment in ("--apply", "DROP ", "TRUNCATE", "rm -rf /opt", "docker volume", "down -v", "runtime.env >"):
        assert fragment not in text.replace("(infra/reset_cases_smoke.sh --apply)", ""), fragment
