"""Switch HTTP operations and per-room WebSocket transport."""
from __future__ import annotations

import httpx

from premise_switch.state import StateStore


async def register_once(server_url: str, registration_key: str, state: StateStore) -> None:
    """Explicit provisioning only; startup must use the stored identity."""
    if state.agent_id:
        return
    async with httpx.AsyncClient(base_url=server_url.rstrip("/"), timeout=30) as http:
        response = await http.post("/agents", headers={"Authorization": f"Bearer {registration_key}"}, json={
            "name": "premise", "description": "Deterministic decision memory and change checks",
            "connector_type": "premise", "integration_profile": {
                "connection_model": "always_on", "message_exchange": True,
                "pre_invocation_mediation": [], "post_invocation_mediation": [], "event_reporting": [],
            },
        })
        if response.status_code == 409:
            raise RuntimeError("Premise is already registered. Restore the owner's existing state.json "
                               "with agent_id and api_key; ask the owner to recover credentials if lost. "
                               "Automatic overwrite is disabled.")
        response.raise_for_status()
        result = response.json()
        state.set_agent(result["id"], result["api_key"])
