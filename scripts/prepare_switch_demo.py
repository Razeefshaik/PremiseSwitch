"""Create an isolated, local-only Kestrel demo repository."""
import argparse
from pathlib import Path
import shutil
import subprocess


INSTRUCTIONS = """# Kestrel payments platform

You are a coding agent working in this repo through a Switch room.

## Before changing anything under `config/`
1. Ask `@premise` in the room to check the exact change, using fact names from `facts.yaml`. Send only the command, for example:
   `@premise check paylane.retry_window_hours = 72`
2. Follow its verdict:
   - **BLOCKED** or **NEEDS_REVIEW**: don't write the change. If Premise lists a verified fix, ask `@premise` to check your change together with the fix, all in one command, for example:
     `@premise check paylane.retry_window_hours = 72, webhook.dedupe_ttl_hours = 72, redis.max_ttl_hours = 72`
   - **CLEAR_WITHIN_SCOPE**: make exactly the checked changes, nothing more.
   - **unchecked** or unavailable: stop and tell the human. This is never a pass.
3. A CLEAR reply ends with a line `Premise-Clearance: pc1...`. Put that line, unchanged, as the last line of your commit message. A git hook refuses any commit touching `config/` without a receipt that matches the staged changes exactly.

## Always
- Never merge or push. A human approves.
- Never edit `.premise/` or `.git/hooks/`, and never use `--no-verify`.
- Say what you changed and paste Premise's verdict line in your reply to the human.
"""


def prepare(dest: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "sandbox" / "kestrel-platform"
    # copytree refuses existing paths, including dangling symlinks.
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
    for name in ("AGENTS.md", "CLAUDE.md"):
        (dest / name).write_text(INSTRUCTIONS, encoding="utf-8")
    # The clearance public key goes here; see the README.
    (dest / ".premise").mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=dest, check=True, capture_output=True, text=True)

    git("init", "-b", "main")
    for key, fallback in (("user.name", "Kestrel Demo"), ("user.email", "demo@example.invalid")):
        configured = subprocess.run(["git", "config", "--get", key], cwd=dest, capture_output=True, text=True)
        if not configured.stdout.strip():
            git("config", "--local", key, fallback)
    git("add", ".")
    git("-c", "commit.gpgsign=false", "commit", "-m", "Initialize local Switch demo")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args()
    try:
        prepare(args.dest)
    except FileExistsError:
        parser.exit(1, "Destination already exists; choose a new path.\n")
    except (OSError, subprocess.CalledProcessError):
        parser.exit(1, "Demo preparation failed; check filesystem permissions and Git installation.\n")
    print("Created local Switch demo with one commit and no remote.")


if __name__ == "__main__":
    main()
