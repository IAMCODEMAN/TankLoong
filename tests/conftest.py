"""Keep application and upstream musicdl test files out of real user data."""
from __future__ import annotations

import shutil
import os
import tempfile
from pathlib import Path

import pytest

# musicdl opens its log during import. Isolate platformdirs before collection,
# not only the application paths after collection.
_musicdl_data = tempfile.mkdtemp(prefix="tankloong-musicdl-tests-")
os.environ["WIN_PD_OVERRIDE_LOCAL_APPDATA"] = _musicdl_data

@pytest.fixture(scope="session", autouse=True)
def isolate_app_data():
    import core

    # Session-scoped, so create the sandbox directory directly instead of using
    # the function-scoped tmp_path_factory fixture.
    base = Path(tempfile.mkdtemp(prefix="tankloong-tests-"))
    core.APP_DATA_DIR = base
    core.TASKS_DIR = base / "tasks"
    core.LOGS_DIR = base / "logs"
    core.QUEUE_FILE = base / "queue.json"
    core.HISTORY_FILE = base / "history.json"

    yield base

    shutil.rmtree(base, ignore_errors=True)
    # Upstream keeps a FileHandler open on Windows; process exit releases it.
