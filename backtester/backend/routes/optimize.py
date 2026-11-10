"""하이퍼옵트 API (전략 파라미터 자동 탐색).

- POST /api/optimize/space  전략에서 바꿔 볼 수 있는 파라미터와 기본 범위
- POST /api/optimize/run    하이퍼옵트 실행. 진행 상황을 NDJSON(한 줄에 JSON 하나)으로 흘려보낸다.
- GET  /api/optimize/status 지금 하이퍼옵트가 돌고 있는지

요청 하나 안에서 끝까지 돌린다 (큐·백그라운드 작업 없음). 시행마다 LEAN을 subprocess.run으로
컨테이너가 끝날 때까지 기다리고, 시행 몇 개를 스레드로 동시에 돌린다. 브라우저 연결이 끊기면
멈추고 돌던 컨테이너를 정리한다. 한 번에 하나만 실행한다.

데이터는 일반 백테스트와 섞이지 않게 .lean-workspace/hyperopt/<실행ID>/data 에 한 번 준비하고
(PostgreSQL), 시행마다 코드만 새로 만든다. 끝나면 실행 폴더를 통째로 지운다.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import optuna
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.routes.backtest import (
    _DATA_PREP_ERRORS,
    DAILY_LEAN_TIMEOUT,
    MINUTE_LEAN_TIMEOUT,
    _classify_lean_error,
    _lean_run_to_api_response,
    _parse_request_dates,
    bars_per_day,
    minute_warmup_start,
    prepare_backtest_data,
    prepare_benchmark_data,
    validate_minute_range,
)
from backend.schemas.optimize import HyperoptRequest, HyperoptSpaceRequest
from kis_backtest.codegen.generator import CodeGenConfig, LeanCodeGenerator
from kis_backtest.errors import BOUNDARY_ERRORS
from kis_backtest.lean.executor import LeanExecutor
from kis_backtest.lean.project_manager import LeanProject, LeanProjectManager
from kis_backtest.optimize.hyperopt import (
    TRAIN,
    Evaluation,
    HyperoptAborted,
    HyperoptConfig,
    HyperoptRunner,
)
from kis_backtest.optimize.objective import OBJECTIVES, summarize_result
from kis_backtest.optimize.space import (
    ParamSpec,
    StrategyTarget,
    load_target,
    search_space,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# 동시에 돌릴 LEAN 컨테이너 수 상한 (로컬 PC 6코어 기준 4)
MAX_CONCURRENCY = int(os.getenv("HYPEROPT_MAX_CONCURRENCY", "4"))
# 한 번에 탐색할 수 있는 파라미터 수
MAX_SEARCH_PARAMS = 12
# 학습·검증 구간의 최소 평일 수
MIN_TRAIN_WEEKDAYS = 10
MIN_VALID_WEEKDAYS = 5
# 실행 시간 추정 (실측: LEAN 기동 약 5초 + 초당 약 3,500봉)
LEAN_STARTUP_SECONDS = 5.0
LEAN_BARS_PER_SECOND = 3500.0
# 진행 이벤트가 없을 때 연결 유지용 신호 간격 (초)
HEARTBEAT_SECONDS = 5.0
# 브라우저가 이 시간 동안 아무것도 읽지 않으면 연결이 끊긴 것으로 보고 멈춘다 (초)
STALE_READER_SECONDS = 120.0

_RUN_LOCK = threading.Lock()


def _hyperopt_root() -> Path:
    return LeanProjectManager.workspace.resolve() / "hyperopt"


def _shared_data_dir() -> Path:
    return LeanProjectManager.workspace.resolve() / "data"


def _weekdays(start: date, end: date) -> List[date]:
    days = (start + timedelta(days=offset) for offset in range((end - start).days + 1))
    return [day for day in days if day.weekday() < 5]


def split_period(start: date, end: date, ratio: float) -> Tuple[Tuple[date, date], Optional[Tuple[date, date]]]:
    """기간을 평일 수 기준으로 학습(앞)·검증(뒤)으로 나눈다. ratio=0이면 검증 없음."""
    if ratio <= 0:
        return (start, end), None
    days = _weekdays(start, end)
    valid_count = round(len(days) * ratio)
    train_count = len(days) - valid_count
    if train_count < MIN_TRAIN_WEEKDAYS or valid_count < MIN_VALID_WEEKDAYS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"기간이 짧아 학습/검증으로 나눌 수 없습니다 (평일 {len(days)}일 → 학습 {train_count}일, "
                f"검증 {valid_count}일). 학습 {MIN_TRAIN_WEEKDAYS}일·검증 {MIN_VALID_WEEKDAYS}일 이상이 되도록 "
                "기간을 늘리거나 검증 비율을 0으로 하세요."
            ),
        )
    return (start, days[train_count - 1]), (days[train_count], end)


def estimate_run_seconds(symbol_count: int, start: date, end: date, resolution: str, bar_minutes: int) -> float:
    """백테스트 한 번의 예상 시간 (혼자 돌 때)."""
    per_day = bars_per_day(bar_minutes) if resolution == "minute" else 1
    bars = symbol_count * len(_weekdays(start, end)) * per_day
    return LEAN_STARTUP_SECONDS + bars / LEAN_BARS_PER_SECOND


def parallel_speedup(concurrency: int, run_seconds: float) -> float:
    """동시 실행으로 빨라지는 배수 (실측: 일봉 4개 동시 약 1.7배, 긴 분봉은 CPU를 더 나눠 쓴다)."""
    gain = 0.25 if run_seconds < 15 else 0.6
    return 1 + gain * (max(concurrency, 1) - 1)


def _codegen_config(request: HyperoptRequest) -> CodeGenConfig:
    # 일반 백테스트(/api/backtest/run)와 같은 규칙으로 채워야 "이 값으로 백테스트" 결과가 같다.
    return CodeGenConfig(
        commission_rate=request.commission_rate or 0.00015,
        tax_rate=request.tax_rate or 0.002,
        slippage=request.slippage or 0.0,
        initial_capital=request.initial_capital,
        resolution=request.resolution,
        bar_minutes=request.bar_minutes,
        intraday_exit=request.intraday_exit,
    )


@dataclass
class _Plan:
    request: HyperoptRequest
    target: StrategyTarget
    space: List[ParamSpec]
    fixed: Dict[str, Any]
    train: Tuple[date, date]
    valid: Optional[Tuple[date, date]]
    data_end: date
    warmup_bars: int
    run_seconds: float
    initial_timeout: float
    concurrency: int
    warnings: List[str] = field(default_factory=list)

    def codegen_config(self) -> CodeGenConfig:
        return _codegen_config(self.request)

    def estimate_total_seconds(self) -> float:
        n_runs = self.request.n_trials + 1
        speedup = parallel_speedup(self.concurrency, self.run_seconds)
        total = n_runs * self.run_seconds / speedup
        if self.valid is not None:
            total += self.run_seconds  # 검증 4개는 거의 한 번에 돈다
        return min(total, self.request.time_budget_minutes * 60 + self.initial_timeout)


def _load_target(strategy_id: Optional[str], yaml_content: Optional[str]) -> StrategyTarget:
    try:
        return load_target(strategy_id=strategy_id, yaml_content=yaml_content)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"전략을 찾을 수 없습니다: {strategy_id}")
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail=f"전략을 읽을 수 없습니다: {exc}")


def _warmup_bars(target: StrategyTarget, values: Dict[str, Any], config: CodeGenConfig) -> int:
    return LeanCodeGenerator(target.build(values), config=config).warmup_bars()


def _build_plan(request: HyperoptRequest) -> _Plan:
    """요청을 검증하고 실행 계획을 만든다 (문제가 있으면 HTTPException)."""
    target = _load_target(request.strategy_id, request.yaml_content)
    try:
        space, fixed = search_space(target, [choice.model_dump() for choice in request.params])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if len(space) > MAX_SEARCH_PARAMS:
        raise HTTPException(
            status_code=400,
            detail=f"한 번에 {MAX_SEARCH_PARAMS}개까지 탐색할 수 있습니다 (지금 {len(space)}개). 일부는 고정하세요.",
        )

    req_start, req_end = _parse_request_dates(request.start_date, request.end_date)
    warnings: List[str] = []
    data_end = req_end
    if request.resolution == "minute":
        from psycopg import Error as PostgresError

        from kis_backtest.providers.postgres import PostgresDataProvider

        try:
            coverage = PostgresDataProvider().get_minute_coverage()
        except (PostgresError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=f"PostgreSQL 분봉 범위 조회 실패: {type(exc).__name__}")
        req_start, req_end, warnings = validate_minute_range(
            request.start_date, request.end_date, len(request.symbols), request.bar_minutes, coverage
        )
        data_end = min(req_end, coverage[1])

    train, valid = split_period(req_start, data_end, request.validation_ratio)

    # 기본값으로 전략·코드가 만들어지는지 먼저 확인하고, 분봉 워밍업 범위(탐색 범위의 양 끝 중 큰 값)를 구한다.
    try:
        config = _codegen_config(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    defaults = {**fixed, **{spec.name: target.defaults()[spec.name] for spec in space}}
    try:
        LeanCodeGenerator(target.build(defaults), config=config).generate(
            symbols=request.symbols,
            start_date=train[0].isoformat(),
            end_date=train[1].isoformat(),
            initial_capital=request.initial_capital,
        )
        warmup = _warmup_bars(target, defaults, config)
    except BOUNDARY_ERRORS as exc:
        raise HTTPException(status_code=400, detail=f"기본값으로 전략 코드를 만들 수 없습니다: {exc}")
    for corner in ("low", "high"):
        values = {**fixed, **{spec.name: spec.normalize(getattr(spec, corner)) for spec in space}}
        try:
            warmup = max(warmup, _warmup_bars(target, values, config))
        except BOUNDARY_ERRORS:
            continue

    if not (_shared_data_dir() / "symbol-properties" / "symbol-properties-database.csv").exists():
        raise HTTPException(
            status_code=503,
            detail="LEAN 기본 데이터(symbol-properties)가 없습니다. 로컬 PC에서 setup_lean_data.sh를 실행하세요.",
        )
    if not LeanExecutor.check_docker():
        raise HTTPException(
            status_code=503,
            detail="LEAN을 돌리는 로컬 PC의 Docker가 꺼져 있습니다. 그 PC에서 Docker Desktop을 켜면 바로 실행됩니다.",
        )
    if not LeanExecutor.check_image():
        raise HTTPException(
            status_code=503,
            detail="Lean 이미지가 없습니다. 'docker pull quantconnect/lean:latest' 실행 후 재시도해주세요.",
        )

    run_seconds = estimate_run_seconds(len(request.symbols), train[0], train[1], request.resolution, request.bar_minutes)
    cap = MINUTE_LEAN_TIMEOUT if request.resolution == "minute" else DAILY_LEAN_TIMEOUT
    return _Plan(
        request=request,
        target=target,
        space=space,
        fixed=fixed,
        train=train,
        valid=valid,
        data_end=data_end,
        warmup_bars=warmup,
        run_seconds=run_seconds,
        initial_timeout=float(min(cap, max(90.0, run_seconds * 4 + 60))),
        concurrency=max(1, min(request.concurrency, MAX_CONCURRENCY)),
        warnings=warnings,
    )


# ---- 데이터 준비 ----------------------------------------------------------
def _link_or_copy(source: str, destination: str) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _prepare_data(plan: _Plan, root: Path) -> Dict[str, Any]:
    """하이퍼옵트 전용 data 폴더를 만든다: LEAN 기본 데이터 + KOSPI + PostgreSQL 주가."""
    request = plan.request
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    shared = _shared_data_dir()
    for name in ("market-hours", "symbol-properties", "forex"):
        if (shared / name).is_dir():
            shutil.copytree(shared / name, data_dir / name, copy_function=_link_or_copy, dirs_exist_ok=True)

    start, end = request.start_date, request.end_date
    try:
        # KOSPI는 공용 캐시를 먼저 맞춘 뒤 복사한다 (일반 백테스트가 덮어써도 영향이 없게 링크하지 않음).
        asyncio.run(prepare_benchmark_data(start_date=start, end_date=end, workspace=LeanProjectManager.workspace))
    except _DATA_PREP_ERRORS as exc:
        logger.warning(f"[Hyperopt] 벤치마크 준비 실패 (무시): {type(exc).__name__}")
    if (shared / "index").is_dir():
        shutil.copytree(shared / "index", data_dir / "index", dirs_exist_ok=True)

    return asyncio.run(prepare_backtest_data(
        resolution=request.resolution,
        symbols=request.symbols,
        start_date=start,
        end_date=end,
        workspace=root,
        bar_minutes=request.bar_minutes,
        warmup_bars=plan.warmup_bars,
    ))


def _cleanup_old_runs(keep: Path) -> None:
    """예전 실행이 비정상 종료돼 남긴 폴더를 지운다 (한 번에 하나만 돌기 때문에 지금 것 말고는 다 남은 것)."""
    root = _hyperopt_root()
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and child.resolve() != keep.resolve():
            shutil.rmtree(child, ignore_errors=True)


# ---- 시행 실행 ------------------------------------------------------------
class _Containers:
    """돌고 있는 LEAN 컨테이너 이름 (취소할 때 한꺼번에 멈춘다)."""

    def __init__(self) -> None:
        self._names: set[str] = set()
        self._lock = threading.Lock()

    def add(self, name: str) -> None:
        with self._lock:
            self._names.add(name)

    def discard(self, name: str) -> None:
        with self._lock:
            self._names.discard(name)

    def kill_all(self) -> None:
        with self._lock:
            names = list(self._names)
        for name in names:
            LeanExecutor._kill_container(name, reason="하이퍼옵트 중단")


def _make_evaluator(
    plan: _Plan,
    opt_id: str,
    root: Path,
    data: Dict[str, Any],
    containers: _Containers,
) -> Callable[[Dict[str, Any], str, float], Evaluation]:
    request = plan.request
    trading_days: List[date] = data.get("trading_days") or []
    counter = itertools.count(1)
    counter_lock = threading.Lock()
    data_dir = root / "data"

    def evaluate(params: Dict[str, Any], period: str, timeout: float) -> Evaluation:
        start, end = plan.train if period == TRAIN else plan.valid
        config = plan.codegen_config()
        generator = LeanCodeGenerator(plan.target.build(params), config=config)
        if request.resolution == "minute":
            warmup_start = minute_warmup_start(trading_days, start, generator.warmup_bars(), request.bar_minutes)
            config.warmup_days = (start - warmup_start).days
        code = generator.generate(
            symbols=request.symbols,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            initial_capital=request.initial_capital,
        )
        with counter_lock:
            number = next(counter)
        project_dir = root / "trials" / f"t{number:05d}"
        (project_dir / "backtests").mkdir(parents=True)
        project = LeanProject(
            run_id=f"{opt_id}-t{number}",
            project_dir=project_dir,
            data_dir=data_dir,
            symbols=request.symbols,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            initial_capital=request.initial_capital,
        )
        project.main_py.write_text(code)
        name = LeanExecutor.container_name_for(project.run_id)
        containers.add(name)
        try:
            lean_run = LeanExecutor.run(project, timeout=timeout, data_path=data_dir, container_name=name)
            response = _lean_run_to_api_response(
                lean_run, plan.target.name, request.symbols,
                start.isoformat(), end.isoformat(), request.initial_capital,
                workspace=None,
                resolution=request.resolution,
                bar_minutes=request.bar_minutes,
                intraday_exit=request.intraday_exit,
            )
            return Evaluation(
                ok=True,
                metrics=summarize_result(response),
                equity_curve=response.get("equity_curve") or None,
                duration=lean_run.duration_seconds,
            )
        except (RuntimeError, ValueError) as exc:
            message = str(exc)
            return Evaluation(ok=False, error=_classify_lean_error(message, ""), timed_out="타임아웃" in message)
        finally:
            containers.discard(name)
            # 결과 파일은 컨테이너(root)가 만들지만 폴더는 우리 것이라 지울 수 있다.
            shutil.rmtree(project_dir, ignore_errors=True)

    return evaluate


def _period_dict(period: Optional[Tuple[date, date]]) -> Optional[Dict[str, str]]:
    return None if period is None else {"start": period[0].isoformat(), "end": period[1].isoformat()}


def _start_event(plan: _Plan, opt_id: str) -> Dict[str, Any]:
    request = plan.request
    return {
        "type": "start",
        "opt_id": opt_id,
        "strategy": {"kind": plan.target.kind, "id": plan.target.id, "name": plan.target.name},
        "objective": OBJECTIVES[request.objective].to_dict(),
        "n_trials": request.n_trials,
        "min_trades": request.min_trades,
        "concurrency": plan.concurrency,
        "resolution": request.resolution,
        "bar_minutes": request.bar_minutes if request.resolution == "minute" else None,
        "train": _period_dict(plan.train),
        "valid": _period_dict(plan.valid),
        "space": [spec.to_dict() for spec in plan.space],
        "fixed": plan.fixed,
        "constraints": [c.to_dict() for c in plan.target.constraints],
        "estimate": {
            "per_run": round(plan.run_seconds, 1),
            "total": round(plan.estimate_total_seconds(), 1),
            "timeout": round(plan.initial_timeout, 1),
        },
        "time_budget": request.time_budget_minutes * 60,
        "warnings": plan.warnings,
    }


def _execute(plan: _Plan, emit: Callable[[Dict[str, Any]], None], cancel: threading.Event) -> None:
    """하이퍼옵트 한 번 (작업 스레드). 끝나면 done / error / cancelled 이벤트 중 하나를 꼭 보낸다."""
    request = plan.request
    opt_id = LeanProjectManager.generate_run_id("opt")
    root = _hyperopt_root() / opt_id
    containers = _Containers()
    started = time.monotonic()
    try:
        root.mkdir(parents=True)
        _cleanup_old_runs(keep=root)
        emit(_start_event(plan, opt_id))
        emit({"type": "phase", "phase": "data", "message": "PostgreSQL에서 주가 데이터를 준비하는 중..."})
        data = _prepare_data(plan, root)
        if data.get("errors"):
            failed = ", ".join(item["symbol"] for item in data["errors"])
            first_error = data["errors"][0]["error"]
            emit({"type": "error", "message": f"데이터 준비 실패 ({failed}): {first_error}"})
            return
        if data.get("warnings"):
            emit({"type": "warnings", "warnings": data["warnings"]})
        if cancel.is_set():
            emit({"type": "cancelled", "elapsed": round(time.monotonic() - started, 1)})
            return

        config = HyperoptConfig(
            space=plan.space,
            fixed=plan.fixed,
            defaults=plan.target.defaults(),
            constraints=plan.target.constraints,
            objective=request.objective,
            n_trials=request.n_trials,
            min_trades=request.min_trades,
            concurrency=plan.concurrency,
            time_budget=request.time_budget_minutes * 60,
            initial_timeout=plan.initial_timeout,
            validate=plan.valid is not None,
            seed=request.seed,
        )
        runner = HyperoptRunner(
            config,
            _make_evaluator(plan, opt_id, root, data, containers),
            emit,
            cancel=cancel,
            on_cancel=containers.kill_all,
        )
        result = runner.run()
        if cancel.is_set():
            emit({"type": "cancelled", "elapsed": round(time.monotonic() - started, 1)})
            return
        for candidate in result["candidates"]:
            candidate["yaml"] = plan.target.to_yaml(candidate["full_params"])
        emit({"type": "done", **result})
    except HyperoptAborted as exc:
        emit({"type": "error", "message": str(exc)})
    except HTTPException as exc:
        emit({"type": "error", "message": str(exc.detail)})
    except (*BOUNDARY_ERRORS, optuna.exceptions.OptunaError) as exc:  # 작업 스레드 경계
        logger.exception("[Hyperopt] 실행 오류")
        emit({"type": "error", "message": f"하이퍼옵트 실행 오류: {type(exc).__name__}: {exc}"})
    finally:
        containers.kill_all()
        shutil.rmtree(root, ignore_errors=True)
        logger.info(f"[Hyperopt] 종료 {opt_id}: {time.monotonic() - started:.1f}초")


def _ndjson(event: Dict[str, Any]) -> bytes:
    return (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")


# ---- 라우트 ---------------------------------------------------------------
@router.get("/status", summary="하이퍼옵트 실행 여부")
async def hyperopt_status() -> Dict[str, Any]:
    return {"running": _RUN_LOCK.locked(), "max_concurrency": MAX_CONCURRENCY}


@router.post(
    "/space",
    summary="탐색 공간 조회",
    description="전략(프리셋 ID 또는 YAML)에서 하이퍼옵트로 바꿔 볼 수 있는 파라미터와 기본 범위.",
)
async def hyperopt_space(request: HyperoptSpaceRequest) -> Dict[str, Any]:
    target = _load_target(request.strategy_id, request.yaml_content)
    return {
        "success": True,
        "data": {
            **target.describe(),
            "objectives": [objective.to_dict() for objective in OBJECTIVES.values()],
            "max_search_params": MAX_SEARCH_PARAMS,
            "max_concurrency": MAX_CONCURRENCY,
        },
    }


@router.post(
    "/run",
    summary="하이퍼옵트 실행 (스트리밍)",
    description=(
        "파라미터를 바꿔 가며 백테스트를 반복해 목표 점수가 가장 좋은 조합을 찾는다. "
        "응답은 application/x-ndjson: start → phase → trial ... → validation ... → done (또는 error/cancelled)."
    ),
)
async def run_hyperopt(request: HyperoptRequest) -> StreamingResponse:
    plan = await asyncio.to_thread(_build_plan, request)
    if not _RUN_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="다른 하이퍼옵트가 실행 중입니다. 끝나거나 멈춘 뒤 다시 시작하세요.",
        )

    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()
    cancel = threading.Event()
    last_read = [time.monotonic()]

    def emit(event: Dict[str, Any]) -> None:
        # 읽는 쪽(브라우저)이 오래 조용하면 연결이 끊긴 것으로 보고 멈춘다.
        if time.monotonic() - last_read[0] > STALE_READER_SECONDS:
            cancel.set()
        try:
            loop.call_soon_threadsafe(events.put_nowait, event)
        except RuntimeError:  # 이벤트 루프가 닫힘
            cancel.set()

    def work() -> None:
        try:
            _execute(plan, emit, cancel)
        finally:
            _RUN_LOCK.release()

    threading.Thread(target=work, name="hyperopt", daemon=True).start()

    async def stream():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(events.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    event = {"type": "ping"}
                last_read[0] = time.monotonic()
                yield _ndjson(event)
                if event.get("type") in ("done", "error", "cancelled"):
                    break
        finally:
            # 끝났거나 브라우저 연결이 끊김 (StreamingResponse가 이 제너레이터를 취소한다)
            cancel.set()

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
