"""The envelope every worker emits. Modelled on CloudEvents 1.0, with three separate identities.

* ``event_id``    unique per emission (derived, so replays of the same version share it)
* ``entity_key``  the item itself, stable across edits: ``source:native_id``
* ``content_hash`` detects no-op updates; compared only against the latest version of the same item

``sequence`` orders versions of one item (an edit has a higher sequence). Deletions are explicit
``op="delete"`` tombstones, never inferred from a missing item in one poll.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA = "premise.v2.envelope/1"
MAX_TEXT = 100_000


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def content_hash(title: str | None, text: str) -> str:
    return hashlib.sha256(f"{normalize_text(title or '')}\n{normalize_text(text)}".encode()).hexdigest()


class AuthorRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_user_id: str | None = None
    display_name: str | None = None
    email: str | None = None
    email_verified: bool = False


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: Literal["premise.v2.envelope/1"] = Field(default=SCHEMA, alias="schema")
    event_id: str = ""
    entity_key: str = Field(min_length=3, max_length=300)
    op: Literal["upsert", "delete"] = "upsert"
    sequence: int = Field(ge=0)
    source: Literal["slack", "jira", "github", "pages", "config", "switch"]
    scope: str | None = None             # connector instance that owns this item
    kind: Literal["message", "ticket", "comment", "page", "file", "pull_request", "issue"]
    native_id: str = Field(min_length=1, max_length=200)
    url: str | None = None
    author: AuthorRef = Field(default_factory=AuthorRef)
    time: datetime                       # when the source says it happened (posted or edited)
    recorded_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    title: str | None = None
    text: str = Field(default="", max_length=MAX_TEXT)
    thread: str | None = None            # entity_key of the thread root or parent ticket
    container: str | None = None         # channel or project
    visibility: Literal["public", "private"] = "public"
    content_hash: str = ""
    worker_version: str = "1"

    @model_validator(mode="after")
    def _derive(self):
        if self.entity_key != f"{self.source}:{self.native_id}":
            raise ValueError("entity_key must be source:native_id")
        if self.op == "delete":
            self.text, self.title = "", None
        if not self.content_hash:
            self.content_hash = content_hash(self.title, self.text)
        if not self.event_id:
            self.event_id = hashlib.sha256(f"{self.entity_key}|{self.sequence}|{self.op}|{self.content_hash}".encode()).hexdigest()[:32]
        return self

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "Envelope":
        return cls.model_validate_json(raw)
