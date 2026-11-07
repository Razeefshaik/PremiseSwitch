"""Reproduction checks: no live Switch server or graph required."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import sys

import pytest
import httpx
import yaml

from premise_switch.doctor import run_doctor

ROOT = Path(__file__).resolve().parents[1]


def test_template():
    doc = yaml.safe_load((ROOT / "templates/kestrel.group.yaml").read_text())
    assert doc["version"] == 1
    assert "kickoff" not in doc
    assert doc["group"]["name"] == "Kestrel"
    assert doc["params"]["bridge"] == {"type": "bridge", "default": ["$first"]}
    assert doc["params"]["coder"]["type"] == "agent"
    rooms = {room["name"]: room for room in doc["rooms"]}
    assert set(rooms) == {"payments", "kestrel-dev"}
    for room in rooms.values():
        assert room["bridge"] == "{bridge}"
        assert room["users"] == ["{$creator}"]
    assert rooms["payments"]["agents"] == ["premise"]
    assert rooms["kestrel-dev"]["agents"] == ["premise", "{coder}"]
    assert "@premise check paylane.retry_window_hours = 72" in rooms["kestrel-dev"]["instructions"]
    assert doc["links"] == [{"from": "kestrel-dev", "to": "payments", "label": "payments decisions"}]


def test_prepare_demo(tmp_path, monkeypatch):
    # Exercise local identity fallback even on a configured developer machine.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    dest = tmp_path / "demo with spaces"
    command = [sys.executable, str(ROOT / "scripts/prepare_switch_demo.py"), "--dest", str(dest)]
    subprocess.run(command, check=True, capture_output=True)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(dest), *args], text=True).strip()
    assert git("remote", "-v") == ""
    assert git("branch", "--show-current") == "main"
    assert git("rev-list", "--count", "HEAD") == "1"


    for name in ("AGENTS.md", "CLAUDE.md"):
        text = (dest / name).read_text()
        assert "@premise" in text and "config/" in text and "Never merge" in text
        assert "check_files" not in text
    assert (dest / "config").is_dir()
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert git("rev-list", "--count", "HEAD") == "1"


def test_up_shell_syntax():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")
    result = subprocess.run([bash, "-n", "scripts/up.sh"], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


class FakeTools:
    def __init__(self, verdict="BLOCKED", error=False):
        self.verdict, self.error = verdict, error

    def call(self, name, arguments):
        assert name == "check_change"
        assert arguments == {"changes": [{"fact": "paylane.retry_window_hours", "value": 72}]}
        if self.error:
            raise RuntimeError("fake-secret-value")
        return {"verdict": self.verdict}


@pytest.fixture
def doctor_case(tmp_path):
    (tmp_path / ".env").write_text(
        "SWITCH_SERVER_URL=http://switch.test\nSWITCH_REGISTRATION_KEY=fake-secret-value\n"
        "SWITCH_STATE_DIR=.runtime/switch\nLLM_PROVIDER=none\n"
        "FALKORDB_HOST=localhost\nFALKORDB_PORT=6380\nGRAPH_NAME=premise_switch\n")
    state_path = tmp_path / ".runtime/switch/state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"agent_id": "premise-id", "api_key": "fake-secret-value", "future_field": True}))
    # Independent fixture built from the protocol table, rather than doctor's map.
    operations = {}
    for line in (ROOT / "docs/SWITCH_PROTOCOL.md").read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = line.split("|")
        name = cells[1].strip(" `")
        args = []
        for code in re.findall(r"`([^`]+)`", cells[2]):
                args.extend(match[0] for piece in code.split(",")
                            if (match := re.match(r"[a-z_]+", piece.strip())))
        operations[name] = {"description": "mock", "input_schema": {"properties": {arg: {} for arg in args}}}
    case = {"operations": operations, "health": 200, "rooms": [{"room_id": "room-1"}],
            "remote": "origin https://github.com/Razeefshaik/PremiseSwitch.git (fetch)", "calls": []}

    def handle(request):
        case["calls"].append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(case["health"], text="fake-secret-value")
        assert request.headers["Authorization"] == "Bearer fake-secret-value"
        if request.url.path == "/agents/premise-id/ops":
            assert request.method == "GET"
            return httpx.Response(200, json={"operations": case["operations"]})
        assert request.url.path == "/agents/premise-id/ops/list_rooms"
        assert request.method == "POST"
        assert json.loads(request.content) == {"include_archived": False}
        return httpx.Response(200, json={"result": case["rooms"]})

    def run(tools=None):
        lines = []
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            code = run_doctor(root=tmp_path, http_client=client, tool_backend=tools or FakeTools(),
                              environ={}, remote_reader=lambda: case["remote"], emit=lines.append)
        output = "\n".join(lines)
        assert "fake-secret-value" not in output
        assert len(lines) == 8
        for line in lines:
            if not line.startswith("PASS"):
                assert " | Fix: " in line
        return code, output

    case.update(run=run, root=tmp_path, state_path=state_path)
    return case


def test_doctor_all_good(doctor_case):
    code, output = doctor_case["run"]()
    assert code == 0
    assert all(line.startswith("PASS") for line in output.splitlines())
    assert "9 operations" in output
    assert "BLOCKED" in output
    print("\n" + output)


def test_doctor_health_down(doctor_case):
    doctor_case["health"] = 503
    code, output = doctor_case["run"]()
    assert code == 1 and "FAIL Switch /health" in output


@pytest.mark.parametrize("name", [
    "post_message", "send_targeted_message", "read_context", "list_rooms", "list_participants",
    "list_linked_rooms", "create_room_document", "update_room_document", "connect_to_room",
])
def test_doctor_missing_op(doctor_case, name):
    del doctor_case["operations"][name]
    code, output = doctor_case["run"]()
    assert code == 1 and "missing op " + name in output


def test_doctor_renamed_argument(doctor_case):
    props = doctor_case["operations"]["send_targeted_message"]["input_schema"]["properties"]
    props["targets"] = props.pop("target_names")
    code, output = doctor_case["run"]()
    assert code == 1 and "send_targeted_message missing arguments target_names" in output


def test_doctor_new_required_argument(doctor_case):
    doctor_case["operations"]["post_message"]["input_schema"]["required"] = ["body", "new_argument"]
    code, output = doctor_case["run"]()
    assert code == 1 and "post_message has undocumented required arguments" in output


def test_doctor_llm_enabled(doctor_case):
    env_path = doctor_case["root"] / ".env"
    env_path.write_text(env_path.read_text().replace("LLM_PROVIDER=none", "LLM_PROVIDER=anthropic"))
    code, output = doctor_case["run"]()
    assert code == 1 and "FAIL LLM_PROVIDER" in output


@pytest.mark.parametrize("tools", [FakeTools("ERROR"), FakeTools("CLEAR_WITHIN_SCOPE"), FakeTools(error=True)])
def test_doctor_graph_failure(doctor_case, tools):
    code, output = doctor_case["run"](tools)
    assert code == 1 and "FAIL graph check_change" in output


@pytest.mark.parametrize("remote", [
    "https://github.com/Razeefshaik/Premise", "git@github.com:Razeefshaik/Premise.git",
    "ssh://git@github.com/Razeefshaik/Premise.git/", "https://github.com/elsewhere/kestrel-platform.git",
])
def test_doctor_original_remote(doctor_case, remote):
    doctor_case["remote"] = "origin " + remote + " (fetch)"
    code, output = doctor_case["run"]()
    assert code == 1 and "FAIL git remotes" in output


def test_doctor_used_registration_key(doctor_case):
    env_path = doctor_case["root"] / ".env"
    env_path.write_text(env_path.read_text().replace("SWITCH_REGISTRATION_KEY=fake-secret-value", "SWITCH_REGISTRATION_KEY="))
    code, output = doctor_case["run"]()
    assert code == 0 and "PASS .env" in output


def test_doctor_zero_rooms(doctor_case):
    doctor_case["rooms"] = []
    code, output = doctor_case["run"]()
    assert code == 0 and "WARN rooms: 0" in output


def test_doctor_unregistered(doctor_case):
    doctor_case["state_path"].unlink()
    code, output = doctor_case["run"]()
    assert code == 0 and "WARN state: not registered yet" in output
    assert doctor_case["calls"] == [("GET", "/health")]


def test_doctor_missing_env(doctor_case):
    (doctor_case["root"] / ".env").unlink()
    code, output = doctor_case["run"]()
    assert code == 1 and "FAIL .env: missing names:" in output


def test_doctor_empty_registration_key_without_state(doctor_case):
    doctor_case["state_path"].unlink()
    env_path = doctor_case["root"] / ".env"
    env_path.write_text(env_path.read_text().replace("SWITCH_REGISTRATION_KEY=fake-secret-value", "SWITCH_REGISTRATION_KEY="))
    code, output = doctor_case["run"]()
    assert code == 1 and "missing names: SWITCH_REGISTRATION_KEY" in output
