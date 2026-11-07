#!/usr/bin/env bash
# Clear Listen's evidence so a demo can be replayed from the start.
# Keeps the agent's identity, room connections, clearance key and the main Premise graph.
# Run on the host where the adapter runs, from the repo root: scripts/reset_listen.sh
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
state_dir="${SWITCH_STATE_DIR:-.runtime/switch}"
container="${FALKORDB_CONTAINER:-premise-switch-falkordb-1}"

pids=$(ps -C python -o pid=,args= | awk '/-m premise_switch/{print $1}' || true)
if [[ -n "$pids" ]]; then
    echo "Stopping the adapter ($pids)"
    kill $pids
    sleep 3
fi

rm -f "$state_dir/messages.jsonl" "$state_dir/notices.jsonl"
echo "Cleared the evidence journal and announced proposals in $state_dir"

# Listen keeps its staging graph, decision graph and stream under this prefix only.
count=0
while read -r key; do
    [[ -z "$key" ]] && continue
    if [[ "$(docker exec "$container" redis-cli TYPE "$key")" == "graphdata" ]]; then
        docker exec "$container" redis-cli GRAPH.DELETE "$key" >/dev/null
    else
        docker exec "$container" redis-cli DEL "$key" >/dev/null
    fi
    count=$((count + 1))
done < <(docker exec "$container" redis-cli --scan --pattern 'premise_switch_v2_*')
echo "Removed $count Listen keys from FalkorDB"

if [[ "${1:-}" != "--no-start" ]]; then
    setsid nohup .venv/bin/python -m premise_switch >> "$state_dir/adapter.log" 2>&1 < /dev/null &
    sleep 8
    echo "Adapter restarted: $(ps -C python -o pid=,args= | awk '/-m premise_switch/{print $1}')"
fi
