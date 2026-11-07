"""Read-only reproduction checks. Run with python -m premise_switch.doctor."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

from dotenv import dotenv_values
import httpx


# Every argument in docs/SWITCH_PROTOCOL.md, including optional ones we may send.
OP_ARGUMENTS = {
    "post_message": {"body", "thread_id"},
    "send_targeted_message": {"body", "target_names", "target_roles", "thread_id"},
    "read_context": {"limit", "since", "before", "room_id"},
    "list_rooms": {"include_archived"},
    "list_participants": set(),
    "list_linked_rooms": set(),
    "create_room_document": {"name", "description", "instructions", "content"},
    "update_room_document": {"document_id", "name", "description", "instructions", "content"},
    "connect_to_room": {"room_id", "include_general_instructions"},
}
NEEDED_NAMES = (
    "SWITCH_SERVER_URL", "SWITCH_REGISTRATION_KEY", "SWITCH_STATE_DIR",
    "LLM_PROVIDER", "FALKORDB_HOST", "FALKORDB_PORT", "GRAPH_NAME",
)


def run_doctor(*, root: Path | None = None, http_client=None, tool_backend=None,
               environ=None, remote_reader=None, emit=print) -> int:
    """Return 1 on any FAIL. Inject clients/backends for offline checks.

    Only health, operation discovery, list_rooms and hypothetical check_change
    are called. Never echo response bodies, config values or exception text.
    """
    root = Path(root) if root is not None else Path.cwd()
    failed = False

    def report(status, name, detail="", fix=""):
        nonlocal failed
        failed |= status == "FAIL"
        emit(f"{status} {name}" + (f": {detail}" if detail else "") + (f" | Fix: {fix}" if fix else ""))

    try:
        file_env = dotenv_values(root / ".env", interpolate=False)
    except (OSError, UnicodeError):
        file_env = {}
    env = dict(file_env)
    env.update(os.environ if environ is None else environ)
    state_path = Path(env.get("SWITCH_STATE_DIR") or ".runtime/switch") / "state.json"
    if not state_path.is_absolute():
        state_path = root / state_path
    missing = [name for name in NEEDED_NAMES if name not in file_env or (
        not env.get(name) and not (name == "SWITCH_REGISTRATION_KEY" and state_path.is_file()))]
    if not (root / ".env").is_file() or missing:
        report("FAIL", ".env", "missing names: " + ", ".join(missing or NEEDED_NAMES),
               "Copy .env.example to .env and fill the named settings.")
    else:
        report("PASS", ".env", "needed names present")
    if env.get("LLM_PROVIDER") != "none":
        report("FAIL", "LLM_PROVIDER", fix="Set LLM_PROVIDER=none in .env and the process environment.")
    else:
        report("PASS", "LLM_PROVIDER", "none")

    try:
        remotes = remote_reader() if remote_reader else subprocess.run(
            ["git", "remote", "-v"], cwd=root, capture_output=True, text=True, check=True).stdout
        forbidden = "kestrel-platform" in remotes.lower() or re.search(
            r"(?:/|:)razeefshaik/premise(?:\.git)?/?(?:\s|$)", remotes, re.I)
        if forbidden:
            report("FAIL", "git remotes", "original project remote found",
                   "Remove the original project remote from this clone.")
        else:
            report("PASS", "git remotes", "no original project remote")
    except Exception:
        report("FAIL", "git remotes", fix="Run from the PremiseSwitch Git clone with Git installed.")

    state = {}
    try:
        candidate = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(candidate, dict) and all(isinstance(candidate.get(k), str) and candidate[k].strip()
                                               for k in ("agent_id", "api_key")):
            state = candidate
    except (OSError, ValueError):
        pass

    owned_client = http_client is None
    client = http_client if http_client is not None else httpx.Client(timeout=5)
    base = (env.get("SWITCH_SERVER_URL") or "").rstrip("/")
    try:
        try:
            client.get(base + "/health", follow_redirects=False).raise_for_status()
            report("PASS", "Switch /health")
        except Exception:
            report("FAIL", "Switch /health", fix="Check SWITCH_SERVER_URL and start the Switch server.")

        if state:
            report("PASS", "state", "agent_id and api_key present")
        else:
            report("WARN", "state", "not registered yet",
                   "Have the owner register once and save agent_id and api_key in SWITCH_STATE_DIR/state.json.")

        headers = {"Authorization": "Bearer " + state["api_key"]} if state else {}
        agent_path = base + "/agents/" + quote(state.get("agent_id", ""), safe="")
        if not state:
            report("WARN", "Switch operations", "not registered yet", "Register once, then rerun doctor.")
        else:
            try:
                response = client.get(agent_path + "/ops", headers=headers, follow_redirects=False)
                response.raise_for_status()
                operations = response.json()["operations"]
                issues = []
                for name, arguments in OP_ARGUMENTS.items():
                    if name not in operations:
                        issues.append("missing op " + name)
                        continue
                    schema = operations[name]["input_schema"]
                    absent = arguments - set(schema.get("properties", {}))
                    # A new required argument also breaks our documented calls.
                    extra_required = set(schema.get("required", [])) - arguments
                    if absent:
                        issues.append(name + " missing arguments " + ", ".join(sorted(absent)))
                    if extra_required:
                        issues.append(name + " has undocumented required arguments")
                if issues:
                    report("FAIL", "Switch operations", "; ".join(issues),
                           "Use a Switch server matching docs/SWITCH_PROTOCOL.md.")
                else:
                    report("PASS", "Switch operations", "9 operations and all documented arguments match")
            except Exception:
                report("FAIL", "Switch operations", fix="Check agent credentials and the server /ops response schema.")

        try:
            if tool_backend is None:
                from premise.config import Settings
                from premise.graph.client import GraphClient
                from premise.graph.tools import GraphTools
                settings = Settings(
                    falkordb_host=env.get("FALKORDB_HOST", "localhost"),
                    falkordb_port=int(env.get("FALKORDB_PORT", "6380")),
                    falkordb_username=env.get("FALKORDB_USERNAME", ""),
                    falkordb_password=env.get("FALKORDB_PASSWORD", ""),
                    falkordb_tls=env.get("FALKORDB_TLS", "false").lower() == "true",
                    graph_name=env.get("GRAPH_NAME", "premise_switch"), llm_provider="none")
                tool_backend = GraphTools(GraphClient.from_settings(settings))
            result = tool_backend.call("check_change", {"changes": [{"fact": "paylane.retry_window_hours", "value": 72}]})
            if result.get("verdict") != "BLOCKED":
                report("FAIL", "graph check_change", "canonical change did not return BLOCKED",
                       "Start the local graph and load the bundled Kestrel fixtures.")
            else:
                report("PASS", "graph check_change", "paylane.retry_window_hours = 72 is BLOCKED")
        except Exception:
            report("FAIL", "graph check_change", "graph unavailable or invalid result",
                   "Check FalkorDB settings and load the bundled Kestrel fixtures.")

        if not state:
            report("WARN", "rooms", "not registered yet", "Register once and invite premise to the demo rooms.")
        else:
            try:
                response = client.post(agent_path + "/ops/list_rooms", headers=headers,
                                       json={"include_archived": False}, follow_redirects=False)
                response.raise_for_status()
                rooms = response.json()["result"]
                if not isinstance(rooms, list):
                    raise ValueError("Invalid rooms response")
                if rooms:
                    report("PASS", "rooms", str(len(rooms)))
                else:
                    report("WARN", "rooms", "0", "Invite premise to payments and kestrel-dev using the template.")
            except Exception:
                report("FAIL", "rooms", fix="Check agent credentials and list_rooms server support.")
    finally:
        if owned_client:
            client.close()
    return int(failed)


def main() -> int:
    return run_doctor()


if __name__ == "__main__":
    raise SystemExit(main())
