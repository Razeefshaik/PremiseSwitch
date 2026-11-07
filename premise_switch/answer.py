"""Synchronous deterministic responder with conservative failure and agent guards."""
from __future__ import annotations

from collections import deque
import re
import threading
import time
from typing import Any, Callable

from .contracts import InboundMessage, Reply, ToolBackend
from .format import HELP, UNAVAILABLE, escape, format_check, format_result
from .router import ParseError, Route, parse


UNPARSED = ("Couldn't parse that command; this change is unchecked. Send only the command, for example: "
            "check paylane.retry_window_hours = 72\ndeterministic")
# Fulltext matches on these alone would answer unrelated questions with confident nonsense.
STOPWORDS = frozenset("""a about all am an and any are as at be been but by can could did do does for from had has
    have how i if in is it its me my of on or our please should so tell than that the their them then there these
    they this to us was we were what when where which who whom whose why will with would you your knows owns owner
    own review""".split())


def content_terms(query: str) -> str:
    return " ".join(w for w in re.findall(r"[A-Za-z0-9_.-]+", query) if w.lower() not in STOPWORDS)


class Answerer:
    def __init__(self, tools: ToolBackend, clock: Callable[[], float] = time.monotonic):
        self.tools = tools
        self.clock = clock
        self._agent_replies: dict[tuple[str, str], deque[float]] = {}
        # Wiring uses to_thread: serialize admission and replies so concurrent calls
        # cannot overrun the per-room/thread budget.
        self._lock = threading.Lock()

    def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self.tools.call(name, arguments)
        if isinstance(result, dict) and result.get("verdict") == "ERROR":
            raise RuntimeError("Tool assessment unavailable")
        return result

    def _resolved(self, query: str, labels: tuple[str, ...]) -> Any:
        rows = self._call("resolve", {"query": query, "limit": 5}) or []
        for label in labels:
            selected = next((row for row in rows if label in row["labels"]), None)
            if selected:
                return selected
        return None

    def _execute(self, route: Route) -> tuple[str, Any]:
        name, args = route.name, route.arguments
        if name in {"ownership", "impact", "question"}:
            query = content_terms(args["query"])
            if not query:
                return "resolve", []
            if name == "impact":
                selected = self._resolved(query, ("Assumption",))
                target = "impact_if_broken"
            else:
                service_first = name == "ownership" or any(word in args["query"].lower() for word in ("who", "owns", "owner", "knows"))
                selected = self._resolved(query, ("Service", "Decision") if service_first else ("Decision", "Service"))
                target = "decision_lineage" if selected and "Decision" in selected["labels"] else "who_knows"
            if selected:
                return target, self._call(target, {"id": selected["id"]})
            if name != "question":
                return "resolve", []
            return "search_evidence", self._call("search_evidence", {"query": query, "limit": 5})
        return name, self._call(name, args)

    def _body(self, route: Route) -> str:
        if route.name == "help":
            return HELP + "\ndeterministic"
        name, result = self._execute(route)
        if name == "check_change" and result:
            # R_UNTRACKED must be handled before enrichment, including mixed checks.
            if any(reason.get("code") == "R_UNTRACKED" for reason in result.get("reasons", [])):
                return format_check(result)
            names = dict(result.get("names", {}))
            for decision in result.get("decisions", []):
                lineage = self._call("decision_lineage", {"id": decision["id"]})
                if lineage:
                    names.update(lineage.get("names", {}))
            return format_check(result, names)
        if name == "decision_lineage" and isinstance(result, dict):
            result = self._with_departed_names(result)
        return format_result(name, result)

    def _with_departed_names(self, lineage: dict[str, Any]) -> dict[str, Any]:
        """decision_lineage names only active people; "both owners left" needs the others too."""
        names = dict(lineage.get("names", {}))
        for person in lineage.get("unavailable", []):
            if person not in names:
                for row in self._call("resolve", {"query": person, "limit": 1}) or []:
                    if row.get("id") == person and str(row.get("text", "")).startswith(person + " "):
                        names[person] = row["text"][len(person) + 1:]
        return {**lineage, "names": names}

    def _admit(self, room_id: str, thread_id: str) -> bool:
        """Count one agent reply against the sliding cap. Caller holds the lock."""
        now = self.clock()
        # Expire idle threads as well, avoiding unbounded room history.
        for key, replies in list(self._agent_replies.items()):
            while replies and now - replies[0] >= 600:
                replies.popleft()
            if not replies:
                del self._agent_replies[key]
        replies = self._agent_replies.setdefault((room_id, thread_id), deque())
        if len(replies) >= 6:
            return False
        replies.append(now)  # Unavailable replies count toward the cap too.
        return True

    def respond(self, message: InboundMessage) -> Reply | None:
        """Never propagate tool/parser/formatter failures or echo exception details."""
        thread_id = None
        mention = ()
        try:
            if not message.addressed:
                return None
            thread_id = message.thread_id or message.message_id
            try:
                route = parse(message.body)
            except (ParseError, ValueError):
                # An agent that tried a command gets the syntax, or it would wait forever for a
                # verdict. The thread cap bounds any retry loop.
                if message.sender_is_agent:
                    with self._lock:
                        if not self._admit(message.room_id, thread_id):
                            return None
                    return Reply(escape(UNPARSED), thread_id, (message.sender_handle,))
                return Reply(UNPARSED, thread_id)
            if message.sender_is_agent and not route.explicit:
                return None
            if message.sender_is_agent:
                mention = (message.sender_handle,)
            with self._lock:
                if message.sender_is_agent and not self._admit(message.room_id, thread_id):
                    return None
                try:
                    body = self._body(route)
                except Exception:
                    body = UNAVAILABLE
                return Reply(escape(body), thread_id, mention)
        except Exception:
            return Reply(UNAVAILABLE, thread_id, mention)
