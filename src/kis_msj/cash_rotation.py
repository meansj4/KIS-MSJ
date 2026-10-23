"""Aggregate eligible BUY demand and recycle oldest LOTs into shared account cash."""
from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime

from .kis_client import KisApiError
from .domestic_quote import KisReadError, NETWORK_READ_ERRORS, is_rate_limit_error
from .lot_manager import LotManager, lot_buy_timestamp
from .market_session import SessionUnavailable, RoutedQuotes
from .models import OrderSide, PositionLifecycle, SellReason
from .price_provider import PriceSampler
from .risk_manager import RiskDecision
from .runtime_control import load_runtime_control, runtime_block_reason
from .strategy import LotGridStrategy, StrategyAction


def funding_enabled(bot):
    """Legacy BUY-funded liquidation is superseded by the cash target policy."""
    return (bot.config.risk.cash_shortfall_rotation_enabled
            and not bot.config.strategy.adaptive_lot_enabled
            and not bot.config.strategy.cash_target_profit_active()
            and not bot.config.strategy.cash_capacity_reentry_enabled()
            and not bot.config.strategy.cash_buy_drop_enabled)


def decide_with_cash_signal(bot, position, price, snapshot, account_risk, symbol_risk):
    if not funding_enabled(bot):
        return bot.strategy.decide(position, price, snapshot, account_risk, symbol_risk)
    reasons = tuple(reason for reason in account_risk.reasons if reason != "cash_below_minimum")
    signal_snapshot = replace(snapshot, cash_available=max(snapshot.cash_available, 10**18))
    return bot.strategy.decide(position, price, signal_snapshot, RiskDecision(not reasons, reasons), symbol_risk)


def rebuy_price_limit(bot, position) -> int | None:
    """A fresh strategy price interval after rotation, with no wall-clock lock."""
    if bot.config.strategy.adaptive_lot_enabled or not bot.config.risk.cash_shortfall_rotation_enabled:
        return None
    if bot.config.strategy.cash_buy_drop_enabled:
        # The cash BUY policy replaces legacy fixed-drop rotation anchors.
        # Its current-cash VWAP ceiling is enforced by strategy and submission.
        return None
    if (bot.config.strategy.cash_capacity_reentry_enabled() and position.quantity == 0
            and position.position_state == PositionLifecycle.WAIT_REENTRY.value):
        # Cash capacity replaces every historical exit-price condition. Keep
        # the rotation price ceiling for additional buys in held positions.
        return None
    anchor = bot.store.cash_rotation_rebuy_anchor(position.code)
    if anchor is None:
        return None
    # The price rule depends only on current configuration and the refreshed
    # position, so OrderManager can enforce the same ceiling independently.
    strategy = LotGridStrategy(bot.config, LotManager(bot.config.strategy))
    lot_manager = getattr(bot, "lot_manager", None)
    count = len(lot_manager.open_lots(position.code)) if lot_manager else bot.store.cash_rotation_open_lot_count(position.code)
    if position.quantity > 0 and count > 0:
        if bot.config.strategy.lot_sizing_mode == "cycle_locked_by_entry_price":
            plan = strategy.add_buy_lot_plan(count)
            drop = plan[0] if plan else abs(bot.config.strategy.normal_reentry_drop_rate)
        else:
            plan = strategy.lot_manager.buy_plan(position.cumulative_invested_amount)
            drop = plan[0]/100 if plan else abs(bot.config.strategy.normal_reentry_drop_rate)
        raw = anchor*(1-drop)
    else:
        details = strategy.reentry_details(position, position.current_price)
        # A fresh post-exit high followed by the strategy's trailing drop is
        # also a new wave, even when its trigger is above the old SELL price.
        if (details["reentry_mode"] == "TRAILING_REENTRY" and
                position.post_exit_high_price >= anchor*(1+bot.config.strategy.trailing_activation_gain)):
            raw = details["reentry_trigger_price"]
        else:
            drop = abs(details["effective_reentry_rate"]) or abs(bot.config.strategy.normal_reentry_drop_rate)
            raw = anchor*(1-drop)
    # Same domestic tick ladder as the existing order price policy; floor the
    # ceiling so rounding cannot authorize a price above the new interval.
    unit = (1 if raw < 1000 else 5 if raw < 5000 else 10 if raw < 10000 else
            50 if raw < 50000 else 100 if raw < 100000 else 500 if raw < 500000 else 1000)
    limit = int(math.floor((raw+1e-9)/unit))*unit
    return max(0, limit)


def buy_block_reason(bot, position) -> str:
    if bot.config.strategy.adaptive_lot_enabled or not bot.config.risk.cash_shortfall_rotation_enabled:
        return ""
    limit = rebuy_price_limit(bot, position)
    if limit is not None and (position.current_price <= 0 or position.current_price > limit):
        return "cash_rotation_wait_new_buy_price"
    if bot.store.open_orders():
        return "cash_rotation_wait_open_orders"
    return ""


def target_record(request):
    return {"code": request.code, "notional": request.quantity*request.limit_price,
            "quantity": request.quantity, "limit_price": request.limit_price, "reason": request.reason,
            "checked_at": datetime.now().isoformat(timespec="seconds")}


def remove_target(bot, code):
    plan = bot.store.load_cash_rotation_plan()
    if plan and plan.get("mode") == "aggregate" and code in plan.get("targets", {}):
        targets = dict(plan["targets"])
        targets.pop(code, None)
        bot.store.set_cash_rotation_targets(targets, plan["baseline_cash"])


def abandon_invalid_target(bot, position, action):
    if bot.config.risk.cash_shortfall_rotation_enabled and (action is None or action.side is not OrderSide.BUY):
        remove_target(bot, position.code)


def handle_shortfall(bot, buyer, action, request, snapshot, *, shadow=False):
    required = max(request.quantity*request.limit_price, bot.config.risk.min_cash_available)
    if snapshot.cash_available >= required:
        return False
    if not shadow and funding_enabled(bot):
        plan = bot.store.load_cash_rotation_plan()
        targets = dict(plan.get("targets", {})) if plan else {}
        targets[buyer.code] = target_record(request)
        bot.store.set_cash_rotation_targets(targets, snapshot.cash_available)
        buyer.skip_reason = "cash_rotation_aggregate_cash_shortfall"
        bot.store.save_position(buyer)
    bot.log_pre_request_block(buyer, "cash_rotation_aggregate_cash_shortfall" if not shadow and funding_enabled(bot) else "insufficient_cash")
    return True


def _quote(bot, code, name):
    if bot.config.extended_hours.enabled:
        route, quote = bot.session_market.select(code, name)
        if route.session != "REGULAR" and bot.config.extended_hours.mode != "live":
            raise SessionUnavailable("cash_rotation_shadow_no_orders")
        return route, quote
    return None, bot.client.quote(code, name=name)


def _trade_window(bot):
    from .main import in_trade_window
    from .market_session import extended_window
    return in_trade_window(bot.config) or (bot.config.extended_hours.enabled and
        bot.config.extended_hours.mode == "live" and extended_window(bot.config))


def _fresh_snapshot(bot, snapshot):
    if not bot.config.order.live_trading:
        return snapshot
    bot.invalidate_account_snapshot_cache("cash_rotation_aggregate_fresh_balance")
    return bot.startup_sync()


def refresh_targets(bot, snapshot, *, max_targets=None):
    """Reprice and remove invalid signals before funding; never invent demand."""
    if not funding_enabled(bot):
        return {}
    plan = bot.store.load_cash_rotation_plan()
    if not plan:
        return {}
    codes = list(plan.get("targets", {}))
    targets = {}
    if max_targets is not None:
        # Revisit a small rotating batch during liquidation. A full REST pass
        # here can otherwise consume the entire donor processing window.
        offset = getattr(bot, "_cash_rotation_refresh_offset", 0) % max(1, len(codes))
        codes = (codes[offset:] + codes[:offset])[:max_targets]
        bot._cash_rotation_refresh_offset = offset + len(codes)
        targets = {code: target for code, target in plan.get("targets", {}).items() if code not in codes}
    else:
        bot._cash_rotation_refresh_offset = 0
    initial_count = bot.store.count_today_initial_buy_orders()
    initial_amount = bot.store.sum_today_initial_buy_order_amount()
    initial_amount_cap = bot.config.risk.max_total_initial_buy_amount_per_day or bot.config.risk.max_new_buy_amount_per_day
    # Unvisited initial signals retain their quota reservation during a batch.
    initial_count += sum(target["reason"] == "initial_buy" for target in targets.values())
    initial_amount += sum(target["notional"] for target in targets.values() if target["reason"] == "initial_buy")
    for code in codes:
        if bot.runtime_interrupt_reason("cash_rotation_refresh_targets") or not _trade_window(bot):
            return None
        stock = bot.stock_config_for_code(code)
        if stock is None or not stock.enabled or stock.manual_only or stock.danger_state:
            continue
        try:
            _, quote = _quote(bot, code, stock.name)
        except SessionUnavailable:
            continue
        except (KisApiError, KisReadError, *NETWORK_READ_ERRORS) as error:
            # Keep retryable demand, but exclude it from liquidation and BUYs
            # until a fresh quote has validated it again.
            targets[code] = dict(plan["targets"][code], validation_error=type(error).__name__)
            bot.logger.warning("cash_rotation_target_unverified code=%s error_type=%s", code, type(error).__name__)
            continue
        if quote.price <= 0:
            continue
        position = bot.position_manager.refresh_from_lots(code, quote.price)
        bot.apply_stock_retirement_config(position, stock)
        position = bot.auto_recheck_review_required(position, quote.price)
        if bot.strategy.update_reentry_tracking(position, quote.price):
            bot.store.save_position(position)
        account_risk = bot.risk_manager.account_buy_allowed(snapshot, bot.position_manager.positions)
        action = decide_with_cash_signal(bot, position, quote.price, snapshot, account_risk,
                                        bot.risk_manager.symbol_buy_allowed(position))
        if action is None or action.side is not OrderSide.BUY or bot.pre_request_block_reason(position, action):
            continue
        request = bot.order_manager.build_request(position, action, quote.price)
        if request:
            if action.reason == "initial_buy":
                # Several individually eligible signals must share the same daily quota.
                if initial_count >= bot.config.risk.max_new_buy_per_day:
                    continue
                if initial_amount_cap and initial_amount+request.quantity*request.limit_price > initial_amount_cap:
                    continue
                initial_count += 1
                initial_amount += request.quantity*request.limit_price
            targets[code] = target_record(request)
    targets = {code: targets[code] for code in plan.get("targets", {}) if code in targets}
    bot.store.set_cash_rotation_targets(targets, snapshot.cash_available)
    return targets


def funding_totals(bot, snapshot):
    plan = bot.store.load_cash_rotation_plan()
    if not plan:
        return {"target_count": 0, "total_required": 0, "shortfall": 0, "pending_credit": 0}
    targets = plan.get("targets", {})
    required = sum(item["notional"] for item in targets.values())
    verified = {code: item for code, item in targets.items() if not item.get("validation_error")}
    verified_required = sum(item["notional"] for item in verified.values())
    if targets:
        required = max(required, bot.config.risk.min_cash_available)
    if verified:
        verified_required = max(verified_required, bot.config.risk.min_cash_available)
    released = bot.store.cash_rotation_proceeds(plan["fill_watermark"], bot.config.strategy.estimated_fee_tax_pct)
    spent = bot.store.cash_rotation_buy_spending(plan["fill_watermark"])
    expected = max(0, plan["baseline_cash"] + released - spent)
    totals = {"target_count": len(targets), "total_required": required,
              "unverified_target_count": len(targets)-len(verified),
              "unverified_required": sum(item["notional"] for item in targets.values() if item.get("validation_error")),
              "buyable_cash": snapshot.cash_available, "expected_cash": expected,
              "pending_credit": max(0, expected-snapshot.cash_available),
              "shortfall": max(0, verified_required-max(snapshot.cash_available, expected)),
              "updated_at": datetime.now().isoformat(timespec="seconds")}
    bot.store.save_cash_rotation_totals(totals)
    return totals


def _buy_affordable(bot, snapshot, attempted):
    if not funding_enabled(bot):
        return snapshot
    plan = bot.store.load_cash_rotation_plan()
    if not plan:
        return snapshot
    for code, target in list(plan.get("targets", {}).items()):
        if target.get("validation_error") or code in attempted or snapshot.cash_available < max(target["notional"], bot.config.risk.min_cash_available):
            continue
        if bot.store.open_orders() or bot.runtime_interrupt_reason("cash_rotation_before_buy") or not _trade_window(bot):
            break
        attempted.add(code)
        stock = bot.stock_config_for_code(code)
        if stock is None:
            remove_target(bot, code)
            continue
        position = bot.position_manager.get(code, stock.name)
        bot.evaluate(position, snapshot, bot.risk_manager.account_buy_allowed(snapshot, bot.position_manager.positions))
        snapshot = _fresh_snapshot(bot, snapshot)
        if not bot.config.order.live_trading:
            totals = funding_totals(bot, snapshot)
            snapshot = replace(snapshot, cash_available=totals.get("expected_cash", snapshot.cash_available))
    return snapshot


def process_rotation(bot, snapshot, *, max_seconds=60):
    """A read failure defers this tick; it must not abort the universe scan."""
    try:
        return _process_rotation(bot, snapshot, max_seconds=max_seconds)
    except (KisApiError, KisReadError, *NETWORK_READ_ERRORS) as error:
        if isinstance(error, KisApiError) and error.method != "GET":
            raise
        bot.logger.warning("cash_rotation_read_unavailable retry_next_tick=true error_type=%s", type(error).__name__)
        return snapshot
    except RuntimeError as error:
        if not is_rate_limit_error(error):
            raise
        bot.logger.warning("cash_rotation_read_unavailable retry_next_tick=true error_type=rate_limit")
        return snapshot


def _process_rotation(bot, snapshot, *, max_seconds=60):
    """Several sequential donor orders per tick, bounded for pause/SELL responsiveness."""
    if not funding_enabled(bot) or not _trade_window(bot):
        return snapshot
    if bot.runtime_interrupt_reason("cash_rotation_begin") or bot.store.open_orders():
        return snapshot
    started_at = time.monotonic()
    snapshot = _fresh_snapshot(bot, snapshot)
    targets = refresh_targets(bot, snapshot)
    if targets is None:
        return snapshot
    attempted_buys = set()
    snapshot = _buy_affordable(bot, snapshot, attempted_buys)
    totals = funding_totals(bot, snapshot)
    bot.logger.info("cash_rotation_aggregate targets=%s required=%s cash=%s pending_credit=%s shortfall=%s unverified=%s",
                    totals["target_count"], totals["total_required"], snapshot.cash_available,
                    totals["pending_credit"], totals["shortfall"], totals.get("unverified_target_count", 0))
    if not totals["target_count"]:
        if not totals["pending_credit"] and not bot.store.open_orders():
            bot.store.clear_cash_rotation_plan()
        return snapshot
    deadline = time.monotonic()+max_seconds
    refreshed_at = time.monotonic()
    lots = sorted((lot for lot in bot.lot_manager.lots.values() if lot.remaining_quantity > 0),
                  key=lambda lot: (lot_buy_timestamp(lot), lot.lot_id))
    skipped = Counter()
    submitted = 0
    stop_reason = "donors_exhausted"
    for lot in lots:
        if time.monotonic() >= deadline or bot.runtime_interrupt_reason("cash_rotation_next_lot") or not _trade_window(bot):
            stop_reason = "time_or_runtime_or_session"
            break
        if bot.store.open_orders():
            stop_reason = "open_orders"
            break
        totals = funding_totals(bot, snapshot)
        if totals["shortfall"] <= 0:
            stop_reason = "shortfall_satisfied"
            break
        if time.monotonic()-refreshed_at >= 30:
            targets = refresh_targets(bot, snapshot, max_targets=5)
            if targets is None:
                stop_reason = "refresh_interrupted"
                break
            refreshed_at = time.monotonic()
            totals = funding_totals(bot, snapshot)
            if totals["shortfall"] <= 0 or time.monotonic() >= deadline:
                stop_reason = "shortfall_satisfied" if totals["shortfall"] <= 0 else "time_budget"
                break
        control = load_runtime_control()
        if control.all_orders_paused or control.buy_paused or control.sell_paused or control.cleanup_paused:
            stop_reason = "orders_paused"
            break
        if control.reentry_paused:
            targets = refresh_targets(bot, snapshot)
            if targets is None or funding_totals(bot, snapshot)["shortfall"] <= 0:
                break
        plan = bot.store.load_cash_rotation_plan()
        if lot.code in plan.get("targets", {}):
            skipped["pending_buy_symbol"] += 1
            continue
        stock = bot.stock_config_for_code(lot.code)
        position = bot.position_manager.positions.get(lot.code)
        if stock is None or not stock.enabled or stock.manual_only or stock.danger_state or position is None:
            skipped["stock_ineligible"] += 1
            continue
        if position.danger_state or position.position_state == PositionLifecycle.RISK_BLOCKED.value:
            skipped["risk_blocked"] += 1
            continue
        if bot.sync_required_block_reason(position) or position.lot_quantity_mismatch or bot.store.has_any_open_order(lot.code):
            skipped["sync_or_open_order"] += 1
            continue
        try:
            route, _ = _quote(bot, lot.code, stock.name)
            quotes = RoutedQuotes(bot.session_market, route) if route else bot.client
            sampler = PriceSampler(quotes, bot.config.order.price_sample_count,
                                   bot.config.order.price_sample_interval_seconds) if route else bot.price_sampler
            samples = sampler.sample(lot.code, stock.name)
            quote = quotes.quote(lot.code, name=stock.name)
            stable, _ = bot.price_sampler.stable((*samples, quote), bot.config.risk.max_price_sample_volatility_pct)
        except (SessionUnavailable, KisApiError, KisReadError, *NETWORK_READ_ERRORS) as error:
            skipped["quote_unavailable"] += 1
            bot.logger.info("cash_rotation_donor_skipped donor=%s lot_id=%s reason=%s",
                            lot.code, lot.lot_id, error)
            continue
        if quote.price <= 0 or not stable:
            skipped["price_unstable_or_missing"] += 1
            continue
        position = bot.position_manager.refresh_from_lots(lot.code, quote.price)
        price = bot.order_manager.sell_limit_price(quote.price)
        net_unit = price*(1-bot.config.strategy.estimated_fee_tax_pct/100)
        if net_unit <= 0:
            continue
        quantity = min(lot.remaining_quantity, position.quantity, math.ceil(totals["shortfall"]/net_unit))
        if quantity < 1:
            continue
        sell = StrategyAction(OrderSide.SELL, 0, quantity, "cash_shortfall_rotation_sell",
                              lot_id=lot.lot_id, sell_reason=SellReason.CASH_SHORTFALL_ROTATION.value,
                              cleanup_flag=True, limit_price=price)
        block_reason = bot.pre_request_block_reason(position, sell)
        if block_reason:
            skipped[block_reason] += 1
            continue
        if bot.runtime_interrupt_reason("cash_rotation_before_sell") or runtime_block_reason(load_runtime_control(), sell):
            break
        request = bot.order_manager.build_request(position, sell, quote.price)
        if route:
            request = replace(request, session=route.session, venue=route.venue,
                              quote_market=route.quote_market, order_division=route.order_division)
        bot.logger.info("cash_rotation_sell targets=%s donor=%s lot_id=%s qty=%s limit=%s shortfall=%s",
                        totals["target_count"], lot.code, lot.lot_id, quantity, price, totals["shortfall"])
        try:
            submitted += 1
            _, fill = bot.order_manager.submit_and_confirm(request)
        except Exception:
            bot.invalidate_account_snapshot_cache("cash_rotation_submission_failed")
            bot.logger.exception("cash_rotation_submission_failed donor=%s", lot.code)
            stop_reason = "submission_failed"
            break
        bot.invalidate_account_snapshot_cache("cash_rotation_order_submitted")
        if fill:
            updated = bot._apply_fill_with_session_audit(fill)
            bot.store.save_position(updated)
            bot.store.save_lots(bot.lot_manager.lots.values())
        if bot.store.open_orders():
            stop_reason = "open_orders"
            break
        snapshot = _fresh_snapshot(bot, snapshot)
        if not bot.config.order.live_trading:
            snapshot = replace(snapshot, cash_available=funding_totals(bot, snapshot)["expected_cash"])
        snapshot = _buy_affordable(bot, snapshot, attempted_buys)
    final_totals = funding_totals(bot, snapshot)
    bot.logger.info("cash_rotation_tick_end submitted=%s shortfall=%s elapsed_seconds=%.1f stop=%s skipped=%s",
                    submitted, final_totals["shortfall"], time.monotonic()-started_at, stop_reason, dict(skipped))
    if not final_totals["target_count"] and not final_totals["pending_credit"] and not bot.store.open_orders():
        bot.store.clear_cash_rotation_plan()
    return snapshot
