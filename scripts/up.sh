#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ ! -f .env ]]; then
    echo 'FAIL: .env is missing. Copy .env.example to .env and configure it.' >&2
    exit 1
fi

docker compose -f docker-compose.switch.yml up -d --build
deadline=$((SECONDS + 120))
until curl --fail --silent --output /dev/null --connect-timeout 2 --max-time 5 http://127.0.0.1:8100/healthz; do
    if (( SECONDS >= deadline )); then
        echo 'FAIL: Premise health check timed out at 127.0.0.1:8100/healthz. Check docker compose -f docker-compose.switch.yml logs app.' >&2
        exit 1
    fi
    sleep 2
done

if [[ ! -d .venv ]]; then
    python_bin=''
    for candidate in python3 python3.14 python3.13 python3.12 python; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
            python_bin="$candidate"
            break
        fi
    done
    if [[ -z "$python_bin" ]]; then
        echo 'FAIL: Python 3.12 or newer is required. Install it and rerun scripts/up.sh.' >&2
        exit 1
    fi
    "$python_bin" -m venv .venv
fi
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    echo 'FAIL: .venv needs a working Python 3.12+ interpreter. Recreate it and rerun scripts/up.sh.' >&2
    exit 1
fi
.venv/bin/python -m pip install -r requirements.lock && .venv/bin/python -m pip install --no-deps -e .
echo 'Next: source .venv/bin/activate && python -m premise_switch.doctor'
