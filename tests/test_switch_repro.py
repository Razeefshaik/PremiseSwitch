"""Reproduction checks: no live Switch server or graph required."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

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
