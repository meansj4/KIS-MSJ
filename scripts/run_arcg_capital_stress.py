"""Post-selection funding stress; never chooses or changes a trading model.

This is an explicitly post-hoc diagnostic prompted by the primary study having
no direct cash exhaustion at 100m KRW. It is not an additional validation search.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from scripts.run_arcg_research import ROOT, _trial, file_hash, write_csv, write_json


def run(out: Path):
    out = out.resolve()
    selection_path = out / "selection_before_test.json"
    selection_hash = file_hash(selection_path)
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    primary = json.loads((out / "backtest_config.json").read_text(encoding="utf-8"))
    chosen = selection["model_selected_on_validation"]
    start, end = primary["splits"]["test"]
    params = {key: value for key, value in selection["arcg"].items()}
    summary, equity, trades, logs = [], [], [], []
    with ProcessPoolExecutor(max_workers=3) as pool:
        futures = []
        for capital in (5_000_000, 20_000_000):
            for model in sorted({"A", chosen}):
                tag = f"posthoc_capital_{capital}"
                settings = dict(primary["common"], start=start, end=end, capital=capital, arcg=params)
                futures.append(pool.submit(_trial, (out / "data/research_daily.sqlite3", out / "data/kospi_index.csv",
                              out / "legacy_strategy_settings.json", model, "posthoc_capital_stress", tag, settings)))
        for future in as_completed(futures):
            tag, model, split, result, log = future.result()
            logs.append(log)
            if result is None:
                continue
            capital = result.summary["initial_equity"]
            summary.append(dict(result.summary, diagnostic="posthoc_no_selection_or_tuning", capital=capital))
            equity.extend(dict(row, stress_capital=capital) for row in result.equity)
            trades.extend(dict(row, stress_capital=capital) for row in result.trades)
            print(f"DONE {tag} model={model}", flush=True)
    if file_hash(selection_path) != selection_hash:
        raise RuntimeError("The original model selection changed during funding stress")
    write_csv(out / "capital_stress_comparison.csv", sorted(summary, key=lambda r: (r["capital"], r["model"])))
    write_csv(out / "capital_stress_equity.csv", equity)
    write_csv(out / "capital_stress_trades.csv", trades)
    write_json(out / "capital_stress_results.json", {"purpose": __doc__, "selection_frozen_sha256": selection_hash,
               "completed_experiments": len(summary), "rows": summary, "logs": logs})
    if len(summary) != 4:
        raise RuntimeError("At least one capital stress experiment failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "analysis_output")
    run(parser.parse_args().output)
