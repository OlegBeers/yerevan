import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
TELEGRAM_SECRETS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ADMIN_CHAT_ID")


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def triggers(wf: dict):
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True
    return wf[True] if True in wf else wf["on"]


def only_job(wf: dict) -> dict:
    jobs = wf["jobs"]
    assert len(jobs) == 1
    return next(iter(jobs.values()))


def step_index(steps: list[dict], *, uses: str | None = None, run: str | None = None) -> int:
    for i, step in enumerate(steps):
        if uses and step.get("uses", "").startswith(uses):
            return i
        if run and run in step.get("run", ""):
            return i
    raise AssertionError(f"step not found: uses={uses!r} run={run!r}")


def test_run_schedule_and_manual_trigger():
    wf = load("run.yml")
    assert wf["name"] == "taps"
    on = triggers(wf)
    assert [s["cron"] for s in on["schedule"]] == ["35 6 * * *", "17 14 * * *"]
    assert "workflow_dispatch" in on
    assert "push" not in on and "pull_request" not in on


def test_run_never_overlaps_and_never_cancels_a_running_job():
    wf = load("run.yml")
    assert wf["concurrency"] == {"group": "taps", "cancel-in-progress": False}


def test_run_permissions_allow_state_push_and_pages_deploy():
    wf = load("run.yml")
    assert wf["permissions"] == {"contents": "write", "pages": "write", "id-token": "write"}


def test_run_job_environment_and_timeout():
    job = only_job(load("run.yml"))
    assert job["runs-on"] == "ubuntu-latest"
    assert job["environment"]["name"] == "github-pages"
    assert "steps.deploy.outputs.page_url" in job["environment"]["url"]
    assert 0 < job["timeout-minutes"] <= 60


def test_run_checks_out_tip_of_main_with_history():
    steps = only_job(load("run.yml"))["steps"]
    checkout = steps[step_index(steps, uses="actions/checkout@v4")]
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["fetch-depth"] == 0


def test_run_installs_python_deps_and_chromium():
    steps = only_job(load("run.yml"))["steps"]
    setup = steps[step_index(steps, uses="actions/setup-python@v5")]
    assert str(setup["with"]["python-version"]) == "3.12"
    assert setup["with"]["cache"] == "pip"
    step_index(steps, run="pip install -r requirements.txt")
    step_index(steps, run="python -m playwright install --with-deps chromium")


def test_playwright_install_failure_does_not_stop_the_run():
    steps = only_job(load("run.yml"))["steps"]
    assert steps[step_index(steps, run="playwright install")]["continue-on-error"] is True


def test_requirements_are_pinned_to_exact_versions():
    lines = [ln.strip() for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert {ln.partition("==")[0].lower() for ln in lines} == {"playwright", "requests", "beautifulsoup4", "pyyaml",
                                                              "pytest"}
    assert all(re.fullmatch(r"[A-Za-z0-9_.-]+==\d+(\.\d+)*", ln) for ln in lines), lines


def test_run_sets_bot_git_identity():
    steps = only_job(load("run.yml"))["steps"]
    script = steps[step_index(steps, run="git config user.name")]["run"]
    assert 'git config user.name "taps-bot"' in script
    assert 'git config user.email "taps-bot@users.noreply.github.com"' in script


def test_run_step_command_and_env_from_secrets_and_vars():
    steps = only_job(load("run.yml"))["steps"]
    step = steps[step_index(steps, run="python -m taps run")]
    assert step["run"].strip() == "python -m taps run"
    env = step["env"]
    assert set(env) == {*TELEGRAM_SECRETS, "SITE_URL", "TAPS_DEBUG_DIR"}
    for name in TELEGRAM_SECRETS:
        assert env[name] == f"${{{{ secrets.{name} }}}}"
    assert env["SITE_URL"] == "${{ vars.SITE_URL }}"
    assert env["TAPS_DEBUG_DIR"] == "${{ runner.temp }}/taps-debug"


def test_run_uploads_untappd_debug_html_if_any_was_captured():
    steps = only_job(load("run.yml"))["steps"]
    step = steps[step_index(steps, uses="actions/upload-artifact@v4")]
    assert step["if"] == "always()"
    assert step["name"] == "taps-debug"
    assert step["with"]["path"] == "${{ runner.temp }}/taps-debug"
    assert step["with"]["if-no-files-found"] == "ignore"
    assert step["with"]["retention-days"] == 1


def test_run_deploys_site_even_after_failed_run_if_data_exists():
    steps = only_job(load("run.yml"))["steps"]
    configure = steps[step_index(steps, uses="actions/configure-pages@v5")]
    upload = steps[step_index(steps, uses="actions/upload-pages-artifact@v3")]
    deploy = steps[step_index(steps, uses="actions/deploy-pages@v4")]
    assert upload["with"]["path"] == "site"
    assert deploy["id"] == "deploy"
    for step in (configure, upload, deploy):
        assert "always()" in step["if"]
        assert "hashFiles('site/data.json') != ''" in step["if"]


def test_run_step_order():
    steps = only_job(load("run.yml"))["steps"]
    order = [
        step_index(steps, uses="actions/checkout@v4"),
        step_index(steps, uses="actions/setup-python@v5"),
        step_index(steps, run="pip install -r requirements.txt"),
        step_index(steps, run="playwright install"),
        step_index(steps, run="git config user.name"),
        step_index(steps, run="python -m taps run"),
        step_index(steps, uses="actions/configure-pages@v5"),
        step_index(steps, uses="actions/upload-pages-artifact@v3"),
        step_index(steps, uses="actions/deploy-pages@v4"),
        step_index(steps, uses="actions/upload-artifact@v4"),
    ]
    assert order == sorted(order)


def test_tests_workflow_runs_pytest_on_push_and_pr_without_browser():
    wf = load("tests.yml")
    on = triggers(wf)
    assert "push" in on and "pull_request" in on
    steps = only_job(wf)["steps"]
    step_index(steps, uses="actions/checkout@v4")
    setup = steps[step_index(steps, uses="actions/setup-python@v5")]
    assert str(setup["with"]["python-version"]) == "3.12"
    install = step_index(steps, run="pip install -r requirements.txt")
    tests = step_index(steps, run="pytest -q")
    assert install < tests
    assert not any("playwright install" in s.get("run", "") for s in steps)


def test_readme_lists_every_setting_the_run_workflow_reads():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in (*TELEGRAM_SECRETS, "SITE_URL"):
        assert name in readme
    for command in ("python -m taps run --dry-run", "--no-digest", "pytest"):
        assert command in readme
    assert "github.com/hopandshot/hopsandshot" in readme
