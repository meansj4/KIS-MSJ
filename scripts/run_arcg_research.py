"""Offline ARCG research, chronological selection, reports and review package.

Never imports a broker client or starts AutoTrader. Run from the repository root:
  .venv/Scripts/python -m scripts.run_arcg_research
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sqlite3
import subprocess
import sys
import time
import traceback
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
_WORKER_DATA = None


def _trial(payload):
    """Workers own independent legacy clocks and reuse only causal indicators."""
    global _WORKER_DATA
    from kis_msj.arcg_backtest import load_daily_dataset, run_backtest
    database, index, baseline, model, split, tag, params = payload
    if _WORKER_DATA is None:
        _WORKER_DATA = load_daily_dataset(database, index, config_path=baseline)
    begin = time.perf_counter()
    try:
        result = run_backtest(_WORKER_DATA, model, params)
        return tag, model, split, result, {"tag": tag, "model": model, "split": split,
                "seconds": time.perf_counter() - begin, "status": "completed",
                "summary": result.summary, "diagnostics": result.diagnostics}
    except Exception as error:
        return tag, model, split, None, {"tag": tag, "model": model, "split": split,
                "seconds": time.perf_counter() - begin, "status": "failed", "error": str(error),
                "traceback": traceback.format_exc()}


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], columns=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({key: json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list, tuple)) else value
                         for key, value in row.items()} for row in rows)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def export_inputs(database: Path, index: Path, out: Path) -> tuple[Path, Path]:
    """Copy ONLY the public-price table; no account/order/database backup."""
    target = out / "data" / "research_daily.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as source:
            schema = source.execute("SELECT sql FROM sqlite_master WHERE name='historical_daily_prices'").fetchone()[0]
            rows = source.execute("SELECT * FROM historical_daily_prices WHERE data_source=?", ("kis_historical_daily_readonly",))
            with sqlite3.connect(target) as dest:
                dest.execute(schema)
                n = len(rows.description)
                dest.executemany("INSERT INTO historical_daily_prices VALUES(" + ",".join("?" for _ in range(n)) + ")", rows)
    index_target = target.parent / "kospi_index.csv"
    index_target.write_bytes(index.read_bytes())
    return target, index_target


def metric(summary: dict, *names: str, default=0.0) -> float:
    for name in names:
        value = summary.get(name)
        if isinstance(value, (int, float)) and math.isfinite(value):
            return float(value)
    return default


def selection_score(summary: dict) -> float:
    """Predeclared NAV/MDD score; rates are fractions, no test metric accepted."""
    if summary.get("error") or metric(summary, "minimum_cash", default=0) < -0.01:
        return -1e9
    cagr = metric(summary, "cagr_pct") / 100
    mdd = -abs(metric(summary, "max_drawdown_pct")) / 100
    blocked = metric(summary, "cash_shortfall_blocks", "cash_shortfall_count", "cash_blocked_count", "cash_blocked_buys")
    return cagr + 0.5 * mdd - 0.000001 * blocked


def candidate_settings() -> list[tuple[str, dict]]:
    # Predeclared one-factor controls, including deliberately worse candidates.
    # All values are experimental. The final test partition never selects them.
    return [
        ("base", {}),
        ("safety_10", {"safety_cash_fraction": .10}),
        ("safety_25", {"safety_cash_fraction": .25}),
        ("atr_075", {"atr_gap_multiplier": .75}),
        ("atr_200", {"atr_gap_multiplier": 2.0}),
        ("rotation_only", {"trend_fraction": 0.0}),
        ("trend_25", {"trend_fraction": .25}),
        ("trend_75", {"trend_fraction": .75}),
        ("trail_04", {"trailing_fraction": .04}),
        ("trail_10", {"trailing_fraction": .10}),
        ("anchor_average", {"reference_mode": "average"}),
        ("anchor_legacy_min", {"reference_mode": "legacy_average"}),
        ("anchor_last", {"reference_mode": "last_buy"}),
        ("immediate_addition", {"stabilization_required": False}),
        ("without_trend_risk_exit", {"trend_risk_exit_enabled": False}),
    ]


def run(args) -> None:
    from kis_msj.arcg import ARCGConfig
    from kis_msj.arcg_backtest import load_daily_dataset, run_backtest

    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    source_db, source_index = export_inputs(Path(args.database).resolve(), Path(args.index).resolve(), out)
    baseline = out / "legacy_strategy_settings.json"
    if not baseline.exists():
        raise RuntimeError("Sanitized current-policy legacy baseline is missing")
    dataset = load_daily_dataset(source_db, source_index, config_path=baseline)
    splits = {"train": ("2010-01-04", "2017-12-31"),
              "validation": ("2018-01-01", "2021-12-31"),
              "test": ("2022-01-01", "2026-05-29")}
    common = {"capital": 100_000_000, "slippage_bps": 5, "buy_fee_bps": 1.5,
              "volume_participation": 0.001, "max_mark_stale_sessions": 5,
              "gap_quarantine_threshold": 0.35}
    settings = candidate_settings()
    config = {"seed": 0, "randomness": "none; deterministic code ordering", "splits": splits,
              "common": common, "candidates": settings, "selection": "validation CAGR - .5*abs(MDD) - 1e-6*cash blocks; train only diagnostics",
              "holdout_limit": "retrospective local history, not pristine prospective holdout",
              "default_arcg": asdict(ARCGConfig()), "live_trading": False, "worker_processes": args.jobs,
              "post_selection_cost_stress_slippage_bps": [10, 20],
              "source_sha256": {str(p.relative_to(ROOT)): file_hash(p) for p in sorted((ROOT/"src/kis_msj").glob("*.py"))}}
    write_json(out / "backtest_config.json", config)
    write_json(out / "data_manifest.json", {"loader": dataset.manifest,
               "sector_assumption": "2026 current code/sector labels, not point-in-time; unlabelled codes share UNKNOWN",
               "sector_map": {code: dataset.sectors.get(code, "UNKNOWN") for code in dataset.codes},
               "input_files": [{"path": str(p.relative_to(out)), "sha256": file_hash(p), "bytes": p.stat().st_size}
                               for p in (source_db, source_index, baseline)]})
    log = []
    optimization = []
    cache = {}

    executor = ProcessPoolExecutor(max_workers=args.jobs)

    def batch(trials, execution=None):
        futures = {}
        for tag, model, split, overrides in trials:
            start, end = splits[split]
            params = dict(common, start=start, end=end, arcg=overrides)
            params.update(execution or {})
            future = executor.submit(_trial, (source_db, source_index, baseline, model, split, tag, params))
            futures[future] = (tag, model, split)
        for future in as_completed(futures):
            tag, model, split, result, entry = future.result()
            log.append(entry)
            if result:
                cache[(tag, model, split)] = result
                print(f"DONE {tag} model={model} split={split} sec={entry['seconds']:.1f}", flush=True)
            else:
                print(f"FAILED {tag} model={model} split={split}: {entry['error']}", flush=True)
            write_json(out / "experiment_log.json", log)

    # Never call the final-test partition in the search loop.
    batch([(tag, "F", split, overrides) for tag, overrides in settings for split in ("train", "validation")])
    for tag, overrides in settings:
        for split in ("train", "validation"):
            result = cache.get((tag, "F", split))
            row = {"candidate": tag, "model": "F", "split": split, "start": splits[split][0],
                   "end": splits[split][1], "parameters": overrides, "status": "completed" if result else "failed"}
            if result:
                row.update(result.summary)
                row["selection_score"] = selection_score(result.summary) if split == "validation" else ""
            optimization.append(row)
        write_csv(out / "04_optimization_results.csv", optimization)
        write_json(out / "experiment_log.json", log)
    valid = [row for row in optimization if row["split"] == "validation" and row["status"] == "completed"]
    if not valid:
        raise RuntimeError("All validation experiments failed; see experiment_log.json")
    selected = max(valid, key=lambda row: row["selection_score"])
    overrides = dict(selected["parameters"])
    validation_results = {}
    batch([("selected", model, "validation", overrides) for model in "ABCDE"])
    for model in "ABCDEF":
        if model == "F":
            result = cache[(selected["candidate"], "F", "validation")]
        else:
            result = cache.get(("selected", model, "validation"))
        if result:
            validation_results[model] = result
    chosen_model = max(validation_results, key=lambda model: selection_score(validation_results[model].summary))
    # Commit selection to disk BEFORE any final test execution.
    selection = {"candidate": selected["candidate"], "arcg": asdict(ARCGConfig(**overrides)),
                 "model_selected_on_validation": chosen_model,
                 "validation_scores": {m: selection_score(r.summary) for m,r in validation_results.items()},
                 "deployment_recommendation": "none: raw prices/universe/sector/production adapter require verification"}
    write_json(out / "selection_before_test.json", selection)
    selection_frozen_hash = file_hash(out / "selection_before_test.json")
    results = {}
    batch([("final", model, "test", overrides) for model in "ABCDEF"])
    for model in "ABCDEF":
        result = cache.get(("final", model, "test"))
        if result:
            results[model] = result
    if len(results) != 6:
        raise RuntimeError("One or more A-F final comparisons failed")
    rows = []
    for split, mapping in (("validation", validation_results), ("test", results)):
        for model, result in mapping.items():
            rows.append(dict(result.summary, model=model, split=split,
                             selected_before_test=model == chosen_model))
    write_csv(out / "03_backtest_comparison.csv", rows)
    for filename, attribute in (("05_equity_curve.csv", "equity"), ("06_trade_history.csv", "trades"),
                                ("annual_returns.csv", "annual"), ("monthly_returns.csv", "monthly")):
        records = [dict(row, model=model, split="test") for model,result in results.items() for row in getattr(result,attribute)]
        write_csv(out / filename, records)
    # Predeclared execution sensitivity is reporting only. It cannot change the
    # selected model/parameters, even if a different test outcome looks better.
    stress_rows = []
    for cost in (10, 20):
        tag = f"cost_stress_slippage_{cost}"
        stress_models = sorted({"A", chosen_model})
        batch([(tag, model, "test", overrides) for model in stress_models], execution={"slippage_bps": cost})
        for model in stress_models:
            result = cache.get((tag, model, "test"))
            stress_rows.append(dict(result.summary if result else {}, model=model,
                                    slippage_bps=cost, purpose="post_selection_execution_sensitivity_no_retuning"))
    if file_hash(out / "selection_before_test.json") != selection_frozen_hash:
        raise RuntimeError("Selection was changed after the final test")
    write_csv(out / "execution_cost_sensitivity.csv", stress_rows)
    write_json(out / "experiment_log.json", log)
    executor.shutdown(wait=True)
    write_json(out / "research_results.json", {"selection": selection, "test": {m:r.summary for m,r in results.items()},
              "validation": {m:r.summary for m,r in validation_results.items()}, "runtime_seconds":sum(x["seconds"] for x in log),
              "completed_experiments": len(log), "selection_frozen_sha256": selection_frozen_hash,
              "cost_sensitivity": stress_rows, "data_limitations": dataset.manifest})
    print("RESEARCH COMPUTATIONS COMPLETE", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(ROOT / "data/backtest_historical.sqlite3"))
    parser.add_argument("--index", default=str(ROOT / "data/kospi_index_daily_2010_present.csv"))
    parser.add_argument("--output", default=str(ROOT / "analysis_output"))
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
