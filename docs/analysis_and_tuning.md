# Analysis And Tuning Data Guide

Authoritative source: `docs/project_handoff_full.md` remains the full baseline. This document explains what data should be accumulated so Codex/ChatGPT can later review DB/log history and suggest safer config tuning.

Last updated: 2026-05-27  
Baseline profile: `expansion_100_safe`  
Safety note: analysis/export must not call KIS order APIs, must not place orders, and must not reset DB.

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

Currently, only the available sampled current price and sampling metadata are filled. Missing market fields are left blank until daily/minute/liquidity collection is added.

This is intentionally broader than what is needed for the live strategy, because it preserves analysis context.

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
  skipped_actions.csv
  config_snapshots.jsonl
  metadata.json
  README.md
  summary.json
```

The export is read-only. It does not mutate DB, does not call KIS APIs, and masks sensitive config keys in `config_snapshots.jsonl`.

`skipped_actions.csv` extracts decision rows where a candidate action existed or a skip/block reason was recorded. It is intended for future opportunity-cost analysis, but true N-day opportunity cost still requires future price data.

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
- Manual interventions should be analyzed separately from fully automatic decisions.
