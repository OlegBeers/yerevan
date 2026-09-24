"""State sync through git: fast-forward pull, commit + push without ever forcing (spec §3, §7)."""
import contextlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

STATE_FILE = "state.json"


class GitError(Exception):
    pass


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as e:
        raise GitError(f"git {' '.join(args)} failed ({e.returncode}): {e.stderr.strip()}") from e


def pull_ff(repo: Path) -> None:
    git(repo, "pull", "--ff-only", "origin", "main")


def _give_up(repo: Path) -> bool:
    """Local never stays diverged from a push we gave up on: reset it to match origin/main."""
    git(repo, "fetch", "origin", "main")
    git(repo, "reset", "--hard", "origin/main")
    return False


def commit_and_push(repo: Path, paths: Sequence[str], message: str, attempts: int = 3) -> bool:
    """True when origin/main has our commit (or there was nothing new); False when the push was not accepted."""
    git(repo, "add", "--", *paths)
    if git(repo, "diff", "--cached", "--name-only", "--", *paths).strip():
        git(repo, "commit", "-m", message, "--", *paths)
    for _ in range(attempts):
        try:
            git(repo, "push", "origin", "HEAD:main")
            return True
        except GitError:
            pass
        git(repo, "fetch", "origin", "main")
        # Remote state.json is never merged automatically: a concurrent run owns it.
        if STATE_FILE in git(repo, "diff", "--name-only", "HEAD...origin/main").splitlines():
            return _give_up(repo)
        try:
            git(repo, "pull", "--rebase", "origin", "main")
        except GitError:
            with contextlib.suppress(GitError):
                git(repo, "rebase", "--abort")
            return _give_up(repo)
    return _give_up(repo)
