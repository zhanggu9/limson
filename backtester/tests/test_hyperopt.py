"""하이퍼옵트: 탐색 공간 추출, 값 넣기, 실행기(가짜 백테스트) 검증."""

from datetime import date
from pathlib import Path
import threading

import pytest
import yaml

from kis_backtest.optimize.hyperopt import (
    VALID,
    Evaluation,
    HyperoptAborted,
    HyperoptConfig,
    HyperoptRunner,
)
from kis_backtest.optimize.objective import score_metrics, summarize_result
from kis_backtest.optimize.space import (
    Constraint,
    ParamSpec,
    load_target,
    nice_step,
    search_space,
)

TEMPLATES = Path(__file__).resolve().parents[1] / "kis_backtest" / "file" / "templates"

BUILDER_YAML = """
version: "1.0"
metadata:
  name: "빌더 전략"
  description: ""
  tags: []
strategy:
  id: builder_strategy
  category: custom
  indicators:
    - id: sma
      alias: sma_fast
      params:
        period: 5
    - id: sma
      alias: sma_slow
      params:
        period: 20
    - id: rsi
      alias: rsi_1
      params:
        period: 14
    - id: roc
      alias: daily_return
      params:
        period: 1
  entry:
    logic: AND
    conditions:
      - indicator: sma_fast
        operator: cross_above
        compare_to: sma_slow
      - indicator: rsi_1
        operator: less_than
        compare_to: 70
  exit:
    logic: OR
    conditions:
      - indicator: sma_fast
        operator: cross_below
        compare_to: sma_slow
      - indicator: daily_return
        operator: less_than
        compare_to: 0
risk:
  stop_loss:
    enabled: true
    percent: 5
  take_profit:
    enabled: true
    percent: 10
"""


# ---- 탐색 공간 -----------------------------------------------------------
def test_nice_step():
    assert nice_step(1, 20, "float") == 0.5
    assert nice_step(2, 50, "float") == 2
    assert nice_step(0.8, 0.95, "float") == 0.005
    assert nice_step(2, 50, "int") == 1
    assert nice_step(10, 200, "int") == 5


def test_preset_space_and_constraint():
    target = load_target(strategy_id="sma_crossover")
    names = [spec.name for spec in target.params]
    assert names == ["fast_period", "slow_period", "stop_loss_pct", "take_profit_pct"]
    slow = next(spec for spec in target.params if spec.name == "slow_period")
    assert (slow.low, slow.high, slow.step, slow.type) == (10, 200, 5, "int")
    assert [(c.smaller, c.larger) for c in target.constraints] == [("fast_period", "slow_period")]
    definition = target.build({"fast_period": 7, "slow_period": 40, "stop_loss_pct": 3.0, "take_profit_pct": 12.0})
    assert definition.id == "sma_crossover"


def test_builder_yaml_numbers_become_params():
    target = load_target(yaml_content=BUILDER_YAML)
    specs = {spec.name: spec for spec in target.params}
    assert set(specs) == {
        "sma_fast.period", "sma_slow.period", "rsi_1.period", "daily_return.period",
        "entry.1.compare_to", "exit.1.compare_to", "risk.stop_loss", "risk.take_profit",
    }
    # 1짜리 지표 기간·0 기준값은 처음엔 고정 (전략 뼈대)
    assert not specs["daily_return.period"].suggested
    assert not specs["exit.1.compare_to"].suggested
    assert specs["sma_slow.period"].suggested
    # 같은 SMA끼리 교차 → 기간 크기 순서 유지
    assert [(c.smaller, c.larger) for c in target.constraints] == [("sma_fast.period", "sma_slow.period")]
    assert (specs["sma_slow.period"].low, specs["sma_slow.period"].high) == (10, 40)


def test_builder_yaml_values_applied_to_schema_and_yaml():
    target = load_target(yaml_content=BUILDER_YAML)
    values = {**target.defaults(), "sma_fast.period": 8, "sma_slow.period": 33, "entry.1.compare_to": 62.0,
              "risk.stop_loss": 3.5}
    schema = target.build(values)
    periods = {ind.alias: ind.params["period"] for ind in schema.indicators}
    assert periods["sma_fast"] == 8 and periods["sma_slow"] == 33
    doc = yaml.safe_load(target.to_yaml(values))
    assert doc["strategy"]["entry"]["conditions"][1]["compare_to"] == 62.0
    assert doc["risk"]["stop_loss"]["percent"] == 3.5
    # 바꾼 YAML을 다시 읽으면 그 값이 새 기본값
    again = load_target(yaml_content=target.to_yaml(values))
    assert again.defaults()["sma_slow.period"] == 33


def test_template_declared_params_override_defaults():
    content = (TEMPLATES / "sma_crossover.kis.yaml").read_text()
    target = load_target(yaml_content=content)
    specs = {spec.name: spec for spec in target.params}
    assert {"fast_period", "slow_period", "risk.stop_loss", "risk.take_profit"} <= set(specs)
    assert [(c.smaller, c.larger) for c in target.constraints] == [("fast_period", "slow_period")]
    schema = target.build({**target.defaults(), "fast_period": 9, "slow_period": 45})
    assert sorted(ind.params["period"] for ind in schema.indicators) == [9, 45]
    doc = yaml.safe_load(target.to_yaml({**target.defaults(), "fast_period": 9}))
    assert doc["strategy"]["params"]["fast_period"]["default"] == 9


def test_unused_declared_param_is_skipped():
    # volatility_breakout 템플릿의 stop_loss_pct 는 선언만 있고 어디에서도 $stop_loss_pct 로 쓰이지 않는다.
    target = load_target(yaml_content=(TEMPLATES / "volatility_breakout.kis.yaml").read_text())
    names = {spec.name for spec in target.params}
    assert "stop_loss_pct" not in names
    assert "risk.stop_loss" in names


def test_all_templates_build_with_defaults():
    for path in sorted(TEMPLATES.glob("*.kis.yaml")):
        target = load_target(yaml_content=path.read_text())
        assert target.params, path.name
        target.build(target.defaults())


def test_search_space_choices():
    target = load_target(strategy_id="sma_crossover")
    space, fixed = search_space(target, [
        {"name": "fast_period", "optimize": True, "low": 3, "high": 20},
        {"name": "slow_period", "optimize": True, "low": 30, "high": 101, "step": 10},
        {"name": "stop_loss_pct", "optimize": False, "value": 4},
    ])
    by_name = {spec.name: spec for spec in space}
    assert (by_name["slow_period"].low, by_name["slow_period"].high) == (30, 100)  # 간격에 맞춰 내림
    assert fixed == {"stop_loss_pct": 4.0, "take_profit_pct": 10.0}
    with pytest.raises(ValueError):
        search_space(target, [{"name": "fast_period", "low": 1, "high": 20}])  # 정의 범위(2~50) 밖
    with pytest.raises(ValueError):
        search_space(target, [{"name": "nope"}])
    with pytest.raises(ValueError):
        search_space(target, [{"name": "fast_period", "optimize": False}])


def test_param_normalize_snaps_to_grid():
    spec = ParamSpec("x", "x", "float", 1.0, 0.5, 2.0, 0.05, "param", 0.0, 10.0)
    assert spec.normalize(0.7300000001) == 0.75
    assert spec.normalize(3.0) == 2.0


# ---- 목표 점수 -----------------------------------------------------------
def test_objectives():
    metrics = {"sharpe": 1.2, "sortino": 1.5, "cagr": 20.0, "total_return": 30.0, "max_drawdown": 10.0}
    assert score_metrics("sharpe", metrics) == 1.2
    assert score_metrics("calmar", metrics) == 2.0
    assert score_metrics("return_dd", metrics) == 3.0
    assert score_metrics("return_dd", {**metrics, "max_drawdown": 0.1}) == 30.0  # 낙폭은 최소 1%로 본다
    assert score_metrics("sharpe", {**metrics, "sharpe": float("nan")}) is None


def test_summarize_result_counts_buys():
    result = {
        "final_capital": 110.0,
        "metrics": {
            "basic": {"total_return": 10.0, "annual_return": 9.0, "max_drawdown": -4.0},
            "risk": {"sharpe_ratio": 1.1, "sortino_ratio": 1.4},
            "trading": {"win_rate": 50.0, "profit_loss_ratio": 1.5},
        },
        "trades": [{"direction": "Buy"}, {"direction": "Sell"}, {"direction": "Buy"}],
    }
    summary = summarize_result(result)
    assert summary["trades"] == 2 and summary["max_drawdown"] == 4.0


# ---- 실행기 (가짜 백테스트) ------------------------------------------------
def _space():
    return [
        ParamSpec("fast", "단기", "int", 5, 2, 30, 1, "param", 2, 30),
        ParamSpec("slow", "장기", "int", 40, 10, 100, 5, "param", 10, 100),
    ]


def _metrics(score, trades=10):
    return {"sharpe": score, "sortino": score, "cagr": score, "total_return": score, "max_drawdown": 5.0,
            "trades": trades, "win_rate": 50.0}


def _config(**overrides):
    base = dict(
        space=_space(), fixed={"stop": 5.0}, defaults={"fast": 5, "slow": 40, "stop": 5.0},
        constraints=[Constraint("fast", "slow", "단기 < 장기")],
        n_trials=30, concurrency=2, launch_gap=0.0, heartbeat=0.05, seed=7,
    )
    base.update(overrides)
    return HyperoptConfig(**base)


def _peak(params):
    return 3.0 - ((params["fast"] - 12) / 10) ** 2 - ((params["slow"] - 60) / 40) ** 2


def test_runner_finds_good_params_and_validates():
    calls = []
    events = []
    lock = threading.Lock()

    def evaluate(params, period, timeout):
        with lock:
            calls.append((dict(params), period))
        assert params["stop"] == 5.0
        assert params["fast"] < params["slow"]  # 크기 순서 조건을 어긴 조합은 돌지 않는다
        score = _peak(params) - (0.5 if period == VALID else 0.0)
        return Evaluation(ok=True, metrics=_metrics(score), equity_curve={"2025-01-02": 1.0})

    result = HyperoptRunner(_config(n_trials=40), evaluate, events.append).run()

    assert result["stopped_reason"] == "completed"
    assert result["counts"]["trials"] == 40
    best = result["candidates"][0]
    assert best["rank"] == 1
    assert best["train"]["score"] > 2.5
    assert best["valid"]["score"] == pytest.approx(best["train"]["score"] - 0.5)
    labels = [candidate["label"] for candidate in result["candidates"]]
    assert labels[:3] == ["1위", "2위", "3위"] and labels[-1] == "기본값"
    # 같은 조합은 한 번만 돈다
    train_keys = [(c[0]["fast"], c[0]["slow"]) for c in calls if c[1] != VALID]
    assert len(train_keys) == len(set(train_keys))
    trial_events = [e for e in events if e["type"] == "trial"]
    assert trial_events[0]["kind"] == "baseline" and trial_events[0]["number"] == 0
    assert [e for e in events if e["type"] == "validation"]


def test_runner_stops_when_space_is_exhausted():
    space = [ParamSpec("fast", "단기", "int", 3, 2, 4, 1, "param", 2, 4)]
    calls = []

    def evaluate(params, period, timeout):
        calls.append(params["fast"])
        return Evaluation(ok=True, metrics=_metrics(params["fast"]))

    config = _config(space=space, constraints=[], fixed={}, defaults={"fast": 3}, n_trials=20, validate=False)
    result = HyperoptRunner(config, evaluate, lambda e: None).run()
    assert result["stopped_reason"] == "space_exhausted"
    assert sorted(set(calls)) == [2, 3, 4]
    assert result["counts"]["trials"] == 3
    assert result["counts"]["duplicates"] > 0


def test_runner_retries_timeouts_once():
    attempts = {}
    lock = threading.Lock()

    def evaluate(params, period, timeout):
        key = (params["fast"], params["slow"])
        with lock:
            attempts[key] = attempts.get(key, 0) + 1
            first = attempts[key] == 1
        if first and params["fast"] % 2 == 0:
            return Evaluation(ok=False, error="Lean 백테스트 타임아웃 (5초)", timed_out=True)
        return Evaluation(ok=True, metrics=_metrics(_peak(params)))

    events = []
    result = HyperoptRunner(_config(n_trials=10, validate=False), evaluate, events.append).run()
    assert result["counts"]["retries"] >= 1
    assert result["counts"]["failed"] == 0
    assert any(e["type"] == "retry" for e in events)
    assert all(e["state"] == "ok" for e in events if e["type"] == "trial")


def test_runner_marks_few_trades_and_excludes_them_from_best():
    def evaluate(params, period, timeout):
        trades = 1 if params["fast"] > 15 else 10
        score = 100.0 if trades == 1 else _peak(params)
        return Evaluation(ok=True, metrics=_metrics(score, trades=trades))

    events = []
    result = HyperoptRunner(_config(n_trials=25, min_trades=3, validate=False), evaluate, events.append).run()
    assert result["counts"]["few_trades"] > 0
    assert all(c["train"]["metrics"]["trades"] >= 3 for c in result["candidates"])
    assert all(c["train"]["score"] < 100 for c in result["candidates"] if c["rank"])


def test_runner_cancel_kills_running_jobs():
    cancel = threading.Event()
    killed = []
    started = threading.Event()

    def evaluate(params, period, timeout):
        started.set()
        if params["fast"] != 5:  # 기본값 시행 다음부터 오래 걸린다
            cancel.wait(5)
        return Evaluation(ok=True, metrics=_metrics(_peak(params)))

    runner = HyperoptRunner(
        _config(n_trials=50), evaluate, lambda e: None, cancel=cancel, on_cancel=lambda: killed.append(True)
    )
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    result = runner.run()
    timer.cancel()
    assert result["stopped_reason"] == "cancelled"
    assert result["candidates"] == []
    assert killed


def test_runner_aborts_after_consecutive_failures():
    def evaluate(params, period, timeout):
        return Evaluation(ok=False, error="Docker가 실행되지 않습니다.")

    with pytest.raises(HyperoptAborted, match="연속 실패"):
        HyperoptRunner(_config(n_trials=20, concurrency=1), evaluate, lambda e: None).run()


# ---- 라우트 도우미 ---------------------------------------------------------
def test_split_period_and_estimate():
    from fastapi import HTTPException

    from backend.routes.optimize import estimate_run_seconds, split_period

    train, valid = split_period(date(2025, 1, 1), date(2025, 12, 31), 0.3)
    assert train[0] == date(2025, 1, 1) and valid[1] == date(2025, 12, 31)
    assert train[1] < valid[0]
    assert split_period(date(2025, 1, 1), date(2025, 12, 31), 0) == ((date(2025, 1, 1), date(2025, 12, 31)), None)
    with pytest.raises(HTTPException):
        split_period(date(2025, 1, 1), date(2025, 1, 10), 0.3)
    daily = estimate_run_seconds(1, date(2025, 1, 1), date(2025, 12, 31), "daily", 1)
    minute = estimate_run_seconds(1, date(2025, 1, 1), date(2025, 12, 31), "minute", 5)
    assert 5 < daily < 6 < minute


def test_minute_warmup_start_matches_backtest_rule():
    from backend.routes.backtest import bars_per_day, minute_warmup_start

    days = [date(2025, 11, d) for d in (3, 4, 5, 6, 7, 10, 11, 12, 13, 14)]
    # 5분봉 하루 77봉 → 100봉이면 2거래일 + 여유 1일 = 3거래일 전
    assert bars_per_day(5) == 77
    assert minute_warmup_start(days, date(2025, 11, 12), 100, 5) == date(2025, 11, 7)
    assert minute_warmup_start(days, date(2025, 11, 12), 0, 5) == date(2025, 11, 12)
    assert minute_warmup_start(days, date(2025, 11, 4), 1000, 5) == date(2025, 11, 3)  # 있는 만큼만
