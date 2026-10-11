from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from gunicorn.config import Config

from retrieval.config import ANSWER_TIME_BUDGET_SECONDS
from retrieval.server_settings import InvalidServerSettingError

CONFIG_FILE = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"

APP_MODULES = ["retrieval.service", "pymongo", "google.genai", "onnxruntime", "tokenizers"]

LOADED_MODULES_SCRIPT = """
import json, runpy, sys
runpy.run_path(sys.argv[1])
print(json.dumps(sorted(set(sys.argv[2:]) & set(sys.modules))))
"""


def load_gunicorn_config() -> Config:
    """Read the config file the way gunicorn does, each value passing gunicorn's own validation.
    gunicorn ignores names it does not know, so a misspelt setting would be dropped silently;
    here every lowercase value must be a known setting, while imported constants are skipped."""
    config = Config()
    for name, value in runpy.run_path(str(CONFIG_FILE)).items():
        if not name.islower() or name.startswith("_") or not isinstance(value, str | int | list):
            continue
        assert name in config.settings, f"{name} is not a gunicorn setting"
        config.set(name, value)
    return config


@pytest.fixture(autouse=True)
def _no_server_variables(monkeypatch):
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)


def test_one_worker_process_keeps_the_one_job_and_one_question_rules():
    config = load_gunicorn_config()

    assert config.workers == 1


def test_the_worker_runs_threads_so_status_polls_are_not_stuck_behind_a_question():
    config = load_gunicorn_config()

    assert config.worker_class_str == "gthread"
    assert config.threads > 1


def test_a_slow_question_never_looks_like_a_hung_worker():
    config = load_gunicorn_config()

    assert config.timeout > ANSWER_TIME_BUDGET_SECONDS


def test_the_app_is_built_by_the_service_factory():
    config = load_gunicorn_config()

    assert config.wsgi_app == "retrieval.service:create_app()"


def test_no_access_log_is_written():
    config = load_gunicorn_config()

    assert config.accesslog is None


def test_a_local_run_listens_only_on_this_machine():
    config = load_gunicorn_config()

    assert config.bind == ["127.0.0.1:5001"]


def test_the_deployment_sets_the_address_and_port(monkeypatch):
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "10000")

    config = load_gunicorn_config()

    assert config.bind == ["0.0.0.0:10000"]


def test_a_bad_port_stops_start_up(monkeypatch):
    monkeypatch.setenv("PORT", "99999")

    with pytest.raises(InvalidServerSettingError):
        load_gunicorn_config()


def test_the_control_socket_that_could_add_workers_is_off():
    config = load_gunicorn_config()

    assert config.control_socket_disable is True


def test_reading_the_config_never_loads_the_app_into_the_process_that_forks_workers():
    """gunicorn's main process reads this file and then forks the worker. Libraries such as
    onnxruntime start native threads when imported, and forking a process that has threads
    is unsafe (macOS crashes the child outright), so the app must load only in the worker."""
    finished = subprocess.run(
        [sys.executable, "-c", LOADED_MODULES_SCRIPT, str(CONFIG_FILE), *APP_MODULES],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(finished.stdout) == []
