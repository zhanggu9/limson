"""종목 검색(초성·정렬·공백 무시) 테스트."""

import sys
from pathlib import Path

BACKTESTER_DIR = Path(__file__).resolve().parents[1]
if str(BACKTESTER_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTESTER_DIR))

from backend.routes import symbols  # noqa: E402

STOCKS = [
    {"code": "448330", "name": "KODEX 삼성전자채권혼합", "exchange": "kospi"},
    {"code": "005935", "name": "삼성전자우", "exchange": "kospi"},
    {"code": "005930", "name": "삼성전자", "exchange": "kospi"},
    {"code": "000660", "name": "SK하이닉스", "exchange": "kospi"},
    {"code": "035720", "name": "카카오", "exchange": "kospi"},
    {"code": "086520", "name": "에코프로", "exchange": "kosdaq"},
]


def _codes(monkeypatch, query, **kwargs):
    monkeypatch.setattr(symbols, "_get_all_symbols", lambda: STOCKS)
    return [s["code"] for s in symbols.search_symbols(query, **kwargs)]


def test_chosung_matches_syllables(monkeypatch):
    assert _codes(monkeypatch, "ㅅㅅㅈㅈ") == ["005930", "005935", "448330"]
    assert _codes(monkeypatch, "ㅋㅋㅇ") == ["035720"]
    assert _codes(monkeypatch, "삼ㅅㅈ")[0] == "005930"  # 완성 글자 + 초성 섞기


def test_ranking_and_normalization(monkeypatch):
    assert _codes(monkeypatch, "005930") == ["005930"]
    assert _codes(monkeypatch, "A005930") == ["005930"]  # HTS 형식
    assert _codes(monkeypatch, "sk 하이닉스") == ["000660"]  # 대소문자·공백 무시
    assert _codes(monkeypatch, "삼성전자") == ["005930", "005935", "448330"]  # 일치 → 앞부분 → 중간


def test_exchange_filter_and_limit(monkeypatch):
    assert _codes(monkeypatch, "ㅇ", exchange="kosdaq") == ["086520"]
    assert len(_codes(monkeypatch, "ㅅ", limit=2)) == 2
    assert _codes(monkeypatch, "   ") == []
