import os
import subprocess
from pathlib import Path

import pytest

from taps import gitsync
from taps.gitsync import GitError, commit_and_push, pull_ff


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch):
    # Ignore the developer's global/system git config (signing, pull.rebase, hooks...).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def origin(tmp_path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    seed = clone(tmp_path, "seed", bare)
    (seed / "state.json").write_text('{"v": 0}\n')
    (seed / "corrections.yaml").write_text("sightings: []\n")
    sh(seed, "add", ".")
    sh(seed, "commit", "-m", "seed")
    sh(seed, "push", "origin", "HEAD:main")
    return bare


def clone(tmp_path: Path, name: str, bare: Path) -> Path:
    path = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(bare), str(path)], check=True, capture_output=True)
    sh(path, "config", "user.name", "Test")
    sh(path, "config", "user.email", "test@example.com")
    sh(path, "symbolic-ref", "HEAD", "refs/heads/main")
    return path


def push_change(tmp_path: Path, origin: Path, name: str, file: str, text: str, message: str) -> str:
    other = clone(tmp_path, name, origin)
    (other / file).write_text(text)
    sh(other, "commit", "-am", message)
    sh(other, "push", "origin", "HEAD:main")
    return sh(other, "rev-parse", "HEAD")


def origin_head(origin: Path) -> str:
    return sh(origin, "rev-parse", "main")


@pytest.fixture
def git_calls(monkeypatch):
    calls: list[tuple[str, ...]] = []
    real = gitsync.git

    def recording(repo, *args):
        calls.append(args)
        return real(repo, *args)

    monkeypatch.setattr(gitsync, "git", recording)
    return calls


def assert_never_forced(calls):
    for args in calls:
        assert not any(a in ("-f", "--force", "--force-with-lease") for a in args), args
        if args[0] == "push":
            assert not any(a.startswith("+") for a in args), args


def test_commit_and_push_pushes_new_commit(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    (repo / "state.json").write_text('{"v": 1}\n')

    assert commit_and_push(repo, ["state.json"], "state: run") is True

    assert origin_head(origin) == sh(repo, "rev-parse", "HEAD")
    assert sh(origin, "log", "-1", "--format=%s", "main") == "state: run"
    assert sh(origin, "show", "main:state.json") == '{"v": 1}'


def test_commit_and_push_commits_only_given_paths(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    (repo / "state.json").write_text('{"v": 1}\n')
    (repo / "corrections.yaml").write_text("sightings: [local]\n")
    sh(repo, "add", "corrections.yaml")

    assert commit_and_push(repo, ["state.json"], "state: run") is True

    assert sh(origin, "show", "--name-only", "--format=", "main") == "state.json"
    assert sh(origin, "show", "main:corrections.yaml") == "sightings: []"


def test_nothing_to_commit_returns_true_without_new_commit(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    before = origin_head(origin)

    assert commit_and_push(repo, ["state.json"], "state: run") is True

    assert origin_head(origin) == before
    assert sh(repo, "rev-parse", "HEAD") == before


def test_remote_state_change_returns_false_and_leaves_origin_untouched(tmp_path, origin, git_calls):
    repo = clone(tmp_path, "run", origin)
    other_head = push_change(tmp_path, origin, "other", "state.json", '{"v": "other"}\n', "state: other run")
    (repo / "state.json").write_text('{"v": "mine"}\n')

    assert commit_and_push(repo, ["state.json"], "state: my run") is False

    assert origin_head(origin) == other_head
    assert sh(origin, "show", "main:state.json") == '{"v": "other"}'
    assert "state: my run" not in sh(origin, "log", "--format=%s", "main")
    assert sum(1 for c in git_calls if c[0] == "push") == 1
    assert not any(c[0] == "pull" for c in git_calls)
    assert_never_forced(git_calls)
    # give up: local is reset so it matches origin/main, not left diverged with our failed commit
    assert sh(repo, "rev-parse", "HEAD") == other_head
    assert (repo / "state.json").read_text() == '{"v": "other"}\n'


def test_remote_corrections_change_is_rebased_then_pushed(tmp_path, origin, git_calls):
    repo = clone(tmp_path, "run", origin)
    push_change(tmp_path, origin, "oleg", "corrections.yaml", "sightings: [new]\n", "corrections: add beer")
    (repo / "state.json").write_text('{"v": "mine"}\n')

    assert commit_and_push(repo, ["state.json"], "state: my run") is True

    assert origin_head(origin) == sh(repo, "rev-parse", "HEAD")
    assert sh(origin, "log", "--format=%s", "main").splitlines() == ["state: my run", "corrections: add beer", "seed"]
    assert sh(origin, "rev-list", "--merges", "main") == ""
    assert sh(origin, "show", "main:state.json") == '{"v": "mine"}'
    assert sh(origin, "show", "main:corrections.yaml") == "sightings: [new]"
    assert sum(1 for c in git_calls if c[0] == "push") == 2
    assert_never_forced(git_calls)


def test_rebase_conflict_is_aborted_and_returns_false(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    other_head = push_change(tmp_path, origin, "oleg", "corrections.yaml", "sightings: [theirs]\n", "corrections: theirs")
    (repo / "corrections.yaml").write_text("sightings: [mine]\n")

    assert commit_and_push(repo, ["corrections.yaml"], "corrections: mine") is False

    assert origin_head(origin) == other_head
    assert not (repo / ".git" / "rebase-merge").exists()
    assert not (repo / ".git" / "rebase-apply").exists()
    # give up: the local commit attempt is discarded, local now matches origin/main
    assert sh(repo, "rev-parse", "HEAD") == other_head
    assert sh(repo, "log", "-1", "--format=%s") == "corrections: theirs"
    assert (repo / "corrections.yaml").read_text() == "sightings: [theirs]\n"


def test_gives_up_after_attempts(tmp_path, origin, git_calls):
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    before = origin_head(origin)
    repo = clone(tmp_path, "run", origin)
    (repo / "state.json").write_text('{"v": 1}\n')

    assert commit_and_push(repo, ["state.json"], "state: run", attempts=3) is False

    assert sum(1 for c in git_calls if c[0] == "push") == 3
    assert origin_head(origin) == before
    assert_never_forced(git_calls)
    # give up: local matches origin/main again, our unpushed commit is discarded
    assert sh(repo, "rev-parse", "HEAD") == before
    assert (repo / "state.json").read_text() == '{"v": 0}\n'


def test_pull_ff_brings_remote_changes(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    other_head = push_change(tmp_path, origin, "oleg", "corrections.yaml", "sightings: [new]\n", "corrections: add beer")

    pull_ff(repo)

    assert sh(repo, "rev-parse", "HEAD") == other_head
    assert (repo / "corrections.yaml").read_text() == "sightings: [new]\n"


def test_pull_ff_refuses_diverged_history(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    push_change(tmp_path, origin, "other", "state.json", '{"v": "other"}\n', "state: other run")
    (repo / "state.json").write_text('{"v": "mine"}\n')
    sh(repo, "commit", "-am", "state: my run")
    mine = sh(repo, "rev-parse", "HEAD")

    with pytest.raises(GitError):
        pull_ff(repo)

    assert sh(repo, "rev-parse", "HEAD") == mine
    assert (repo / "state.json").read_text() == '{"v": "mine"}\n'


def test_git_returns_stdout_and_raises_git_error_with_stderr(tmp_path, origin):
    repo = clone(tmp_path, "run", origin)
    assert gitsync.git(repo, "log", "-1", "--format=%s") == "seed\n"
    with pytest.raises(GitError, match="no-such-ref"):
        gitsync.git(repo, "show", "no-such-ref")
