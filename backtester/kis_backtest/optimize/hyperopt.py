"""하이퍼옵트 실행기.

Optuna(TPE)가 다음에 시험할 파라미터를 고르고, 백테스트를 동시에 몇 개씩 돌려 목표 점수가 가장 좋은
조합을 찾는다 (freqtrade hyperopt와 같은 방식). 백테스트 한 번은 evaluate 콜백이 맡는다
(실제로는 LEAN, 테스트에서는 가짜 함수).

- 시행마다 시간 제한을 둔다. LEAN을 동시에 띄우면 가끔 시작 직후 멈추므로, 시간 초과는 한 번 다시 돌린다.
- 이미 돌렸거나 돌리는 중인 값 조합은 다시 돌리지 않고 그 결과를 그대로 알려 준다.
- 크기 순서 조건(예: 단기 기간 < 장기 기간)을 어기는 조합은 돌리지 않는다.
- 거래 수가 최소 기준보다 적으면 "거래 부족"으로 보고 최고 기록 후보에서 뺀다.
- 탐색이 끝나면 상위 조합과 기본값을 검증 구간(학습에 안 쓴 뒤쪽 기간)에서 다시 돌려 과최적화를 확인한다.
"""

from __future__ import annotations

import logging
import math
import statistics
import threading
import time
import warnings
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

import optuna
from optuna.trial import TrialState

from kis_backtest.errors import BOUNDARY_ERRORS

from .objective import OBJECTIVES, score_metrics
from .space import Constraint, ParamSpec

logger = logging.getLogger(__name__)

warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)

ParamValues = Dict[str, Any]

TRAIN = "train"
VALID = "valid"


@dataclass
class Evaluation:
    """백테스트 한 번의 결과."""

    ok: bool
    metrics: Optional[Dict[str, float]] = None
    equity_curve: Optional[Dict[str, float]] = None
    error: Optional[str] = None
    timed_out: bool = False
    duration: float = 0.0


# evaluate(전체 파라미터, 구간 "train"|"valid", 제한 시간 초) -> Evaluation
Evaluator = Callable[[ParamValues, str, float], Evaluation]
Emitter = Callable[[Dict[str, Any]], None]


class HyperoptAborted(RuntimeError):
    """계속해도 의미가 없어 멈춤 (예: 백테스트가 연속으로 실패)."""


@dataclass
class HyperoptConfig:
    space: List[ParamSpec]
    fixed: ParamValues  # 탐색하지 않는 파라미터 값
    defaults: ParamValues  # 전략 기본값 (비교용 기본값 시행)
    constraints: List[Constraint] = field(default_factory=list)
    objective: str = "sharpe"
    n_trials: int = 100
    min_trades: int = 3
    concurrency: int = 4
    time_budget: float = 3600.0  # 이 시간이 지나면 새 시행을 시작하지 않는다 (초)
    initial_timeout: float = 120.0  # 실행 시간을 모를 때의 시행 제한 시간 (초)
    min_timeout: float = 30.0
    validate: bool = True
    top_k: int = 3
    seed: Optional[int] = None
    launch_gap: float = 1.5  # 컨테이너를 한꺼번에 띄우지 않도록 시작 간격을 둔다 (초)
    heartbeat: float = 5.0
    max_attempts: int = 2
    max_consecutive_failures: int = 6


@dataclass
class TrialRecord:
    number: int  # 기본값 시행은 0, 탐색 시행은 1부터
    kind: str  # baseline | trial
    params: ParamValues  # 탐색한 파라미터 값
    full_params: ParamValues
    state: str  # ok | few_trades | no_score | failed
    score: Optional[float]
    metrics: Optional[Dict[str, float]]
    equity_curve: Optional[Dict[str, float]]
    error: Optional[str]
    duration: float
    attempts: int

    def to_event(self) -> Dict[str, Any]:
        return {
            "number": self.number,
            "kind": self.kind,
            "params": self.params,
            "state": self.state,
            "score": _round(self.score),
            "metrics": self.metrics,
            "error": self.error,
            "duration": round(self.duration, 1),
            "attempts": self.attempts,
        }


@dataclass
class _Job:
    kind: str  # baseline | trial | valid
    number: int
    period: str
    full_params: ParamValues
    params: ParamValues
    key: Tuple = ()
    trial: Optional[optuna.trial.Trial] = None
    attempt: int = 1
    candidate: int = -1


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return None if value is None else round(value, digits)


class HyperoptRunner:
    """하이퍼옵트 한 번. run()은 끝날 때까지 돌아오지 않는다 (요청 처리 스레드에서 부른다)."""

    def __init__(
        self,
        config: HyperoptConfig,
        evaluate: Evaluator,
        emit: Emitter,
        *,
        cancel: Optional[threading.Event] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if config.objective not in OBJECTIVES:
            raise ValueError(f"알 수 없는 목표: {config.objective}")
        if not config.space:
            raise ValueError("탐색할 파라미터가 없습니다.")
        self.config = config
        self.cancel = cancel or threading.Event()
        self._evaluate = evaluate
        self._emit = emit
        self._on_cancel = on_cancel
        self._clock = clock
        self._sleep = sleep
        self._names = [spec.name for spec in config.space]
        self.records: List[TrialRecord] = []
        self.baseline: Optional[TrialRecord] = None
        self.best: Optional[TrialRecord] = None
        self.counts = {"duplicates": 0, "rejected": 0, "retries": 0}
        self._cache: Dict[Tuple, TrialRecord] = {}
        self._running_keys: set[Tuple] = set()
        self._waiting: Dict[Tuple, List[optuna.trial.Trial]] = {}
        self._durations: List[float] = []
        self._consecutive_failures = 0
        self._last_launch = -math.inf
        self._started = 0.0
        self._search_started = 0.0
        self._asks = 0
        self._max_asks = config.n_trials * 10 + 100
        self._launched = 0
        self._stop_reason: Optional[str] = None

    # ---- 공통 -----------------------------------------------------------
    def _elapsed(self) -> float:
        return self._clock() - self._started

    def _key(self, params: ParamValues) -> Tuple:
        return tuple((name, params[name]) for name in self._names)

    def _timeout(self) -> float:
        """실행 시간 중앙값의 4배 (처음 3번은 initial_timeout)."""
        cfg = self.config
        if len(self._durations) >= 3:
            adaptive = 4 * statistics.median(self._durations)
            return min(cfg.initial_timeout, max(cfg.min_timeout, adaptive))
        return cfg.initial_timeout

    def _stagger(self) -> None:
        delay = self._last_launch + self.config.launch_gap - self._clock()
        if delay > 0:
            self._sleep(delay)
        self._last_launch = self._clock()

    def _run(self, job: _Job, timeout: float) -> Evaluation:
        started = self._clock()
        try:
            result = self._evaluate(dict(job.full_params), job.period, timeout)
        except BOUNDARY_ERRORS as exc:  # 작업 스레드 경계: 예외가 새면 전체가 멈추므로 시행 실패로 바꾼다
            logger.exception("[Hyperopt] 시행 실행 오류")
            result = Evaluation(ok=False, error=f"{type(exc).__name__}: {exc}")
        if not result.duration:
            result.duration = self._clock() - started
        return result

    def _drive(
        self,
        next_job: Callable[[], Optional[_Job]],
        finished: Callable[[], bool],
        on_done: Callable[[_Job, Evaluation], None],
    ) -> None:
        """작업을 동시에 concurrency개까지 돌린다. 돌릴 작업이 더 없고(finished) 돌아가는 작업도 없으면 끝."""
        cfg = self.config
        pending: Dict[Future, _Job] = {}
        retries: List[_Job] = []
        pool = ThreadPoolExecutor(max_workers=cfg.concurrency, thread_name_prefix="hyperopt")
        try:
            while not self.cancel.is_set():
                while len(pending) < cfg.concurrency and not self.cancel.is_set():
                    job = retries.pop(0) if retries else next_job()
                    if job is None:
                        break
                    self._stagger()
                    pending[pool.submit(self._run, job, self._timeout())] = job
                if not pending:
                    if finished() or self.cancel.is_set():
                        break
                    continue
                done, _ = wait(list(pending), timeout=cfg.heartbeat, return_when=FIRST_COMPLETED)
                if not done:
                    self._emit({"type": "ping", "elapsed": round(self._elapsed(), 1), "running": len(pending)})
                    continue
                for future in done:
                    job = pending.pop(future)
                    result = future.result()
                    if self.cancel.is_set():
                        break
                    if result.timed_out and job.attempt < cfg.max_attempts:
                        self.counts["retries"] += 1
                        retries.append(replace(job, attempt=job.attempt + 1))
                        self._emit({
                            "type": "retry",
                            "kind": job.kind,
                            "number": job.number,
                            "attempt": job.attempt + 1,
                            "message": "응답이 없는 백테스트를 멈추고 다시 실행합니다.",
                        })
                        continue
                    on_done(job, result)
        finally:
            if pending and self._on_cancel is not None:
                self._on_cancel()  # 취소·중단 시 돌고 있는 컨테이너를 멈춘다
            pool.shutdown(wait=True, cancel_futures=True)

    # ---- 탐색 -----------------------------------------------------------
    def _suggest(self, trial: optuna.trial.Trial) -> ParamValues:
        values: ParamValues = {}
        for spec in self.config.space:
            if spec.type == "int":
                raw = trial.suggest_int(spec.name, int(spec.low), int(spec.high), step=int(spec.step))
            else:
                raw = trial.suggest_float(spec.name, spec.low, spec.high, step=spec.step)
            values[spec.name] = spec.normalize(raw)
        return values

    def _tell(self, study: optuna.Study, trial: optuna.trial.Trial, record: TrialRecord) -> None:
        if record.state == "ok":
            study.tell(trial, record.score)
        elif record.state == "failed":
            study.tell(trial, state=TrialState.FAIL)
        else:  # 거래 부족·점수 없음은 가장 나쁜 쪽으로 보게 한다
            study.tell(trial, state=TrialState.PRUNED)

    def _next_trial(self, study: optuna.Study) -> Optional[_Job]:
        if self._stop_reason is None:
            if self._launched >= self.config.n_trials:
                self._stop_reason = "completed"
            elif self._elapsed() >= self.config.time_budget:
                self._stop_reason = "time_budget"
        if self._stop_reason is not None:
            return None
        for _ in range(30):
            if self._asks >= self._max_asks:
                self._stop_reason = "space_exhausted"
                return None
            self._asks += 1
            trial = study.ask()
            params = self._suggest(trial)
            full = {**self.config.fixed, **params}
            if not all(constraint.satisfied(full) for constraint in self.config.constraints):
                study.tell(trial, state=TrialState.PRUNED)
                self.counts["rejected"] += 1
                continue
            key = self._key(params)
            if key in self._cache:
                self._tell(study, trial, self._cache[key])
                self.counts["duplicates"] += 1
                continue
            if key in self._running_keys:
                self._waiting.setdefault(key, []).append(trial)
                self.counts["duplicates"] += 1
                continue
            self._launched += 1
            self._running_keys.add(key)
            return _Job("trial", self._launched, TRAIN, full, params, key, trial=trial)
        return None

    def _make_record(self, job: _Job, result: Evaluation) -> TrialRecord:
        score = score_metrics(self.config.objective, result.metrics) if result.ok and result.metrics else None
        if not result.ok or result.metrics is None:
            state = "failed"
        elif result.metrics.get("trades", 0.0) < self.config.min_trades:
            state = "few_trades"
        elif score is None:
            state = "no_score"
        else:
            state = "ok"
        return TrialRecord(
            number=job.number,
            kind=job.kind,
            params=dict(job.params),
            full_params=dict(job.full_params),
            state=state,
            score=score,
            metrics=result.metrics,
            equity_curve=result.equity_curve,
            error=(result.error or "")[:500] or None,
            duration=result.duration,
            attempts=job.attempt,
        )

    def _trial_done(self, study: optuna.Study, job: _Job, result: Evaluation) -> None:
        if result.ok:
            self._durations.append(result.duration)
        record = self._make_record(job, result)
        if job.kind == "trial":
            self._running_keys.discard(job.key)
            self._cache[job.key] = record
            self._tell(study, job.trial, record)
            for waiting in self._waiting.pop(job.key, []):
                self._tell(study, waiting, record)
            self.records.append(record)
            if record.state == "ok" and (self.best is None or record.score > self.best.score):
                self.best = record
        else:
            self.baseline = record
            self._add_baseline_to_study(study, record)

        self._consecutive_failures = self._consecutive_failures + 1 if record.state == "failed" else 0
        done = len(self.records)
        per_trial = (self._clock() - self._search_started) / max(done, 1)
        remaining = max(self.config.n_trials - done, 0)
        self._emit({
            "type": "trial",
            **record.to_event(),
            "best": self._best_event(),
            "done": done,
            "total": self.config.n_trials,
            "elapsed": round(self._elapsed(), 1),
            "eta": round(min(remaining * per_trial, max(self.config.time_budget - self._elapsed(), 0)), 1),
        })
        if self._consecutive_failures >= self.config.max_consecutive_failures:
            last_error = record.error or "알 수 없음"
            raise HyperoptAborted(
                f"백테스트가 {self._consecutive_failures}번 연속 실패해 멈췄습니다. 마지막 오류: {last_error}"
            )

    def _add_baseline_to_study(self, study: optuna.Study, record: TrialRecord) -> None:
        """기본값 결과도 탐색 기록에 넣어 TPE가 참고하게 한다 (범위·간격 밖이면 넣지 않음)."""
        if record.state != "ok":
            return
        distributions = {}
        for spec in self.config.space:
            if spec.type == "int":
                distributions[spec.name] = optuna.distributions.IntDistribution(
                    int(spec.low), int(spec.high), step=int(spec.step)
                )
            else:
                distributions[spec.name] = optuna.distributions.FloatDistribution(spec.low, spec.high, step=spec.step)
        try:
            study.add_trial(optuna.trial.create_trial(
                params=dict(record.params), distributions=distributions, value=record.score,
            ))
        except ValueError:
            pass

    def _best_event(self) -> Optional[Dict[str, Any]]:
        if self.best is None:
            return None
        return {"number": self.best.number, "score": _round(self.best.score), "params": self.best.params}

    def _search(self, study: optuna.Study) -> None:
        defaults = {**self.config.fixed, **{name: self.config.defaults[name] for name in self._names}}
        searched_defaults = {name: defaults[name] for name in self._names}
        queue: List[_Job] = [_Job("baseline", 0, TRAIN, defaults, searched_defaults, self._key(searched_defaults))]

        def next_job() -> Optional[_Job]:
            return queue.pop(0) if queue else self._next_trial(study)

        self._search_started = self._clock()
        self._drive(
            next_job,
            finished=lambda: not queue and self._stop_reason is not None,
            on_done=lambda job, result: self._trial_done(study, job, result),
        )

    # ---- 검증 -----------------------------------------------------------
    def _candidate(self, record: TrialRecord, rank: Optional[int]) -> Dict[str, Any]:
        return {
            "rank": rank,
            "label": f"{rank}위" if rank else "기본값",
            "is_default": rank is None,
            "number": record.number,
            "params": record.params,
            "full_params": record.full_params,
            "train": {
                "state": record.state,
                "score": _round(record.score),
                "metrics": record.metrics,
                "equity_curve": record.equity_curve,
            },
            "valid": None,
        }

    def candidates(self) -> List[Dict[str, Any]]:
        """학습 점수 상위 top_k개 + 기본값 (기본값과 같은 조합이면 한 줄로 합친다)."""
        ranked = sorted(
            (r for r in self.records if r.state == "ok"),
            key=lambda r: r.score,
            reverse=True,
        )[: self.config.top_k]
        result = [self._candidate(record, rank) for rank, record in enumerate(ranked, start=1)]
        if self.baseline is not None and self.baseline.state != "failed":
            same = next(
                (c for c, r in zip(result, ranked) if self._key(r.params) == self._key(self.baseline.params)),
                None,
            )
            if same is not None:
                same["is_default"] = True
            else:
                result.append(self._candidate(self.baseline, None))
        return result

    def _validate(self, candidates: List[Dict[str, Any]]) -> None:
        queue = [
            _Job("valid", index, VALID, candidate["full_params"], candidate["params"], candidate=index)
            for index, candidate in enumerate(candidates)
        ]
        done_count = [0]

        def on_done(job: _Job, result: Evaluation) -> None:
            candidate = candidates[job.candidate]
            score = score_metrics(self.config.objective, result.metrics) if result.ok and result.metrics else None
            candidate["valid"] = {
                "state": "ok" if result.ok else "failed",
                "score": _round(score),
                "metrics": result.metrics,
                "equity_curve": result.equity_curve,
                "error": (result.error or "")[:500] or None,
            }
            done_count[0] += 1
            self._emit({
                "type": "validation",
                "index": job.candidate,
                "label": candidate["label"],
                "valid": {key: value for key, value in candidate["valid"].items() if key != "equity_curve"},
                "done": done_count[0],
                "total": len(candidates),
                "elapsed": round(self._elapsed(), 1),
            })

        self._drive(lambda: queue.pop(0) if queue else None, lambda: not queue, on_done)

    # ---- 실행 -----------------------------------------------------------
    def _importance(self, study: optuna.Study) -> Optional[Dict[str, float]]:
        complete = [t for t in study.trials if t.state == TrialState.COMPLETE]
        if len(self._names) < 2 or len(complete) < 10:
            return None
        try:
            importances = optuna.importance.get_param_importances(study)
        except (ValueError, RuntimeError, ImportError, ZeroDivisionError) as exc:
            logger.warning(f"[Hyperopt] 파라미터 중요도 계산 실패: {type(exc).__name__}")
            return None
        return {name: round(float(value), 4) for name, value in importances.items()}

    def run(self) -> Dict[str, Any]:
        cfg = self.config
        self._started = self._clock()
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        sampler = optuna.samplers.TPESampler(
            seed=cfg.seed,
            n_startup_trials=min(20, max(5, cfg.n_trials // 4)),
            multivariate=True,
            constant_liar=True,
        )
        study = optuna.create_study(direction="maximize", sampler=sampler)

        self._emit({"type": "phase", "phase": "search", "total": cfg.n_trials, "elapsed": round(self._elapsed(), 1)})
        self._search(study)

        candidates = self.candidates() if not self.cancel.is_set() else []
        if cfg.validate and candidates and not self.cancel.is_set():
            self._emit({
                "type": "phase", "phase": "validate", "total": len(candidates),
                "elapsed": round(self._elapsed(), 1),
            })
            self._validate(candidates)

        states = [record.state for record in self.records]
        return {
            "stopped_reason": "cancelled" if self.cancel.is_set() else (self._stop_reason or "completed"),
            "elapsed": round(self._elapsed(), 1),
            "counts": {
                "trials": len(self.records),
                "ok": states.count("ok"),
                "few_trades": states.count("few_trades"),
                "no_score": states.count("no_score"),
                "failed": states.count("failed"),
                **self.counts,
            },
            "baseline": self.baseline.to_event() if self.baseline else None,
            "candidates": candidates,
            "importance": self._importance(study),
        }
