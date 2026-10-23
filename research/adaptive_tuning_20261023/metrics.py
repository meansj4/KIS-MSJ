"""Pure accounting and risk metrics for the isolated Adaptive LOT study.

All monetary values are KRW. Cash includes reserved order cash; reservations
reduce available cash, never NAV. Slippage already lives in execution prices,
so its separately reported estimate must not be deducted from NAV again.
No trading clients, production state, or file writes are used here.
"""
from __future__ import annotations

import math
from datetime import datetime
from statistics import mean
from typing import Any, Iterable, Mapping


def _number(value: Any, name: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _time(row: Mapping[str, Any]) -> datetime:
    raw = row.get("timestamp", row.get("date", row.get("datetime")))
    if isinstance(raw, datetime):
        return raw
    if raw is None:
        raise ValueError("Equity observation needs a date or timestamp")
    return datetime.fromisoformat(str(raw))


def summarize_path(
    equity_rows: Iterable[Mapping[str, Any]], *, initial_equity: float,
    initial_cash: float | None = None, initial_unrealized: float = 0.0,
    initial_realized: float = 0.0, initial_date: str | datetime | None = None,
    trades: Iterable[Mapping[str, Any]] = (), diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute total NAV metrics, including the initial observation in MDD.

    The caller supplies an unmodified chronological path and the *marked* NAV
    at its left boundary. Seeded holdings' pre-period unrealized P&L is removed
    when reconciling period P&L. With no explicit initial_date, elapsed time is
    measured from the first equity date; sub-year CAGR is labelled descriptive.
    Reentry success means a submitted reentry order obtaining any fill, rather
    than a profitable subsequent cycle. Block counts are evaluation counts.
    """
    rows, fills, diag = list(equity_rows), list(trades), dict(diagnostics or {})
    if not rows:
        raise ValueError("No equity observations")
    capital = _number(initial_equity, "initial_equity")
    if capital <= 0:
        raise ValueError("initial_equity must be positive")
    times = [_time(row) for row in rows]
    if any(current < prior for prior, current in zip(times, times[1:])):
        raise ValueError("Equity observations must be chronological")
    beginning = (initial_date if isinstance(initial_date, datetime) else
                 datetime.fromisoformat(initial_date) if initial_date else times[0])
    if beginning > times[0]:
        raise ValueError("initial_date follows the first equity observation")
    initial_unrealized = _number(initial_unrealized, "initial_unrealized")
    initial_realized = _number(initial_realized, "initial_realized")
    peak, mdd, min_cash, min_available, max_reserved, max_lots = capital, 0., math.inf, math.inf, 0., 0
    cash_ratios, max_ledger_error = [], 0.
    for row in rows:
        nav = _number(row.get("equity", row.get("nav")), "equity")
        cash = _number(row["cash"], "cash")
        reserved = _number(row.get("reserved_cash", 0.), "reserved_cash")
        if reserved < -1e-6:
            raise ValueError("reserved_cash must not be negative")
        available = _number(row.get("available_cash", cash - reserved), "available_cash")
        if abs(available - (cash - reserved)) > .011:
            raise ValueError("available_cash must equal cash minus reserved_cash")
        if "stock_value" in row:
            ledger_error = abs(nav - cash - _number(row["stock_value"], "stock_value"))
            max_ledger_error = max(max_ledger_error, ledger_error)
            if ledger_error > .011:
                raise ValueError("NAV must equal cash plus marked holdings; reserved cash is not another asset")
        peak = max(peak, nav)
        mdd = min(mdd, (nav / peak - 1.) * 100.)
        min_cash, min_available = min(min_cash, cash), min(min_available, available)
        max_reserved = max(max_reserved, reserved)
        max_lots = max(max_lots, int(row.get("open_lots", 0)))
        if nav > 0:
            cash_ratios.append(cash / nav * 100.)
    if initial_cash is not None:
        first_cash = _number(initial_cash, "initial_cash")
        min_cash, min_available = min(min_cash, first_cash), min(min_available, first_cash)
    final = rows[-1]
    final_nav = _number(final.get("equity", final.get("nav")), "equity")
    net_pnl = final_nav - capital
    total_return = net_pnl / capital * 100.
    elapsed = (times[-1] - beginning).total_seconds() / 86400.
    cagr = None
    if elapsed > 0 and final_nav > 0:
        exponent = math.log(final_nav / capital) * 365.2425 / elapsed
        if exponent < 700:
            cagr = math.expm1(exponent) * 100.
    elif elapsed > 0 and final_nav == 0:
        cagr = -100.
    cumulative_realized = _number(final.get("realized_pnl", sum(float(t.get("realized_pnl", 0)) for t in fills)), "realized_pnl")
    realized = cumulative_realized - initial_realized
    unrealized_change = (None if "unrealized_pnl" not in final else
                         _number(final["unrealized_pnl"], "unrealized_pnl") - initial_unrealized)
    reconciliation_error = (None if unrealized_change is None else net_pnl - realized - unrealized_change)
    if reconciliation_error is not None and abs(reconciliation_error) > .011:
        raise ValueError("Period NAV P&L differs from realized plus change in unrealized P&L")
    fees = _number(diag.get("fees_tax", sum(_number(t.get("fees_tax", 0.), "fees_tax") for t in fills)), "fees_tax")
    slip = _number(diag.get("slippage_cost", sum(_number(t.get("slippage_cost", t.get("slippage", 0.)), "slippage_cost") for t in fills)), "slippage_cost")
    sells = [t for t in fills if str(t.get("side", "")).upper() == "SELL"]
    buys = [t for t in fills if str(t.get("side", "")).upper() == "BUY"]
    ages = [(float(t["holding_days"]), int(t.get("quantity", 1))) for t in sells if t.get("holding_days") is not None]
    sold_quantity = sum(quantity for _, quantity in ages)
    closed_ages = diag.get("closed_lot_holding_days", [])
    decay_pnl = sum(float(t.get("realized_pnl", 0.)) for t in sells if "DECAY" in str(t.get("sell_reason", "")).upper())
    blocks = diag.get("buy_blocks", {})
    counts = diag.get("counts", {})
    attempts = diag.get("reentry_attempts", counts.get("reentry_attempts", 0))
    if isinstance(attempts, Mapping):
        attempts = attempts.get("submitted", attempts.get("attempted", 0))
    reentry_fills = counts.get("reentry_fills", diag.get("reentry_fills", sum(
        str(t.get("reentry_type", "NONE")).upper() not in {"", "NONE", "INITIAL"} for t in buys)))
    cash_blocks = sum(int(value) for reason, value in blocks.items()
                      if any(token in reason.lower() for token in ("cash", "reserve")))
    intraday_min = _number(diag.get("minimum_available_cash_intraday", min_available), "minimum_available_cash_intraday")
    intraday_max_lots = int(diag.get("max_open_lots_intraday", max_lots))
    total_buys, total_sells = int(counts.get("buy_fills", len(buys))), int(counts.get("sell_fills", len(sells)))
    result = {
        "initial_equity": capital, "final_equity": final_nav, "net_pnl": net_pnl,
        "total_return_pct": total_return, "cagr_pct": cagr,
        "cagr_calendar_days": elapsed, "cagr_is_short_period_extrapolation": elapsed < 365.2425,
        "cagr_interpretation": "Descriptive annualization, not a forecast or long-term verified return",
        "max_drawdown_pct": mdd, "calmar_ratio": cagr / abs(mdd) if cagr is not None and mdd < 0 else None,
        "min_cash": min_cash, "minimum_available_cash": min(min_available, intraday_min),
        "sampled_minimum_available_cash": min_available,
        "available_cash_frequency": "Intraday events plus equity marks" if "minimum_available_cash_intraday" in diag else "Equity observations only",
        "max_reserved_cash": max_reserved, "average_cash_ratio_pct": mean(cash_ratios) if cash_ratios else None,
        "final_cash": float(final["cash"]), "realized_pnl": realized,
        "unrealized_change": unrealized_change, "pnl_reconciliation_error": reconciliation_error,
        "max_nav_ledger_error": max_ledger_error,
        "total_trades": total_buys + total_sells, "buy_fills": total_buys, "sell_fills": total_sells,
        "fees_tax": fees, "slippage_cost": slip, "total_execution_cost": fees + slip,
        "execution_cost_note": "Fees are cash debits; slippage is embedded in fill prices, never subtracted twice",
        "decay_realized_pnl": float(diag.get("decay_realized_pnl", decay_pnl)),
        "maximum_simultaneous_open_lots": max(max_lots, intraday_max_lots),
        "sampled_maximum_simultaneous_open_lots": max_lots,
        "remaining_open_lot_count": int(final.get("open_lots", 0)),
        "average_sold_quantity_holding_days": sum(age * qty for age, qty in ages) / sold_quantity if sold_quantity else None,
        "average_completed_lot_holding_days": mean(closed_ages) if closed_ages else None,
        "average_open_lot_age_days": diag.get("average_open_lot_age_days"),
        "cash_shortfall_blocks": cash_blocks, "all_buy_blocks": sum(int(v) for v in blocks.values()),
        "block_count_unit": "Strategy evaluation events; repeated opportunities may be counted again",
        "reentry_attempts": int(attempts), "reentry_filled_orders": int(reentry_fills),
        "reentry_unfilled_orders": max(0, int(attempts) - int(reentry_fills)),
        "reentry_fill_rate_pct": float(reentry_fills) / float(attempts) * 100. if attempts else None,
        "reentry_success_definition": "Submitted reentry orders obtaining at least one fill; not eventual profitability",
        "observations": len(rows), "start": beginning.isoformat(), "end": times[-1].isoformat(),
    }
    return result


summarise_path = summarize_path
