"""Connects room messages to Listen and Answer. Holds no Switch or graph details itself."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
import re

from .contracts import InboundMessage, Listener, Reply, Responder, RoomGateway
from .extras import DecisionLedger, related_rooms_line

log = logging.getLogger("premise_switch.adapter")

UNAVAILABLE = "Premise is unavailable right now, so this change is unchecked."
VERDICTS = ("BLOCKED", "NEEDS_REVIEW", "CLEAR_WITHIN_SCOPE")


def verdicts_in(body: str) -> list[str]:
    return [line for line in body.splitlines() if line in VERDICTS]


class Adapter:
    """Records every message, answers addressed ones, and announces proposals after a quiet gap.

    `gateway` is set after construction because the client needs `on_message` to be built,
    and the adapter needs the client to post.
    """

    def __init__(self, responder: Responder, listener: Listener | None = None,
                 gateway: RoomGateway | None = None, debounce_seconds: float = 3.0, ledger: bool = False):
        self.responder, self.listener, self.gateway = responder, listener, gateway
        # The ledger needs the gateway, which is set after construction.
        self.use_ledger = ledger
        self._ledger: DecisionLedger | None = None
        self.debounce_seconds = debounce_seconds
        self._debounce: asyncio.Task | None = None
        self._listen_lock = asyncio.Lock()

    async def on_message(self, message: InboundMessage) -> None:
        """Never raises: one bad message must not stop the room's connection."""
        # Commands to Premise are questions, not statements about the world, so never evidence.
        if self.listener is not None and not message.addressed:
            try:
                await asyncio.to_thread(self.listener.record, message)
                self._schedule_listen()
            except Exception as exc:
                log.error("listen record failed: %s", type(exc).__name__)
        await self._answer(message)

    async def _answer(self, message: InboundMessage) -> None:
        try:
            reply = await asyncio.to_thread(self.responder.respond, message)
        except Exception as exc:
            # The responder promises not to raise, but an addressed question must never
            # go unanswered in a way that could be read as "no objection".
            log.error("responder failed: %s", type(exc).__name__)
            reply = self._unavailable(message)
        if reply is None:
            return
        found = verdicts_in(reply.body)
        if any(v != "CLEAR_WITHIN_SCOPE" for v in found):
            # Point at the linked room where the decision is discussed. related_rooms_line
            # never raises and lists only rooms Premise belongs to.
            related = await related_rooms_line(self.gateway, message.room_id)
            if related:
                reply = replace(reply, body=reply.body + "\n" + related)
        try:
            await self.gateway.post(message.room_id, reply)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "")
            log.error("post failed in room %s: %s %s", message.room_id, type(exc).__name__, status)
            return
        if found:
            command = re.sub(r"(?i)@premise\b", "", message.body).strip()[:300]
            await self._record(message.room_id, "verdict", {
                "verdict": ", ".join(found), "changes": command, "thread_id": reply.thread_id})

    async def _record(self, room_id: str, kind: str, summary: dict) -> None:
        """The ledger is a convenience. DecisionLedger.record never raises."""
        if not self.use_ledger:
            return
        if self._ledger is None:
            self._ledger = DecisionLedger(self.gateway)
        await self._ledger.record(room_id, kind, summary)

    @staticmethod
    def _unavailable(message: InboundMessage) -> Reply | None:
        if not message.addressed:
            return None
        mention = (message.sender_handle,) if message.sender_is_agent else ()
        return Reply(UNAVAILABLE, message.thread_id or message.message_id, mention)

    def _schedule_listen(self) -> None:
        if self._debounce is not None and not self._debounce.done():
            self._debounce.cancel()
        self._debounce = asyncio.create_task(self._listen_after_gap())

    async def _listen_after_gap(self) -> None:
        await asyncio.sleep(self.debounce_seconds)
        # Shielded so a new message restarts the wait, but never cancels a running pipeline.
        await asyncio.shield(self._listen_once())

    async def _listen_once(self) -> None:
        if self._listen_lock.locked():
            # A run is in progress and may have missed the newest message, so look again after it.
            self._schedule_listen()
            return
        async with self._listen_lock:
            try:
                notices = await asyncio.to_thread(self.listener.run_once)
            except Exception as exc:
                log.error("listen run failed: %s", type(exc).__name__)
                return
            for notice in notices:
                try:
                    await self.gateway.post(notice.room_id, Reply(notice.body))
                except Exception as exc:
                    log.error("proposal post failed in room %s: %s", notice.room_id, type(exc).__name__)
                    continue
                await self._record(notice.room_id, "proposal", {
                    "summary": notice.body.splitlines()[0], "changes": notice.drift_key, "thread_id": None})

    async def aclose(self) -> None:
        if self._debounce is not None and not self._debounce.done():
            self._debounce.cancel()
