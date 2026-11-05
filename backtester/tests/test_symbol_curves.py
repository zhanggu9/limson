"""결과 차트용 종목 가격선(_load_symbol_curves) 테스트."""

import sys
from pathlib import Path

BACKTESTER_DIR = Path(__file__).resolve().parents[1]
if str(BACKTESTER_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTESTER_DIR))

from backend.routes.backtest import _load_symbol_curves  # noqa: E402


def test_daily_curve_reads_workspace_csv_in_range(tmp_path):
    daily = tmp_path / "data" / "equity" / "krx" / "daily"
    daily.mkdir(parents=True)
    (daily / "000660.csv").write_text(
        "20251009,1,1,1,100,10\n"   # 기간 밖
        "20251010,1,1,1,110,10\n"
        "20251013,1,1,1,121,10\n"
        "bad line\n"
    )
    curves = _load_symbol_curves(tmp_path, ["000660", "005930"], "2025-10-10", "2025-10-13")
    # 파일이 없는 005930은 빠진다
    assert curves == {"000660": {"2025-10-10": 110.0, "2025-10-13": 121.0}}


def test_minute_curve_uses_last_bar_close_of_each_day(tmp_path):
    day_dir = tmp_path / "data" / "equity" / "krx" / "minute" / "5m" / "000660"
    day_dir.mkdir(parents=True)
    (day_dir / "20251010.csv").write_text(
        "202510100900,1,1,1,200,5,202510100905\n"
        "202510101530,1,1,1,205,5,202510101530\n"
    )
    (day_dir / "20251009.csv").write_text("202510090900,1,1,1,190,5,202510090905\n")  # 워밍업(기간 밖)
    (day_dir / "20251013.csv").write_text("")  # 빈 파일은 건너뜀
    curves = _load_symbol_curves(
        tmp_path, ["000660"], "2025-10-10", "2025-10-13", resolution="minute", bar_minutes=5,
    )
    assert curves == {"000660": {"2025-10-10": 205.0}}


def test_no_data_returns_none(tmp_path):
    assert _load_symbol_curves(tmp_path, ["000660"], "2025-10-10", "2025-10-13") is None
    assert _load_symbol_curves(tmp_path, ["000660"], "bad", "2025-10-13") is None
