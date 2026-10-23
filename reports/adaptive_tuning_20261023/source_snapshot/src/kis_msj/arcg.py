"""Broker-independent ARCG decisions and capital accounting.

All times, completed observations, cash and order facts are supplied by the caller.
There is deliberately no clock, network client or order submission in this module.
Defaults are research starting points, not validated investment recommendations.
The existing production Strategy is not changed or activated by this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import math
from typing import Mapping, Sequence


def instant(value: datetime | str) -> datetime:
    """Interpret an ISO timestamp deterministically; naive timestamps mean UTC."""
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime):
        raise ValueError("a datetime or ISO timestamp is required")
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _finite(value: float, *, positive: bool = False) -> bool:
    return math.isfinite(value) and (value > 0 if positive else value >= 0)


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    completed: bool = True


@dataclass(frozen=True)
class ARCGConfig:
    fast_period: int = 10
    slow_period: int = 40
    slope_period: int = 5
    atr_period: int = 14
    confirmation_bars: int = 2
    trend_band: float = 0.002
    slope_threshold: float = 0.001
    market_volatility_limit: float = 0.08
    safety_cash_fraction: float = 0.15
    account_cap_fraction: float = 0.85
    symbol_cap_fraction: float = 0.10
    sector_cap_fraction: float = 0.35
    max_symbol_amount: float = 0.0
    initial_fraction: float = 0.15
    addition_fraction: float = 0.15
    max_lots: int = 16
    base_gap: float = 0.025
    atr_gap_multiplier: float = 1.25
    utilization_gap_multiplier: float = 1.0
    neutral_gap_multiplier: float = 1.25
    max_gap: float = 0.20
    stabilization_required: bool = True
    buy_interval_seconds: float = 86400.0
    reentry_cooldown_seconds: float = 86400.0
    profit_target: float = 0.03
    profit_atr_multiplier: float = 1.0
    minimum_net_profit: float = 0.005
    buy_fee_rate: float = 0.00015
    sell_fee_rate: float = 0.00015
    sell_tax_rate: float = 0.002
    slippage_rate: float = 0.0005
    trend_fraction: float = 0.50
    trailing_fraction: float = 0.06
    trailing_atr_multiplier: float = 2.5
    min_average_turnover: float = 10000000.0
    min_price: float = 100.0
    max_entry_extension: float = 0.04
    max_snapshot_age_seconds: float = 300.0
    long_hold_days: float = 120.0
    review_interval_days: float = 20.0
    risk_reduction_fraction: float = 0.50
    reference_mode: str = "cycle_low"
    trend_risk_exit_enabled: bool = True
    # reference_mode = cycle_low, last_buy, or average for controlled comparisons.

    def __post_init__(self) -> None:
        for name in ("stabilization_required", "trend_risk_exit_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"boolean parameter required: {name}")
        for name in ("fast_period", "slow_period", "slope_period", "atr_period", "confirmation_bars", "max_lots"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"integer parameter required: {name}")
        if min(self.fast_period, self.slow_period, self.slope_period, self.atr_period, self.confirmation_bars, self.max_lots) < 1:
            raise ValueError("periods, confirmations and max_lots must be positive")
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be less than slow_period")
        for name, value in self.__dict__.items():
            if isinstance(value, (float, int)) and not isinstance(value, bool) and not _finite(value):
                raise ValueError(f"invalid nonnegative parameter: {name}")
        for name in ("safety_cash_fraction", "account_cap_fraction", "symbol_cap_fraction", "sector_cap_fraction", "initial_fraction", "addition_fraction", "trend_fraction", "risk_reduction_fraction"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"invalid fraction: {name}")
        if self.symbol_cap_fraction <= 0 or self.account_cap_fraction <= 0 or self.sector_cap_fraction <= 0:
            raise ValueError("capital caps must be positive")
        if self.initial_fraction <= 0 or self.addition_fraction <= 0 or self.max_gap >= 1:
            raise ValueError("invalid tranche fraction or max_gap")
        if self.risk_reduction_fraction <= 0:
            raise ValueError("risk_reduction_fraction must be positive")
        if self.reference_mode not in {"cycle_low", "last_buy", "average", "legacy_average"}:
            raise ValueError("unknown reference_mode")


@dataclass(frozen=True)
class Lot:
    lot_id: str
    quantity: int
    buy_price: float
    opened_at: datetime
    role: str = "rotation"
    peak_price: float = 0.0
    buy_costs: float = 0.0

    @property
    def invested(self) -> float:
        return self.quantity * self.buy_price + self.buy_costs


@dataclass(frozen=True)
class SymbolState:
    symbol: str
    sector: str = "UNKNOWN"
    lots: tuple[Lot, ...] = ()
    cycle_id: int = 0
    cycle_low: float | None = None
    cycle_budget: float = 0.0
    history_complete: bool = True
    market_regime: str = "NEUTRAL"
    symbol_regime: str = "RANGE"
    market_candidate: str = ""
    market_confirmations: int = 0
    symbol_candidate: str = ""
    symbol_confirmations: int = 0
    last_observation: datetime | None = None
    last_buy_at: datetime | None = None
    last_exit_at: datetime | None = None
    last_signal_at: datetime | None = None
    last_review_at: datetime | None = None
    paused: bool = False
    cycle_last_buy: float | None = None
    last_market_observation: datetime | None = None

    @property
    def quantity(self) -> int:
        return sum(lot.quantity for lot in self.lots)

    @property
    def invested(self) -> float:
        return sum(lot.invested for lot in self.lots)


@dataclass(frozen=True)
class PendingOrder:
    order_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: float
    sector: str = "UNKNOWN"
    filled_quantity: int = 0
    trend_fraction: float = 0.0
    lot_quantities: tuple[tuple[str, int], ...] = ()
    cycle_budget: float = 0.0

    @property
    def remaining_quantity(self) -> int:
        return self.quantity - self.filled_quantity


@dataclass(frozen=True)
class Portfolio:
    equity: float
    buyable_cash: float
    positions: Mapping[str, SymbolState]
    pending: tuple[PendingOrder, ...] = ()
    balance_ok: bool = True
    orders_ok: bool = True
    reconciled: bool = True
    observed_at: datetime | None = None
    session: str = "regular"
    market_values: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Features:
    close: float = 0.0
    atr: float = 0.0
    atr_fraction: float = 0.0
    fast_ma: float = 0.0
    slow_ma: float = 0.0
    slope: float = 0.0
    average_turnover: float = 0.0
    raw_symbol_regime: str = "RANGE"
    raw_market_regime: str = "NEUTRAL"
    entry_signal: bool = False
    stabilized: bool = False
    bar_time: datetime | None = None
    ready: bool = False


@dataclass(frozen=True)
class CashPools:
    broker_buyable_cash: float
    safety_cash: float
    outstanding_buy_reservations: float
    logical_addition_reserve: float
    new_entry_cash: float


@dataclass(frozen=True)
class CapitalAllowance:
    amount: float
    cycle_budget: float
    reason: str
    pools: CashPools


@dataclass(frozen=True)
class Decision:
    side: str
    symbol: str
    quantity: int
    limit_price: float
    reason: str
    lot_quantities: tuple[tuple[str, int], ...] = ()
    cycle_budget: float = 0.0
    signal_time: datetime | None = None
    trend_fraction: float = 0.0


@dataclass(frozen=True)
class RiskContext:
    thesis_valid: bool = True
    tradable: bool = True
    severe_deterioration: bool = False
    corporate_risk: bool = False


@dataclass(frozen=True)
class Evaluation:
    decision: Decision | None
    state: SymbolState
    features: Features
    market_features: Features
    reason: str
    pools: CashPools


@dataclass(frozen=True)
class Fill:
    execution_id: str
    order_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    time: datetime
    fees: float = 0.0
    lot_quantities: tuple[tuple[str, int], ...] = ()
    trend_fraction: float = 0.0
    trend_quantity: int | None = None


def validate_bars(bars: Sequence[Bar], now: datetime | None = None) -> None:
    previous = None
    for bar in bars:
        stamp = instant(bar.time)
        if not bar.completed:
            raise ValueError("only completed bars may be observed")
        if now is not None and stamp > instant(now):
            raise ValueError("future bar supplied")
        if previous is not None and stamp <= previous:
            raise ValueError("bars must have strictly increasing unique timestamps")
        if not all(_finite(x, positive=True) for x in (bar.open, bar.high, bar.low, bar.close)) or not _finite(bar.volume):
            raise ValueError("invalid OHLCV value")
        if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close) or bar.low > bar.high:
            raise ValueError("inconsistent OHLC range")
        previous = stamp


def compute_features(bars: Sequence[Bar], config: ARCGConfig = ARCGConfig()) -> Features:
    """Trailing simple MA slope and arithmetic ATR; no centered/future windows."""
    needed = max(config.slow_period + config.slope_period, config.atr_period + 1, config.fast_period + 1)
    # Work is bounded even when an adapter supplies the entire observed history.
    window = bars[-needed:]
    validate_bars(window)
    if not window:
        return Features()
    last = window[-1]
    if len(window) < needed:
        return Features(close=last.close, bar_time=last.time)
    closes = [bar.close for bar in window]
    fast = sum(closes[-config.fast_period:]) / config.fast_period
    slow = sum(closes[-config.slow_period:]) / config.slow_period
    prior_slow = sum(closes[-config.slow_period - config.slope_period:-config.slope_period]) / config.slow_period
    slope = slow / prior_slow - 1.0
    tr = [max(window[i].high - window[i].low, abs(window[i].high - window[i - 1].close), abs(window[i].low - window[i - 1].close)) for i in range(len(window) - config.atr_period, len(window))]
    atr = sum(tr) / config.atr_period
    atr_fraction = atr / last.close
    up = fast > slow * (1 + config.trend_band) and slope > config.slope_threshold and last.close >= fast
    down = fast < slow * (1 - config.trend_band) and slope < -config.slope_threshold and last.close <= fast
    symbol = "UPTREND" if up else "DOWNTREND" if down else "RANGE"
    market = "RISK_OFF" if down or atr_fraction > config.market_volatility_limit else "RISK_ON" if up else "NEUTRAL"
    prior_fast = sum(closes[-config.fast_period - 1:-1]) / config.fast_period
    previous = window[-2]
    stabilized = last.close >= previous.close
    cross = previous.close <= prior_fast and last.close > fast
    breakout = last.close > previous.high and last.close <= fast * (1 + config.max_entry_extension)
    rebound = stabilized and previous.close < prior_fast and last.close <= slow * (1 + config.max_entry_extension)
    turnover = sum(bar.close * bar.volume for bar in window[-config.fast_period:]) / config.fast_period
    return Features(last.close, atr, atr_fraction, fast, slow, slope, turnover, symbol, market, cross or breakout or rebound, stabilized, last.time, True)


def feature_cache_key(config: ARCGConfig) -> tuple:
    """Only settings which affect completed-bar indicator computation."""
    return (config.fast_period, config.slow_period, config.slope_period,
            config.atr_period, config.trend_band, config.slope_threshold,
            config.market_volatility_limit, config.max_entry_extension)


def update_regime(current: str, candidate: str, count: int, observed: str, confirmations: int) -> tuple[str, str, int]:
    """Advance once per new completed bar; caller owns timestamp idempotency."""
    if observed == current:
        return current, "", 0
    next_count = count + 1 if candidate == observed else 1
    return (observed, "", 0) if next_count >= confirmations else (current, observed, next_count)


def _reservation(order: PendingOrder, config: ARCGConfig) -> float:
    return order.remaining_quantity * order.limit_price * (1 + config.buy_fee_rate + config.slippage_rate) if order.side == "BUY" else 0.0


def buy_state_error(portfolio: Portfolio, now: datetime, config: ARCGConfig) -> str:
    if not portfolio.balance_ok:
        return "balance_query_failed"
    if not portfolio.orders_ok:
        return "order_state_unknown"
    if not portfolio.reconciled:
        return "position_mismatch"
    if portfolio.observed_at is None:
        return "balance_timestamp_missing"
    age = (instant(now) - instant(portfolio.observed_at)).total_seconds()
    if age < 0 or age > config.max_snapshot_age_seconds:
        return "balance_snapshot_stale"
    if not _finite(portfolio.equity, positive=True) or not _finite(portfolio.buyable_cash):
        return "invalid_cash_snapshot"
    ids = set()
    for order in portfolio.pending:
        if order.order_id in ids or order.side not in {"BUY", "SELL"} or order.quantity <= 0 or not 0 <= order.filled_quantity <= order.quantity or not _finite(order.limit_price, positive=True):
            return "invalid_order_snapshot"
        ids.add(order.order_id)
        if order.symbol not in portfolio.positions or order.sector != portfolio.positions[order.symbol].sector:
            return "pending_position_mismatch"
    for symbol, state in portfolio.positions.items():
        if symbol != state.symbol or not state.history_complete:
            return "position_history_incomplete"
        if state.quantity and (state.cycle_low is None or not _finite(state.cycle_low, positive=True) or state.cycle_budget <= 0):
            return "cycle_state_incomplete"
        if len({lot.lot_id for lot in state.lots}) != len(state.lots):
            return "duplicate_lot_id"
        if any(type(lot.quantity) is not int or lot.quantity <= 0 or not _finite(lot.buy_price, positive=True) or not _finite(lot.buy_costs) for lot in state.lots):
            return "invalid_lot_state"
        if state.market_regime not in {"RISK_ON", "NEUTRAL", "RISK_OFF"} or state.symbol_regime not in {"UPTREND", "RANGE", "DOWNTREND"}:
            return "invalid_regime_state"
    return ""


def cash_pools(portfolio: Portfolio, config: ARCGConfig = ARCGConfig()) -> CashPools:
    """Broker buyable cash is NET of broker-held pending buys; never debit it twice."""
    pending = sum(_reservation(order, config) for order in portfolio.pending)
    reserves = 0.0
    for symbol, state in portfolio.positions.items():
        if state.quantity or any(order.symbol == symbol and order.side == "BUY" and order.remaining_quantity > 0 for order in portfolio.pending):
            own_pending = sum(_reservation(order, config) for order in portfolio.pending if order.symbol == symbol)
            reserves += max(0.0, state.cycle_budget - state.invested - own_pending)
    safety = max(0.0, portfolio.equity * config.safety_cash_fraction)
    return CashPools(portfolio.buyable_cash, safety, pending, reserves, max(0.0, portfolio.buyable_cash - safety - reserves))


def capital_limit(symbol: str, state: SymbolState, portfolio: Portfolio, config: ARCGConfig, requested_amount: float, is_entry: bool, *, now: datetime | None = None) -> CapitalAllowance:
    """Limit an ALL-IN buy amount including fee/slippage reserve.

    A new cycle must have its *entire* budget available after protecting existing
    logical allocations. Additions can consume their own allocation, never the
    allocations of other held symbols. Pending sells contribute no cash.
    """
    pools = cash_pools(portfolio, config)
    def blocked(reason: str) -> CapitalAllowance:
        return CapitalAllowance(0.0, state.cycle_budget, reason, pools)
    if now is not None:
        error = buy_state_error(portfolio, now, config)
        if error:
            return blocked(error)
    elif not (portfolio.balance_ok and portfolio.orders_ok and portfolio.reconciled):
        return blocked("untrusted_account_snapshot")
    if not state.history_complete:
        return blocked("position_history_incomplete")
    if not _finite(requested_amount, positive=True):
        return blocked("invalid_requested_amount")
    if symbol != state.symbol:
        return blocked("symbol_state_mismatch")
    invested = sum(position.invested for position in portfolio.positions.values())
    reserved = pools.outstanding_buy_reservations
    sector_spent = sum(position.invested for position in portfolio.positions.values() if position.sector == state.sector)
    sector_pending = sum(_reservation(order, config) for order in portfolio.pending if order.sector == state.sector)
    own_pending = sum(_reservation(order, config) for order in portfolio.pending if order.symbol == symbol)
    symbol_cap = portfolio.equity * config.symbol_cap_fraction
    if config.max_symbol_amount > 0:
        symbol_cap = min(symbol_cap, config.max_symbol_amount)
    account_room = max(0.0, portfolio.equity * config.account_cap_fraction - invested - reserved)
    sector_room = max(0.0, portfolio.equity * config.sector_cap_fraction - sector_spent - sector_pending)
    symbol_room = max(0.0, symbol_cap - state.invested - own_pending)
    if is_entry:
        if state.quantity or own_pending:
            return blocked("entry_requires_flat_position")
        budget = symbol_cap
        # A previously reserved but unfilled cycle retains exactly its budget.
        if state.cycle_budget > 0 and state.last_exit_at is None:
            budget = state.cycle_budget
        sector_logical = sum(max(0.0, item.cycle_budget - item.invested - sum(_reservation(order, config) for order in portfolio.pending if order.symbol == key)) for key, item in portfolio.positions.items() if item.sector == state.sector and (item.quantity or any(order.symbol == key and order.side == "BUY" and order.remaining_quantity > 0 for order in portfolio.pending)))
        if budget <= 0 or pools.new_entry_cash + 1e-8 < budget:
            return blocked("cash_reserved_for_existing_cycles")
        if account_room + 1e-8 < budget + pools.logical_addition_reserve or sector_room + 1e-8 < budget + sector_logical or symbol_room + 1e-8 < budget:
            return blocked("full_cycle_budget_unavailable")
        amount = min(requested_amount, budget, pools.new_entry_cash, account_room, sector_room, symbol_room)
    else:
        budget = state.cycle_budget
        if not state.quantity or budget <= 0:
            return blocked("addition_requires_locked_cycle")
        own_logical = max(0.0, budget - state.invested - own_pending)
        other_logical = max(0.0, pools.logical_addition_reserve - own_logical)
        spendable = max(0.0, portfolio.buyable_cash - pools.safety_cash - other_logical)
        amount = min(requested_amount, own_logical, spendable, account_room, sector_room, symbol_room)
    return CapitalAllowance(max(0.0, amount), budget, "allowed" if amount > 0 else "capital_limit", pools)


def adaptive_gap(features: Features, utilization: float, market_regime: str, config: ARCGConfig = ARCGConfig()) -> float:
    gap = max(config.base_gap, config.atr_gap_multiplier * features.atr_fraction)
    gap *= 1 + config.utilization_gap_multiplier * max(0.0, utilization)
    if market_regime == "NEUTRAL":
        gap *= config.neutral_gap_multiplier
    return min(config.max_gap, gap)


def reference_price(state: SymbolState, mode: str = "cycle_low") -> float | None:
    if not state.lots:
        return None
    if mode == "average":
        return sum(lot.quantity * lot.buy_price for lot in state.lots) / state.quantity
    if mode == "legacy_average":
        return min(sum(lot.quantity * lot.buy_price for lot in state.lots) / state.quantity,
                   sum(lot.buy_price for lot in state.lots) / len(state.lots))
    if mode == "last_buy":
        return state.cycle_last_buy
    return state.cycle_low


def net_profit(lot: Lot, price: float, config: ARCGConfig = ARCGConfig()) -> float:
    """Conservative per-share return, including paid entry costs and exit costs."""
    entry = lot.buy_price + lot.buy_costs / lot.quantity
    proceeds = price * (1 - config.sell_fee_rate - config.sell_tax_rate - config.slippage_rate)
    return proceeds / entry - 1.0


def _allocations(lots: Sequence[Lot], quantity: int) -> tuple[tuple[str, int], ...]:
    remaining = quantity
    result = []
    for lot in lots:
        take = min(remaining, lot.quantity)
        if take > 0:
            result.append((lot.lot_id, take))
            remaining -= take
    return tuple(result)


def evaluate(symbol: str, bars: Sequence[Bar], market_bars: Sequence[Bar], portfolio: Portfolio, state: SymbolState, now: datetime, config: ARCGConfig = ARCGConfig(), model: str = "F", *, features: Features | None = None, market_features: Features | None = None, risk: RiskContext = RiskContext()) -> Evaluation:
    """One completed-bar decision with priority risk > exit > buy.

    Persist the returned state even when no decision is emitted. Cached features
    are accepted only for the exact last observed bar. A caller must atomically
    reserve an accepted intent before evaluating another symbol or execution.
    Model A remains the preserved external legacy Strategy; B/C legacy intents
    may also be filtered with ``capital_limit`` by a comparison adapter.
    """
    if model not in {"B", "C", "D", "E", "F"}:
        raise ValueError("ARCG evaluate supports B-F; A uses the preserved legacy Strategy")
    if symbol != state.symbol:
        raise ValueError("symbol/state mismatch")
    # Validate every supplied observation, including unused older bars, so a
    # malformed future-bearing history cannot silently pass through a cache.
    validate_bars(bars, now)
    validate_bars(market_bars, now)
    f = features if features is not None else compute_features(bars, config)
    mf = market_features if market_features is not None else compute_features(market_bars, config)
    if bars and (f.bar_time is None or instant(f.bar_time) != instant(bars[-1].time)):
        raise ValueError("cached symbol features do not match observed bar")
    if market_bars and (mf.bar_time is None or instant(mf.bar_time) != instant(market_bars[-1].time)):
        raise ValueError("cached market features do not match observed bar")
    pools = cash_pools(portfolio, config)
    def hold(reason: str, updated: SymbolState = state) -> Evaluation:
        return Evaluation(None, updated, f, mf, reason, pools)
    recorded = portfolio.positions.get(symbol)
    if recorded is not None and (recorded.quantity != state.quantity or [(lot.lot_id, lot.quantity, lot.buy_price) for lot in recorded.lots] != [(lot.lot_id, lot.quantity, lot.buy_price) for lot in state.lots]):
        return hold("position_state_mismatch")
    if not f.ready or not mf.ready or not bars:
        return hold("insufficient_completed_history")
    stamp = instant(bars[-1].time)
    if state.last_observation is not None and stamp < instant(state.last_observation):
        raise ValueError("observation moved backwards")
    new_bar = state.last_observation is None or stamp > instant(state.last_observation)
    updated = state
    if new_bar:
        market, mc, mn = state.market_regime, state.market_candidate, state.market_confirmations
        market_stamp = instant(mf.bar_time) if mf.bar_time is not None else None
        market_new = market_stamp is not None and (state.last_market_observation is None or market_stamp > instant(state.last_market_observation))
        if market_stamp is not None and state.last_market_observation is not None and market_stamp < instant(state.last_market_observation):
            raise ValueError("market observation moved backwards")
        if market_new:
            market, mc, mn = update_regime(state.market_regime, state.market_candidate, state.market_confirmations, mf.raw_market_regime, config.confirmation_bars)
        regime, sc, sn = update_regime(state.symbol_regime, state.symbol_candidate, state.symbol_confirmations, f.raw_symbol_regime, config.confirmation_bars)
        updated = replace(state, market_regime=market, market_candidate=mc, market_confirmations=mn, symbol_regime=regime, symbol_candidate=sc, symbol_confirmations=sn, last_observation=bars[-1].time,
                          last_market_observation=mf.bar_time if market_new else state.last_market_observation,
                          lots=tuple(replace(lot, peak_price=max(lot.peak_price, lot.buy_price, bars[-1].high)) for lot in state.lots))
    if not new_bar or (updated.last_signal_at is not None and stamp <= instant(updated.last_signal_at)):
        return hold("bar_already_evaluated", updated)
    if any(order.symbol == symbol and order.remaining_quantity > 0 for order in portfolio.pending):
        return hold("symbol_order_pending", updated)
    if not risk.tradable:
        return hold("symbol_not_tradable", updated)
    if not updated.history_complete:
        return hold("position_history_incomplete", updated)
    regime_enabled = model in {"C", "D", "E", "F"}
    trend_enabled = model in {"E", "F"}
    # Existing lots can be sold only against a successful, reconciled position
    # snapshot. A separate execution adapter still refreshes all broker facts.
    if updated.quantity and (not portfolio.balance_ok or not portfolio.reconciled):
        return hold("position_snapshot_untrusted", updated)
    def emit(decision: Decision) -> Evaluation:
        return Evaluation(decision, replace(updated, last_signal_at=bars[-1].time), f, mf, decision.reason, pools)
    if updated.quantity:
        market_value = portfolio.market_values.get(symbol, updated.quantity * f.close)
        symbol_cap = portfolio.equity * config.symbol_cap_fraction
        if config.max_symbol_amount > 0:
            symbol_cap = min(symbol_cap, config.max_symbol_amount)
        sector_value = sum(portfolio.market_values.get(key, item.invested) for key, item in portfolio.positions.items() if item.sector == updated.sector)
        over_cap = market_value > symbol_cap * 1.10 or sector_value > portfolio.equity * config.sector_cap_fraction * 1.10
        hard_risk = not risk.thesis_valid or risk.corporate_risk
        deteriorated = risk.severe_deterioration or (updated.symbol_regime == "DOWNTREND" and updated.market_regime == "RISK_OFF" and f.close < f.slow_ma * (1 - max(0.05, f.atr_fraction * 2)))
        if hard_risk or over_cap or (regime_enabled and config.trend_risk_exit_enabled and deteriorated):
            quantity = updated.quantity if hard_risk else max(1, math.ceil(updated.quantity * config.risk_reduction_fraction))
            reason = "risk_thesis_invalid" if hard_risk else "risk_concentration" if over_cap else "risk_trend_deterioration"
            return emit(Decision("SELL", symbol, quantity, f.close, reason, _allocations(updated.lots, quantity), signal_time=bars[-1].time))
        if model == "F":
            oldest = min(instant(lot.opened_at) for lot in updated.lots)
            aged = (instant(now) - oldest).total_seconds() >= config.long_hold_days * 86400
            review_due = updated.last_review_at is None or (instant(now) - instant(updated.last_review_at)).total_seconds() >= config.review_interval_days * 86400
            if aged and review_due:
                # Age initiates a review; only observed deterioration pauses buys.
                updated = replace(updated, last_review_at=now, paused=updated.symbol_regime == "DOWNTREND" or not risk.thesis_valid)
            elif updated.paused and updated.symbol_regime != "DOWNTREND" and risk.thesis_valid:
                updated = replace(updated, paused=False)
        target = max(config.profit_target, f.atr_fraction * config.profit_atr_multiplier)
        exits = []
        reason = "profit_rotation"
        for lot in updated.lots:
            profitable = net_profit(lot, f.close, config) >= config.minimum_net_profit
            reached = f.close >= lot.buy_price * (1 + target)
            if trend_enabled and lot.role == "trend":
                trail_gap = max(config.trailing_fraction, config.trailing_atr_multiplier * f.atr_fraction)
                trail_hit = f.close <= lot.peak_price * (1 - trail_gap)
                trend_exit = updated.symbol_regime != "UPTREND"
                if profitable and (trail_hit or (trend_exit and reached)):
                    exits.append((lot.lot_id, lot.quantity))
                    reason = "profit_trailing" if trail_hit else "profit_trend_exit"
            elif profitable and reached:
                exits.append((lot.lot_id, lot.quantity))
        if exits:
            return emit(Decision("SELL", symbol, sum(qty for _, qty in exits), f.close, reason, tuple(exits), signal_time=bars[-1].time))
    error = buy_state_error(portfolio, now, config)
    if error:
        return hold(error, updated)
    if regime_enabled and (updated.market_regime == "RISK_OFF" or updated.symbol_regime == "DOWNTREND"):
        return hold("regime_blocks_buy", updated)
    if updated.paused:
        return hold("long_hold_review_paused", updated)
    if f.average_turnover < config.min_average_turnover or f.close < config.min_price:
        return hold("liquidity_or_price_filter", updated)
    if risk.corporate_risk or not risk.thesis_valid:
        return hold("risk_blocks_buy", updated)
    entry = updated.quantity == 0
    if entry:
        if not f.entry_signal:
            return hold("entry_signal_absent", updated)
        if updated.last_exit_at is not None:
            if stamp <= instant(updated.last_exit_at) or (instant(now) - instant(updated.last_exit_at)).total_seconds() < config.reentry_cooldown_seconds:
                return hold("reentry_cooldown", updated)
            if model == "F" and (updated.last_signal_at is not None and stamp <= instant(updated.last_signal_at)):
                return hold("fresh_reentry_signal_required", updated)
        requested = portfolio.equity * config.symbol_cap_fraction * config.initial_fraction
        if config.max_symbol_amount:
            requested = min(requested, config.max_symbol_amount * config.initial_fraction)
        reason = "reentry_signal" if updated.last_exit_at is not None else "entry_signal"
    else:
        if len(updated.lots) >= config.max_lots:
            return hold("auxiliary_lot_limit", updated)
        if updated.last_buy_at is not None and (instant(now) - instant(updated.last_buy_at)).total_seconds() < config.buy_interval_seconds:
            return hold("buy_frequency_limit", updated)
        reference = reference_price(updated, config.reference_mode)
        if reference is None:
            return hold("cycle_low_missing", updated)
        gap = adaptive_gap(f, updated.invested / updated.cycle_budget, updated.market_regime, config) if model in {"D", "E", "F"} else config.base_gap
        if f.close > reference * (1 - gap):
            return hold("addition_price_not_reached", updated)
        if model in {"D", "E", "F"} and config.stabilization_required and not f.stabilized:
            return hold("addition_stabilization_pending", updated)
        requested = updated.cycle_budget * config.addition_fraction
        reason = "adaptive_addition" if model in {"D", "E", "F"} else "grid_addition"
    # Use the just-observed state for internal exposure; all other positions are
    # unchanged. This also guards callers whose state object was not the mapping.
    positions = dict(portfolio.positions)
    positions[symbol] = updated
    allowance = capital_limit(symbol, updated, replace(portfolio, positions=positions), config, requested, entry, now=now)
    per_share = f.close * (1 + config.buy_fee_rate + config.slippage_rate)
    quantity = math.floor((allowance.amount + 1e-8) / per_share)
    if quantity <= 0:
        return hold(allowance.reason if allowance.amount == 0 else "integer_share_unaffordable", updated)
    fraction = config.trend_fraction if trend_enabled and updated.symbol_regime == "UPTREND" else 0.0
    return emit(Decision("BUY", symbol, quantity, f.close, reason, cycle_budget=allowance.cycle_budget, signal_time=bars[-1].time, trend_fraction=fraction))


def lock_cycle_budget(state: SymbolState, budget: float) -> SymbolState:
    """Lock before entry; a held or pending cycle can never expand its budget."""
    if not _finite(budget, positive=True):
        raise ValueError("cycle budget must be positive")
    if state.quantity or (state.cycle_budget > 0 and state.last_exit_at is None):
        if not math.isclose(state.cycle_budget, budget, rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError("cycle budget is immutable")
        return state
    return replace(state, cycle_budget=budget)


def apply_fill(state: SymbolState, fill: Fill) -> SymbolState:
    """Pure fill transition; persistent execution-ID idempotency lives in StateBook.

    Sold lots do not change a cycle's historical low. The low resets on the
    first *actual buy execution* after flat, not on an intent or cancellation.
    Integer splitting rounds trend shares down; a one-share fill is rotation.
    """
    if fill.symbol != state.symbol or fill.side not in {"BUY", "SELL"}:
        raise ValueError("fill symbol/side mismatch")
    if not fill.execution_id or not fill.order_id or type(fill.quantity) is not int or fill.quantity <= 0 or not _finite(fill.price, positive=True) or not _finite(fill.fees):
        raise ValueError("invalid execution")
    if not 0 <= fill.trend_fraction <= 1:
        raise ValueError("invalid trend fraction")
    instant(fill.time)
    if fill.side == "BUY":
        if state.cycle_budget <= 0:
            raise ValueError("budget must be locked before the first fill")
        if state.quantity and (state.cycle_low is None or not state.history_complete):
            raise ValueError("existing cycle history is incomplete")
        if state.invested + fill.quantity * fill.price + fill.fees > state.cycle_budget + 1e-6:
            raise ValueError("actual fill exceeds immutable cycle budget")
        starting = state.quantity == 0
        low = fill.price if starting else min(state.cycle_low if state.cycle_low is not None else fill.price, fill.price)
        trend_qty = math.floor(fill.quantity * fill.trend_fraction) if fill.trend_quantity is None else fill.trend_quantity
        if type(trend_qty) is not int or not 0 <= trend_qty <= fill.quantity:
            raise ValueError("invalid integer trend allocation")
        rotation_qty = fill.quantity - trend_qty
        additions = []
        for suffix, quantity, role in (("R", rotation_qty, "rotation"), ("T", trend_qty, "trend")):
            if quantity:
                additions.append(Lot(f"{fill.execution_id}:{suffix}", quantity, fill.price, fill.time, role, fill.price, fill.fees * quantity / fill.quantity))
        if any(lot.lot_id in {old.lot_id for old in state.lots} for lot in additions):
            raise ValueError("execution already represented in lots")
        return replace(state, lots=state.lots + tuple(additions), cycle_id=state.cycle_id + int(starting), cycle_low=low, cycle_last_buy=fill.price, last_buy_at=fill.time, paused=False)
    if fill.quantity > state.quantity:
        raise ValueError("sell exceeds confirmed holdings")
    allocations = fill.lot_quantities or _allocations(state.lots, fill.quantity)
    if sum(quantity for _, quantity in allocations) != fill.quantity or len({key for key, _ in allocations}) != len(allocations):
        raise ValueError("invalid sell allocations")
    by_id = {lot.lot_id: lot for lot in state.lots}
    for key, quantity in allocations:
        if key not in by_id or quantity <= 0 or quantity > by_id[key].quantity:
            raise ValueError("sell allocation exceeds lot")
    selected = dict(allocations)
    remaining = tuple(replace(lot, quantity=lot.quantity - selected.get(lot.lot_id, 0), buy_costs=lot.buy_costs * (lot.quantity - selected.get(lot.lot_id, 0)) / lot.quantity) for lot in state.lots if lot.quantity > selected.get(lot.lot_id, 0))
    return replace(state, lots=remaining, last_exit_at=fill.time if not remaining else state.last_exit_at, paused=False if not remaining else state.paused)
