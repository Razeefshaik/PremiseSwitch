"""Run Premise as a Switch agent: python -m premise_switch"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os

from dotenv import load_dotenv

from premise.config import settings
from premise.graph.client import GraphClient
from premise.graph.tools import GraphTools

from .adapter import Adapter
from .answer import Answerer
from .client import SwitchClient, register_once
from .state import StateStore

log = logging.getLogger("premise_switch")


def build_listener(enabled: bool):
    """Listen is optional. If it can't start, Answer and Guard must still run."""
    if not enabled:
        return None
    try:
        from .listen import SwitchListener
        return SwitchListener()
    except Exception as exc:
        log.warning("listen disabled: %s", type(exc).__name__)
        return None


async def run(no_listen: bool) -> None:
    server_url = os.environ["SWITCH_SERVER_URL"]
    state = StateStore()
    if not state.agent_id:
        key = os.environ.get("SWITCH_REGISTRATION_KEY", "").strip()
        if not key:
            raise SystemExit("premise isn't registered. Put a registration key in .env as "
                             "SWITCH_REGISTRATION_KEY, then run again.")
        await register_once(server_url, key, state)
        log.info("registered agent %s", state.agent_id)
    tools = GraphTools(GraphClient.from_settings(settings()))
    adapter = Adapter(Answerer(tools), build_listener(not no_listen))
    client = SwitchClient(server_url, state, adapter.on_message)
    adapter.gateway = client
    log.info("connecting agent %s, listen=%s", state.agent_id, adapter.listener is not None)
    try:
        await client.run()
    finally:
        await adapter.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-listen", action="store_true", help="answer and guard only")
    args = parser.parse_args()
    load_dotenv(override=False)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs each request at INFO, which adds nothing here.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(run(args.no_listen))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
