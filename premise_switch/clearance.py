"""Ed25519 receipts for exact config changes; no model, database or network."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
import yaml

DELETED = {"$delete": True}
TOKEN = re.compile(r"pc1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
FIELDS = {"changes", "graph_rev", "verdict", "issued_at", "room", "thread"}


def _json_value(value):
    if value is None or type(value) in {str, int, bool}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    raise ValueError("value must be finite JSON without key coercion")


def canonical(value) -> bytes:
    _json_value(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def changes_sorted(changes) -> list[dict]:
    if not isinstance(changes, list):
        raise ValueError("changes must be a list")
    seen, output = set(), []
    for row in changes:
        if not isinstance(row, dict) or set(row) != {"file", "key", "value"}:
            raise ValueError("invalid change fields")
        path, key = row["file"], row["key"]
        if (not isinstance(path, str) or not path.startswith("config/") or "\\" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or any(ord(c) < 32 for c in path) or ":" in path):
            raise ValueError("invalid config path")
        if not isinstance(key, str) or not key or any(not p for p in key.split(".")) or any(ord(c) < 32 for c in key):
            raise ValueError("invalid dotted key")
        pair = (path, key)
        if pair in seen:
            raise ValueError("duplicate changed key")
        seen.add(pair)
        canonical(row["value"])  # finite JSON values only
        output.append({"file": path, "key": key, "value": row["value"]})
    return sorted(output, key=lambda row: (row["file"], row["key"]))


def _epoch(value) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("time must include a timezone")
        value = value.timestamp()
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ValueError("invalid time")
    return float(value)


def load_or_create_key(state_dir: str | Path) -> Ed25519PrivateKey:
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "clearance.key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink():
            raise ValueError("clearance key must not be a symlink")
        os.chmod(path, 0o600)
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("clearance key must be Ed25519")
        return key
    key = Ed25519PrivateKey.generate()
    with os.fdopen(fd, "wb") as handle:
        os.chmod(path, 0o600)
        handle.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
        handle.flush()
        os.fsync(handle.fileno())
    return key


def public_key_pem(key: Ed25519PrivateKey | Ed25519PublicKey) -> bytes:
    public = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    if not isinstance(public, Ed25519PublicKey):
        raise ValueError("clearance public key must be Ed25519")
    return public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


class Clearance:
    def __init__(self, key: Ed25519PrivateKey, *, facts_path: str | Path):
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("clearance key must be Ed25519")
        self.key = key
        self.facts = yaml.safe_load(Path(facts_path).read_text(encoding="utf-8"))["facts"]

    def public_key_pem(self) -> bytes:
        return public_key_pem(self.key)

    def issue(self, result: dict, *, room: str, thread: str | None, now) -> str | None:
        if not isinstance(result, dict) or result.get("verdict") != "CLEAR_WITHIN_SCOPE":
            return None
        try:
            if (type(result.get("graph_rev")) is not int or result["graph_rev"] < 0
                    or any(not isinstance(result.get(k), list) for k in ("reasons", "not_holding", "decisions", "changed"))
                    or result["not_holding"]
                    or any(g.get("status") != "holds" for g in result.get("guarantees", []))
                    or any(r.get("code") not in {"R_ALL_HOLD", "R_NO_TRACKED_FACTS"} for r in result["reasons"])
                    or not isinstance(room, str) or not room
                    or thread is not None and not isinstance(thread, str)):
                return None
            changes = []
            for row in result["changed"]:
                mapping = self.facts[row["fact"]]
                changes.append({"file": mapping["file"], "key": mapping["key"],
                                "value": DELETED.copy() if row["new"] is None else row["new"]})
            payload = canonical({"changes": changes_sorted(changes), "graph_rev": result["graph_rev"],
                                 "verdict": "CLEAR_WITHIN_SCOPE", "issued_at": int(_epoch(now)),
                                 "room": room, "thread": thread})
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError, RecursionError):
            return None
        encode = lambda raw: base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return f"pc1.{encode(payload)}.{encode(self.key.sign(payload))}"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str
    payload: dict | None = None

    def __bool__(self):
        return self.ok


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def verify(receipt: str, public_key, changes: list[dict], now, max_age_hours=24) -> Verdict:
    if not isinstance(receipt, str) or len(receipt) > 100_000 or not TOKEN.fullmatch(receipt):
        return Verdict(False, "malformed receipt")
    try:
        _, encoded, signature = receipt.split(".")
        decode = lambda text: base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
        payload_bytes, signature_bytes = decode(encoded), decode(signature)
    except ValueError:
        return Verdict(False, "invalid base64 encoding")
    try:
        if isinstance(public_key, str):
            public_key = public_key.encode("ascii")
        if isinstance(public_key, bytes):
            public_key = serialization.load_pem_public_key(public_key)
        if not isinstance(public_key, Ed25519PublicKey):
            return Verdict(False, "invalid public key")
    except (ValueError, TypeError, UnicodeError, UnsupportedAlgorithm):
        return Verdict(False, "invalid public key")
    try:
        public_key.verify(signature_bytes, payload_bytes)
    except (InvalidSignature, ValueError, UnsupportedAlgorithm):
        return Verdict(False, "invalid signature")
    try:
        payload = json.loads(payload_bytes, object_pairs_hook=_object)
        if (not isinstance(payload, dict) or set(payload) != FIELDS or canonical(payload) != payload_bytes
                or canonical(changes_sorted(payload["changes"])) != canonical(payload["changes"])
                or payload["verdict"] != "CLEAR_WITHIN_SCOPE"
                or type(payload["graph_rev"]) is not int or payload["graph_rev"] < 0
                or type(payload["issued_at"]) is not int or payload["issued_at"] < 0
                or not isinstance(payload["room"], str) or not payload["room"]
                or payload["thread"] is not None and not isinstance(payload["thread"], str)):
            return Verdict(False, "invalid payload")
        current = _epoch(now)
        if type(max_age_hours) not in {int, float} or not math.isfinite(max_age_hours) or max_age_hours < 0:
            return Verdict(False, "invalid maximum age")
        if payload["issued_at"] > current:
            return Verdict(False, "receipt issued in the future")
        if current - payload["issued_at"] > max_age_hours * 3600:
            return Verdict(False, "receipt expired")
        if canonical(payload["changes"]) != canonical(changes_sorted(changes)):
            return Verdict(False, "changes do not match staged config")
    except (ValueError, KeyError, TypeError, UnicodeError, OverflowError, RecursionError):
        return Verdict(False, "invalid payload or changes")
    return Verdict(True, "valid", payload)
