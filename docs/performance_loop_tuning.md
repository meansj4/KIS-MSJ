# Bot Core Loop Performance Tuning

Last updated: 2026-05-29

This page documents how to measure the LOT auto-trader loop without placing orders.

## Safety

- The benchmark script copies the SQLite DB into `exports/loop_benchmark_*/benchmark_state.sqlite3`.
- It does not reset the production DB.
- It blocks order submission in-process with `benchmark_order_submission_disabled`.
- `--mock-quotes` uses local quote data.
- `--read-only-quotes` may call KIS inquiry APIs only; buy/sell/cancel APIs must not be used.

## Commands

Fast local baseline:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_loop.py --config config\lot_auto_trader.json --symbols 120 --iterations 5 --mock-quotes --price-sample-interval 0
```

Current sampling policy check on a small subset:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_loop.py --config config\lot_auto_trader.json --symbols 3 --iterations 1 --mock-quotes --price-sample-interval -1
```

Read-only KIS quote/account inquiry benchmark:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_loop.py --config config\lot_auto_trader.json --symbols 3 --iterations 1 --read-only-quotes --price-sample-interval -1
```

## Profiling Logs

Each active bot loop writes a `loop_profile` log line with:

- `loop_id`
- `loop_duration_ms`
- `symbols_total`, `symbols_processed`, `symbols_skipped`
- `avg_symbol_duration_ms`, `p50_symbol_duration_ms`, `p95_symbol_duration_ms`, `max_symbol_duration_ms`
- `quote_fetch_duration_ms`
- `decision_duration_ms`
- `db_duration_ms`
- `reconciliation_duration_ms`
- `manual_request_duration_ms`
- `order_guard_duration_ms`
- `decision_logging_duration_ms`
- `sleep_duration_ms`
- `bottleneck_stage`
- `loop_over_interval`

The local UI dashboard shows recent loop performance from these logs.

## 2026-05-29 Measurement

Environment: Windows local workspace, copied DB, 120 configured symbols, 119 processed, mock quotes, order submission blocked.

Fast baseline with `price_sample_interval_seconds=0`:

- 5 iterations
- average loop: `9.10s`
- p50 loop: `7.42s`
- p95/max loop: `15.43s`
- average symbol: `40.37ms`
- common bottlenecks: `decision_logging`, `db`

Current sampling policy subset with config value `price_sample_count=5`, `price_sample_interval_seconds=2`:

- 3 symbols, 1 iteration
- loop: `25.09s`
- average symbol: `8.05s`
- quote fetch: `24.01s`

Projected 120-symbol current-policy loop:

- 119 enabled symbols x about `8.0s` sampling sleep each = about `952s`
- plus baseline DB/logging/account overhead = roughly `16 minutes`

This projection is deterministic because the sampler sleeps between samples: 5 samples create 4 sleeps, so `4 * 2s = 8s` per evaluated symbol.

## Loop Interval

Current config:

- `loop_interval_seconds=3`
- `order.price_sample_count=5`
- `order.price_sample_interval_seconds=2`

Loops do not overlap. The next loop starts only after the current loop finishes and then `responsive_sleep()` completes. If a loop duration exceeds the interval, the bot logs `loop_duration_exceeded_interval`.

With the current sampling policy, `3s` is much shorter than actual loop duration. It does not create overlap, but it means the bot immediately starts the next loop after long work completes.

## Safe Optimization Ideas

Safe candidates:

- Reduce decision log verbosity or write compact loop-mode decision logs.
- Avoid saving unchanged positions during evaluate.
- Avoid repeated empty manual request polling for every symbol; poll once per loop plus wake-up checks.
- Cache per-loop open order summaries used by guard logging.
- Keep UI dashboard aggregation outside the Bot Core loop.
- Run reconciliation on a controlled cadence when there are no open orders, while keeping startup and post-cancel checks.

Use caution:

- Reducing quote sampling reduces volatility confirmation.
- Caching open order checks too aggressively can allow duplicate orders.
- Stretching reconciliation delays mismatch detection.
- Slowing manual request polling delays manual actions.
- Slowing runtime-control polling delays emergency pause response.

Do not remove:

- fill/reconciliation checks
- open order guards
- runtime pause checks
- `SYNC_REQUIRED`, `REVIEW_REQUIRED`, `RISK_BLOCKED` guards
- final pre-submit guard
- `record_fill()` dedupe
- Bot Core-only order submission path

## Practical Target

Mock fast baseline target after safe logging/DB optimization:

- realistic: `4-7s` for 120 symbols
- aggressive but still safe: `3-5s` if decision logs are compacted

Current 5-sample/2-second policy:

- realistic: about `16 minutes` for 120 symbols
- lower bound without changing sampling: still about `16 minutes`

To get a safe live loop near `30-60s`, the sampling policy must change. A safer path is to use one fresh quote for normal scanning and reserve multi-sample volatility confirmation for symbols that are about to submit an order. Keep final open-order, runtime, risk, and reconciliation guards intact.
