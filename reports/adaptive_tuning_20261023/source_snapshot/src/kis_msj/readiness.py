"""Check a live account and strategy without submitting or cancelling orders."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
import time

from .config import config_hash
from .volatility import KST


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    reasons: tuple[str, ...]
    report: dict


@contextmanager
def broker_read_only(client):
    """Block trading POSTs even if a called reconciliation path changes later."""
    saved = {}

    def forbidden(*args, **kwargs):
        raise RuntimeError("live_readiness_order_mutation_forbidden")

    if hasattr(client, "_request"):
        original = client._request

        def request(method, *args, **kwargs):
            if str(method).upper() != "GET":
                raise RuntimeError("live_readiness_non_get_forbidden")
            return original(method, *args, **kwargs)

        saved["_request"] = original
        client._request = request
    for name in ("place_order", "cancel_order"):
        if hasattr(client, name):
            saved[name] = getattr(client, name)
            setattr(client, name, forbidden)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(client, name, value)


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_readiness(trader, *, refresh_volatility: bool = True, report_path=None) -> ReadinessResult:
    """Reconcile confirmed fills, compare actual holdings, then price each symbol.

    This intentionally does not call run_once, reconcile_open_orders (cancels),
    or the heuristic missing-fill repair in startup_sync. Unproven differences
    remain blocked instead of inventing fills from an old order limit.
    """
    reasons = []
    config = trader.config
    path = Path(report_path) if report_path else Path(config.log_path).resolve().parent.parent / "reports" / "live_readiness_latest.json"
    report = {"version": 1, "observed_at": datetime.now(KST).isoformat(timespec="seconds"),
              "config_hash": config_hash(config), "storage_path": str(Path(config.storage_path).resolve()),
              "order_submission_enabled": False, "symbols": [], "ready": False, "reasons": []}
    try:
        if not config.order.live_trading:
            reasons.append("live_trading_not_enabled")
        if not config.strategy.adaptive_lot_enabled:
            reasons.append("adaptive_strategy_not_enabled")
        if getattr(trader.client, "is_demo", True):
            reasons.append("real_account_required")
        if reasons:
            raise RuntimeError("live_readiness_configuration_invalid")
        with broker_read_only(trader.client):
            trader.logger.info("live_readiness_started order_submission_enabled=false")
            trader.reconcile_recent_executions_on_startup()
            snapshot = trader.client.account_snapshot()
            for label, value in (("cash", snapshot.cash_available), ("assets", snapshot.total_asset)):
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (label == "assets" and value == 0):
                    reasons.append(f"invalid_account_{label}")
            if reasons:
                raise ValueError("invalid_account_snapshot")
            trader.position_manager.sync_account(snapshot)
            broker_open = trader.client.open_orders()
            local_open = trader.store.open_orders()
            manual_pending = [request for status in ("REQUESTED", "PROCESSING")
                              for request in trader.store.manual_order_requests(status)]
            if broker_open:
                reasons.append("broker_open_orders_require_reconciliation")
            if local_open:
                reasons.append("local_open_orders_require_reconciliation")
            if manual_pending:
                reasons.append("pending_manual_requests_require_review")
            mismatches = [position.code for position in trader.position_manager.positions.values()
                          if position.lot_quantity_mismatch or position.sync_status == "SYNC_REQUIRED"]
            if mismatches or trader.position_manager.account_mismatch_detected:
                reasons.append("account_lot_quantity_mismatch")
            trader.risk_manager.data_mismatch_detected = bool(mismatches or broker_open or local_open)
            trader._last_account_snapshot = snapshot
            trader._last_account_snapshot_at = time.monotonic()
            trader._account_snapshot_rate_limited_until = 0.0
            report.update(cash_available=snapshot.cash_available, total_asset=snapshot.total_asset,
                          broker_holding_symbols=len(snapshot.positions), broker_open_orders=len(broker_open),
                          local_open_orders=len(local_open), pending_manual_requests=len(manual_pending),
                          quantity_mismatch_codes=mismatches)
            if refresh_volatility:
                trader.refresh_daily_volatility()
            for stock in config.stocks:
                if not stock.enabled or stock.manual_only or trader.automatic_symbol_is_retired(stock):
                    continue
                try:
                    quote = trader.client.quote(stock.code, name=stock.name)
                    if type(quote.price) not in (int, float) or not math.isfinite(quote.price) or quote.price <= 0:
                        raise ValueError("invalid_current_quote")
                    position = trader.position_manager.refresh_from_lots(stock.code, quote.price)
                    trader.apply_stock_retirement_config(position, stock)
                    context = trader.strategy.context(position, quote.price, snapshot)
                    estimate = trader.volatility_cache.get(stock.code)
                    if estimate.reason not in {"ok", "insufficient_data"}:
                        reasons.append(f"volatility_unavailable:{stock.code}")
                    lots = trader.lot_manager.open_lots(stock.code)
                    report["symbols"].append({"code": stock.code, "name": stock.name,
                        "quote": quote.price, "quantity": position.quantity, "open_lots": len(lots),
                        "volatility": asdict(estimate), "buy_target": context.adaptive_buy_target_price,
                        "buy_gap_pct": context.target_buy_drop_rate * 100,
                        "consecutive_buy_count": position.consecutive_buy_count,
                        "consecutive_sell_count": position.consecutive_sell_count,
                        "rebuy_restriction_active": position.rebuy_restriction_active,
                        "last_sell_price": position.last_sell_price,
                        "reentry_required_cash": context.reentry_required_cash,
                        "reentry_cash_condition_met": context.reentry_cash_condition_met,
                        "position_state": position.position_state,
                        "lot_targets": [{"lot_id": lot.lot_id, "buy_price": lot.buy_price,
                            "buy_filled_at": lot.buy_filled_at, "remaining_quantity": lot.remaining_quantity,
                            "target_profit_pct": lot.effective_target_profit_rate * 100,
                            "target_sell_price": lot.target_sell_price} for lot in lots]})
                except Exception as error:
                    reasons.append(f"symbol_check_failed:{stock.code}:{type(error).__name__}")
                    trader.logger.exception("live_readiness_symbol_failed code=%s", stock.code)
            trader.store.save_positions(trader.position_manager.positions.values())
            trader.store.save_lots(trader.lot_manager.lots.values())
            risk = trader.risk_manager.account_buy_allowed(snapshot, trader.position_manager.positions)
            report.update(account_buy_allowed=risk.allowed, account_buy_block_reasons=list(risk.reasons),
                          verified_symbols=len(report["symbols"]),
                          open_lots=sum(1 for lot in trader.lot_manager.lots.values() if lot.remaining_quantity > 0))
            trader.logger.info("cash_target_account_snapshot cash_available=%s snapshot_at=%s total_asset=%s",
                               snapshot.cash_available, datetime.now(KST).replace(tzinfo=None).isoformat(timespec="seconds"), snapshot.total_asset)
    except Exception as error:
        if not reasons:
            reasons.append(f"preflight_failed:{type(error).__name__}")
        trader.logger.exception("live_readiness_failed error_type=%s", type(error).__name__)
        report["error_type"] = type(error).__name__
    reasons = list(dict.fromkeys(reasons))
    report.update(ready=not reasons, reasons=reasons, completed_at=datetime.now(KST).isoformat(timespec="seconds"))
    _write_report(path, report)
    trader.logger.info("live_readiness_completed ready=%s symbols=%s reasons=%s report=%s",
                       report["ready"], len(report["symbols"]), ",".join(reasons) or "NONE", path)
    return ReadinessResult(not reasons, tuple(reasons), report)
