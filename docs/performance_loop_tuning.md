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

## 2026-05-29 Sampling Optimization

The auto loop no longer performs 5-sample stability checks for every enabled symbol.

Current flow:

1. Scan each enabled symbol with one quote.
2. Build the strategy action candidate from that scan quote.
3. If there is no action candidate, skip stability sampling.
4. If there is a BUY/SELL candidate, perform the configured stability sampling.
5. Fetch one final quote immediately before order request construction.
6. Refresh lots/position, account risk, symbol risk, strategy action, runtime guard, open-order guard, and pre-request guards on the final quote path.
7. Build quantity and limit price from the final quote.
8. Apply fills only after `record_fill()` succeeds, unchanged from the previous safety rule.

Safety guards intentionally left unchanged:

- startup recent execution reconciliation
- open-order reconciliation
- post-cancel execution check
- fill dedupe
- `SYNC_REQUIRED` handling

Post-change mock benchmark with current `2s` sampling interval, 10 symbols:

- loop: `43.03s`
- average symbol: `4.07s`
- quote fetch: `40.01s`
- interpretation: only action candidates pay the 5-sample cost; non-candidate symbols use one quote.

The actual loop duration now depends heavily on how many symbols produce BUY/SELL candidates in that loop.

Operational changes to accept:

- Non-candidate symbols no longer run the 5-sample stability check.
- Candidate symbols can be blocked more conservatively after the final quote.
- BUY quantity, SELL limit price, BUY limit price, and a new/unlocked LOT sizing bucket can change based on the final quote.
- If many symbols become BUY/SELL candidates, the loop can still be long because each candidate pays the stability-sampling cost.

Rollback reference:

- Final optimized commit: `42c37fe` (`loop 시간 줄이기 3차`).
- Pre-optimization baseline: `0e8405d` (`block reason 초기화 안되던 문제 해결`).
- The optimization series is `572247a..42c37fe`.
- Rollback scope: `src/kis_msj/main.py`, `src/kis_msj/config.py`, `src/kis_msj/loop_profile.py`, `scripts/benchmark_loop.py`, `src/kis_msj/ui_service.py`, `src/kis_msj/ui_server.py`, related tests/docs, and the two profiling config keys in `config/lot_auto_trader.json`.

Decision and price snapshot traceability:

- For non-candidate symbols, decision `current_price` and the linked `price_snapshots.current_price` are the one scan quote. `raw_json.sample_count` is `1`.
- For BUY/SELL candidates, the linked decision and price snapshot use the final quote as `current_price`, and `sampled_at` is the final quote timestamp.
- For BUY/SELL candidates, `raw_json.sample_count` is `6`: the configured 5 stability samples plus the final quote. The scan quote is not included in that count.
- The actual order request quantity and limit price are built from this final quote path. The order row remains the authoritative record for submitted quantity/limit price.
- Current limitation: `raw_json` records only the total sample count and does not separately store each stability sample price or label the final quote as a separate nested object. The final quote is still traceable through decision/price snapshot `current_price` and `sampled_at`.

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

That safer path has now been implemented for the automatic loop. Manual order request processing still uses its own stability check path.
