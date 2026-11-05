"""LEAN 타임아웃 시 컨테이너를 직접 멈추는지 테스트."""

import subprocess
import sys
from pathlib import Path

import pytest

BACKTESTER_DIR = Path(__file__).resolve().parents[1]
if str(BACKTESTER_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTESTER_DIR))

from kis_backtest.lean import executor  # noqa: E402
from kis_backtest.lean.project_manager import LeanProject  # noqa: E402


def test_timeout_kills_named_container(tmp_path, monkeypatch):
    project_dir = tmp_path / "projects" / "bt_test"
    project_dir.mkdir(parents=True)
    props = tmp_path / "data" / "symbol-properties"
    props.mkdir(parents=True)
    (props / "symbol-properties-database.csv").write_text("")
    project = LeanProject(
        run_id="bt_test", project_dir=project_dir, data_dir=tmp_path / "data", symbols=["005930"],
        start_date="2025-01-01", end_date="2025-01-31", initial_capital=1_000_000,
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(executor.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="타임아웃"):
        executor.LeanExecutor.run(project, timeout=1)

    name = calls[0][calls[0].index("--name") + 1]
    assert name.startswith("lean-bt_test-")
    assert calls[1] == ["docker", "kill", name]
