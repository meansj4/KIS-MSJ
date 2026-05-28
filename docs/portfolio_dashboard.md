# Portfolio / Risk Dashboard

The local UI has a read-only `운용 현황 Portfolio/Risk` tab. It is designed to show the current LOT bot operating state, PnL, and config limit usage at a glance.

This tab does not call KIS order APIs, does not create orders, does not mutate lots/positions/fills, and does not reset the DB.

## Pre/After/Night Status

Pre/After/Night market-session expansion is on hold and should be treated as a separate branch/review item. The current main flow remains regular-session oriented. This dashboard uses existing DB rows and regular trading logic only.

## Overall Metrics

- `total_buy_amount`: sum of BUY fill `quantity * price`.
- `total_buy_lot_count`: unique LOT ids created by BUY fills. If `lot_id` is missing, the dashboard falls back to fill/order identifiers.
- `current_holding_buy_amount`: OPEN LOT `remaining_quantity * buy_price`.
- `current_holding_lot_count`: OPEN LOT count where `remaining_quantity > 0` and `status != CLOSED`.
- `realized_pnl`: SELL fill PnL minus estimated fee/tax when sell fill and LOT cost are available. If not available, stored LOT `realized_profit_loss` is used.
- `realized_pnl_rate`: `realized_pnl / sold cost`.
- `unrealized_pnl`: OPEN LOT current saved price minus buy price, multiplied by remaining quantity.
- `unrealized_pnl_rate`: `unrealized_pnl / current_holding_buy_amount`.

Fee/tax is estimated with `strategy.estimated_fee_tax_pct`.

## Daily Metrics

Daily rows use the KST date portion of `fills.filled_at`.

- `buy_amount`: BUY fills on that date.
- `buy_lot_count`: unique BUY LOTs on that date.
- `sell_amount`: SELL fills on that date.
- `sell_lot_count`: unique LOTs with SELL fills on that date. Partial and full sells are counted by unique LOT id.
- `realized_pnl`: SELL fill realized PnL for that date, net of estimated fee/tax.
- `realized_pnl_rate`: date realized PnL divided by the sold LOT cost basis.
- `unrealized_pnl`: current-basis unrealized PnL for open LOTs bought on that date.
- `unrealized_pnl_rate`: current-basis unrealized PnL divided by remaining cost for those open LOTs.

Daily unrealized PnL is not a historical close-price value. It becomes more precise only if daily close snapshots are collected and a historical valuation model is added later.

## Limit Usage Gauges

The dashboard shows `current / limit / usage_pct` with progress bars:

- `risk.max_total_invested_amount`: OPEN LOT remaining cost.
- `risk.max_total_open_lots`: OPEN LOT count.
- `risk.max_active_symbols`: symbols with open LOTs, pending orders, or active/review/sync position states.
- `risk.max_new_buy_per_day`: today's `initial_buy` BUY order count.
- `risk.max_new_buy_amount_per_day`: today's `initial_buy` BUY order amount.
- `risk.max_total_initial_buy_amount_per_day`: today's `initial_buy` BUY order amount.

Gauge levels:

- `normal`: 0-50%
- `warning`: 50-80%
- `danger`: 80-100%
- `over`: 100% or more
- `unlimited`: limit is 0 or not configured

The dashboard also lists top symbol exposure rows using `positions.max_symbol_amount` and `positions.max_lots_per_symbol` where available.

## Data Quality Notes

The dashboard reports data quality notes in the UI:

- valuation uses stored `positions.current_price` or latest `price_snapshots.current_price`
- daily unrealized PnL is current-basis, not historical close-basis
- fee/tax is estimated
- missing or stale price snapshots may make unrealized PnL less reliable

