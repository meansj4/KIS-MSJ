# Analysis And Tuning Data Guide

Authoritative source: `docs/project_handoff_full.md` remains the full baseline. This document explains what data should be accumulated so Codex/ChatGPT can later review DB/log history and suggest safer config tuning.

Last updated: 2026-05-27  
Baseline profile: `expansion_100_safe`  
Safety note: analysis/export must not call KIS order APIs, must not place orders, and must not reset DB.

Quick operator guide: see `docs/market_data_tuning_guide.md` for the daily `Save market data now` workflow and the 2026-05-27 saved-data verification snapshot.

## Purpose

The goal is not just to see whether the bot made money. The goal is to preserve enough decision, order, fill, LOT, and config context so future analysis can compare config versions and suggest better values for:

- `price_lot_bands`
- `add_buy_lot_bands`
- `target_profit_lot_bands`
- `max_lots_per_symbol`
- stale/review thresholds
- reentry settings
- daily/global risk limits
- cleanup policy

The preferred tuning target is risk-adjusted operation: profit, turnover, capital efficiency, stale LOT count, review burden, and drawdown should be considered together.

## What Current DB/Logs Can Already Analyze

| Analysis | Current support | Notes |
| --- | --- | --- |
| Symbol/LOT realized PnL | Good | `lots`, `fills`, `positions` |
| LOT holding period | Good | `buy_filled_at`, sell/closed state; exact sell close time is inferred from fills |
| Add-buy count by symbol | Good | OPEN/CLOSED LOT count and fill history |
| `add_buy_lot_band` performance | Improved | decision records now persist band context |
| `target_profit_lot_band` performance | Improved | decision records now persist current dynamic target context |
| `price_lot_band` performance | Good | position lot sizing fields and decisions |
| Reentry type performance | Good | `fills.reentry_type`, decision `reentry_type` |
| SELL type performance | Good | `fills.sell_reason`, lots `last_sell_reason` |
| REVIEW_REQUIRED before/after | Partial | review fields exist; event timing is better in logs/decision records |
| Skip/block opportunity cost | Partial | decisions persist blocked action context; future price path still needed |
| Manual vs automatic orders | Good | manual request table plus order reason/source context |

## Continuous Accumulation Map

| Data | Accumulates when only Bot Core runs? | Needs separate script? | Not implemented? | Stored in | Analysis value | Impact if missing |
| --- | --- | --- | --- | --- | --- | --- |
| `decisions` | Yes | No | No | `decisions` | Decision context, skipped/blocked actions, config comparison | Cannot explain why the bot bought/skipped/blocked |
| `orders` | Yes, when orders are requested | No | No | `orders` | Order status, partial/open order behavior | Cannot study fill latency or open order issues |
| `fills` | Yes, after new fill insert succeeds | No | No | `fills` | Realized trades, dedupe, execution quality | Cannot compute actual realized outcomes |
| `lots` | Yes, only after fill application | No | No | `lots` | LOT PnL, holding period, stale/review analysis | Cannot tune LOT bands or sell targets safely |
| `positions` | Yes | No | No | `positions` | Current exposure/state/sync/review status | Cannot reconstruct portfolio state |
| `manual_order_requests` | Yes, if UI/operator requests are created | No | No | `manual_order_requests` | Manual vs auto comparison, operator intervention count | Manual effects are hard to separate |
| `config_snapshots` | Yes, on Bot Core init | No | No | `config_snapshots` | Config-hash/run comparison | Cannot compare results by config version |
| `price_snapshots` | Yes, at decision logging | Optional script adds more | No | `price_snapshots` | Decision-time price context | What-if analysis becomes weak |
| `daily_prices` | No | Yes: `collect_market_data.py --daily --execute` | No | `daily_prices` | +N day follow-up, MFE/MDD approximation | Missed opportunity and what-if analysis are very limited |
| `liquidity_snapshots` | No | No | Collection future work | `liquidity_snapshots` schema | Spread/liquidity/slippage analysis | Cannot evaluate spread/fill quality precisely |
| `minute_prices` | No | No | Yes | Not present | Intraday backtest precision | Cannot simulate intraday triggers accurately |
| `skipped_actions` | Exported from `decisions` | Export script | No separate table | `skipped_actions.csv` | Opportunity cost candidates | Requires `daily_prices` to measure future outcome |
| runtime events | Partly through `runtime_control` and logs | No | Event table not implemented | runtime file/logs | Pause/emergency-stop attribution | Harder to quantify operator pauses |
| review events | Partly through position fields/logs | No | Event table not implemented | `positions`, logs | REVIEW_REQUIRED lifecycle | Exact event timeline may require logs |

## Market Data Now Stored

Actual fills tell what happened under the config that was used. They do not fully answer what would have happened under a different config. To reduce that blind spot, the bot now stores market context in dedicated tables.

| Table | Current status | What is actually filled now |
| --- | --- | --- |
| `price_snapshots` | Implemented | Decision-time current price, sampled time/source, run/config link, missing-field list. `collect_market_data.py --snapshot --execute` also stores KIS quote OHLCV fields where available. |
| `daily_prices` | Implemented for current-day quote snapshots | Existing KIS quote path can save today's open/high/low/current-as-close/volume/trading value. Multi-day historical daily collection is future work unless a separate historical price endpoint is added. |
| `liquidity_snapshots` | Schema only / future work | No orderbook collection is wired yet. bid/ask/spread fields remain null unless a future read-only quote/orderbook endpoint is added. |
| `market_data_collection_runs` | Implemented | Each executed market-data collection records start/end time, mode, symbol counts, inserted/updated rows, errors, dry-run flag, config hash, and experiment run id. |

Decision rows are linked to `price_snapshots` through `decisions.price_snapshot_id` and `price_snapshots.decision_id`.

## Data Still Needed For Better Counterfactual Tuning

Actual fills tell what happened under the config that was used. They do not fully answer “what if a different config had been used?” For counterfactual tuning, accumulate price path data:

| Needed data | Why |
| --- | --- |
| `daily_prices` | MDD/MFE after a buy, missed opportunity analysis, reentry alternative testing |
| `minute_prices`, optional | More precise intraday trigger and limit-order simulation |
| bid/ask/spread snapshots, optional | Slippage and fill-probability estimation |
| volume/trading value | Liquidity risk by symbol/sector |
| previous close / high / low | volatility and regime grouping |

Suggested future tables:

- `daily_prices(code, date, open, high, low, close, volume, trading_value)`
- `minute_prices(code, timestamp, open, high, low, close, volume)`
- `symbol_liquidity_stats(code, date, average_spread, trading_value, volume)`

## Config Versioning

Current implementation stores config snapshots in SQLite:

| Table | Purpose |
| --- | --- |
| `config_snapshots` | `config_hash`, version, source, full config JSON |
| `decisions` | persisted decision context with `config_hash` |
| `orders.config_hash` / `config_version` | links order rows to config |
| `fills.config_hash` / `config_version` | links fill rows to config |
| `manual_order_requests.config_hash` / `config_version` | links manual requests to config at creation |

`config_hash` is a stable SHA-256 based short hash of the normalized config. A config snapshot is recorded when `AutoTrader` initializes.

## Run / Experiment Metadata

`config_hash` is useful for exact config comparison, but it is not friendly enough for human experiment tracking. The config now also supports:

- `run_id`
- `experiment_name`
- `operator_note`
- `purpose`

If `run_id` is empty, Bot Core uses `{risk.profile}_{config_hash}`. If `experiment_name` is empty, it uses `risk.profile`.

Preferred config format:

```json
{
  "experiment": {
    "run_id": "cheap_random_bootstrap_v1",
    "experiment_name": "cheap random bootstrap",
    "operator_note": "0-30000 KRW symbols, first validation run",
    "purpose": "logic_validation"
  }
}
```

`experiment.*` values take precedence over the older top-level `run_id`, `experiment_name`, `operator_note`, and `purpose` fields.

These values are stored with:

- `config_snapshots`
- `decisions`
- `orders`
- `fills`
- `manual_order_requests`
- export `summary.json` / `metadata.json`

Suggested naming:

- `cheap_random_bootstrap_v1`
- `expansion_100_safe`
- `target_band_test_v2`
- `reentry_tightening_v1`

When comparing experiments, group by `run_id` or `experiment_name` first, then drill down into `config_hash`.

## Decision Context Now Persisted

Decision records include:

- `config_hash`
- `run_id`
- `experiment_name`
- `risk_profile`
- `lot_sizing_mode`
- `lot_sizing_bucket`
- `lot_unit_amount`
- `max_symbol_amount`
- `max_lots_per_symbol`
- `add_buy_lot_band`
- `add_buy_drop_rate`
- `target_profit_lot_band`
- `current_base_target_profit_rate`
- `effective_target_profit_rate`
- normal/trailing reentry rates
- stale/review/cleanup settings
- current open LOT count
- reference buy values
- position PnL values
- action/skip/final block state
- global risk limits

Market snapshot fields are also reserved in the decision payload:

- `sampled_price_source`
- `sampled_at`
- `previous_close`
- `day_open`
- `day_high`
- `day_low`
- `volume`
- `trading_value`
- `bid_price`
- `ask_price`
- `spread`
- `spread_rate`
- `price_context_available`
- `price_context_missing_fields`
- `price_context_error`
- `price_context_collected_at`

During normal decision logging, current price and sampling metadata are always filled when a price sample exists. OHLCV fields may be null because the live decision path currently uses the lightweight `Quote` model. Run `collect_market_data.py` to fill KIS quote OHLCV snapshots for configured symbols.

This is intentionally broader than what is needed for the live strategy, because it preserves analysis context.

## Collect Market Data

Dry-run current quote collection for enabled config symbols:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\collect_market_data.py --config config\lot_auto_trader.json --symbols-from-config --snapshot --daily --dry-run
```

Execute read-only quote collection and save to SQLite:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\collect_market_data.py --config config\lot_auto_trader.json --symbols-from-config --snapshot --daily --execute
```

Collect one code:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\collect_market_data.py --config config\lot_auto_trader.json --code 005930 --snapshot --daily --execute
```

Safety:

- Uses the read-only KIS `inquire-price` quote endpoint.
- Does not call KIS order APIs.
- Does not mutate orders, fills, lots, positions, or reset state.
- `--dry-run` is non-mutating.
- Rate limiting is controlled with `--sleep-seconds`.

Current limitation: `--daily --days 60` is accepted for workflow compatibility, but the existing quote path only provides a current-day OHLCV snapshot. Historical multi-day daily prices require a separate read-only historical price endpoint.

## Recommended Collection Flow

Market data must be accumulated continuously. A single one-off export is useful for debugging, but it is not enough for later what-if analysis.

| Timing | Recommended action | Why |
| --- | --- | --- |
| Bot Core decision time | Automatic `price_snapshots` are saved by Bot Core. | Preserves the actual price used when the bot decided to buy, sell, skip, or block. |
| Before market open | Optional dry-run quote collection. | Confirms credentials/rate limits before trading starts without mutating the DB. |
| During market hours | Optional lightweight snapshot collection, not too frequent. | Adds extra intraday context for active symbols, but should be rate-limit conservative. |
| After market close | Run `collect_market_data.py --symbols-from-config --snapshot --daily --execute`. | Captures current-day OHLCV-like quote fields for all enabled symbols. |
| Before analysis export | Run export after the latest market collection. | Ensures `summary.json` includes recent market data counts. |

Minimum recommended frequency:

- Bot decision snapshots: automatic, every evaluated symbol decision.
- Daily/current-day quote snapshots: once after market close.
- Extra intraday snapshots: optional. If used, keep intervals conservative to avoid KIS quote rate limits.

For low-priced or penny-stock style tests, `price_snapshots` and `daily_prices` are especially important because small absolute price moves can create large percentage changes, and liquidity quality can matter more than headline return. Without volume/trading-value history, later tuning cannot distinguish a profitable but illiquid config from a realistically tradable one.

Windows Task Scheduler example for after-market collection:

```powershell
cd C:\MSJ\KIS-MSJ
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe scripts\collect_market_data.py --config config\lot_auto_trader.json --symbols-from-config --snapshot --daily --execute --sleep-seconds 0.35
```

Dry-run versus execute:

- `--dry-run`: may attempt read-only quote lookup, but does not write market data rows.
- `--execute`: writes `price_snapshots` and/or `daily_prices` rows.

Failure review:

- The script prints an `errors` array with `code`, error type, and message.
- Rate-limit or temporary lookup failures should be rerun later rather than worked around with shorter delays.
- Use `--sleep-seconds` to slow collection. For 100 symbols, conservative delays are preferable to missing rows.

Historical daily endpoint status:

- Current codebase has no implemented historical daily price lookup function.
- Existing `KisClient` daily endpoint usage is for execution/fill reconciliation, not historical OHLCV.
- Therefore `--historical-daily --days 60` is not provided yet.
- If a read-only KIS historical OHLCV endpoint is added later, it should upsert into `daily_prices` and remain separate from all order APIs.

## Automatic vs Semi-Automatic Collection

The current intended split is:

- Automatic: Bot Core stores decision-time `price_snapshots`.
- Semi-automatic/manual scheduled: `collect_market_data.py` stores quote-derived snapshots and current-day `daily_prices`.
- Future work: historical `daily_prices`, `minute_prices`, and `liquidity_snapshots`.

This keeps the trading loop light. The bot does not need heavy historical collection inside the live loop.

## Low-Priced Symbol Quality Metrics

The export already contains or preserves inputs for these future quality checks:

| Metric | Current data source |
| --- | --- |
| Trading volume | `daily_prices.volume`, `price_snapshots.volume` when collected through quote script |
| Trading value | `daily_prices.trading_value`, `price_snapshots.trading_value` when collected |
| Price band | `price_lot_band`, `lot_sizing_bucket`, `current_price` |
| Partial fill count | `orders.status`, `fills`, reconciliation logs |
| Unfilled/open order stay time | `orders.requested_at`, `orders.updated_at`, status history approximation |
| SELL delay/failure | SELL `orders`, `fills`, `manual_order_requests`, `order_not_filled` logs |
| Spread | Future `liquidity_snapshots`; currently not collected |

Later analysis can combine these with realized PnL to avoid selecting configs that only work on paper but fail because of poor liquidity or slow exits.

## Export Dataset

Use:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\export_analysis_dataset.py --config config\lot_auto_trader.json
```

Optional filters:

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe scripts\export_analysis_dataset.py --config config\lot_auto_trader.json --date-from 2026-05-27 --date-to 2026-06-10 --code 005930 --config-hash HASH
```

Output:

```text
exports/analysis_dataset_YYYYMMDD_HHMMSS/
  positions.csv
  lots.csv
  orders.csv
  fills.csv
  manual_order_requests.csv
  decisions.csv
  price_snapshots.csv
  daily_prices.csv
  liquidity_snapshots.csv
  skipped_actions.csv
  config_snapshots.jsonl
  metadata.json
  README.md
  summary.json
```

The export is read-only. It does not mutate DB, does not call KIS APIs, and masks sensitive config keys in `config_snapshots.jsonl`.

`skipped_actions.csv` extracts decision rows where a candidate action existed or a skip/block reason was recorded. It is intended for future opportunity-cost analysis, but true N-day opportunity cost still requires future price data.

`summary.json` and `metadata.json` include market data counts:

- `price_snapshots_count`
- `daily_prices_count`
- `liquidity_snapshots_count`
- `symbols_with_price_data_count`
- `earliest_price_date`
- `latest_price_date`
- `market_data_missing_symbols`

## Blocked / Skipped Action Follow-Up

`skipped_actions.csv` includes `decision_id` and `price_snapshot_id`, so later analysis can join skipped decisions back to stored market data:

```text
skipped_actions.decision_id -> decisions.id
skipped_actions.price_snapshot_id -> price_snapshots.id
decisions.code + decisions.created_at -> daily_prices.code/date
```

This supports future +1/+3/+5/+10 day opportunity checks once enough `daily_prices` rows exist. MFE/MDD estimates need daily high/low at minimum; intraday precision needs minute prices.

## Metrics For Future Tuning

Recommended metrics:

- total net realized PnL
- realized return %
- open unrealized PnL
- win rate
- average profit per closed LOT
- average holding days
- turnover
- max drawdown estimate
- max capital used
- capital efficiency
- stale LOT count
- REVIEW_REQUIRED count
- SYNC_REQUIRED count
- average/max open LOT count
- cleanup loss used
- manual intervention count
- blocked action opportunity cost
- profit per capital-day

Example objective:

```text
score = net_realized_pnl
        - penalty(max_capital_used)
        - penalty(stale_lot_count)
        - penalty(review_required_count)
        - penalty(drawdown)
```

## Minimum Data Before Serious Tuning

Avoid overfitting. A few fills are not enough.

Recommended minimum:

- 2 to 4 weeks of live/paper operation
- at least 100 fills
- at least 30 closed LOTs
- multiple market days and sectors
- at least one config snapshot per meaningful config change
- when comparing several config hashes or run IDs, enough samples per config/run rather than only enough samples in total

## Remaining Limits

- Without historical price paths, alternative config simulation is approximate.
- Limit-order fill probability cannot be reconstructed from fills alone.
- Missed opportunity analysis requires blocked decisions plus future prices.
- Bid/ask/spread analysis is future work until an orderbook/liquidity snapshot collector is added.
- Manual interventions should be analyzed separately from fully automatic decisions.
