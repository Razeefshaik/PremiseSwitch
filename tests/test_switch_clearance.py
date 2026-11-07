"""Clearance conformance and real Git hook tests; no database or services."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from premise_switch.clearance import Clearance, DELETED, canonical, load_or_create_key, public_key_pem, verify

ROOT = Path(__file__).resolve().parents[1]
NOW = 1_793_000_000
FACTS = ROOT / "sandbox/kestrel-platform/facts.yaml"
CHANGES = [
    {"file": "config/partners/paylane.yaml", "key": "retry.window_hours", "value": 72},
    {"file": "config/webhookingress.yaml", "key": "dedupe.ttl_hours", "value": 72},
    {"file": "config/platform/redis.yaml", "key": "limits.max_ttl_hours", "value": 72},
]


def clear_result():
    return {"verdict": "CLEAR_WITHIN_SCOPE", "graph_rev": 1, "reasons": [], "not_holding": [],
            "decisions": [], "changed": [
                {"fact": "paylane.retry_window_hours", "old": 24, "new": 72, "controlled_by": "external"},
                {"fact": "webhook.dedupe_ttl_hours", "old": 24, "new": 72, "controlled_by": "us"},
                {"fact": "redis.max_ttl_hours", "old": 24, "new": 72, "controlled_by": "us"}]}


@pytest.fixture
def issuer():
    return Clearance(Ed25519PrivateKey.generate(), facts_path=FACTS)


def receipt(issuer, result=None, now=NOW):
    return issuer.issue(result or clear_result(), room="payments", thread="m1", now=now)


def test_key_is_durable_private_and_exports_only_public_pem(tmp_path):
    key = load_or_create_key(tmp_path)
    assert public_key_pem(load_or_create_key(tmp_path)) == public_key_pem(key)
    assert public_key_pem(key).startswith(b"-----BEGIN PUBLIC KEY-----")
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "clearance.key").stat().st_mode) == 0o600


def test_roundtrip_is_canonical_and_has_no_mentions(issuer):
    token = receipt(issuer)
    assert token.startswith("pc1.") and "@" not in token and "=" not in token
    verdict = verify(token, issuer.public_key_pem(), list(reversed(CHANGES)), NOW)
    assert verdict.ok and verdict.reason == "valid"
    assert verdict.payload["changes"] == sorted(CHANGES, key=lambda row: (row["file"], row["key"]))
    assert verdict.payload["graph_rev"] == 1 and verdict.payload["room"] == "payments"
    assert base64.urlsafe_b64decode(token.split(".")[1] + "===") == canonical(verdict.payload)
    reordered = clear_result()
    reordered["changed"].reverse()
    assert receipt(issuer, reordered) == token
    assert receipt(issuer, now=datetime.fromtimestamp(NOW, timezone.utc)) == token


@pytest.mark.parametrize("verdict", ["BLOCKED", "NEEDS_REVIEW", "ERROR", "CLEAR", None])
def test_only_clear_can_issue(issuer, verdict):
    result = clear_result()
    result["verdict"] = verdict
    assert issuer.issue(result, room="payments", thread=None, now=NOW) is None


@pytest.mark.parametrize("change", [
    {"changed": [{"fact": "missing.fact", "new": 72}]},
    {"not_holding": [{"id": "G_BROKEN"}]}, {"reasons": [{"code": "R_UNTRACKED"}]},
    {"graph_rev": True}, {"guarantees": [{"status": "unknown"}]}, {"reasons": None},
])
def test_incomplete_unmapped_or_contradictory_clear_gets_no_receipt(issuer, change):
    assert issuer.issue({**clear_result(), **change}, room="payments", thread=None, now=NOW) is None


@pytest.mark.parametrize("changes,reason", [
    (CHANGES[:-1], "changes do not match staged config"),
    (CHANGES + [{"file": "config/extra.yaml", "key": "x", "value": 1}], "changes do not match staged config"),
    ([{**CHANGES[0], "value": 73}, *CHANGES[1:]], "changes do not match staged config"),
    ([{**CHANGES[0], "file": "config/elsewhere.yaml"}, *CHANGES[1:]], "changes do not match staged config"),
    ([{**CHANGES[0], "value": True}, *CHANGES[1:]], "changes do not match staged config"),
])
def test_exact_change_set_required(issuer, changes, reason):
    assert verify(receipt(issuer), issuer.public_key_pem(), changes, NOW).reason == reason


def test_bad_signature_tampering_expiry_and_future(issuer):
    token = receipt(issuer)
    assert verify(token, public_key_pem(Ed25519PrivateKey.generate()), CHANGES, NOW).reason == "invalid signature"
    version, body, signature = token.split(".")
    altered = json.loads(base64.urlsafe_b64decode(body + "==="))
    altered["changes"][0]["value"] = 73
    encoded = base64.urlsafe_b64encode(canonical(altered)).decode().rstrip("=")
    assert verify(f"{version}.{encoded}.{signature}", issuer.public_key_pem(), CHANGES, NOW).reason == "invalid signature"
    assert verify(token, issuer.public_key_pem(), CHANGES, NOW + 86401).reason == "receipt expired"
    assert verify(token, issuer.public_key_pem(), CHANGES, NOW - 1).reason == "receipt issued in the future"
    assert verify(token, issuer.public_key_pem(), CHANGES, NOW + 86400).ok
    assert not verify("pc1.nope.invalid@signature", issuer.public_key_pem(), CHANGES, NOW)


def test_deletion_is_distinct_from_yaml_null(issuer):
    result = clear_result()
    result["changed"] = [{"fact": "paylane.retry_window_hours", "new": None}]
    token = receipt(issuer, result)
    deletion = [{**CHANGES[0], "value": DELETED}]
    assert verify(token, issuer.public_key_pem(), deletion, NOW).ok
    assert not verify(token, issuer.public_key_pem(), [{**CHANGES[0], "value": None}], NOW)


@pytest.fixture
def git_repo(tmp_path, issuer):
    root = tmp_path / "repo"
    root.mkdir()
    environment = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
                   "PYTHONPATH": "", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}

    class Repo:
        def run(self, *args, check=True):
            result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                    encoding="utf-8", env=environment, timeout=30)
            if check and result.returncode:
                pytest.fail("temporary Git operation failed: " + result.stderr)
            return result

        def write(self, relative, text):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        def stage_fix(self):
            self.write("config/partners/paylane.yaml", "retry:\n  window_hours: 72\ndelivery:\n  timeout_ms: 5000\n")
            self.write("config/webhookingress.yaml", "dedupe:\n  ttl_hours: 72\n")
            self.write("config/platform/redis.yaml", "limits:\n  max_ttl_hours: 72\n")
            self.run("add", "--", "config/")

        def commit(self, token=None, *, body=None):
            text = body or "apply config change"
            if token is not None:
                text += "\n\nPremise-Clearance: " + token + "\n"
            path = tmp_path / "commit-message"
            path.write_text(text, encoding="utf-8")
            return self.run("commit", "-q", "-F", str(path), check=False)

    repo = Repo()
    repo.root = root
    repo.issuer = issuer
    repo.run("init", "-q", "--initial-branch=main", "--template=")
    for key, value in {"user.name": "Clearance test", "user.email": "clearance@example.invalid",
                       "commit.gpgsign": "false", "core.autocrlf": "false", "core.hooksPath": ".git/hooks"}.items():
        repo.run("config", key, value)
    repo.write("config/partners/paylane.yaml", "retry:\n  window_hours: 24\ndelivery:\n  timeout_ms: 5000\n")
    repo.write("config/webhookingress.yaml", "dedupe:\n  ttl_hours: 24\n")
    repo.write("config/platform/redis.yaml", "limits:\n  max_ttl_hours: 24\n")
    public = root / ".premise/clearance.pub"
    public.parent.mkdir()
    public.write_bytes(issuer.public_key_pem())
    repo.run("add", ".")
    repo.run("commit", "-q", "-m", "baseline")
    hook = root / ".git/hooks/commit-msg"
    hook.parent.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "demo/hooks/commit-msg", hook)
    hook.chmod(0o755)
    return repo


def live_receipt(issuer, result=None, *, age=0):
    return receipt(issuer, result, now=int(time.time()) - age)


def test_real_commit_accepts_correct_receipt_from_staged_index(git_repo):
    git_repo.stage_fix()
    # A worktree edit must not change the assessed/staged snapshot.
    git_repo.write("config/partners/paylane.yaml", "retry:\n  window_hours: 96\n")
    outcome = git_repo.commit(live_receipt(git_repo.issuer))
    assert outcome.returncode == 0, outcome.stderr
    assert "window_hours: 72" in git_repo.run("show", "HEAD:config/partners/paylane.yaml").stdout


@pytest.mark.parametrize("case", [
    "no_receipt", "another_key", "tampered", "expired", "extra_changed_key", "missing_changed_key",
    "changed_value", "different_file", "malformed_yaml", "missing_public_key", "bad_public_key",
    "extra_receipt_key", "missing_receipt_key", "duplicate_trailer", "duplicate_yaml_key", "dotted_yaml_key",
])
def test_real_commit_refuses_invalid_clearance(git_repo, case, capsys):
    git_repo.stage_fix()
    before = git_repo.run("rev-parse", "HEAD").stdout
    token = live_receipt(git_repo.issuer)
    if case == "no_receipt":
        token = None
    elif case == "another_key":
        token = live_receipt(Clearance(Ed25519PrivateKey.generate(), facts_path=FACTS))
    elif case == "tampered":
        version, encoded, signature = token.split(".")
        payload = json.loads(base64.urlsafe_b64decode(encoded + "==="))
        payload["graph_rev"] += 1
        token = f"{version}.{base64.urlsafe_b64encode(canonical(payload)).decode().rstrip('=')}.{signature}"
    elif case == "expired":
        token = live_receipt(git_repo.issuer, age=86401)
    elif case == "extra_changed_key":
        path = git_repo.root / "config/webhookingress.yaml"
        path.write_text(path.read_text() + "unassessed: 1\n", encoding="utf-8")
    elif case == "missing_changed_key":
        git_repo.write("config/platform/redis.yaml", "limits:\n  max_ttl_hours: 24\n")
    elif case == "changed_value":
        git_repo.write("config/webhookingress.yaml", "dedupe:\n  ttl_hours: 96\n")
    elif case == "different_file":
        git_repo.run("mv", "config/partners/paylane.yaml", "config/partners/other.yaml")
    elif case == "malformed_yaml":
        git_repo.write("config/webhookingress.yaml", "dedupe: [unterminated")
    elif case == "missing_public_key":
        (git_repo.root / ".premise/clearance.pub").unlink()
    elif case == "bad_public_key":
        (git_repo.root / ".premise/clearance.pub").write_text("not a public key", encoding="utf-8")
    elif case == "extra_receipt_key":
        result = clear_result()
        result["changed"].append({"fact": "paylane.delivery_timeout_ms", "new": 6000})
        token = live_receipt(git_repo.issuer, result)
    elif case == "missing_receipt_key":
        result = clear_result()
        result["changed"].pop()
        token = live_receipt(git_repo.issuer, result)
    elif case == "duplicate_yaml_key":
        git_repo.write("config/webhookingress.yaml", "dedupe:\n  ttl_hours: 999\n  ttl_hours: 72\n")
    elif case == "dotted_yaml_key":
        git_repo.write("config/webhookingress.yaml", "dedupe.ttl_hours: 72\n")
    git_repo.run("add", "--", "config/")
    body = f"apply config\n\nPremise-Clearance: {token}\nPremise-Clearance: {token}\n" if case == "duplicate_trailer" else None
    outcome = git_repo.commit(None if body else token, body=body)
    assert outcome.returncode != 0
    assert "Premise clearance refused:" in outcome.stderr and "Ask @premise to check these exact staged config changes" in outcome.stderr
    assert git_repo.run("rev-parse", "HEAD").stdout == before
    if case == "no_receipt":
        print("Actual hook refusal:\n" + outcome.stderr.strip())


def test_non_config_commit_needs_no_receipt_or_public_key(git_repo):
    (git_repo.root / ".premise/clearance.pub").unlink()
    git_repo.write("README.md", "Documentation only\n")
    git_repo.run("add", "README.md")
    outcome = git_repo.commit()
    assert outcome.returncode == 0, outcome.stderr


@pytest.mark.parametrize("entire_file", [False, True])
def test_real_commit_supports_deleted_keys_and_files(git_repo, entire_file):
    result = clear_result()
    result["changed"] = [{"fact": "paylane.retry_window_hours", "new": None}]
    if entire_file:
        git_repo.run("rm", "config/partners/paylane.yaml")
        result["changed"].append({"fact": "paylane.delivery_timeout_ms", "new": None})
    else:
        git_repo.write("config/partners/paylane.yaml", "delivery:\n  timeout_ms: 5000\n")
        git_repo.run("add", "config/partners/paylane.yaml")
    outcome = git_repo.commit(live_receipt(git_repo.issuer, result))
    assert outcome.returncode == 0, outcome.stderr


@pytest.mark.parametrize("value", [True, 1.0])
def test_hook_preserves_scalar_types(git_repo, value):
    result = clear_result()
    result["changed"] = [{"fact": "paylane.retry_window_hours", "new": 1}]
    git_repo.write("config/partners/paylane.yaml", yaml.safe_dump({"retry": {"window_hours": value}, "delivery": {"timeout_ms": 5000}}))
    git_repo.run("add", "config/partners/paylane.yaml")
    outcome = git_repo.commit(live_receipt(git_repo.issuer, result))
    assert outcome.returncode != 0 and "changes do not match staged config" in outcome.stderr


def test_semantically_unchanged_config_uses_an_empty_receipt(git_repo):
    path = git_repo.root / "config/partners/paylane.yaml"
    path.write_text("# formatting only\n" + path.read_text(), encoding="utf-8")
    git_repo.run("add", "config/partners/paylane.yaml")
    result = {**clear_result(), "changed": []}
    outcome = git_repo.commit(live_receipt(git_repo.issuer, result))
    assert outcome.returncode == 0, outcome.stderr


@pytest.mark.parametrize("verdict", ["CLEAR_WITHIN_SCOPE", "BLOCKED", "NEEDS_REVIEW", "ERROR"])
def test_answerer_only_appends_receipt_on_clear(issuer, verdict):
    from premise_switch.answer import Answerer
    from switch_fakes import FakeTools, message

    calls = []

    class Counted:
        def issue(self, result, **context):
            calls.append(context)
            return issuer.issue(result, **context)

    result = {**clear_result(), "verdict": verdict}
    reply = Answerer(FakeTools({"check_change": result}), clock=lambda: 0, clearance=Counted()).respond(
        message("check paylane.retry_window_hours = 72", thread_id="thread-1"))
    assert ("Premise-Clearance: " in reply.body) == (verdict == "CLEAR_WITHIN_SCOPE")
    assert len(calls) == (1 if verdict == "CLEAR_WITHIN_SCOPE" else 0)
    if calls:
        token = reply.body.split("Premise-Clearance: ")[1]
        checked = verify(token, issuer.public_key_pem(), CHANGES, time.time())
        assert checked.ok and checked.payload["thread"] == "thread-1"
        assert checked.payload["room"] == "room-1" and checked.payload["issued_at"] > 1_000_000


def test_answerer_receipt_is_verbatim_after_escaping(issuer):
    from premise_switch.answer import Answerer
    from switch_fakes import FakeTools, message

    result = {**clear_result(), "sources": [{"path": "notes/@coder.md", "line": 1}]}
    reply = Answerer(FakeTools({"check_change": result}), clearance=issuer).respond(
        message("check paylane.retry_window_hours = 72", room_id="room@name", thread_id="thread@name"))
    body, token = reply.body.rsplit("\nPremise-Clearance: ", 1)
    assert "@\u200bcoder" in body and "@coder" not in body and "@" not in token
    checked = verify(token, issuer.public_key_pem(), CHANGES, time.time())
    assert checked.ok and checked.payload["room"] == "room@name" and checked.payload["thread"] == "thread@name"


@pytest.mark.parametrize("change", [
    {"not_holding": [{"id": "G_BROKEN"}]}, {"reasons": [{"code": "R_UNTRACKED"}]},
    {"changed": [{"fact": "no.mapping", "new": 72}]}, {"graph_rev": None},
])
def test_unavailable_untracked_and_unmapped_results_never_get_receipts(issuer, change):
    from premise_switch.answer import Answerer
    from switch_fakes import FakeTools, message

    reply = Answerer(FakeTools({"check_change": {**clear_result(), **change}}), clearance=issuer).respond(
        message("check paylane.retry_window_hours = 72"))
    assert "Premise-Clearance:" not in reply.body


@pytest.mark.parametrize("bad", ["pc1.abc.def@coder", "pc1.abc.def\n@coder", "not a receipt", RuntimeError("private details")])
def test_bad_signers_fail_unavailable_without_mentions_or_details(bad):
    from premise_switch.answer import Answerer
    from premise_switch.format import UNAVAILABLE
    from switch_fakes import FakeTools, message

    class Broken:
        def issue(self, *args, **kwargs):
            if isinstance(bad, Exception):
                raise bad
            return bad

    reply = Answerer(FakeTools({"check_change": clear_result()}), clearance=Broken()).respond(
        message("check paylane.retry_window_hours = 72"))
    assert reply.body == UNAVAILABLE


def test_answerer_receipt_passes_the_real_commit_hook(git_repo):
    from premise_switch.answer import Answerer
    from switch_fakes import FakeTools, message

    git_repo.stage_fix()
    tools = FakeTools({"check_change": clear_result()})
    reply = Answerer(tools, clearance=git_repo.issuer).respond(message(
        "check paylane.retry_window_hours = 72, webhook.dedupe_ttl_hours = 72, redis.max_ttl_hours = 72"))
    token = reply.body.rsplit("\nPremise-Clearance: ", 1)[1]
    outcome = git_repo.commit(token)
    assert outcome.returncode == 0, outcome.stderr


def test_answerer_without_clearance_keeps_default_behavior():
    from premise_switch.answer import Answerer
    from switch_fakes import FakeTools, message

    tools = FakeTools({"check_change": clear_result()})
    reply = Answerer(tools, clock=lambda: 0).respond(message("check paylane.retry_window_hours = 72"))
    assert reply.body.startswith("CLEAR_WITHIN_SCOPE\n") and "Premise-Clearance:" not in reply.body
    assert reply.body.endswith("deterministic · graph rev 1")
