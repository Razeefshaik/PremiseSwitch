"""Create an isolated, local-only Kestrel demo repository."""
import argparse
from pathlib import Path
import shutil
import subprocess


INSTRUCTIONS = """# Kestrel Switch demo

Before editing anything under `config/`, ask `@premise` in the Switch room
to check the proposed change and follow its verdict. For example:
`@premise check paylane.retry_window_hours = 72`.
Never merge; a human approves.
"""


def prepare(dest: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "sandbox" / "kestrel-platform"
    # copytree refuses existing paths, including dangling symlinks.
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__"))
    for name in ("AGENTS.md", "CLAUDE.md"):
        (dest / name).write_text(INSTRUCTIONS, encoding="utf-8")

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
