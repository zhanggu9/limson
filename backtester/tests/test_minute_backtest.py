"""분봉 백테스트 경로 테스트.

실제 DB·KIS API·LEAN Docker 없이 검증한다.
- KIS 주식일별분봉조회 페이지네이션/휴장일 필터 (분봉 수집용 공급자)
- 1분봉 → N분봉 집계, 종가 단일가 봉, 가격 불연속 탐지
- PostgreSQL 분봉으로 날짜별 CSV 준비 (워밍업 구간, 빈 날, 경고)
- 기간·규모 검증과 /minute-coverage
- 분봉용 LEAN 코드 생성(봉 주기, 워밍업, 장마감 청산)과 생성된 데이터 리더 동작
- /api/backtest/run 의 resolution·bar_minutes·intraday_exit 처리
"""

import asyncio
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKTESTER_DIR = Path(__file__).resolve().parents[1]
if str(BACKTESTER_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTESTER_DIR))

from kis_backtest.codegen.generator import CodeGenConfig, LeanCodeGenerator  # noqa: E402
from kis_backtest.exceptions import KISError  # noqa: E402
from kis_backtest.lean.data_converter import DataConverter  # noqa: E402
from kis_backtest.providers.kis.data import KISDataProvider  # noqa: E402
from kis_backtest.strategies.registry import StrategyRegistry  # noqa: E402
import kis_backtest.strategies.preset  # noqa: E402,F401  (전략 등록)


# ---------------------------------------------------------------------------
# KIS 분봉 조회
# ---------------------------------------------------------------------------

def _session_minutes(day: str) -> list[dict]:
    """09:00~15:19 + 15:30 체결 분봉 (KIS 응답 형식, 최신 시각이 먼저)."""
    rows = []
    t = datetime.strptime(f"{day}0900", "%Y%m%d%H%M")
    while t <= datetime.strptime(f"{day}1519", "%Y%m%d%H%M"):
        rows.append(t)
        t += timedelta(minutes=1)
    rows.append(datetime.strptime(f"{day}1530", "%Y%m%d%H%M"))
    return [
        {
            "stck_bsop_date": day,
            "stck_cntg_hour": r.strftime("%H%M%S"),
            "stck_oprc": "70000", "stck_hgpr": "70100", "stck_lwpr": "69900",
            "stck_prpr": "70050", "cntg_vol": "10",
        }
        for r in sorted(rows, reverse=True)
    ]


class _FakeResp:
    def __init__(self, rows=None, ok=True, code="", message=""):
        self._rows = rows or []
        self._ok = ok
        self.error_code = code
        self.error_message = message

    def is_ok(self):
        return self._ok

    def get_output2(self):
        return self._rows


class _FakeAuth:
    """FID_INPUT_HOUR_1 이하 시각을 최신순으로 최대 120건 돌려주는 가짜 KIS."""

    is_paper = False

    def __init__(self, days: dict[str, list[dict]], fail: bool = False):
        self.days = days
        self.fail = fail
        self.calls = []

    def get(self, endpoint, params, tr_id):
        self.calls.append((params["FID_INPUT_DATE_1"], params["FID_INPUT_HOUR_1"], tr_id))
        if self.fail:
            return _FakeResp(ok=False, code="EGW02004", message="모의투자에서는 해당업무가 제공되지 않습니다.")
        day = params["FID_INPUT_DATE_1"]
        hour = params["FID_INPUT_HOUR_1"]
        rows = [r for r in self.days.get(day, []) if r["stck_cntg_hour"] <= hour]
        return _FakeResp(rows[:120])

    def smart_sleep(self):
        pass


def test_minute_bars_paginate_full_session():
    day = "20261001"
    auth = _FakeAuth({day: _session_minutes(day)})
    bars = KISDataProvider(auth).get_minute_bars_for_date("005930", date(2026, 10, 1))

    assert len(bars) == 381  # 09:00~15:19 380개 + 15:30 종가 1개
    assert bars[0].time == datetime(2026, 10, 1, 9, 0)
    assert bars[-1].time == datetime(2026, 10, 1, 15, 30)
    assert all(a.time < b.time for a, b in zip(bars, bars[1:]))
    assert {c[2] for c in auth.calls} == {"FHKST03010230"}
    assert len(auth.calls) <= 5


def test_minute_bars_drop_other_business_dates():
    """휴장일 요청에 직전 거래일 데이터가 와도 그날 봉으로 쓰지 않는다."""
    holiday = "20261003"
    previous = _session_minutes("20261002")
    auth = _FakeAuth({holiday: previous})
    assert KISDataProvider(auth).get_minute_bars_for_date("005930", date(2026, 10, 3)) == []


def test_minute_bars_raise_on_api_error():
    auth = _FakeAuth({}, fail=True)
    with pytest.raises(KISError, match="분봉 조회 실패"):
        KISDataProvider(auth).get_minute_bars_for_date("005930", date(2026, 10, 1))




# ---------------------------------------------------------------------------
# 1분봉 → N분봉 집계, 가격 불연속 탐지
# ---------------------------------------------------------------------------

def _db_session(close_bar: bool = True, end_hhmm: int = 1519) -> list[tuple]:
    """DB 형식 하루치 1분봉: 09:00~end_hhmm 연속 + (선택) 15:30 종가 단일가 봉."""
    bars = []
    minute = 9 * 60
    while (minute // 60) * 100 + minute % 60 <= end_hhmm:
        hhmm = (minute // 60) * 100 + minute % 60
        bars.append((hhmm, 1000 + minute, 1001 + minute, 999 + minute, 1000 + minute, 1))
        minute += 1
    if close_bar:
        bars.append((1530, 2000, 2000, 2000, 2000, 50))
    return bars


def test_aggregate_one_minute_keeps_bars_and_end_times():
    out = DataConverter.aggregate_minute_bars(_db_session(), 1)
    assert len(out) == 381
    assert out[0][:2] == (540, 541)  # 09:00~09:01
    assert out[-2][:2] == (919, 920)  # 15:19~15:20
    assert out[-1] == (930, 931, 2000, 2000, 2000, 2000, 50)  # 15:30 종가 단일가 봉


def test_aggregate_five_minutes_ohlcv():
    out = DataConverter.aggregate_minute_bars(_db_session(), 5)
    assert len(out) == 77  # 380분 / 5 + 종가 봉
    first = out[0]
    assert first == (540, 545, 1540, 1545, 1539, 1544, 5)  # 시가=첫 봉, 고가=max, 저가=min, 종가=끝 봉, 거래량 합
    assert out[-2][:2] == (915, 920)
    assert out[-1][:2] == (930, 931)


def test_aggregate_sixty_minutes_clamps_to_closing_auction():
    out = DataConverter.aggregate_minute_bars(_db_session(), 60)
    assert [b[:2] for b in out] == [
        (540, 600), (600, 660), (660, 720), (720, 780), (780, 840), (840, 900),
        (900, 930),  # 15:00~ 봉은 16:00이 아니라 종가 단일가 시작(15:30)에서 끝난다
        (930, 931),
    ]


def test_aggregate_late_close_day_and_illiquid_days():
    # 수능일처럼 16:20까지 연속 매매 후 16:30 종가 봉
    late = [b for b in _db_session(close_bar=False, end_hhmm=1619)] + [(1630, 5, 5, 5, 5, 1)]
    out = DataConverter.aggregate_minute_bars(late, 30)
    assert out[-2][:2] == (960, 990) and out[-1][:2] == (990, 991)
    # 종가 봉만 있는 날
    assert DataConverter.aggregate_minute_bars([(1530, 7, 7, 7, 7, 3)], 5) == [(930, 931, 7, 7, 7, 7, 3)]
    # 종가 봉 없이 15:12에 끝난 날: 마지막 봉을 종가 봉으로 오인하지 않는다
    out = DataConverter.aggregate_minute_bars([(1510, 1, 1, 1, 1, 1), (1512, 2, 2, 2, 2, 1)], 5)
    assert out == [(910, 915, 1, 2, 1, 2, 2)]
    assert DataConverter.aggregate_minute_bars([], 5) == []


def test_write_lean_minute_day(tmp_path):
    path = tmp_path / "a" / "20261001.csv"
    DataConverter.write_lean_minute_day([(540, 545, 1, 2, 0, 1, 9)], date(2026, 10, 1), path)
    assert path.read_text() == "202610010900,1,2,0,1,9,202610010905\n"
    DataConverter.write_lean_minute_day([], date(2026, 10, 1), path)
    assert path.read_text() == ""


def test_find_price_gaps_flags_split_like_jumps():
    days = {
        date(2026, 10, 1): [(900, 100, 100, 100, 100, 1), (1530, 8000, 8000, 8000, 8000, 1)],
        date(2026, 10, 2): [(900, 4000, 4000, 4000, 4000, 1)],   # 0.5배 → 분할 의심
        date(2026, 10, 5): [],                                    # 거래정지: 건너뜀
        date(2026, 10, 6): [(900, 5000, 5000, 5000, 5000, 1)],   # +25%: 가격제한폭 이내
    }
    assert DataConverter.find_price_gaps(days) == [(date(2026, 10, 2), 0.5)]


# ---------------------------------------------------------------------------
# 기간·규모 검증
# ---------------------------------------------------------------------------

@pytest.fixture
def backtest_routes():
    from backend.routes import backtest as routes
    return routes


COVERAGE = (date(2025, 10, 10), date(2026, 10, 30))


@pytest.mark.parametrize("start,end,message", [
    ("2025-10-01", "2025-10-31", "2025-10-10부터"),
    ("2026-11-02", "2026-11-03", "2026-10-30까지"),
    ("2025-10-10", "2026-10-30", "규모가 너무 큽니다"),  # 20종목 × 1년 × 1분봉
])
def test_validate_minute_range_rejects(backtest_routes, start, end, message):
    with pytest.raises(HTTPException) as exc:
        backtest_routes.validate_minute_range(start, end, 20, 1, COVERAGE)
    assert exc.value.status_code == 400 and message in exc.value.detail


def test_validate_minute_range_future_and_missing_table(backtest_routes):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(HTTPException) as exc:
        backtest_routes.validate_minute_range("2026-10-01", tomorrow, 1, 1, COVERAGE)
    assert "오늘 이후" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        backtest_routes.validate_minute_range("2026-10-01", "2026-10-02", 1, 1, None)
    assert "PG_MINUTE_TABLE" in exc.value.detail


def test_validate_minute_range_allows_bigger_runs_with_longer_bars(backtest_routes):
    start, end, warnings = backtest_routes.validate_minute_range("2025-10-10", "2026-10-30", 20, 5, COVERAGE)
    assert (start, end, warnings) == (date(2025, 10, 10), date(2026, 10, 30), [])
    _, _, warnings = backtest_routes.validate_minute_range("2026-10-20", "2026-11-02", 1, 1, COVERAGE)
    assert "2026-10-30 이후 분봉은 아직 DB에 없습니다" in warnings[0]


def test_bar_estimates(backtest_routes):
    assert backtest_routes.bars_per_day(1) == 381
    assert backtest_routes.bars_per_day(5) == 77
    assert backtest_routes.bars_per_day(60) == 8
    # 2026-10-26(월) ~ 2026-11-01(일): 영업일 5일
    assert backtest_routes.estimate_minute_bars(2, date(2026, 10, 26), date(2026, 11, 1), 5) == 2 * 5 * 77


# ---------------------------------------------------------------------------
# PostgreSQL 분봉 → LEAN CSV 준비
# ---------------------------------------------------------------------------

class _FakeMinuteProvider:
    """PostgresDataProvider 대역: 평일마다 _db_session() 하루치를 돌려준다."""

    def __init__(self, coverage=COVERAGE, symbols_with_data=("005930",), gap_day=None, fail=False):
        self.coverage = coverage
        self.symbols_with_data = symbols_with_data
        self.gap_day = gap_day
        self.fail = fail
        self.requests = []

    def __call__(self):  # PostgresDataProvider() 대신 호출된다
        return self

    def get_minute_coverage(self):
        if self.fail:
            from psycopg import OperationalError
            raise OperationalError("connection refused")
        return self.coverage

    def get_minute_days(self, symbols, start, end):
        self.requests.append((tuple(symbols), start, end))
        result = {s: {} for s in symbols}
        for symbol in symbols:
            if symbol not in self.symbols_with_data:
                continue
            day = start
            while day <= end:
                if day.weekday() < 5:
                    bars = _db_session()
                    if day == self.gap_day:
                        bars = [(t, o * 3, h * 3, lo * 3, c * 3, v) for t, o, h, lo, c, v in bars]
                    result[symbol][day] = bars
                day += timedelta(days=1)
        return result


def _patch_provider(monkeypatch, fake):
    monkeypatch.setattr("kis_backtest.providers.postgres.PostgresDataProvider", fake)


def test_prepare_minute_data_writes_bars_warmup_and_empty_days(tmp_path, monkeypatch, backtest_routes):
    fake = _FakeMinuteProvider()
    _patch_provider(monkeypatch, fake)

    # 2026-10-26(월) ~ 2026-11-01(일), 5분봉, 지표 준비에 100봉 필요 → 77봉/일이라 2거래일 + 여유 1일
    result = asyncio.run(backtest_routes.prepare_minute_market_data(
        ["005930"], "2026-10-26", "2026-11-01", tmp_path, bar_minutes=5, warmup_bars=100))

    assert result["errors"] == [] and result["downloaded"] == ["005930"]
    assert result["source"] == "PostgreSQL 분봉"
    # 직전 3거래일: 10/21(수), 10/22(목), 10/23(금) → 워밍업은 10/21부터 5일
    assert result["warmup_days"] == 5
    assert "2026-10-30 이후 분봉은 아직 DB에 없습니다" in result["warnings"][0]

    symbol_dir = tmp_path / "data" / "equity" / "krx" / "minute" / "5m" / "005930"
    monday = (symbol_dir / "20261026.csv").read_text().splitlines()
    assert len(monday) == 77
    assert monday[0] == "202610260900,1540,1545,1539,1544,5,202610260905"
    assert monday[-1] == "202610261530,2000,2000,2000,2000,50,202610261531"
    assert (symbol_dir / "20261021.csv").read_text() != ""  # 워밍업 데이터
    assert (symbol_dir / "20261025.csv").read_text() == ""  # 주말: 빈 파일
    assert (symbol_dir / "20261101.csv").exists()
    assert not (symbol_dir / "20261020.csv").exists()  # 워밍업보다 이른 날은 쓰지 않는다


def test_prepare_minute_data_without_warmup_and_with_errors(tmp_path, monkeypatch, backtest_routes):
    fake = _FakeMinuteProvider(gap_day=date(2026, 10, 28))
    _patch_provider(monkeypatch, fake)

    result = asyncio.run(backtest_routes.prepare_minute_market_data(
        ["005930", "999999"], "2026-10-26", "2026-10-30", tmp_path, bar_minutes=1, warmup_bars=0))

    assert fake.requests == [(("005930", "999999"), date(2026, 10, 26), date(2026, 10, 30))]
    assert result["warmup_days"] == 0
    assert result["downloaded"] == ["005930"]
    assert [e["symbol"] for e in result["errors"]] == ["999999"]
    assert any("005930 2026-10-28" in w and "가격 불연속" in w for w in result["warnings"])
    assert (tmp_path / "data/equity/krx/minute/1m/005930/20261026.csv").exists()


def test_prepare_minute_data_reports_db_errors(tmp_path, monkeypatch, backtest_routes):
    _patch_provider(monkeypatch, _FakeMinuteProvider(fail=True))
    result = asyncio.run(backtest_routes.prepare_minute_market_data(
        ["005930", "000660"], "2026-10-26", "2026-10-30", tmp_path))
    assert [e["symbol"] for e in result["errors"]] == ["005930", "000660"]
    assert "PG_MINUTE_TABLE" in result["errors"][0]["error"]


def test_minute_coverage_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app

    _patch_provider(monkeypatch, _FakeMinuteProvider())
    body = TestClient(app).get("/api/backtest/minute-coverage").json()
    assert body["earliest"] == "2025-10-10" and body["latest"] == "2026-10-30"
    assert body["bar_minutes"] == [1, 3, 5, 10, 15, 30, 60]
    assert body["bars_per_day"]["5"] == 77 and body["max_bars"] > 0

    _patch_provider(monkeypatch, _FakeMinuteProvider(fail=True))
    assert TestClient(app).get("/api/backtest/minute-coverage").status_code == 503


# ---------------------------------------------------------------------------
# LEAN 코드 생성
# ---------------------------------------------------------------------------

def _generate(resolution: str, **config) -> str:
    definition = StrategyRegistry.build("sma_crossover")
    generator = LeanCodeGenerator(definition, config=CodeGenConfig(resolution=resolution, **config))
    return generator.generate(["005930"], "2026-09-01", "2026-09-30", 100_000_000)


def test_codegen_minute_uses_interval_folder_and_warmup():
    code = _generate("minute", bar_minutes=5, warmup_days=4)
    assert 'f"/Data/equity/krx/minute/5m/{symbol}/{date.strftime(\'%Y%m%d\')}.csv"' in code
    assert "self.AddData(KRXEquity, symbol_str, Resolution.Minute)" in code
    assert "self.SetWarmUp(timedelta(days=4))" in code
    assert "5분봉" in code
    # 벤치마크 지수는 일봉 그대로
    assert 'self.AddData(KRXIndex, "kospi", Resolution.Daily)' in code
    assert "entry_signal = False" not in code  # 장마감 청산 미사용


def test_codegen_minute_without_warmup_data():
    code = _generate("minute")
    assert "self.SetWarmUp(" not in code
    assert "워밍업 없이" in code


def test_codegen_intraday_exit_guards():
    code = _generate("minute", bar_minutes=5, intraday_exit="15:15")
    assert "self.Time.hour * 60 + self.Time.minute >= 915:  # 15:15 이후 신규 진입 금지" in code
    assert "entry_signal = False" in code
    assert ">= 915 and holdings > 0:  # 15:15 장마감 청산" in code
    # 진입 판단 직전·청산 판단 직전에 들어간다
    assert code.index("entry_signal = False") < code.index("if entry_signal and holdings == 0")
    assert code.index("exit_signal = True") < code.index("if exit_signal and holdings > 0")


def test_codegen_daily_is_unchanged():
    code = _generate("daily")
    assert 'source = f"/Data/equity/krx/daily/{symbol}.csv"' in code
    assert "self.AddData(KRXEquity, symbol_str, Resolution.Daily)" in code
    assert ", Resolution.Daily)" in code.split("self.SetWarmUp(")[1].splitlines()[0]
    assert "Resolution.Minute" not in code


@pytest.mark.parametrize("config", [
    {"resolution": "hour"},
    {"market": "us", "resolution": "minute"},
    {"resolution": "minute", "bar_minutes": 7},
    {"resolution": "daily", "bar_minutes": 5},
    {"resolution": "daily", "intraday_exit": "15:00"},
    {"resolution": "minute", "intraday_exit": "16:00"},
    {"resolution": "minute", "intraday_exit": "09:00"},
    {"resolution": "minute", "intraday_exit": "1500"},
])
def test_codegen_rejects_invalid_config(config):
    with pytest.raises(ValueError):
        CodeGenConfig(**config)


def test_warmup_bars_follow_indicator_periods():
    definition = StrategyRegistry.build_with_params("sma_crossover", fast_period=5, slow_period=60)
    assert LeanCodeGenerator(definition).warmup_bars() >= 60


def _load_generated_module(code: str) -> dict:
    """LEAN 없이 생성 코드의 데이터 클래스를 실행해 보기 위한 최소 스텁."""

    class PythonData(dict):
        def __setitem__(self, key, value):
            dict.__setitem__(self, key, value)

    class SubscriptionDataSource:
        def __init__(self, source, medium, fmt):
            self.Source = source

    class _Anything:
        def __getattr__(self, name):
            return name

    stub = types.ModuleType("AlgorithmImports")
    for name in ("SubscriptionTransportMedium", "FileFormat", "Resolution", "OrderDirection"):
        setattr(stub, name, _Anything())
    stub.PythonData = PythonData
    stub.SubscriptionDataSource = SubscriptionDataSource
    stub.FeeModel = object
    stub.QCAlgorithm = object
    stub.TradeBar = object
    stub.OrderFee = stub.CashAmount = lambda *a, **k: None
    sys.modules["AlgorithmImports"] = stub
    try:
        namespace: dict = {}
        exec(compile(code, "<lean_generated>", "exec"), namespace)
        return namespace
    finally:
        del sys.modules["AlgorithmImports"]


def test_generated_minute_reader_parses_csv_rows():
    ns = _load_generated_module(_generate("minute", bar_minutes=5))
    equity = ns["KRXEquity"]()
    config = types.SimpleNamespace(Symbol=types.SimpleNamespace(Value="005930"))

    source = equity.GetSource(config, datetime(2026, 9, 1), False)
    assert source.Source == "/Data/equity/krx/minute/5m/005930/20260901.csv"

    bar = equity.Reader(config, "202609011500,70000,70100,69900,70050,1234,202609011520", datetime(2026, 9, 1), False)
    assert bar.Time == datetime(2026, 9, 1, 15, 0)
    assert bar.EndTime == datetime(2026, 9, 1, 15, 20)
    assert bar["Close"] == 70050.0 and bar["Volume"] == 1234
    assert equity.Reader(config, "", datetime(2026, 9, 1), False) is None
    assert equity.Reader(config, "broken", datetime(2026, 9, 1), False) is None
    # 끝 시각이 없는 예전 1분봉 캐시 형식은 읽지 않는다
    assert equity.Reader(config, "202609010901,1,1,1,1,1", datetime(2026, 9, 1), False) is None


# ---------------------------------------------------------------------------
# /api/backtest/run
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_lean(tmp_path, monkeypatch):
    """데이터 준비·벤치마크·LEAN 실행을 가짜로 바꾸고 호출 내용을 기록한다."""
    from backend.routes import backtest as routes
    from kis_backtest.lean.executor import LeanExecutor, LeanRun
    from kis_backtest.lean.project_manager import LeanProjectManager

    monkeypatch.setattr(LeanProjectManager, "workspace", tmp_path)
    calls = {}

    async def fake_minute(symbols, start_date, end_date, workspace, bar_minutes=1, warmup_bars=0):
        calls["minute"] = (symbols, start_date, end_date, bar_minutes, warmup_bars)
        return {"downloaded": symbols, "skipped": [], "errors": [], "source": "PostgreSQL 분봉",
                "warnings": ["005930 2026-10-28: 가격 불연속"], "warmup_days": 3}

    async def fake_benchmark(**kwargs):
        return False

    def fake_run(project, **kwargs):
        calls["code"] = project.main_py.read_text()
        calls["timeout"] = kwargs.get("timeout")
        run = LeanRun(project=project, success=True, output_dir=project.output_dir)
        run.raw_result = {"statistics": {"Net Profit": "1.5%", "Start Equity": "100000000",
                                         "End Equity": "101500000"}, "charts": {}, "orders": {}}
        return run

    monkeypatch.setattr(routes, "prepare_minute_market_data", fake_minute)
    monkeypatch.setattr(routes, "prepare_benchmark_data", fake_benchmark)
    monkeypatch.setattr(LeanExecutor, "check_docker", classmethod(lambda cls: True))
    monkeypatch.setattr(LeanExecutor, "check_image", classmethod(lambda cls: True))
    monkeypatch.setattr(LeanExecutor, "run", classmethod(lambda cls, project, **kw: fake_run(project, **kw)))
    return calls


def test_run_endpoint_minute_end_to_end(fake_lean):
    from fastapi.testclient import TestClient

    from backend.main import app

    client = TestClient(app)
    response = client.post("/api/backtest/run", json={
        "strategy_id": "sma_crossover", "symbols": ["005930"],
        "start_date": "2026-10-01", "end_date": "2026-10-30",
        "resolution": "minute", "bar_minutes": 5, "intraday_exit": "15:15",
    })
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["resolution"] == "minute" and data["bar_minutes"] == 5
    assert data["intraday_exit"] == "15:15"
    assert data["data_warnings"] == ["005930 2026-10-28: 가격 불연속"]

    symbols, start, end, bar_minutes, warmup_bars = fake_lean["minute"]
    assert (symbols, start, end, bar_minutes) == (["005930"], "2026-10-01", "2026-10-30", 5)
    assert warmup_bars >= 30
    assert "/Data/equity/krx/minute/5m/" in fake_lean["code"]
    assert "self.SetWarmUp(timedelta(days=3))" in fake_lean["code"]
    assert "entry_signal = False" in fake_lean["code"]
    assert fake_lean["timeout"] == 840


@pytest.mark.parametrize("extra,status", [
    ({"resolution": "hour"}, 422),
    ({"resolution": "minute", "bar_minutes": 7}, 422),
    ({"resolution": "minute", "intraday_exit": "3pm"}, 422),
    ({"resolution": "minute", "intraday_exit": "16:00"}, 400),
    ({"resolution": "daily", "bar_minutes": 5}, 400),
])
def test_run_endpoint_rejects_bad_minute_options(fake_lean, extra, status):
    from fastapi.testclient import TestClient

    from backend.main import app

    response = TestClient(app).post("/api/backtest/run", json={
        "strategy_id": "sma_crossover", "symbols": ["005930"],
        "start_date": "2026-10-01", "end_date": "2026-10-30", **extra,
    })
    assert response.status_code == status, response.text
    assert "minute" not in fake_lean  # 데이터 준비 전에 거절


def test_run_custom_endpoint_passes_minute_options(fake_lean):
    from fastapi.testclient import TestClient

    from backend.main import app

    yaml_content = (
        "version: \"1.0\"\n"
        "metadata:\n  name: test\n  id: test_custom\n"
        "strategy:\n"
        "  id: test_custom\n"
        "  indicators:\n    - id: sma\n      alias: sma_fast\n      params: {period: 5}\n"
        "  entry:\n    conditions:\n      - indicator: close\n        operator: greater_than\n        compare_to: sma_fast\n"
        "  exit:\n    conditions:\n      - indicator: close\n        operator: less_than\n        compare_to: sma_fast\n"
    )
    response = TestClient(app).post("/api/backtest/run-custom", json={
        "yaml_content": yaml_content, "symbols": ["005930"],
        "start_date": "2026-10-01", "end_date": "2026-10-30",
        "resolution": "minute", "bar_minutes": 15,
    })
    assert response.status_code == 200, response.text
    assert response.json()["data"]["bar_minutes"] == 15
    assert fake_lean["minute"][3] == 15
    assert "/Data/equity/krx/minute/15m/" in fake_lean["code"]


def test_lean_order_times_come_back_on_the_data_clock(backtest_routes):
    """LEAN은 뉴욕 시간대로 해석한 데이터 시각을 UTC로 기록한다 → CSV의 한국 시각으로 되돌린다 (서머타임 포함)."""
    to_clock = backtest_routes._lean_time_to_data_clock
    assert to_clock("2025-11-03T15:40:00Z") == "2025-11-03T10:40:00"  # 표준시 (UTC-5)
    assert to_clock("2026-07-01T13:05:00Z") == "2026-07-01T09:05:00"  # 서머타임 (UTC-4)
    assert to_clock("") == "" and to_clock("2026-07-01 09:05") == "2026-07-01 09:05"
