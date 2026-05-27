# Today Bootstrap 120 Runbook

Last updated: 2026-05-27

Safety notes:

- This guide does not place orders by itself.
- UI manual sell/buy requests must go through `manual_order_requests` and Bot Core.
- UI must not call KIS order APIs directly.
- Do not reset DB until all open lots are gone, pending orders are gone, pending manual requests are gone, and DB/KIS balance mismatch is resolved.

## Current Config Intent

The active `config/lot_auto_trader.json` is prepared for a low-price bootstrap run:

- candidate stocks: 120
- enabled stocks: 120
- `order.enable_execution_raw_log=false`
- `ui_manual_trading_enabled=true`
- `risk.max_active_symbols=120`
- `risk.max_new_buy_per_day=120`
- `risk.max_new_buy_amount_per_day=30,000,000`
- `risk.max_total_initial_buy_amount_per_day=30,000,000`
- `risk.max_total_open_lots=500`
- `risk.max_total_invested_amount=30,000,000`
- experiment run id: `bootstrap_120_low_price_v1`

The configured price LOT bands are:

| min price | max price | 1 LOT amount | max symbol amount |
| ---: | ---: | ---: | ---: |
| 0 | 300 | 1,000 | 10,000 |
| 301 | 1,000 | 3,000 | 30,000 |
| 1,001 | 3,000 | 10,000 | 100,000 |
| 3,001 | 10,000 | 30,000 | 300,000 |
| 10,001 | 30,000 | 100,000 | 1,000,000 |
| 30,001 | 100,000 | 300,000 | 3,000,000 |
| 100,001 | 300,000 | 1,000,000 | 10,000,000 |
| 300,001 | 1,000,000 | 3,000,000 | 30,000,000 |
| 1,000,001 | 3,000,000 | 10,000,000 | 100,000,000 |

## UI Order Of Operations

### 1. Start UI

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m kis_msj.ui_server --config config\lot_auto_trader.json --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

### 2. Start With A Backup

Open the `New Season` tab and create/archive the current state first.

Do not delete current DB or current log before liquidation is finished. The current DB is needed to know which lots must be sold.

### 3. Generate KIS Balance Snapshot

In `New Season`, generate or validate the KIS balance snapshot.

The snapshot must include:

- `generated_at`
- `code`
- `holding_quantity`
- `sellable_quantity`

For actual liquidation request creation, `generated_at` and `sellable_quantity` are required. Preview may warn and continue, but request creation must be blocked if those fields are missing.

### 4. Create Liquidation Plan

Use the UI to create a liquidation plan from:

- current DB open lots
- latest KIS balance snapshot

If the plan says it is stale, missing, or mismatched, regenerate it after fixing the cause.

### 5. Create Liquidation Requests

When the plan is valid, create liquidation manual SELL requests from the UI.

Confirm text:

```text
전량매도 요청 확인
```

This creates `manual_order_requests` only. It does not directly call KIS order APIs. Bot Core must consume those requests through the existing guard/order_manager path.

### 6. Run Bot Core To Process Requests

Run Bot Core only after confirming runtime controls are not blocking sells unless intentionally paused.

Bot Core will:

- claim manual requests
- revalidate runtime/risk/open order guards
- submit orders through the existing order manager path
- update lots/positions only after fill insert succeeds

### 7. Confirm Fills And Reconciliation

Before reset, confirm:

- all SELL orders are FILLED/CANCELED/REJECTED/FAILED
- pending orders count is 0
- pending manual request count is 0
- OPEN LOT count is 0
- DB/KIS balance mismatch is 0
- SYNC_REQUIRED is 0

### 8. Reset DB

Only after the checks above pass, use the New Season reset action.

Confirm text:

```text
RESET 확인
```

### 9. Reload Config And Start Bootstrap

After reset, reload config/start Bot Core with the prepared 120-stock config.

The first run is intended to allow at least one initial LOT for all 120 enabled candidates, subject to live market price, cash, runtime pause, risk flags, open-order guards, and KIS API availability.

## Old Logs / DB Cleanup

Do not delete the active DB or active log before the liquidation/reset sequence is complete.

Recommended safe cleanup:

1. Use New Season archive first.
2. Keep the current DB until reset succeeds.
3. Keep the current `logs/lot_auto_trader.log` during trading.
4. After reset succeeds, move old archived logs/DB snapshots out of the working view if needed.

Deleting or moving files before liquidation can make it harder to prove which lots/orders/fills were still open.
