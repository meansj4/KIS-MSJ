"""Offline daily research replay; this module never imports a broker client.

The legacy baseline calls the production decision/book/risk classes. Its execution
context is deliberately different: completed daily close signals, next-session
opening-auction approximation, historical volume, and explicit estimated costs.
It is a current-policy counterfactual, not a reconstruction of an actual account.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
import statistics
import threading
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config as config_module
from . import lot_manager as lot_module
from . import position_manager as position_module
from . import strategy as strategy_module
from .config import BotConfig, StockConfig, load_config
from .lot_manager import LotManager, ceil_price, round_price
from .models import AccountSnapshot, OrderSide, TradeFill
from .position_manager import PositionManager
from .risk_manager import RiskManager
from .strategy import LotGridStrategy


ROOT = Path(__file__).resolve().parents[2]
_CLOCK_LOCK = threading.RLock()


@dataclass(frozen=True)
class DailyBar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    completed: bool = True


@dataclass
class DailyDataset:
    bars: dict[str, dict[str, DailyBar]]
    index: dict[str, DailyBar]
    config: BotConfig
    manifest: dict[str, Any] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    sectors: dict[str, str] = field(default_factory=dict)
    # Cache market indicators between candidate runs; no trade outcomes cached.
    feature_cache: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def dates(self) -> list[str]:
        return sorted(set(self.bars) & set(self.index))

    @property
    def codes(self) -> list[str]:
        return sorted({code for day in self.bars.values() for code in day})


@dataclass
class BacktestResult:
    summary: dict[str, Any]
    equity: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    annual: list[dict[str, Any]]
    monthly: list[dict[str, Any]]
    diagnostics: dict[str, Any]


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _valid_bar(bar: DailyBar) -> bool:
    values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
    return (all(math.isfinite(v) for v in values) and min(values[:4]) > 0
            and bar.volume >= 0 and bar.low <= min(bar.open, bar.close)
            and bar.high >= max(bar.open, bar.close) and bar.high >= bar.low)


def _floor_tick(price: float) -> int:
    unit = (1 if price < 1000 else 5 if price < 5000 else 10 if price < 10000 else
            50 if price < 50000 else 100 if price < 100000 else 500 if price < 500000 else 1000)
    return max(unit, int(price // unit) * unit)


def load_daily_dataset(
    db_path: str | Path = ROOT / 'data/backtest_historical.sqlite3',
    index_path: str | Path = ROOT / 'data/kospi_index_daily_2010_present.csv',
    config_path: str | Path = ROOT / 'config/lot_auto_trader.json',
    *, start: str | None = None, end: str | None = None,
) -> DailyDataset:
    """Load only genuine daily market tables using a read-only SQL connection.

    ``start``/``end`` filter the returned dataset, not an individual simulation.
    Load the complete history when warmup before a simulation start is needed.
    All acquired stock codes are retained, irrespective of today's enable flags.
    """
    db_path, index_path, config_path = map(Path, (db_path, index_path, config_path))
    cfg = load_config(config_path)
    bars: dict[str, dict[str, DailyBar]] = defaultdict(dict)
    names, rejected, source_counts = {}, [], Counter()
    con = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        con.execute('PRAGMA query_only=ON')
        rows = con.execute("SELECT code,date,name,open,high,low,close,volume FROM historical_daily_prices "
                           "WHERE data_source='kis_historical_daily_readonly' ORDER BY date,code")
        for code, day, name, op, high, low, close, volume in rows:
            if (start and day < start) or (end and day > end):
                continue
            if len(code) != 6 or not code.isdigit():
                continue
            bar = DailyBar(datetime.fromisoformat(day).replace(hour=15, minute=30),
                           float(op), float(high), float(low), float(close), float(volume))
            if not _valid_bar(bar):
                rejected.append({'code': code, 'date': day, 'reason': 'invalid_ohlcv'})
                continue
            if code in bars[day]:
                raise ValueError(f'Duplicate selected market source: {code} {day}')
            bars[day][code] = bar
            names[code] = name
            source_counts['kis_historical_daily_readonly'] += 1
    finally:
        con.close()
    index = {}
    with index_path.open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            day = row['date']
            if (start and day < start) or (end and day > end):
                continue
            bar = DailyBar(datetime.fromisoformat(day).replace(hour=15, minute=30),
                           *(float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume')))
            if not _valid_bar(bar):
                rejected.append({'code': 'KOSPI_INDEX', 'date': day, 'reason': 'invalid_ohlcv'})
                continue
            if day in index:
                raise ValueError(f'Duplicate index date: {day}')
            index[day] = bar
    if not bars or not index:
        raise ValueError('No usable stock/index history')
    sectors = {s.code: s.sector or 'UNKNOWN' for s in cfg.stocks}
    names.update({s.code: s.name for s in cfg.stocks if s.name})
    dates = sorted(set(bars) & set(index))
    manifest = {
        'database': db_path.name, 'database_sha256': _sha(db_path),
        'index_file': index_path.name, 'index_sha256': _sha(index_path),
        'configuration_file': config_path.name, 'configuration_sha256': _sha(config_path),
        'source_counts': dict(source_counts), 'stock_rows': sum(map(len, bars.values())),
        'stock_codes': sorted({c for day in bars.values() for c in day}),
        'index_rows': len(index), 'first_common_session': dates[0] if dates else None,
        'last_common_session': dates[-1] if dates else None,
        'rejected_rows': rejected, 'timezone': 'Asia/Seoul',
        'universe': 'All acquired historical cache codes; current enabled flags do not select the sample.',
        'limitations': [
            'Acquired universe was selected in 2026; historical membership/delisted coverage unavailable.',
            'Prices are supplied raw with adjusted=0; dividends and corporate entitlements are absent.',
            'Current numeric policy is uniformly projected into the past; this is not actual historical policy.',
            'Official opening auction/order book and historical tick-rule changes are not reconstructed.',
        ],
    }
    return DailyDataset(dict(bars), index, cfg, manifest, names, sectors)


@contextmanager
def historical_clock(initial: datetime):
    """Temporarily inject one deterministic clock; restore every module on error.

    Process-global legacy datetime names mean replays within one process are
    serialized. Run separate processes for concurrent research.
    """
    with _CLOCK_LOCK:
        class Clock(datetime):
            current = initial

            @classmethod
            def now(cls, tz=None):
                return cls.current.replace(tzinfo=tz) if tz else cls.current

        modules = (config_module, lot_module, position_module, strategy_module)
        old = [(module, module.datetime) for module in modules]
        try:
            for module in modules:
                module.datetime = Clock
            yield Clock
        finally:
            for module, prior in old:
                module.datetime = prior


@dataclass
class _Order:
    code: str
    side: str
    quantity: int
    limit: int
    reason: str
    signal_date: str
    lots: tuple[tuple[str, int], ...] = ()
    cycle_budget: float = 0
    market_regime: str = ''
    symbol_regime: str = ''
    trend_fraction: float = 0
    sell_reason: str = 'UNKNOWN'
    reentry_type: str = 'NONE'


class _AllocatedLegacyStrategy(LotGridStrategy):
    """B-stage adapter: the new allocator replaces only the old money cap.

    The production helper would otherwise turn ordinary budget exhaustion into
    a permanent review flag. All other legacy eligibility and genuine review
    conditions remain intact. Model A never uses this adapter.
    """

    def lot_sizing_buy_block_reason(self, position, current_price, sizing, *, next_buy_amount, open_lot_count):
        reason = super().lot_sizing_buy_block_reason(position, current_price, sizing,
            next_buy_amount=next_buy_amount, open_lot_count=open_lot_count)
        return '' if reason == 'max_symbol_amount_reached' else reason


def _period_returns(equity: list[dict], initial: float, length: int) -> list[dict]:
    result, current, prior, last = [], None, initial, initial
    for row in equity:
        key = row['date'][:length]
        if current is not None and key != current:
            result.append({'period': current, 'return_pct': (last / prior - 1) * 100,
                           'start_equity': prior, 'end_equity': last})
            prior = last
        current, last = key, row['equity']
    if current is not None:
        result.append({'period': current, 'return_pct': (last / prior - 1) * 100,
                       'start_equity': prior, 'end_equity': last})
    return result


def _metrics(rows: list[dict], capital: float) -> dict:
    if not rows:
        raise ValueError('No equity observations')
    changes, previous = [], capital
    underwater_start, longest_underwater = None, 0
    for row in rows:
        changes.append(row['equity'] / previous - 1 if previous > 0 else 0)
        previous = row['equity']
        day = datetime.fromisoformat(row['date'])
        if row['drawdown_pct'] < -1e-9:
            underwater_start = underwater_start or day
            longest_underwater = max(longest_underwater, (day - underwater_start).days + 1)
        else:
            underwater_start = None
    elapsed = max(1, (datetime.fromisoformat(rows[-1]['date']) - datetime.fromisoformat(rows[0]['date'])).days + 1)
    vol = statistics.stdev(changes) * math.sqrt(252) if len(changes) > 1 else 0
    final = rows[-1]['equity']
    return {
        'initial_equity': capital, 'final_equity': final, 'net_pnl': final - capital,
        'total_return_pct': (final / capital - 1) * 100,
        'cagr_pct': ((final / capital) ** (365.2425 / elapsed) - 1) * 100 if final > 0 else -100,
        'max_drawdown_pct': min(r['drawdown_pct'] for r in rows),
        'annualized_volatility_pct': vol * 100,
        'sharpe_zero_risk_free': statistics.mean(changes) * 252 / vol if vol else 0,
        'max_underwater_calendar_days': longest_underwater,
        'average_cash_ratio_pct': statistics.mean(r['cash_ratio_pct'] for r in rows),
        'minimum_cash_ratio_pct': min(r['cash_ratio_pct'] for r in rows),
        'minimum_cash': min(r['cash'] for r in rows),
        'maximum_invested_cost': max(r['invested_cost'] for r in rows),
        'realized_pnl': rows[-1]['realized_pnl'], 'unrealized_pnl': rows[-1]['unrealized_pnl'],
        'trading_sessions': len(rows), 'first_date': rows[0]['date'], 'last_date': rows[-1]['date'],
    }


def run_backtest(dataset: DailyDataset, model: str, parameters: dict | None = None) -> BacktestResult:
    """Run one independent, all-cash, no-broker experiment.

    Parameters: start/end (inclusive), capital, arcg (ARCGConfig overrides),
    slippage_bps, buy_fee_bps, sell_fee_tax_bps, volume_participation,
    max_mark_stale_sessions, gap_quarantine_threshold, collect_trades, and
    project_current_policy (defaults true; clears only activation dates).
    Every pending signal expires after the very next common market session.
    """
    from . import arcg

    p = dict(parameters or {})
    if model not in 'ABCDEF' or len(model) != 1:
        raise ValueError('model must be one of A through F')
    capital = float(p.get('capital', 100_000_000))
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError('capital must be positive and finite')
    cfg = dataset.config
    if p.get('project_current_policy', True):
        cfg = replace(cfg, strategy=replace(cfg.strategy, cash_target_start_date='', reentry_start_date=''))
    core_cfg = replace(arcg.ARCGConfig(), **p.get('arcg', {}))
    slip = float(p.get('slippage_bps', 5)) / 10000
    buy_fee_rate = float(p.get('buy_fee_bps', 1.5)) / 10000
    sell_fee_rate = float(p.get('sell_fee_tax_bps', cfg.strategy.estimated_fee_tax_pct * 100)) / 10000
    participation = float(p.get('volume_participation', .001))
    stale_limit = int(p.get('max_mark_stale_sessions', 5))
    gap_limit = float(p.get('gap_quarantine_threshold', .35))
    if min(slip, buy_fee_rate, sell_fee_rate) < 0 or not 0 < participation <= 1 or stale_limit < 0:
        raise ValueError('Invalid execution costs, participation, or stale limit')
    core_cfg = replace(core_cfg, buy_fee_rate=buy_fee_rate, sell_fee_rate=0.,
                       sell_tax_rate=sell_fee_rate, slippage_rate=slip)
    all_days = dataset.dates
    dates = [d for d in all_days if (not p.get('start') or d >= p['start']) and (not p.get('end') or d <= p['end'])]
    if not dates:
        raise ValueError('Requested interval contains no common stock/index sessions')
    codes = dataset.codes
    histories = dataset.feature_cache.get('_histories')
    if histories is None:
        histories = defaultdict(list)
        for d in all_days:
            for c, b in dataset.bars.get(d, {}).items():
                histories[c].append(b)
        dataset.feature_cache['_histories'] = histories
        dataset.feature_cache['_history_indices'] = {
            c: {bar.time.date().isoformat(): i for i, bar in enumerate(bs)} for c, bs in histories.items()}
        dataset.feature_cache['_index_history'] = [dataset.index[d] for d in all_days]
    history_indices = dataset.feature_cache['_history_indices']
    index_history = dataset.feature_cache['_index_history']
    day_index = {d: i for i, d in enumerate(all_days)}
    # Only indicator settings affect Features; gap, budget, anchor and sell
    # parameters may share exactly the same precomputed observations.
    indicator_key = getattr(arcg, 'feature_cache_key', lambda c: repr(c))(core_cfg)
    feature_store = dataset.feature_cache.setdefault(('indicators', indicator_key), {})
    lookback = max(core_cfg.slow_period + core_cfg.slope_period, core_cfg.atr_period + 1, core_cfg.fast_period + 1)

    positions, managers, strategies, lot_managers = {}, {}, {}, {}
    for code in codes:
        lm = LotManager(cfg.strategy)
        pm = PositionManager(cfg.strategy, lm)
        pm.positions = positions
        pm.get(code, dataset.names.get(code, code))
        strategy_class = LotGridStrategy if model == 'A' else _AllocatedLegacyStrategy
        managers[code], lot_managers[code], strategies[code] = pm, lm, strategy_class(cfg, lm)
    risk = RiskManager(cfg)
    states = {c: arcg.SymbolState(c, sector=dataset.sectors.get(c, 'UNKNOWN')) for c in codes}
    active_codes: set[str] = set()
    cash, realized, fee_cost, slip_cost = capital, 0., 0., 0.
    buy_costs, symbol_max = {}, defaultdict(float)
    last_prices, last_seen = {}, {}
    quarantined, quarantine_events, stale_events = set(), [], []
    pending: list[_Order] = []
    trades, equity, completed_ages = [], [], []
    counts, blocks = Counter(), Counter()
    peak, prev_equity, max_demand, economic_valid = capital, capital, 0., True
    observed_reasons = Counter()
    regime_returns = defaultdict(lambda: {'sessions': 0, 'pnl': 0.})
    common_regime, common_candidate, common_confirmations = 'NEUTRAL', '', 0
    previous_bars = {}
    scan_nav = capital

    def sync_allocated_sizing(code: str) -> None:
        state = states[code]
        if model != 'A' and state.lots:
            position = positions[code]
            position.max_symbol_amount = int(state.cycle_budget)
            position.lot_unit_amount = max(1, int(state.cycle_budget * core_cfg.addition_fraction))
            position.max_lots_per_symbol = core_cfg.max_lots
    # Prior quotes are allowed as warmup/last known observations; no holdings.
    for d in all_days:
        if d >= dates[0]:
            break
        previous_bars.update(dataset.bars[d])

    def marked_value(day_number: int) -> tuple[float, float]:
        value = invested = 0.
        for code in active_codes:
            state = states[code]
            quantity = sum(l.quantity for l in state.lots)
            invested += sum(l.quantity * l.buy_price for l in state.lots)
            if quantity and day_number - last_seen.get(code, -100000) <= stale_limit:
                value += quantity * last_prices.get(code, 0.)
        return value, invested

    def portfolio(now: datetime, reservation: list[_Order] | None = None, current_code: str | None = None):
        orders = tuple(arcg.PendingOrder(str(i), o.code, o.side, o.quantity, o.limit,
                                        sector=dataset.sectors.get(o.code, 'UNKNOWN'))
                       for i, o in enumerate(reservation or ()) if o.side == 'BUY')
        reserved = sum(o.quantity * o.limit_price * (1 + core_cfg.buy_fee_rate + core_cfg.slippage_rate) for o in orders)
        members = active_codes | ({current_code} if current_code else set())
        active = {c: states[c] for c in members}
        values = {c: states[c].quantity * last_prices.get(c, 0.) for c in active_codes if states[c].lots}
        return arcg.Portfolio(scan_nav, max(0., cash - reserved), active, pending=orders,
                              observed_at=now, market_values=values)

    def legacy_order(code, action, day, price) -> _Order | None:
        if action is None:
            return None
        side = action.side.value
        limit = int(action.limit_price or round_price(price * (
            1 + cfg.order.buy_limit_markup_pct / 100 if side == 'BUY' else
            1 - cfg.order.sell_limit_markdown_pct / 100)))
        quantity = int(action.quantity or (action.amount // max(1, limit)))
        if quantity <= 0:
            return None
        return _Order(code, side, quantity, limit, action.reason, day,
                      ((action.lot_id, quantity),) if side == 'SELL' else (),
                      sell_reason=action.sell_reason, reentry_type=action.reentry_type)

    def core_order(decision, state, day):
        return _Order(decision.symbol, decision.side, decision.quantity, int(decision.limit_price),
                      decision.reason, day, tuple(decision.lot_quantities), decision.cycle_budget,
                      state.market_regime, state.symbol_regime,
                      getattr(decision, 'trend_fraction', 0) if model in 'EF' else 0)

    with historical_clock(datetime.fromisoformat(dates[0])) as clock:
        for day in dates:
            number = day_index[day]
            bars = dataset.bars[day]
            open_time = datetime.fromisoformat(day).replace(hour=9)
            close_time = datetime.fromisoformat(day).replace(hour=15, minute=30)
            clock.current = open_time
            # An opening gap is observed before processing that open's orders.
            # It never excludes the same code before the actual event date.
            for code, bar in bars.items():
                prior = previous_bars.get(code)
                if prior and abs(bar.open / prior.close - 1) > gap_limit and code not in quarantined:
                    exposure = sum(l.quantity * l.buy_price for l in states[code].lots)
                    quarantine_events.append({'date': day, 'code': code, 'previous_close': prior.close,
                        'open': bar.open, 'gap_pct': (bar.open / prior.close - 1) * 100,
                        'held_cost': exposure, 'action': 'causal_quarantine_no_future_fills'})
                    quarantined.add(code)
                    economic_valid = economic_valid and exposure == 0
            # Actual modeled sell fills precede buys. Unfilled sell orders never
            # contribute buying power. Closing-signal reservations constrain BUY.
            for order in sorted(pending, key=lambda o: (o.side == 'BUY', o.code)):
                bar, prior = bars.get(order.code), previous_bars.get(order.code)
                if order.code in quarantined or bar is None or bar.volume <= 0 or prior is None:
                    counts['expired_missing_halted_quarantine'] += 1
                    continue
                if order.side == 'BUY' and bar.open > order.limit or order.side == 'SELL' and bar.open < order.limit:
                    counts['expired_limit_not_marketable'] += 1
                    continue
                # Capacity uses the PREVIOUS observed session's volume. The
                # current day's zero-volume flag only vetoes impossible fills.
                quantity = min(order.quantity, int(prior.volume * participation))
                raw_execution = bar.open * (1 + slip if order.side == 'BUY' else 1 - slip)
                price = ceil_price(raw_execution) if order.side == 'BUY' else _floor_tick(raw_execution)
                if order.side == 'BUY' and price > order.limit or order.side == 'SELL' and price < order.limit:
                    counts['expired_slippage_limit'] += 1
                    continue
                if price < bar.low or price > bar.high:
                    counts['expired_observed_price_envelope'] += 1
                    continue
                if order.side == 'BUY':
                    quantity = min(quantity, int(cash / (price * (1 + buy_fee_rate))))
                else:
                    quantity = min(quantity, sum(l.quantity for l in states[order.code].lots))
                if quantity <= 0:
                    counts['zero_fill_capacity_or_cash'] += 1
                    continue
                if quantity < order.quantity:
                    counts['partial_fills'] += 1
                counts[order.side.lower() + '_fills'] += 1
                execution_id = f'{model}-{day}-{order.code}-{counts["buy_fills"] + counts["sell_fills"]}'
                fee = price * quantity * (buy_fee_rate if order.side == 'BUY' else sell_fee_rate)
                cash_before = cash
                fee_cost += fee
                slip_cost += abs(price - bar.open) * quantity
                state = states[order.code]
                state = replace(state, cycle_budget=order.cycle_budget or state.cycle_budget)
                if model == 'A' and order.side == 'BUY':
                    # The baseline uses its exact production sizing cap, without
                    # imposing the new allocator's budget restriction.
                    legacy_cap = positions[order.code].max_symbol_amount or cfg.strategy.absolute_max_investment
                    state = replace(state, cycle_budget=max(legacy_cap, state.invested + price * quantity + fee))
                allocations, remaining = [], quantity
                if order.side == 'SELL':
                    for lot_id, requested in order.lots:
                        lot = next((l for l in state.lots if l.lot_id == lot_id), None)
                        take = min(remaining, requested, lot.quantity if lot else 0)
                        if take:
                            allocations.append((lot_id, take))
                            remaining -= take
                    if remaining:
                        raise RuntimeError('Sell allocation exceeds selected available LOTs')
                fill = arcg.Fill(execution_id, execution_id, order.code, order.side, quantity,
                                 price, open_time, fee, tuple(allocations), order.trend_fraction)
                updated = arcg.apply_fill(state, fill)
                trade_realized = 0.
                if order.side == 'BUY':
                    cash -= price * quantity + fee
                    new_lots = [l for l in updated.lots if l.lot_id not in {x.lot_id for x in state.lots}]
                    allocations = [(l.lot_id, l.quantity) for l in new_lots]
                    for lot in new_lots:
                        buy_costs[lot.lot_id] = fee * lot.quantity / quantity
                        managers[order.code].apply_fill(TradeFill(order.code, dataset.names.get(order.code, ''),
                            OrderSide.BUY, lot.quantity, price, execution_id, open_time, lot.lot_id,
                            execution_id=execution_id, reentry_type=order.reentry_type))
                else:
                    cash += price * quantity - fee
                    for lot_id, take in allocations:
                        before = next(l for l in state.lots if l.lot_id == lot_id)
                        allocated_buy_fee = buy_costs.get(lot_id, 0.) * take / before.quantity
                        buy_costs[lot_id] = buy_costs.get(lot_id, 0.) - allocated_buy_fee
                        net = (price - before.buy_price) * take - fee * take / quantity - allocated_buy_fee
                        trade_realized += net
                        managers[order.code].apply_fill(TradeFill(order.code, dataset.names.get(order.code, ''),
                            OrderSide.SELL, take, price, execution_id, open_time, lot_id,
                            execution_id=execution_id, sell_reason=order.sell_reason))
                        if take == before.quantity:
                            completed_ages.append((open_time - before.opened_at).total_seconds() / 86400)
                    realized += trade_realized
                states[order.code] = updated
                sync_allocated_sizing(order.code)
                if updated.lots:
                    active_codes.add(order.code)
                else:
                    active_codes.discard(order.code)
                if cash < -1e-6:
                    raise RuntimeError('Negative research cash')
                if p.get('collect_trades', True):
                    trades.append({'datetime': open_time.isoformat(), 'signal_date': order.signal_date,
                        'model': model, 'code': order.code, 'name': dataset.names.get(order.code, ''),
                        'side': order.side, 'reason': order.reason, 'quantity': quantity, 'price': price,
                        'amount': quantity * price, 'lot_ids': '|'.join(i for i, _ in allocations),
                        'cycle_id': updated.cycle_id, 'fees_tax': fee, 'slippage': abs(price - bar.open) * quantity,
                        'realized_pnl': trade_realized, 'cash_before': cash_before, 'cash_after': cash,
                        'market_regime': order.market_regime, 'symbol_regime': order.symbol_regime})
            pending = []
            for code, state in states.items():
                # An expired unfilled entry owns no future allocation. Historical
                # cycle low is still retained until the next actual BUY fill.
                if not state.lots and state.cycle_budget:
                    states[code] = replace(state, cycle_budget=0.)
                    active_codes.discard(code)
            clock.current = close_time
            for code, bar in bars.items():
                last_prices[code] = bar.close
                if bar.volume > 0:
                    last_seen[code] = number
            for code, state in states.items():
                if state.lots and number - last_seen.get(code, -100000) > stale_limit:
                    if not any(e['code'] == code for e in stale_events):
                        stale_events.append({'date': day, 'code': code, 'cost': sum(l.quantity * l.buy_price for l in state.lots),
                                             'action': 'stale_mark_haircut_to_zero_no_sale'})
                    economic_valid = False
                mark = last_prices.get(code, 0.) if number - last_seen.get(code, -100000) <= stale_limit else 0.
                position = managers[code].refresh_from_lots(code, int(mark))
                sync_allocated_sizing(code)
                position.daily_buy_amount = position.daily_sell_amount = 0
                symbol_max[code] = max(symbol_max[code], position.cumulative_invested_amount)
            stock_value, invested = marked_value(number)
            nav = cash + stock_value
            scan_nav = nav
            # KIS total_profit_loss represents current holdings valuation P&L,
            # not lifetime realized gains plus valuation P&L. Daily P&L lacks a
            # historical broker field and remains an explicit NAV approximation.
            snapshot = AccountSnapshot(int(cash), int(nav), int(stock_value - invested), int(nav - prev_equity))
            account_risk = risk.account_buy_allowed(snapshot, positions)
            market_key = ('INDEX', day)
            market_window = index_history[max(0, number - lookback + 1):number + 1]
            if market_key not in feature_store:
                feature_store[market_key] = arcg.compute_features(market_window, core_cfg)
            market_feature = feature_store[market_key]
            if market_feature.ready:
                common_regime, common_candidate, common_confirmations = arcg.update_regime(
                    common_regime, common_candidate, common_confirmations,
                    market_feature.raw_market_regime, core_cfg.confirmation_bars)
            demand = 0.
            # Candidate order is deterministic by code for A/B. Regime-enabled
            # variants rank observable entry strength, never future performance.
            signals = []
            for code, bar in sorted(bars.items()):
                if code in quarantined or bar.volume <= 0:
                    continue
                state = states[code]
                if model == 'D' and state.lots:
                    # D retains exact legacy SELL calculations. Its adaptive
                    # BUY must not invoke an unrelated legacy BUY branch and
                    # inherit that branch's review/sizing side effects.
                    legacy = strategies[code]._sell_action(positions[code], int(bar.close), snapshot)
                elif model in 'ABC' or (model in 'DE' and not state.lots):
                    legacy = strategies[code].decide(positions[code], int(bar.close), snapshot,
                        account_risk, risk.symbol_buy_allowed(positions[code]))
                else:
                    legacy = None
                intent = legacy_order(code, legacy, day, bar.close)
                feature = evaluation = None
                if model in 'CDEF':
                    idx = history_indices[code][day]
                    symbol_window = histories[code][max(0, idx - lookback + 1):idx + 1]
                    key = (code, day)
                    if key not in feature_store:
                        feature_store[key] = arcg.compute_features(symbol_window, core_cfg)
                    feature = feature_store[key]
                    # Full trailing windows were validated during feature
                    # construction. The pure evaluator needs the matched most
                    # recent completed bar plus those cached indicators.
                    evaluation = arcg.evaluate(code, symbol_window[-1:],
                        market_window[-1:], portfolio(close_time, current_code=code), state,
                        close_time, core_cfg, model=model, features=feature, market_features=market_feature)
                    # Fill-owned cycle fields remain authoritative; evaluation
                    # owns only causal observation, regime, and trailing state.
                    states[code] = evaluation.state
                    state = evaluation.state
                if model == 'C' and intent and intent.side == 'BUY':
                    is_entry = not state.lots
                    if state.market_regime == 'RISK_OFF' or state.symbol_regime == 'DOWNTREND' or (is_entry and not feature.entry_signal):
                        blocks['regime_or_entry'] += 1
                        intent = None
                elif model == 'D':
                    if intent is None or intent.side != 'SELL':
                        if state.lots:
                            intent = core_order(evaluation.decision, state, day) if evaluation.decision and evaluation.decision.side == 'BUY' else None
                        elif not state.last_exit_at:
                            intent = core_order(evaluation.decision, state, day) if evaluation.decision else None
                        elif intent and intent.side == 'BUY':
                            if state.market_regime == 'RISK_OFF' or state.symbol_regime == 'DOWNTREND' or not feature.entry_signal:
                                intent = None
                elif model == 'E':
                    if not state.lots and state.last_exit_at:
                        # E retains the legacy reentry decision, adding the same
                        # market/entry gates already present since C.
                        if not (intent and intent.side == 'BUY' and feature.entry_signal
                                and state.market_regime != 'RISK_OFF' and state.symbol_regime != 'DOWNTREND'):
                            intent = None
                    else:
                        intent = core_order(evaluation.decision, state, day) if evaluation.decision else None
                elif model == 'F':
                    intent = core_order(evaluation.decision, state, day) if evaluation.decision else None
                if evaluation:
                    observed_reasons[evaluation.reason] += 1
                if intent:
                    intent.market_regime, intent.symbol_regime = common_regime, state.symbol_regime
                    signals.append((code, intent, feature))
                # Recovery state only sees a completed close. The current close
                # becomes available for the next signal evaluation, not earlier.
                strategies[code].observe_completed_session(code, day, int(bar.close))
            if model in 'CDEF':
                signals.sort(key=lambda item: (-float(getattr(item[2], 'slope', 0) if item[2] else 0), item[0]))
            for code, intent, feature in signals:
                if intent.side == 'BUY':
                    if model != 'A':
                        state = states[code]
                        budget = state.cycle_budget if state.lots else nav * core_cfg.symbol_cap_fraction
                        if core_cfg.max_symbol_amount:
                            budget = min(budget, core_cfg.max_symbol_amount)
                        tranche = core_cfg.addition_fraction if state.lots else core_cfg.initial_fraction
                        # B introduces allocation AND sizing. The exact same
                        # tranche formula is retained for all later ablations.
                        intent.quantity = int(budget * tranche / (intent.limit * (1 + buy_fee_rate)))
                    requested = intent.quantity * intent.limit * (1 + buy_fee_rate)
                    demand += requested
                    # Core buys retain production risk gates. Cash scarcity
                    # never authorizes an otherwise blocked live BUY policy.
                    symbol_risk = risk.symbol_buy_allowed(positions[code])
                    if not account_risk.allowed or not symbol_risk.allowed:
                        blocks['production_risk'] += 1
                        continue
                    if model != 'A':
                        allowance = arcg.capital_limit(code, states[code], portfolio(close_time, pending, code),
                            core_cfg, requested, not states[code].lots, now=close_time)
                        intent.quantity = min(intent.quantity, int(allowance.amount / (intent.limit * (1 + buy_fee_rate))))
                        intent.cycle_budget = allowance.cycle_budget
                        if intent.quantity <= 0:
                            blocks['capital_allocator:' + allowance.reason] += 1
                            continue
                    reservation = sum(o.quantity * o.limit * (1 + buy_fee_rate) for o in pending if o.side == 'BUY')
                    allowed = int(max(0., cash - reservation) / (intent.limit * (1 + buy_fee_rate)))
                    if allowed < intent.quantity:
                        blocks['cash_shortfall'] += 1
                    intent.quantity = min(intent.quantity, allowed)
                    if intent.quantity <= 0:
                        continue
                    if model != 'A' and not states[code].lots:
                        # Reserve the complete cycle before the next candidate,
                        # rather than merely reserving this first order's cash.
                        states[code] = replace(states[code], cycle_budget=intent.cycle_budget)
                        active_codes.add(code)
                pending.append(intent)
            max_demand = max(max_demand, demand)
            peak = max(peak, nav)
            unrealized = stock_value - invested - sum(buy_costs.values())
            if abs((nav - capital) - realized - unrealized) > .01:
                raise RuntimeError('NAV/realized/unrealized accounting invariant failed')
            market_regime = common_regime
            regime_returns[market_regime]['sessions'] += 1
            regime_returns[market_regime]['pnl'] += nav - prev_equity
            equity.append({'date': day, 'model': model, 'equity': nav, 'cash': cash,
                'stock_value': stock_value, 'invested_cost': invested, 'realized_pnl': realized,
                'unrealized_pnl': unrealized, 'drawdown_pct': (nav / peak - 1) * 100,
                'cash_ratio_pct': cash / nav * 100 if nav > 0 else 0,
                'open_lots': sum(len(s.lots) for s in states.values()),
                'market_regime': market_regime, 'quarantined_codes': len(quarantined)})
            prev_equity = nav
            previous_bars.update(bars)
    summary = _metrics(equity, capital)
    benchmark_start, benchmark_end = dataset.index[dates[0]].close, dataset.index[dates[-1]].close
    summary.update({'model': model, 'economic_valid': False,
        'conditional_price_replay_valid': economic_valid,
        'no_detected_held_gap_or_stale_event': economic_valid,
        'benchmark_price_return_pct': (benchmark_end / benchmark_start - 1) * 100,
        'excess_return_vs_price_benchmark_pp': summary['total_return_pct'] - (benchmark_end / benchmark_start - 1) * 100,
        'buy_fills': counts['buy_fills'], 'sell_fills': counts['sell_fills'],
        'total_trades': counts['buy_fills'] + counts['sell_fills'],
        'fees_tax': fee_cost, 'slippage_cost': slip_cost, 'total_execution_cost': fee_cost + slip_cost,
        'cash_shortfall_blocks': blocks['cash_shortfall'], 'all_buy_blocks': sum(blocks.values()),
        'allocation_cash_blocks': blocks['capital_allocator:cash_reserved_for_existing_cycles'] + observed_reasons['cash_reserved_for_existing_cycles'],
        'full_cycle_budget_blocks': blocks['capital_allocator:full_cycle_budget_unavailable'] + observed_reasons['full_cycle_budget_unavailable'],
        'blocked_by_regime': blocks['regime_or_entry'] + observed_reasons['regime_blocks_buy'],
        'blocked_by_stabilization': observed_reasons['addition_stabilization_pending'],
        'maximum_simultaneous_buy_demand': max_demand,
        'average_completed_lot_holding_days': statistics.mean(completed_ages) if completed_ages else None,
        'remaining_open_lot_count': sum(len(s.lots) for s in states.values()),
        'average_open_lot_age_days': statistics.mean(
            (datetime.fromisoformat(dates[-1]).replace(hour=15, minute=30) - l.opened_at).total_seconds() / 86400
            for s in states.values() for l in s.lots) if any(s.lots for s in states.values()) else None,
        'maximum_symbol_investment': max(symbol_max.values(), default=0),
        'quarantine_event_count': len(quarantine_events), 'stale_held_code_count': len(stale_events),
        'source_stock_count': len(codes), 'source_manifest_sha256': hashlib.sha256(json.dumps(dataset.manifest, sort_keys=True).encode()).hexdigest(),
        'execution_context': 'Completed close signals; next common session open only; production tick ladder projected historically.',
        'study_status': 'retrospective_raw_price_counterfactual_not_pristine_holdout',
    })
    diagnostics = {'parameters': p, 'effective_arcg_parameters': asdict(core_cfg),
        'counts': dict(counts), 'buy_blocks': dict(blocks), 'core_reasons': dict(observed_reasons),
        'quarantine_events': quarantine_events, 'stale_mark_events': stale_events,
        'symbol_max_investment': dict(symbol_max), 'market_regime_performance': dict(regime_returns),
        'data_manifest': dataset.manifest, 'remaining_orders_expire_at_end': len(pending),
        'baseline': 'Production LotGridStrategy, LotManager, PositionManager, RiskManager; no live order loop or broker calls.',
        'policy_projection': 'Only activation dates cleared' if p.get('project_current_policy', True) else 'Actual configured activation dates',
        'capital_adapter': ('None: exact production sizing and monetary cap' if model == 'A' else
            'B-stage replacement: held legacy sizing mirrors immutable allocated budget and common tranche; '
            'only legacy max_symbol_amount_reached is delegated to capital_limit, preventing false permanent review. '
            'Legacy flat reentry signal retains configured price-band funding eligibility before common order resizing.'),
        'limitations': dataset.manifest.get('limitations', []) + [
            'One decision per stock per daily close; production intraday order timing and actual account state are not replayed.',
            'Missing/zero-volume bars prohibit fills; stale holdings marked at zero after the declared grace period, without fabricated sales.',
            'Opening volume participation uses previous observed volume; auction queue and opening auction volume are unknown.',
            'Estimated historical transaction tax is constant; benchmark excludes dividends, fees and reinvestment.',
            'Account total P&L uses current holdings valuation P&L excluding fees, matching the KIS field meaning; '
            'daily account P&L is approximated by modeled daily NAV change, not a reconstructed broker daily field.',
        ]}
    return BacktestResult(summary, equity, trades, _period_returns(equity, capital, 4),
                          _period_returns(equity, capital, 7), diagnostics)
