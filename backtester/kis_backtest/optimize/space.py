"""하이퍼옵트 탐색 공간.

전략에서 바꿔 볼 숫자(파라미터)와 범위·간격을 뽑고, 시행마다 고른 값을 전략에 넣는다.

- 프리셋 전략: PARAM_DEFINITIONS(default/min/max/type)를 그대로 쓴다.
- YAML 전략(전략 빌더·템플릿):
  - strategy.params 에 선언되고 $이름 으로 실제로 쓰이는 파라미터
  - 지표 파라미터에 직접 적힌 숫자 (예: sma period: 20)
  - 조건의 비교 숫자 (compare_to / value / compare_scalar)
  - 손절·익절·트레일링 스탑 비율 (risk.*.percent)
  전략 빌더가 만든 YAML은 params 없이 숫자를 직접 적기 때문에 숫자 자리를 자동으로 탐색 대상으로 만든다.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Tuple

import yaml

ParamType = Literal["int", "float"]
ParamGroup = Literal["param", "indicator", "condition", "risk"]

_PARAM_REF = re.compile(r"^\$([a-zA-Z_][a-zA-Z0-9_]*)$")

# 프리셋에서 크기 순서를 지켜야 하는 파라미터 쌍 (작은 쪽, 큰 쪽)
PRESET_CONSTRAINTS: Dict[str, List[Tuple[str, str]]] = {
    "sma_crossover": [("fast_period", "slow_period")],
    "ma_divergence": [("buy_ratio", "sell_ratio")],
}

# 자동으로 뽑은 숫자의 허용 한도 (사용자가 범위를 넓혀도 이 밖으로는 못 나간다)
_AUTO_INT_LIMITS = (1.0, 1000.0)
_AUTO_FLOAT_LIMITS = (-1_000_000.0, 1_000_000.0)
_RISK_LIMITS = (0.1, 100.0)

_INDICATOR_PARAM_LABELS = {
    "period": "기간",
    "fast": "단기 기간",
    "slow": "장기 기간",
    "signal": "시그널 기간",
    "std": "표준편차 배수",
    "std_dev": "표준편차 배수",
    "k": "배수",
    "multiplier": "배수",
    "minutes": "분",
    "lookback": "기간",
}

_OPERATOR_LABELS = {
    "greater_than": ">",
    "less_than": "<",
    "greater_equal": "≥",
    "less_equal": "≤",
    "equals": "=",
    "cross_above": "상향 돌파",
    "cross_below": "하향 돌파",
}

_RISK_LABELS = {
    "stop_loss": "손절 %",
    "take_profit": "익절 %",
    "trailing_stop": "트레일링 스탑 %",
}

_GROUP_LABELS = {"entry": "진입", "exit": "청산"}

# 값의 범위가 정해진 지표: 이 지표와 비교하는 숫자는 범위 안에서만 찾는다 (예: RSI는 0~100)
_BOUNDED_INDICATORS: Dict[str, Tuple[float, float]] = {
    "rsi": (0.0, 100.0),
    "stochastic": (0.0, 100.0),
    "stochrsi": (0.0, 100.0),
    "mfi": (0.0, 100.0),
    "adx": (0.0, 100.0),
    "adxr": (0.0, 100.0),
    "ultosc": (0.0, 100.0),
    "chop": (0.0, 100.0),
    "schaff": (0.0, 100.0),
    "williams_r": (-100.0, 0.0),
    "cmo": (-100.0, 100.0),
}


class _Dumper(yaml.SafeDumper):
    """따옴표가 필요한 문자열은 큰따옴표로 쓴다 (프로젝트 YAML 표기와 맞춘다)."""

    def choose_scalar_style(self):  # noqa: D401 - PyYAML 확장 지점
        style = super().choose_scalar_style()
        return "\"" if style == "'" else style


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _nice_ceil(value: float) -> float:
    """value 이상인 가장 작은 1·2·5 × 10^k."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    for base in (1, 2, 5, 10):
        candidate = base * 10.0 ** exponent
        if candidate >= value * (1 - 1e-9):
            return float(Decimal(str(candidate)))
    return 10.0 ** (exponent + 1)


def nice_step(low: float, high: float, param_type: ParamType) -> float:
    """범위를 정수형은 약 50칸, 실수형은 약 40칸으로 나누는 보기 좋은 간격."""
    span = max(high - low, 0.0)
    if param_type == "int":
        return max(1.0, _nice_ceil(span / 50)) if span > 50 else 1.0
    if span == 0:
        return 1.0
    return _nice_ceil(span / 40)


def step_decimals(step: float) -> int:
    exponent = Decimal(str(step)).normalize().as_tuple().exponent
    return max(0, -int(exponent))


@dataclass(frozen=True)
class ParamSpec:
    """탐색할 수 있는 숫자 하나."""

    name: str
    label: str
    type: ParamType
    default: float
    low: float
    high: float
    step: float
    group: ParamGroup
    min_allowed: float
    max_allowed: float
    # YAML에서 값을 넣을 자리 (API 응답에는 넣지 않는다)
    target: Tuple[str, ...] = field(default=(), compare=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "low": self.low,
            "high": self.high,
            "step": self.step,
            "group": self.group,
            "min_allowed": self.min_allowed,
            "max_allowed": self.max_allowed,
            "suggested": self.suggested,
        }

    @property
    def suggested(self) -> bool:
        """처음에 탐색 대상으로 체크해 둘지.

        ROC(1)로 만든 "하루 수익률"처럼 1~2짜리 지표 기간이나 "0보다 크면" 같은 0 기준값은
        전략의 뼈대인 경우가 많아 기본으로는 고정해 둔다.
        """
        if self.group == "indicator" and self.type == "int" and self.default <= 2:
            return False
        if self.group == "condition" and self.default == 0:
            return False
        return True

    def with_range(self, low: float, high: float, step: Optional[float] = None) -> ParamSpec:
        """사용자가 고른 범위로 바꾼 사본. 간격이 맞지 않으면 최댓값을 간격에 맞춰 내린다."""
        if not (math.isfinite(low) and math.isfinite(high)):
            raise ValueError(f"{self.label}: 범위는 숫자여야 합니다.")
        if low > high:
            raise ValueError(f"{self.label}: 최솟값({low})이 최댓값({high})보다 큽니다.")
        if low < self.min_allowed or high > self.max_allowed:
            raise ValueError(
                f"{self.label}: 범위는 {self.min_allowed:g} ~ {self.max_allowed:g} 안이어야 합니다."
            )
        if self.type == "int":
            low, high = float(math.ceil(low)), float(math.floor(high))
            if low > high:
                raise ValueError(f"{self.label}: 정수 범위에 들어가는 값이 없습니다.")
            step = float(max(1, round(step))) if step else nice_step(low, high, "int")
        else:
            step = float(step) if step else nice_step(low, high, "float")
        if step <= 0:
            raise ValueError(f"{self.label}: 간격은 0보다 커야 합니다.")
        count = math.floor((high - low) / step + 1e-9)
        high = round(low + count * step, step_decimals(step) + 2)
        return ParamSpec(
            name=self.name, label=self.label, type=self.type, default=self.default,
            low=low, high=high, step=step, group=self.group,
            min_allowed=self.min_allowed, max_allowed=self.max_allowed, target=self.target,
        )

    def normalize(self, value: float) -> float | int:
        """간격에 맞춰 반올림하고 형식을 맞춘다 (정수형은 int)."""
        if self.type == "int":
            return round(value)
        decimals = step_decimals(self.step)
        snapped = self.low + round((value - self.low) / self.step) * self.step
        return round(min(max(snapped, self.low), self.high), decimals)

    def grid_size(self) -> int:
        return math.floor((self.high - self.low) / self.step + 1e-9) + 1


@dataclass(frozen=True)
class Constraint:
    """smaller < larger 를 지켜야 하는 파라미터 쌍."""

    smaller: str
    larger: str
    label: str

    def satisfied(self, values: Mapping[str, float]) -> bool:
        a, b = values.get(self.smaller), values.get(self.larger)
        return a is None or b is None or a < b

    def to_dict(self) -> Dict[str, str]:
        return {"smaller": self.smaller, "larger": self.larger, "label": self.label}


def _derived_range(value: float, param_type: ParamType, *, kind: str = "") -> Tuple[float, float]:
    """숫자 하나에서 기본 탐색 범위를 만든다."""
    if param_type == "int":
        low = max(1, math.floor(value / 2))
        high = max(low + 1, math.ceil(value * 2))
        return float(low), float(high)
    if kind == "risk":
        return max(_RISK_LIMITS[0], round(value * 0.4, 2)), min(_RISK_LIMITS[1], round(value * 2.5, 2))
    if kind == "scalar":
        return round(value * 0.8, 4), round(value * 1.2, 4)
    if value == 0:
        return -1.0, 1.0
    a, b = value * 0.5, value * 1.5
    return round(min(a, b), 4), round(max(a, b), 4)


def _make_spec(
    name: str,
    label: str,
    value: float,
    group: ParamGroup,
    *,
    low: Optional[float] = None,
    high: Optional[float] = None,
    param_type: Optional[ParamType] = None,
    step: Optional[float] = None,
    limits: Optional[Tuple[float, float]] = None,
    kind: str = "",
    target: Tuple[str, ...] = (),
) -> ParamSpec:
    ptype: ParamType = param_type or ("int" if isinstance(value, int) else "float")
    if low is None or high is None:
        derived_low, derived_high = _derived_range(float(value), ptype, kind=kind)
        low = derived_low if low is None else low
        high = derived_high if high is None else high
    low, high = float(min(low, high)), float(max(low, high))
    if limits is None:
        limits = (low, high)
    spec = ParamSpec(
        name=name, label=label, type=ptype, default=float(value),
        low=low, high=high, step=1.0, group=group,
        min_allowed=min(limits[0], low), max_allowed=max(limits[1], high), target=target,
    )
    return spec.with_range(low, high, step)


class StrategyTarget:
    """하이퍼옵트 대상 전략 (프리셋 또는 YAML)."""

    kind: Literal["preset", "yaml"]
    id: str
    name: str
    params: List[ParamSpec]
    constraints: List[Constraint]

    def build(self, values: Mapping[str, float]) -> Any:  # pragma: no cover - 하위 클래스 구현
        raise NotImplementedError

    def to_yaml(self, values: Mapping[str, float]) -> Optional[str]:
        return None

    def defaults(self) -> Dict[str, float | int]:
        return {spec.name: (int(spec.default) if spec.type == "int" else spec.default) for spec in self.params}

    def describe(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "params": [spec.to_dict() for spec in self.params],
            "constraints": [c.to_dict() for c in self.constraints],
        }


class PresetTarget(StrategyTarget):
    """kis_backtest.strategies.preset 의 등록 전략."""

    def __init__(self, strategy_id: str) -> None:
        import kis_backtest.strategies.preset  # noqa: F401 - 전략 자동 등록
        from kis_backtest.strategies.registry import StrategyRegistry

        meta = StrategyRegistry.get_metadata(strategy_id)
        if meta is None:
            raise KeyError(strategy_id)
        self.kind = "preset"
        self.id = strategy_id
        self.name = meta.get("name", strategy_id)
        definitions = StrategyRegistry.get_param_definitions(strategy_id)
        self.params = []
        for name, definition in definitions.items():
            ptype: ParamType = "int" if definition.get("type") == "int" else "float"
            default = definition.get("default", 0)
            low = float(definition.get("min", default))
            high = float(definition.get("max", default))
            self.params.append(_make_spec(
                name, definition.get("description") or name, default, "param",
                low=low, high=high, param_type=ptype, step=definition.get("step"),
            ))
        labels = {spec.name: spec.label for spec in self.params}
        self.constraints = [
            Constraint(a, b, f"{labels[a]} < {labels[b]}")
            for a, b in PRESET_CONSTRAINTS.get(strategy_id, [])
            if a in labels and b in labels
        ]
        self._registry = StrategyRegistry

    def build(self, values: Mapping[str, float]) -> Any:
        definition = self._registry.build_with_params(self.id, **dict(values))
        if definition is None:
            raise KeyError(self.id)
        return definition


def _fill_aliases(strategy: Dict[str, Any]) -> None:
    """alias 없는 지표·캔들스틱에 {id}_{번호}를 붙인다 (StrategyConfig.auto_fill_aliases 와 같은 규칙)."""
    counters: Dict[str, int] = {}
    for key in ("indicators", "candlesticks"):
        for item in strategy.get(key) or []:
            if isinstance(item, dict) and not item.get("alias"):
                item_id = item.get("id", "")
                counters[item_id] = counters.get(item_id, 0) + 1
                item["alias"] = f"{item_id}_{counters[item_id]}"


def _referenced_params(strategy: Dict[str, Any]) -> set[str]:
    """지표·조건에서 $이름 으로 쓰이는 선언 파라미터 이름."""
    used: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            match = _PARAM_REF.match(value)
            if match:
                used.add(match.group(1))
        elif isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(strategy.get("indicators") or [])
    for group in ("entry", "exit"):
        visit((strategy.get(group) or {}).get("conditions") or [])
    return used


class YamlTarget(StrategyTarget):
    """.kis.yaml 문자열로 받은 전략."""

    def __init__(self, yaml_content: str) -> None:
        from kis_backtest.file.loader import StrategyFileLoader

        # 형식 검증 (잘못된 YAML이면 ValueError)
        strategy_file = StrategyFileLoader.load_from_string(yaml_content)
        doc = yaml.safe_load(yaml_content)
        if not isinstance(doc, dict) or not isinstance(doc.get("strategy"), dict):
            raise ValueError("strategy 항목이 없는 YAML입니다.")
        _fill_aliases(doc["strategy"])
        self._doc = doc
        self.kind = "yaml"
        self.id = strategy_file.strategy.id
        self.name = strategy_file.metadata.name
        self.params = []
        self.constraints = []
        self._collect()

    # ---- 탐색 공간 수집 -------------------------------------------------
    def _collect(self) -> None:
        strategy = self._doc["strategy"]
        declared: Dict[str, Any] = strategy.get("params") or {}
        used = _referenced_params(strategy)
        names: set[str] = set()

        def add(spec: ParamSpec) -> None:
            if spec.name not in names:
                names.add(spec.name)
                self.params.append(spec)

        for name, definition in declared.items():
            if name not in used:
                continue  # 어디에서도 쓰이지 않는 선언은 바꿔도 결과가 같다
            if isinstance(definition, dict):
                default = definition.get("default")
                low, high = definition.get("min"), definition.get("max")
                ptype = definition.get("type")
                label = definition.get("description") or name
            else:
                default, low, high, ptype, label = definition, None, None, None, name
            if not _is_number(default):
                continue
            param_type: ParamType = "int" if ptype == "int" or (ptype is None and isinstance(default, int)) else "float"
            limits = None
            if low is None or high is None:
                limits = _AUTO_INT_LIMITS if param_type == "int" else _AUTO_FLOAT_LIMITS
            add(_make_spec(
                name, label, default, "param",
                low=float(low) if _is_number(low) else None,
                high=float(high) if _is_number(high) else None,
                param_type=param_type, limits=limits,
            ))

        indicators = [ind for ind in strategy.get("indicators") or [] if isinstance(ind, dict)]
        for ind in indicators:
            alias = ind.get("alias")
            display = ind.get("name") or alias
            for key, value in (ind.get("params") or {}).items():
                if not _is_number(value):
                    continue
                ptype: ParamType = "int" if isinstance(value, int) else "float"
                label = f"{display} {_INDICATOR_PARAM_LABELS.get(key, key)}"
                add(_make_spec(
                    f"{alias}.{key}", label, value, "indicator",
                    param_type=ptype,
                    limits=_AUTO_INT_LIMITS if ptype == "int" else _AUTO_FLOAT_LIMITS,
                    target=("indicator", alias, key),
                ))

        by_alias = {ind.get("alias"): ind for ind in indicators}
        for group in ("entry", "exit"):
            conditions = (strategy.get(group) or {}).get("conditions") or []
            for index, cond in enumerate(conditions):
                if not isinstance(cond, dict) or cond.get("candlestick"):
                    continue
                left = cond.get("indicator") or "값"
                operator = _OPERATOR_LABELS.get(cond.get("operator") or "", cond.get("operator") or "")
                prefix = f"{_GROUP_LABELS[group]} 조건 {index + 1}: {left} {operator}".strip()
                bounds = _BOUNDED_INDICATORS.get((by_alias.get(cond.get("indicator")) or {}).get("id"))
                for key in ("compare_to", "value"):
                    value = cond.get(key)
                    if not _is_number(value):
                        continue
                    low = high = None
                    if bounds:
                        low, high = _derived_range(float(value), "float")
                        low, high = max(low, bounds[0]), min(high, bounds[1])
                        if low >= high:
                            low, high = bounds
                    add(_make_spec(
                        f"{group}.{index}.{key}", f"{prefix} 비교값", value, "condition",
                        low=low, high=high, param_type="float", limits=bounds or _AUTO_FLOAT_LIMITS,
                        target=("condition", group, str(index), key),
                    ))
                scalar = cond.get("compare_scalar")
                if _is_number(scalar):
                    add(_make_spec(
                        f"{group}.{index}.compare_scalar", f"{prefix} 배수·오프셋", scalar, "condition",
                        param_type="float", limits=_AUTO_FLOAT_LIMITS, kind="scalar",
                        target=("condition", group, str(index), "compare_scalar"),
                    ))

        risk = self._doc.get("risk") or {}
        for key, label in _RISK_LABELS.items():
            item = risk.get(key)
            if isinstance(item, dict) and item.get("enabled") and _is_number(item.get("percent")):
                add(_make_spec(
                    f"risk.{key}", label, float(item["percent"]), "risk",
                    param_type="float", limits=_RISK_LIMITS, kind="risk",
                    target=("risk", key),
                ))

        self.constraints = self._period_constraints(strategy, indicators)

    def _period_constraints(self, strategy: Dict[str, Any], indicators: List[Dict[str, Any]]) -> List[Constraint]:
        """같은 종류 지표끼리 비교하는 조건(예: 단기 SMA > 장기 SMA)은 기간의 크기 순서를 지킨다."""
        by_alias = {ind.get("alias"): ind for ind in indicators}
        spec_by_name = {spec.name: spec for spec in self.params}
        declared = strategy.get("params") or {}

        def period_key(ind: Dict[str, Any]) -> Optional[Tuple[str, float]]:
            value = (ind.get("params") or {}).get("period")
            if _is_number(value):
                alias = ind.get("alias")
                name = f"{alias}.period"
                return (name, float(value)) if name in spec_by_name else None
            if isinstance(value, str):
                match = _PARAM_REF.match(value)
                if match and match.group(1) in spec_by_name:
                    definition = declared.get(match.group(1))
                    default = definition.get("default") if isinstance(definition, dict) else definition
                    return (match.group(1), float(default)) if _is_number(default) else None
            return None

        constraints: List[Constraint] = []
        seen: set[Tuple[str, str]] = set()
        for group in ("entry", "exit"):
            for cond in (strategy.get(group) or {}).get("conditions") or []:
                if not isinstance(cond, dict):
                    continue
                left, right = by_alias.get(cond.get("indicator")), by_alias.get(cond.get("compare_to"))
                if not left or not right or left is right or left.get("id") != right.get("id"):
                    continue
                a, b = period_key(left), period_key(right)
                if not a or not b or a[0] == b[0] or a[1] == b[1]:
                    continue
                smaller, larger = (a, b) if a[1] < b[1] else (b, a)
                pair = (smaller[0], larger[0])
                if pair in seen:
                    continue
                seen.add(pair)
                constraints.append(Constraint(
                    smaller[0], larger[0],
                    f"{spec_by_name[smaller[0]].label} < {spec_by_name[larger[0]].label}",
                ))
        return constraints

    # ---- 값 넣기 -------------------------------------------------------
    def _apply(self, values: Mapping[str, float], *, write_defaults: bool) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        doc = copy.deepcopy(self._doc)
        strategy = doc["strategy"]
        by_alias = {ind.get("alias"): ind for ind in strategy.get("indicators") or [] if isinstance(ind, dict)}
        overrides: Dict[str, Any] = {}
        for spec in self.params:
            if spec.name not in values:
                continue
            value = values[spec.name]
            if spec.group == "param":
                overrides[spec.name] = value
                if write_defaults:
                    definition = strategy["params"][spec.name]
                    if isinstance(definition, dict):
                        definition["default"] = value
                    else:
                        strategy["params"][spec.name] = value
                continue
            kind = spec.target[0]
            if kind == "indicator":
                by_alias[spec.target[1]].setdefault("params", {})[spec.target[2]] = value
            elif kind == "condition":
                _, group, index, key = spec.target
                strategy[group]["conditions"][int(index)][key] = value
            elif kind == "risk":
                doc["risk"][spec.target[1]]["percent"] = value
        return doc, overrides

    def build(self, values: Mapping[str, float]) -> Any:
        from kis_backtest.core.converters import from_yaml_file
        from kis_backtest.file.schema import KisStrategyFile

        doc, overrides = self._apply(values, write_defaults=False)
        return from_yaml_file(KisStrategyFile.model_validate(doc), param_overrides=overrides or None)

    def to_yaml(self, values: Mapping[str, float]) -> Optional[str]:
        """고른 값을 넣은 YAML (선언 파라미터는 default를 그 값으로 바꾼다)."""
        doc, _ = self._apply(values, write_defaults=True)
        return yaml.dump(doc, Dumper=_Dumper, allow_unicode=True, sort_keys=False)


def load_target(strategy_id: Optional[str] = None, yaml_content: Optional[str] = None) -> StrategyTarget:
    """프리셋 ID 또는 YAML 문자열에서 하이퍼옵트 대상을 만든다."""
    if bool(strategy_id) == bool(yaml_content):
        raise ValueError("strategy_id 와 yaml_content 중 하나만 보내야 합니다.")
    if strategy_id:
        return PresetTarget(strategy_id)
    return YamlTarget(yaml_content or "")


def search_space(
    target: StrategyTarget,
    choices: Iterable[Mapping[str, Any]],
) -> Tuple[List[ParamSpec], Dict[str, float | int]]:
    """사용자 선택(optimize/low/high/step/value)을 반영해 (탐색할 파라미터, 고정 값)을 만든다.

    선택에 없는 파라미터는 기본값으로 고정한다. 선택을 하나도 안 보내면 suggested 인 것만 탐색한다.
    """
    choices = list(choices) or [{"name": spec.name, "optimize": spec.suggested} for spec in target.params]
    by_name = {spec.name: spec for spec in target.params}
    optimized: List[ParamSpec] = []
    fixed: Dict[str, float | int] = target.defaults()
    seen: set[str] = set()
    for choice in choices:
        name = choice.get("name")
        spec = by_name.get(name)
        if spec is None:
            raise ValueError(f"이 전략에 없는 파라미터입니다: {name}")
        if name in seen:
            raise ValueError(f"파라미터가 두 번 들어왔습니다: {name}")
        seen.add(name)
        if choice.get("optimize", True):
            low = choice.get("low", spec.low)
            high = choice.get("high", spec.high)
            ranged = spec.with_range(
                float(spec.low if low is None else low),
                float(spec.high if high is None else high),
                choice.get("step"),
            )
            optimized.append(ranged)
            fixed.pop(name, None)
        else:
            value = choice.get("value")
            value = spec.default if value is None else float(value)
            if not spec.min_allowed <= value <= spec.max_allowed:
                raise ValueError(
                    f"{spec.label}: 고정값은 {spec.min_allowed:g} ~ {spec.max_allowed:g} 안이어야 합니다."
                )
            fixed[name] = round(value) if spec.type == "int" else value
    if not optimized:
        raise ValueError("최적화할 파라미터를 하나 이상 고르세요.")
    return optimized, fixed
