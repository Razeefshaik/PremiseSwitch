"""Clearance conformance and real Git hook tests; no database or services."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

import pytest
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
    {"changed": [{"fact": "missing.fact", "new": 72}]}, {"changed": []},
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
