"""Isolated adaptive numeric adapters and no-broker chronological replays.

Every state is an in-memory research state. The only OrderManager operation
used is its pure request builder. No AutoTrader, StateStore, live clients,
runtime-control writer, or production database is constructed.
"""
from __future__ import annotations

from . import legacy_policy
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
import math
import statistics
import heapq
from types import SimpleNamespace
from typing import Any, Iterable

from adaptive_oct23_policy.adaptive_policy import floor_order_price, lowest_lot_buy_target, reentry_buy_target
from adaptive_oct23_policy.arcg_backtest import BacktestResult, DailyDataset, historical_clock, _metrics, _period_returns
from adaptive_oct23_policy.config import BotConfig
from adaptive_oct23_policy.lot_manager import LotManager, ceil_price, round_price
from adaptive_oct23_policy.models import AccountSnapshot, OrderRequest, OrderSide, SellReason, TradeFill
from adaptive_oct23_policy.order_manager import OrderManager
from adaptive_oct23_policy.position_manager import PositionManager
from adaptive_oct23_policy.risk_manager import RiskManager
from adaptive_oct23_policy.strategy import LotGridStrategy
from adaptive_oct23_policy.volatility import calculate_volatility

from .parameters import BASELINE, Parameters


class TuneLotManager(LotManager):
    """Override numeric base target and decay coefficient; inherit ordering."""
    def __init__(self, config, parameters=BASELINE, lots=None):
        super().__init__(config, lots)
        self.parameters = parameters
        self._by_code = defaultdict(list)
        for lot in self.lots.values():
            self._by_code[lot.code].append(lot)

    def open_lots(self, code):
        # Same predicate and insertion order as production; the local index
        # avoids repeatedly scanning unrelated symbols in portfolio reserves.
        return [lot for lot in self._by_code.get(code, ())
                if lot.remaining_quantity > 0 and lot.status != "CLOSED"]

    def create_buy_lot(self, fill):
        before = len(self.lots)
        lot = super().create_buy_lot(fill)
        if len(self.lots) > before:
            self._by_code[lot.code].append(lot)
        return lot

    def current_target_profit_info(self, code, exposure=None, cash_available=None):
        if not self.config.adaptive_lot_enabled:
            return super().current_target_profit_info(code, exposure, cash_available)
        p = self.parameters
        rate = p.sell_profit_pct / 100 * self.volatility_multiplier_for(code) * p.trend_multiplier(
            self.consecutive_sell_counts.get(code, 0))
        return rate, "", "adaptive_volatility_trend"

    def age_decay_rate_for(self, code, now=None):
        if self.config.adaptive_lot_enabled:
            return len(self.open_lots(code)) * self.parameters.decay_pp / 100
        return super().age_decay_rate_for(code, now)


class TuneStrategy(LotGridStrategy):
    """Retain all production guards; replace only numeric price calculations."""
    def __init__(self, config, lot_manager, parameters=BASELINE):
        super().__init__(config, lot_manager)
        self.parameters = parameters

    def adaptive_buy_details(self, position, current_price, snapshot):
        result = super().adaptive_buy_details(position, current_price, snapshot)
        # Parent executes all retirement, risk, sizing, reserves and cash gates.
        # Its price failure is the final guard, so only that final guard may be
        # removed/replaced when this experiment changes the numeric ceiling.
        p = self.parameters
        lots = self.lot_manager.open_lots(position.code)
        multiplier = self.lot_manager.volatility_multiplier_for(position.code)
        gap = p.buy_gap_pct * multiplier * p.trend_multiplier(position.consecutive_buy_count)
        normal = lowest_lot_buy_target(lots, gap)
        rebuy = reentry_buy_target(position.last_sell_price, p.rebuy_gap_pct * multiplier) if position.rebuy_restriction_active else 0.0
        ceilings = ([normal] if lots else []) + ([rebuy] if position.rebuy_restriction_active else [])
        target = floor_order_price(min(ceilings) if ceilings else current_price)
        condition = current_price > 0 and target > 0 and current_price <= target
        if not lots and not self._lot_sizing_enabled() and current_price > self.config.strategy.initial_buy_amount:
            condition = False
        block = result["skip_reason"]
        if block == "adaptive_buy_price_not_reached":
            block = ""
        if not condition:
            block = block or "adaptive_buy_price_not_reached"
        result.update(buy_target_price=target, normal_buy_target=normal, reentry_target=rebuy,
                      effective_buy_gap_pct=gap, effective_rebuy_gap_pct=p.rebuy_gap_pct * multiplier,
                      buy_condition_met=condition, skip_reason=block)
        return result


def experiment_components(config: BotConfig, parameters: Parameters = BASELINE):
    """Return independent managers, with a fail-closed mock client and no DB."""
    cfg = replace(config, strategy=replace(config.strategy,
        reentry_reserve_lots_per_symbol=parameters.reserve_lots,
        reentry_cash_buffer_ratio=parameters.cash_buffer_ratio),
        order=replace(config.order, live_trading=False))
    if not cfg.strategy.adaptive_lot_enabled:
        raise ValueError("The source config must already enable Adaptive LOT")
    lm = TuneLotManager(cfg.strategy, parameters)
    pm = PositionManager(cfg.strategy, lm)
    strategy = TuneStrategy(cfg, lm, parameters)
    strategy.positions = pm.positions
    # This stub cannot submit orders or open account/market-data connections.
    orders = OrderManager(cfg, SimpleNamespace(), None, logging.getLogger(__name__))
    return cfg, lm, pm, strategy, RiskManager(cfg), orders


@dataclass(frozen=True)
class QuoteEvent:
    time: datetime
    code: str
    price: int
    execution_time: datetime | None = None
    execution_price: int | None = None
    volume_capacity: int | None = None


@dataclass
class _Pending:
    request: OrderRequest
    signal_time: datetime
    execute_after: datetime
    execution_price: int | None = None
    expires_at: datetime | None = None


def exchange_tick(price):
    """KRX common-share 2026 execution grid; strategy grid remains inherited."""
    return (1 if price < 2_000 else 5 if price < 5_000 else 10 if price < 20_000 else
            50 if price < 50_000 else 100 if price < 200_000 else 500 if price < 500_000 else 1_000)


def execution_price(raw, slip, buy, official_grid=True):
    value = raw * (1 + slip if buy else 1 - slip)
    if not official_grid:
        return ceil_price(value) if buy else floor_order_price(value)
    unit = exchange_tick(value)
    return int(math.ceil((value - 1e-9) / unit) if buy else math.floor((value + 1e-9) / unit)) * unit


class _PortfolioReplay:
    def __init__(self, config, parameters, capital, options, names=None):
        self.cfg, self.lm, self.pm, self.strategy, self.risk, self.orders = experiment_components(config, parameters)
        self.parameters, self.capital, self.cash = parameters, float(capital), float(capital)
        self.options, self.names = options, names or {}
        self.pending: dict[str, _Pending] = {}
        self.prices, self.last_buy, self.last_request = {}, {}, {}
        self.quantities, self.exposures = {}, {}
        self.marked_value = self.invested = 0.0
        self.buy_fees, self.trades, self.equity, self.ages = {}, [], [], []
        self.counts, self.blocks, self.observed = Counter(), Counter(), Counter()
        self.realized = self.fees = self.slippage_cost = self.decay_pnl = 0.0
        self.peak = self.prev_equity = self.capital
        self.max_lots = 0
        self.reentry_attempts = self.reentry_signals = self.reentry_fills = 0
        self.minimum_available_cash = self.capital
        self.daily_minimum_available_cash = self.capital
        self.daily_max_lots = 0
        self.last_day = ""
        self.initial_order_count = self.initial_order_amount = 0
        self.buy_fee = float(options.get("buy_fee_bps", 1.5)) / 10000
        self.sell_fee = float(options.get("sell_fee_tax_bps", config.strategy.estimated_fee_tax_pct * 100)) / 10000
        self.slip = float(options.get("slippage_bps", 5)) / 10000
        self.stocks = {s.code: s for s in config.stocks}
        for code, stock in self.stocks.items():
            position = self.pm.get(code, stock.name)
            position.retire_after_exit, position.retire_reason = stock.retire_after_exit, stock.retire_reason
            position.danger_state = stock.danger_state
            position.auto_buy_enabled = stock.enabled and not stock.manual_only and not stock.danger_state

    def reservation(self):
        return sum(p.request.quantity * p.request.limit_price * (1 + self.buy_fee)
                   for p in self.pending.values() if p.request.side is OrderSide.BUY)

    def snapshot(self):
        nav = self.cash + self.marked_value
        available = max(0.0, self.cash - self.reservation())
        self.minimum_available_cash = min(self.minimum_available_cash, available)
        self.daily_minimum_available_cash = min(self.daily_minimum_available_cash, available)
        return AccountSnapshot(int(available), int(nav), int(self.marked_value - self.invested), int(nav - self.prev_equity))

    def update_price(self, code, price):
        previous = self.prices.get(code, price)
        self.marked_value += self.quantities.get(code, 0) * (price - previous)
        self.prices[code] = price

    def expire(self, now):
        for code, pending in list(self.pending.items()):
            if pending.expires_at is not None and now > pending.expires_at:
                self.pending.pop(code)
                self.counts["expired_orders"] += 1

    def fill(self, code, now, price, capacity=None, *, low=None, high=None):
        pending = self.pending.get(code)
        if pending is None or now < pending.execute_after:
            return
        request = pending.request
        # Time-limited pending orders do not trade on stale next-day quotes.
        if pending.expires_at is not None and now > pending.expires_at:
            self.pending.pop(code)
            self.counts["expired_orders"] += 1
            return
        raw = price
        execution = execution_price(raw, self.slip, request.side is OrderSide.BUY,
                                    self.options.get("official_execution_grid", True))
        if (request.side is OrderSide.BUY and execution > request.limit_price or
                request.side is OrderSide.SELL and execution < request.limit_price):
            self.counts["quote_not_marketable"] += 1
            return
        if low is not None and execution < low or high is not None and execution > high:
            self.counts["outside_observed_price_envelope"] += 1
            return
        quantity = request.quantity if capacity is None else min(request.quantity, max(0, capacity))
        if request.side is OrderSide.BUY:
            # Keep other acknowledged pending BUY reservations available.
            own = request.quantity * request.limit_price * (1 + self.buy_fee)
            spendable = max(0, self.cash - max(0, self.reservation() - own))
            affordable = int(spendable // (execution * (1 + self.buy_fee)))
            if affordable < quantity:
                self.blocks["cash_shortfall_execution"] += 1
            quantity = min(quantity, affordable)
        else:
            lot = self.lm.lots.get(request.lot_id)
            quantity = min(quantity, lot.remaining_quantity if lot else 0)
        if quantity <= 0:
            self.counts["zero_fill_capacity"] += 1
            return
        self.counts["partial_fills"] += int(quantity < request.quantity)
        self.counts[request.side.value.lower() + "_fills"] += 1
        trade_id = f"R{sum(self.counts[k] for k in ('buy_fills', 'sell_fills')):09d}"
        fee = quantity * execution * (self.buy_fee if request.side is OrderSide.BUY else self.sell_fee)
        self.fees += fee
        slip_cost = abs(execution - raw) * quantity
        self.slippage_cost += slip_cost
        pnl, holding, lot_id = 0.0, None, request.lot_id or trade_id
        closed_lot = False
        active_lot_count_before = len(self.lm.open_lots(code))
        if request.side is OrderSide.BUY:
            self.cash -= quantity * execution + fee
            self.buy_fees[lot_id] = fee
            self.last_buy[code] = now
            self.reentry_fills += int(request.reentry_type != "NONE")
            self.counts["reentry_fills"] += int(request.reentry_type != "NONE")
        else:
            lot = self.lm.lots[lot_id]
            allocated = self.buy_fees.get(lot_id, 0.0) * quantity / lot.remaining_quantity
            self.buy_fees[lot_id] = self.buy_fees.get(lot_id, 0.0) - allocated
            pnl = (execution - lot.buy_price) * quantity - fee - allocated
            holding = (now - datetime.fromisoformat(lot.buy_filled_at)).total_seconds() / 86400
            if quantity == lot.remaining_quantity:
                closed_lot = True
                self.ages.append(holding)
            self.cash += quantity * execution - fee
            self.realized += pnl
            if request.sell_reason == SellReason.AUTO_DECAY_CLEANUP_SELL.value:
                self.decay_pnl += pnl
        fill = TradeFill(code, self.names.get(code, code), request.side, quantity, execution,
            trade_id, now, lot_id, trade_id, request.sell_reason, request.reentry_type)
        updated = self.pm.apply_fill(fill)
        old_quantity = self.quantities.get(code, 0)
        self.marked_value += (updated.quantity - old_quantity) * self.prices.get(code, price)
        self.quantities[code] = updated.quantity
        self.invested += updated.cumulative_invested_amount - self.exposures.get(code, 0)
        self.exposures[code] = updated.cumulative_invested_amount
        self.update_price(code, price)
        if self.cash < -1e-5:
            raise RuntimeError("Negative experiment cash")
        # Conservative partial-order model cancels all unfilled remainder;
        # no extra LOT or fictitious fill is created for the canceled remainder.
        self.pending.pop(code)
        self.max_lots = max(self.max_lots, sum(l.remaining_quantity > 0 for l in self.lm.lots.values()))
        self.daily_max_lots = max(self.daily_max_lots, sum(l.remaining_quantity > 0 for l in self.lm.lots.values()))
        if self.options.get("collect_trades", True):
            self.trades.append(dict(datetime=now.isoformat(), signal_date=pending.signal_time.isoformat(),
                code=code, side=request.side.value, quantity=quantity, price=execution, amount=quantity * execution,
                lot_id=lot_id, fees_tax=fee, slippage_cost=slip_cost, realized_pnl=pnl,
                holding_days=holding, reason=request.reason, sell_reason=request.sell_reason,
                closed_lot=closed_lot, active_lot_count_before=active_lot_count_before,
                reentry_type=request.reentry_type, cash_after=self.cash))

    def signal(self, code, now, price, execute_after, execution_price=None, expires_at=None):
        if code in self.pending:
            self.counts["open_order_block"] += 1
            return
        stock = self.stocks.get(code)
        if stock and (not stock.enabled or stock.manual_only):
            self.blocks["stock_disabled_or_manual"] += 1
            return
        position = self.pm.refresh_from_lots(code, price)
        self.strategy.lot_manager.set_symbol_trade_state(code, position.consecutive_sell_count)
        snapshot = self.snapshot()
        account = self.risk.account_buy_allowed(snapshot, self.pm.positions)
        symbol = self.risk.symbol_buy_allowed(position)
        restricted = position.rebuy_restriction_active
        self.reentry_attempts += int(restricted)
        action = self.strategy.decide(position, price, snapshot, account, symbol)
        if action is None:
            self.blocks[position.skip_reason or "no_signal"] += 1
            return
        if stock and action.side is OrderSide.BUY and stock.buy_blocked:
            self.blocks["BUY_BLOCKED_BY_STOCK_FLAG"] += 1
            return
        if action.side is OrderSide.BUY:
            # Mirror the production pre-request portfolio guards. The strategy
            # and RiskManager alone do not contain these aggregate limits.
            open_lots = [lot for lot in self.lm.lots.values() if lot.remaining_quantity > 0]
            risk = self.cfg.risk
            if len(open_lots) >= risk.max_total_open_lots:
                self.blocks["max_total_open_lots_reached"] += 1
                return
            if sum(lot.open_amount for lot in open_lots) >= risk.max_total_invested_amount:
                self.blocks["max_total_invested_amount_reached"] += 1
                return
            if action.reason in {"initial_buy", "adaptive_initial_buy"}:
                active = {l.code for l in open_lots} | set(self.pending)
                active.update(c for c, held in self.pm.positions.items() if held.position_state in {
                    "HOLDING", "WAIT_REENTRY", "COOLDOWN_AFTER_CLEANUP", "REVIEW_REQUIRED", "RISK_BLOCKED", "SYNC_REQUIRED"})
                block = ("max_active_symbols_reached" if code not in active and len(active) >= risk.max_active_symbols
                         else "max_new_buy_per_day_reached" if self.initial_order_count >= risk.max_new_buy_per_day else "")
                day_limit = risk.max_total_initial_buy_amount_per_day or risk.max_new_buy_amount_per_day
                if not block and day_limit and self.initial_order_amount + action.amount > day_limit:
                    block = "max_new_buy_amount_per_day_reached"
                if block:
                    self.blocks[block] += 1
                    return
            if restricted and any(p.request.side is OrderSide.BUY for p in self.pending.values()):
                self.blocks["adaptive_reentry_wait_pending_buy"] += 1
                return
            if code in self.last_buy and (now - self.last_buy[code]).total_seconds() < self.cfg.order.order_cooldown_seconds:
                self.blocks["buy_fill_cooldown"] += 1
                return
            if position.needs_review:
                self.blocks["review_required"] += 1
                return
        if code in self.last_request and (now - self.last_request[code]).total_seconds() < self.cfg.order.min_order_request_interval_seconds:
            self.blocks["min_order_request_interval"] += 1
            return
        request = self.orders.build_request(position, action, price)
        if request is None:
            self.blocks["build_request_rejected"] += 1
            return
        if request.side is OrderSide.BUY:
            required = request.quantity * request.limit_price * (1 + self.buy_fee)
            if required > snapshot.cash_available:
                self.blocks["cash_shortfall_reservation"] += 1
                return
        self.pending[code] = _Pending(request, now, execute_after, execution_price, expires_at)
        self.last_request[code] = now
        self.reentry_signals += int(action.reentry_type != "NONE")
        if action.reason in {"initial_buy", "adaptive_initial_buy"}:
            self.initial_order_count += 1
            self.initial_order_amount += request.quantity * request.limit_price
        self.snapshot()

    def mark(self, day):
        snapshot = self.snapshot()
        value, invested = self.marked_value, self.invested
        # A complete independent book reconciliation runs at each daily mark,
        # rather than rescanning every symbol's history on every quote.
        full_value = sum(l.remaining_quantity * self.prices.get(l.code, l.buy_price)
                         for l in self.lm.lots.values() if l.remaining_quantity > 0)
        full_invested = sum(l.open_amount for l in self.lm.lots.values() if l.remaining_quantity > 0)
        if abs(value - full_value) > .001 or abs(invested - full_invested) > .001:
            raise RuntimeError("Incremental portfolio bookkeeping differs from full LOT reconciliation")
        nav = self.cash + value
        unrealized = value - invested - sum(self.buy_fees.values())
        if abs((nav - self.capital) - self.realized - unrealized) > .05:
            raise RuntimeError("NAV = initial + realized + unrealized invariant failed")
        self.peak = max(self.peak, nav)
        available = max(0.0, self.cash - self.reservation())
        if self.reservation() > self.cash + 1e-6:
            raise RuntimeError("Reservations exceed available experiment cash")
        self.equity.append(dict(date=day, equity=nav, cash=self.cash, available_cash=available,
            reserved_cash=self.reservation(), stock_value=value, invested_cost=invested,
            realized_pnl=self.realized, unrealized_pnl=unrealized,
            drawdown_pct=(nav / self.peak - 1) * 100, cash_ratio_pct=self.cash / nav * 100 if nav > 0 else 0,
            open_lots=sum(l.remaining_quantity > 0 for l in self.lm.lots.values()),
            minimum_available_cash_intraday_day=self.daily_minimum_available_cash,
            maximum_open_lots_intraday_day=self.daily_max_lots,
            cumulative_counts=dict(self.counts), cumulative_buy_blocks=dict(self.blocks),
            cumulative_reentry_orders=self.reentry_signals, cumulative_reentry_fills=self.reentry_fills,
            cumulative_fees_tax=self.fees, cumulative_slippage_cost=self.slippage_cost,
            cumulative_decay_realized_pnl=self.decay_pnl))
        self.prev_equity = nav

    def result(self, source, limitations):
        summary = _metrics(self.equity, self.capital)
        mdd = abs(summary["max_drawdown_pct"])
        summary.update(total_trades=self.counts["buy_fills"] + self.counts["sell_fills"],
            buy_fills=self.counts["buy_fills"], sell_fills=self.counts["sell_fills"],
            fees_tax=self.fees, slippage_cost=self.slippage_cost, total_execution_cost=self.fees + self.slippage_cost,
            minimum_available_cash=self.minimum_available_cash,
            cash_shortfall_blocks=sum(v for k, v in self.blocks.items() if "cash" in k),
            average_completed_lot_holding_days=statistics.mean(self.ages) if self.ages else None,
            maximum_simultaneous_open_lots=self.max_lots, decay_realized_pnl=self.decay_pnl,
            reentry_attempts=self.reentry_signals, reentry_restricted_evaluations=self.reentry_attempts,
            reentry_order_signals=self.reentry_signals,
            reentry_fills=self.reentry_fills, calmar_ratio=summary["cagr_pct"] / mdd if mdd else None,
            economic_valid=False, source=source)
        diagnostics = dict(parameters=self.parameters.to_dict(), counts=dict(self.counts),
            buy_blocks=dict(self.blocks), reentry_attempts=self.reentry_signals,
            reentry_restricted_evaluations=self.reentry_attempts,
            closed_lot_holding_days=self.ages, decay_realized_pnl=self.decay_pnl,
            fees_tax=self.fees, slippage_cost=self.slippage_cost,
            minimum_available_cash_intraday=self.minimum_available_cash,
            max_open_lots_intraday=self.max_lots,
            remaining_pending_orders=len(self.pending), assumptions=self.options,
            policy_reuse="Production strategy, LOT, positions, risk, request builder; numeric-only inherited adapters",
            limitations=limitations)
        last = datetime.fromisoformat(self.equity[-1]["date"]).replace(hour=15, minute=30)
        open_ages = [(last - datetime.fromisoformat(l.buy_filled_at)).total_seconds() / 86400
                     for l in self.lm.lots.values() if l.remaining_quantity > 0]
        diagnostics["open_lot_holding_days"] = open_ages
        diagnostics["average_open_lot_age_days"] = statistics.mean(open_ages) if open_ages else None
        summary["average_open_lot_age_days"] = statistics.mean(open_ages) if open_ages else None
        return BacktestResult(summary, self.equity, self.trades,
            _period_returns(self.equity, self.capital, 4), _period_returns(self.equity, self.capital, 7), diagnostics)


def run_backtest(dataset: DailyDataset, parameters: Parameters = BASELINE, options: dict | None = None):
    """One completed-close decision; following session open fills only.

    This deliberately never assumes whether a day's high preceded its low.
    It under-samples the production intraday strategy and is labelled a
    conditional daily counterfactual, not full intraday or actual performance.
    """
    options = dict(options or {})
    capital = float(options.get("capital", 100_000_000))
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("capital must be positive and finite")
    dates = [d for d in dataset.dates if (not options.get("start") or d >= options["start"])
             and (not options.get("end") or d <= options["end"])]
    if not dates:
        raise ValueError("No replay dates")
    replay = _PortfolioReplay(dataset.config, parameters, capital, options, dataset.names)
    estimates = dataset.feature_cache.setdefault("adaptive_vol20_60", {})
    closes = defaultdict(list)
    for day in sorted(dataset.bars):
        if day >= dates[0]:
            break
        for code, bar in dataset.bars[day].items():
            closes[code].append(bar.close)
    participation = float(options.get("volume_participation", .001))
    previous_bars = {}
    quarantined, events = set(), []
    previous_days = [d for d in sorted(dataset.bars) if d < dates[0]]
    for d in previous_days:
        previous_bars.update(dataset.bars[d])
    gap_limit = float(options.get("gap_quarantine_threshold", .30))
    with historical_clock(datetime.fromisoformat(dates[0])) as clock:
        for day in dates:
            bars = dataset.bars[day]
            now = datetime.fromisoformat(day).replace(hour=9)
            clock.current = now
            for position in replay.pm.positions.values():
                position.daily_buy_amount = position.daily_sell_amount = 0
            replay.initial_order_count = replay.initial_order_amount = 0
            for code, bar in bars.items():
                prior = previous_bars.get(code)
                if prior and abs(bar.open / prior.close - 1) > gap_limit:
                    quarantined.add(code)
                    events.append(dict(date=day, code=code, reason="raw_gap_unresolved_corporate_action"))
                replay.update_price(code, bar.open)
            # Same-timestamp order sequence follows deterministic configured
            # symbol order, without sorting sales ahead of purchases.
            for code in list(replay.pending):
                bar, prior = bars.get(code), previous_bars.get(code)
                if code in quarantined or not bar or bar.volume <= 0 or prior is None:
                    replay.counts["missing_halted_quarantine"] += 1
                    continue
                capacity = int(prior.volume * participation)
                replay.fill(code, now, int(bar.open), capacity, low=bar.low, high=bar.high)
            replay.counts["expired_daily_remainders"] += len(replay.pending)
            replay.pending.clear()
            now = datetime.fromisoformat(day).replace(hour=15, minute=30)
            clock.current = now
            for code, bar in bars.items():
                replay.update_price(code, bar.close)
                closes[code].append(bar.close)
                key = (day, code)
                if key not in estimates:
                    estimate = calculate_volatility(closes[code])
                    estimates[key] = (estimate.vol20, estimate.vol60)
                vol20, vol60 = estimates[key]
                multiplier = parameters.volatility_multiplier(vol20, vol60) if vol20 is not None and vol60 is not None else 1.0
                replay.lm.set_volatility_multiplier(code, multiplier)
                replay.counts["volatility_fallback"] += int(vol20 is None or vol60 is None)
            for code, bar in sorted(bars.items(), key=lambda item: (-replay.stocks.get(item[0]).priority if item[0] in replay.stocks else 0, item[0])):
                if code not in quarantined and bar.volume > 0:
                    replay.signal(code, now, int(bar.close), now + timedelta(hours=1))
            replay.mark(day)
            for code, bar in bars.items():
                replay.strategy.observe_completed_session(code, day, int(bar.close))
            previous_bars.update(bars)
    result = replay.result("conditional_daily_next_open", dataset.manifest.get("limitations", []) + [
        "Completed daily close signals and next-session open only; production intraday polling and turnover absent.",
        "Raw daily returns substitute for point-in-time adjusted returns; corporate actions and dividends unresolved.",
        "Daily high/low sequence never inferred; limit fills only at next observed opening price, subject to slip/limit/envelope.",
        "All-cash counterfactual initial state; historical actual LOT ages/cash/external trades not reconstructed.",
        "Current configured universe/stock flags and current tick ladder projected retrospectively; survivorship/historical tick bias.",
        "Next-open daily order lifetime is a separate execution sensitivity; does not reproduce live 30-second cancellation.",
        "No fabricated liquidation for missing bars; unresolved raw gaps quarantined prospectively.",
        "Transaction taxes and buy/sell commissions are fixed scenario assumptions, not reconstructed broker bills.",
    ])
    result.diagnostics["quarantine_events"] = events
    result.diagnostics["data_manifest"] = dataset.manifest
    return result


def _as_datetime(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def run_quote_backtest(config: BotConfig, records: Iterable[dict] | Any,
                       parameters: Parameters = BASELINE, options: dict | None = None,
                       volatility: dict | None = None, names: dict | None = None):
    """Replay recorded raw quotes, sharing cash and timestamped reservations.

    Records use the frozen audit-cache columns: ``timestamp`` is the last
    genuinely observed quote before ``exposure_timestamp``; the strictly later
    next_after_exposure pair is execution-only. Neither the pair's price nor
    eventual fill outcome participates in signal generation. The portfolio
    processes all scheduled fills chronologically between signal timestamps.

    ``volatility[session][code]`` must be (vol20, vol60), computed exclusively
    from completed closes BEFORE that session (or a dict with those fields).
    Missing estimates use production's multiplier-one fallback and are counted.
    Optional ``completed_closes[session][code]`` updates independent deep-loss
    recovery only when that completed session becomes observable next day.
    """
    options = dict(options or {})
    capital = float(options.get("capital", 100_000_000))
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("capital must be positive and finite")
    replay = _PortfolioReplay(config, parameters, capital, options, names)
    records = records() if callable(records) else records
    excluded = set(options.get("exclude_codes", ()))
    max_delay = float(options.get("execution_timeout_seconds", config.order.limit_order_timeout_seconds))
    max_age = options.get("max_signal_age_seconds")
    if max_delay < 0:
        raise ValueError("execution_timeout_seconds cannot be negative")
    start, end = options.get("start"), options.get("end")
    volatility = volatility or {}
    heap, serial = [], 0
    last_time, current_day = None, None
    stale_age_sum, stale_age_max, signal_rows = 0.0, 0.0, 0
    session_op = datetime.strptime(config.market_hours.open_time, "%H:%M").time()
    session_cl = datetime.strptime(config.market_hours.close_time, "%H:%M").time()

    def process_due(now, clock):
        while heap and heap[0][0] <= now:
            execute_at, _, code, price, signal_at = heapq.heappop(heap)
            clock.current = execute_at
            replay.expire(execute_at)
            # The raw observation belongs to the market, independently of
            # whether this candidate submitted or filled any order.
            replay.update_price(code, price)
            pending = replay.pending.get(code)
            if pending is not None and pending.signal_time == signal_at:
                replay.fill(code, execute_at, price)
        replay.expire(now)

    def finish_day(day, clock):
        prior_end = datetime.fromisoformat(day).replace(hour=23, minute=59, second=59)
        process_due(prior_end, clock)
        closes = options.get("completed_closes", {}).get(day, {})
        clock.current = datetime.fromisoformat(day).replace(hour=15, minute=30)
        if options.get("mark_daily_closes", True):
            for c, close in closes.items():
                if float(close) > 0 and math.isfinite(float(close)):
                    replay.update_price(c, float(close))
        replay.mark(day)
        for c, close in closes.items():
            replay.strategy.observe_completed_session(c, day, int(close))

    with historical_clock(datetime(2026, 1, 1)) as clock:
        for row in records:
            exposure = _as_datetime(row.get("exposure_timestamp") or row["timestamp"])
            day = exposure.date().isoformat()
            if start and day < start or end and day > end:
                continue
            if last_time is not None and exposure < last_time:
                raise ValueError("Quote records must be sorted chronologically by exposure_timestamp")
            if current_day is not None and day != current_day:
                # All admissible pairs share their originating regular session.
                # Execute the prior day's remaining within-timeout observations
                # before computing its closing mark, never against today's data.
                finish_day(current_day, clock)
            if day != current_day:
                current_day = day
                replay.daily_minimum_available_cash = max(0, replay.cash - replay.reservation())
                replay.daily_max_lots = sum(l.remaining_quantity > 0 for l in replay.lm.lots.values())
                replay.initial_order_count = replay.initial_order_amount = 0
                for position in replay.pm.positions.values():
                    position.daily_buy_amount = position.daily_sell_amount = 0
                for code, estimate in volatility.get(day, {}).items():
                    if isinstance(estimate, dict):
                        vol20, vol60 = estimate.get("vol20"), estimate.get("vol60")
                    else:
                        vol20, vol60 = estimate
                    if vol20 is not None and vol60 is not None:
                        replay.lm.set_volatility_multiplier(code, parameters.volatility_multiplier(float(vol20), float(vol60)))
                    else:
                        replay.lm.set_volatility_multiplier(code, 1.0)
                # Unavailable data must not silently inherit yesterday's value;
                # production DailyVolatilityCache refresh falls back to one.
                known = set(volatility.get(day, {}))
                for c in list(replay.lm.volatility_multipliers):
                    if c not in known:
                        replay.lm.set_volatility_multiplier(c, 1.0)
            process_due(exposure, clock)
            clock.current = exposure
            last_time = exposure
            code = str(row["code"]).zfill(6)
            if code in excluded or row.get("market_session", "REGULAR") != "REGULAR":
                replay.counts["excluded_or_nonregular"] += 1
                continue
            observed = _as_datetime(row["timestamp"])
            if observed > exposure or observed.date() != exposure.date():
                raise ValueError("Signal quote is future-dated or from another session")
            age = (exposure - observed).total_seconds()
            stale_age_sum += age
            stale_age_max = max(stale_age_max, age)
            signal_rows += 1
            price = int(float(row["price"]))
            if price <= 0:
                replay.counts["invalid_quote"] += 1
                continue
            replay.update_price(code, price)
            next_timestamp = row.get("next_after_exposure_timestamp")
            next_price = row.get("next_after_exposure_price")
            market_pair = False
            execute_at = exposure + timedelta(seconds=max_delay)
            if next_timestamp and next_price:
                execute_at = _as_datetime(next_timestamp)
                delta = (execute_at - exposure).total_seconds()
                market_pair = (delta > 0 and execute_at.date() == exposure.date()
                               and session_op <= execute_at.time() <= session_cl
                               and math.isfinite(float(next_price)) and float(next_price) > 0)
                if market_pair:
                    serial += 1
                    heapq.heappush(heap, (execute_at, serial, code, int(float(next_price)), exposure))
                    replay.counts["scheduled_market_quote_observations"] += 1
            valid_pair = market_pair and delta <= max_delay
            # The age cutoff blocks trading, while this causally available
            # quote still updates valuation (it is newer than yesterday).
            if max_age is not None and age > float(max_age):
                replay.counts["stale_signal_quote"] += 1
                continue
            # Current configured regular-session block windows are preserved.
            lower = datetime.combine(exposure.date(), session_op) + timedelta(minutes=config.market_hours.block_after_open_minutes)
            upper = datetime.combine(exposure.date(), session_cl) - timedelta(minutes=config.market_hours.block_before_close_minutes)
            if not lower <= exposure <= upper:
                replay.counts["outside_trade_window"] += 1
                continue
            estimate = volatility.get(day, {}).get(code)
            values = ((estimate.get("vol20"), estimate.get("vol60")) if isinstance(estimate, dict)
                      else estimate if estimate else (None, None))
            vol_valid = all(v is not None and math.isfinite(float(v)) and float(v) >= 0 for v in values)
            if not vol_valid:
                replay.counts["volatility_fallback"] += 1
                replay.lm.set_volatility_multiplier(code, 1.0)
                if options.get("require_volatility", False):
                    replay.counts["missing_volatility_signal_block"] += 1
                    continue
            # Missing/late execution observations do not suppress a signal:
            # its cash reservation still exists until cancellation timeout.
            replay.signal(code, exposure, price, execute_at,
                int(float(next_price)) if valid_pair else None,
                expires_at=exposure + timedelta(seconds=max_delay))
            pending = replay.pending.get(code)
            if pending is not None and pending.signal_time == exposure and not valid_pair:
                replay.counts["no_execution_observation_within_timeout"] += 1
        if current_day is None:
            raise ValueError("No selected quote observations")
        finish_day(current_day, clock)
    limitations = [
        "Conditional raw-quote counterfactual, not actual account performance or a long-term validated optimum.",
        "Current selected universe and configuration flags projected into the past; first quote availability is respected, historical selection/delisted membership unknown.",
        "Signal uses the last observed quote revealed at the strictly later cache bucket endpoint; sparse/stale quotes and missed intrabucket opportunities remain.",
        "Only the first actually recorded same-session quote after signal may fill, within the configured timeout; no favorable daily high/low ordering is assumed.",
        "Recorded last-trade prices do not establish bid/ask liquidity, queue priority, tradable depth, halt status or actual executions; full fill is a stated scenario assumption.",
        "Production's five fresh quote samples at two-second spacing cannot be reconstructed from bucket caches; price-sample stability and broker submission/reconciliation errors are unverified.",
        "Production strategy/request grid is retained for exact baseline policy; execution slippage uses separate 2026 KRX common-share ticks by default. Earlier historical grid changes are not reconstructed.",
        "Slippage, fees and constant transaction-tax rates are scenario inputs; actual brokerage bills, dividends and corporate entitlements are not available.",
        "All-cash initial state is an explicit capital scenario; actual historical LOT ages, tax lots, external cash flows and broker daily risk fields are not reconstructed.",
        "Holdings use causally observed quotes intraday; supplied completed raw daily closes optionally replace end-of-day marks. Missing closes retain stale last quotes and cannot establish actual account NAV.",
        "Partial fills are supported by production LOT application but recorded quote data lacks trade volume/depth; no inferred volume capacity is fabricated.",
        "Review states persist according to production PositionManager; no simulated manual review clearance is introduced.",
    ]
    result = replay.result("conditional_recorded_quote_next_observation", limitations)
    result.diagnostics.update(signal_rows=signal_rows, maximum_signal_quote_age_seconds=stale_age_max,
        mean_signal_quote_age_seconds=stale_age_sum / signal_rows if signal_rows else None,
        excluded_codes=sorted(excluded), volatility_source="Externally supplied completed-prior-session 20/60 estimates; missing production fallback one",
        reentry_eligible_orders=replay.reentry_signals)
    return result
