"""시가 범위 돌파(ORB) 전략과 opening_range 지표 테스트.

실제 LEAN Docker 없이 생성 코드의 범위 계산 부분을 그대로 실행해 검증한다.
"""

import sys
import textwrap
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BACKTESTER_DIR = Path(__file__).resolve().parents[1]
if str(BACKTESTER_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTESTER_DIR))

from kis_backtest.codegen.generator import CodeGenConfig, LeanCodeGenerator  # noqa: E402
from kis_backtest.strategies.registry import StrategyRegistry  # noqa: E402
import kis_backtest.strategies.preset  # noqa: E402,F401  (전략 등록)


def _generate(resolution: str = "minute", **params) -> str:
    definition = StrategyRegistry.build_with_params("opening_range_breakout", **params) if params \
        else StrategyRegistry.build("opening_range_breakout")
    config = CodeGenConfig(resolution=resolution, bar_minutes=5) if resolution == "minute" \
        else CodeGenConfig(resolution=resolution)
    return LeanCodeGenerator(definition, config=config).generate(["000660"], "2026-01-02", "2026-01-30")


def test_orb_is_registered_with_params():
    assert "opening_range_breakout" in StrategyRegistry.list()
    params = StrategyRegistry.get_param_definitions("opening_range_breakout")
    assert params["range_minutes"]["default"] == 30


def test_orb_codegen_conditions_and_risk():
    code = _generate()
    assert "entry_signal = price > orb_high" in code
    assert "exit_signal = price < orb_low" in code
    assert "orb_high = self.indicators[symbol]['orb']['high']" in code
    assert "self.indicators[symbol]['orb'] = {'day': None}" in code
    assert "loss_pct <= -2.0" in code and "profit_pct >= 4.0" in code
    # opening_range는 TradeBar 업데이트 루프에 들어가지 않는다
    assert "self.indicators[symbol]['orb'].Update" not in code


def test_orb_requires_minute_resolution():
    with pytest.raises(ValueError, match="분봉 백테스트에서만"):
        _generate("daily")


def _run_range_snippet(code: str, bars: list[tuple[datetime, int, float, float]]) -> list[tuple[float, float]]:
    """생성 코드의 opening_range 갱신 부분만 꺼내 봉마다 실행하고 (orb_high, orb_low)를 돌려준다."""
    lines = code.splitlines()
    start = next(i for i, line in enumerate(lines) if "# opening_range 장 시작 후" in line)
    # orb_high / orb_low 줄의 순서는 고정돼 있지 않으므로 둘 중 뒤에 오는 줄까지 자른다
    end = max(i for i, line in enumerate(lines) if "orb_high = self.indicators" in line or "orb_low = self.indicators" in line)
    snippet = textwrap.dedent("\n".join(lines[start:end + 1]))

    algo = types.SimpleNamespace(indicators={"000660": {"orb": {"day": None}}})
    seen = []
    for start_time, minutes, high, low in bars:
        bar = types.SimpleNamespace(
            Time=start_time, EndTime=start_time + timedelta(minutes=minutes), High=high, Low=low,
        )
        namespace = {"self": algo, "symbol": "000660", "bar": bar}
        exec(snippet, namespace)
        seen.append((namespace["orb_high"], namespace["orb_low"]))
    return seen


def test_orb_range_builds_then_publishes_and_resets_daily():
    code = _generate(range_minutes=15)
    day1 = datetime(2026, 1, 5, 9, 0)
    bars = [
        (day1, 5, 101, 99),                            # 09:00~09:05
        (day1 + timedelta(minutes=5), 5, 103, 100),    # 09:05~09:10
        (day1 + timedelta(minutes=10), 5, 102, 98),    # 09:10~09:15 → 범위 완성
        (day1 + timedelta(minutes=15), 5, 110, 90),    # 범위 밖: 반영하지 않음
        (datetime(2026, 1, 6, 10, 0), 5, 50, 49),       # 다음 날(10시 개장): 초기화
        (datetime(2026, 1, 6, 10, 5), 5, 52, 48),
        (datetime(2026, 1, 6, 10, 10), 5, 51, 47),      # 10:15 완성
    ]
    seen = _run_range_snippet(code, bars)
    inf = float("inf")
    assert seen[0] == (inf, -inf) and seen[1] == (inf, -inf)  # 범위가 완성되기 전엔 신호 없음
    assert seen[2] == (103, 98) and seen[3] == (103, 98)
    assert seen[4] == (inf, -inf) and seen[5] == (inf, -inf)
    assert seen[6] == (52, 47)


def test_orb_range_longer_bar_than_range():
    """봉(30분)이 범위(15분)보다 길면 첫 봉이 끝날 때 범위가 완성된다."""
    code = _generate(range_minutes=15)
    day = datetime(2026, 1, 5, 9, 0)
    seen = _run_range_snippet(code, [(day, 30, 105, 95), (day + timedelta(minutes=30), 30, 120, 80)])
    assert seen == [(105, 95), (105, 95)]


def test_run_endpoint_rejects_daily_orb(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    response = TestClient(app).post("/api/backtest/run", json={
        "strategy_id": "opening_range_breakout", "symbols": ["000660"],
        "start_date": "2026-01-02", "end_date": "2026-01-30",
    })
    assert response.status_code == 400
    assert "분봉 백테스트에서만" in response.json()["detail"]
