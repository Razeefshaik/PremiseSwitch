"""Durable, redacted Switch evidence and deterministic, room-local notices.

Call ``record`` on inbound messages and ``run_once`` on the wiring's debounce.
The listener never publishes model changes or opens pull requests.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import redis

from premise.config import Settings
from premise.graph.client import GraphClient
from premise_v2.envelope import AuthorRef, Envelope
from premise_v2.gateway import redact
from premise_v2.pipeline import Pipeline

from .contracts import InboundMessage, ProposalNotice


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    # Fail closed on a damaged journal: a partial listing must never tombstone evidence.
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class SwitchJournal:
    """Append-only human messages, deduplicated by Switch message_id across restarts.

    One listener owns a state directory; its record/run calls may use different
    threads. Multiple processes must use separate state directories.
    """

    def __init__(self, state_dir: str | Path | None = None):
        self.state_dir = Path(state_dir or os.environ.get("SWITCH_STATE_DIR", ".runtime/switch"))
        self.path = self.state_dir / "messages.jsonl"
        self._lock = threading.RLock()

    def messages(self) -> list[dict]:
        with self._lock:
            return _read_jsonl(self.path)

    def record(self, message: InboundMessage) -> None:
        if message.sender_is_agent:
            return
        with self._lock:
            if any(row["message_id"] == message.message_id for row in self.messages()):
                return
            row = asdict(message)
            # Use the v2 gateway before any room text reaches durable storage.
            for field in ("body", "room_name", "sender_name", "sender_handle"):
                row[field] = redact(row[field])[0]
            _append_jsonl(self.path, row)


class SwitchWorker:
    """A complete room journal, with an append-position checkpoint.

    Source timestamps need not arrive in order and may tie; a timestamp cursor
    would lose late messages. Envelope sequences still use timestamp_ms.
    """

    name = "switch"

    def __init__(self, journal: SwitchJournal, room_id: str):
        self.journal = journal
        self.room_id = room_id
        self.scope = f"switch:{room_id}"

    def _rows(self) -> list[dict]:
        return [row for row in self.journal.messages() if row["room_id"] == self.room_id]

    def list_keys(self) -> set[str]:
        return {f"{self.scope}:{row['message_id']}" for row in self._rows()}

    def load(self, checkpoint: dict | None = None):
        rows = self._rows()
        offset = int((checkpoint or {}).get("offset", 0))
        if offset < 0 or offset > len(rows):
            raise ValueError("Switch journal checkpoint is outside the room snapshot")
        for row in rows[offset:]:
            thread_id = row["thread_id"]
            yield Envelope(
                source=self.name, scope=self.scope, kind="message",
                entity_key=f"{self.scope}:{row['message_id']}",
                native_id=f"{self.room_id}:{row['message_id']}", sequence=row["timestamp_ms"],
                container=row["room_name"],
                thread=f"{self.scope}:{thread_id}" if thread_id and thread_id != row["message_id"] else None,
                author=AuthorRef(source_user_id=row["sender"], display_name=row["sender_name"]),
                time=datetime.fromtimestamp(row["timestamp_ms"] / 1000, tz=timezone.utc), text=row["body"],
            )
        return {"offset": len(rows)}


def _notice_body(drift: dict, proposal: dict, evidence: list[dict], room_keys: set[str]) -> str:
    support = [e for e in evidence if e["envelope_key"] in room_keys
               and e["status"] == "asserted" and e["value"] == drift["to"]]
    ignored = {e["envelope_key"] for e in evidence
               if e["envelope_key"] in room_keys and e["status"] == "instruction"}
    body = (f"**Proposal:** `{drift['fact']}` {drift['from']} → {drift['to']}.\n"
            f"Support: {drift['support']}; contested: {'yes' if drift['contested'] else 'no'}; "
            f"independent origins: {len(drift['origins'])}.\n"
            f"Ignored injection-like mentions: {len(ignored)}.\n"
            f"Verdict preview: **{proposal['verdict_preview']}**.\nEvidence: ")
    body += "; ".join(f"{e['envelope_key']} — “{e['quote']}”" for e in support)
    body = body.replace("@", "@\u200b")
    return body if len(body) < 800 else body[:798].rstrip("@") + "…"


class SwitchListener:
    """Listener protocol implementation; no LLM adapter or GitHub client is used.

    ``r``, ``staging`` and ``decision`` allow callers to supply database clients.
    Graph names must remain inside the premise_switch_v2_ namespace.
    """

    def __init__(self, model_root: str | Path | None = None, state_dir: str | Path | None = None,
                 *, r=None, staging: GraphClient | None = None, decision: GraphClient | None = None):
        self.journal = SwitchJournal(state_dir)
        self._run_lock = threading.Lock()
        self._announced_path = self.journal.state_dir / "notices.jsonl"
        config = Settings()  # Environment only; do not open a .env file here.
        root = Path(model_root or config.sandbox_dir).resolve()
        namespace = hashlib.sha256(f"{self.journal.state_dir.resolve()}|{root}".encode()).hexdigest()[:16]
        prefix = f"premise_switch_v2_{namespace}"
        staging = staging or GraphClient.from_settings(config, graph_name=f"{prefix}_staging")
        decision = decision or GraphClient.from_settings(config, graph_name=f"{prefix}_decision")
        if staging.graph_name == decision.graph_name or any(
            not c.graph_name.startswith("premise_switch_v2_") for c in (staging, decision)
        ):
            raise ValueError("Switch needs separate premise_switch_v2_ staging and decision graphs")
        self.r = r if r is not None else redis.Redis(
            host=config.falkordb_host, port=config.falkordb_port,
            socket_timeout=5, socket_connect_timeout=2, **config.database_options)
        self.pipeline = Pipeline(self.r, staging, decision, root, [], github=None,
                                 stream_name=f"{prefix}_raw", llm_provider="none")
        self._decision_marker = f"{prefix}_loaded:{decision.graph_name}"
        self._decision_loaded = False
        self.last_summary = None
        self.last_duration_seconds = 0.0

    def record(self, message: InboundMessage) -> None:
        self.journal.record(message)

    def run_once(self) -> list[ProposalNotice]:
        with self._run_lock:
            started = time.perf_counter()
            try:
                return self._run_once()
            finally:
                self.last_duration_seconds = time.perf_counter() - started

    def _run_once(self) -> list[ProposalNotice]:
        rows = self.journal.messages()
        if not rows:
            return []
        self.pipeline.workers = [SwitchWorker(self.journal, room) for room in sorted({r["room_id"] for r in rows})]
        if not self._decision_loaded:
            self._decision_loaded = bool(self.r.exists(self._decision_marker)
                                         and self.r.exists(self.pipeline.decision.graph_name))
        summary = self.pipeline.run(load_decision=not self._decision_loaded)
        self.last_summary = summary
        if summary.registry_errors:
            raise RuntimeError("Switch pipeline registry validation failed")
        self._decision_loaded = True
        self.r.set(self._decision_marker, "1")
        if not summary.caught_up or summary.connector_failures or summary.stream.get("dead") or summary.stream.get("failed"):
            raise RuntimeError("Switch pipeline evidence processing is incomplete")

        announced = {(row["room_id"], row["drift_key"]) for row in _read_jsonl(self._announced_path)}
        rooms = {}
        # record() may append while the pipeline runs. Route every processed
        # supporting item, including arrivals collected after the initial read.
        for row in self.journal.messages():
            rooms.setdefault(row["room_id"], set()).add(f"switch:{row['room_id']}:{row['message_id']}")
        drifts = {f"{d['fact']}={d['to']}": d for d in summary.drifts}
        notices = []
        for proposal in summary.proposals:
            if proposal["state"] not in {"created", "updated", "unchanged"}:
                continue
            key = proposal["key"]
            stored = self.pipeline.staging.proposal(key)
            evidence = json.loads(stored["evidence_json"])
            drift = drifts[key]
            supporting = {e["envelope_key"] for e in evidence
                          if e["status"] == "asserted" and e["value"] == drift["to"]}
            for room_id, room_keys in sorted(rooms.items()):
                if (room_id, key) in announced or not supporting & room_keys:
                    continue
                notice = ProposalNotice(room_id, key, _notice_body(drift, proposal, evidence, room_keys))
                _append_jsonl(self._announced_path, {"room_id": room_id, "drift_key": key})
                announced.add((room_id, key))
                notices.append(notice)
        return notices
