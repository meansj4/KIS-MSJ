"""Verify exported results without broker access or recomputing a strategy."""
import csv
import hashlib
import json
import sqlite3
from pathlib import Path

OUT = Path(__file__).resolve().parent


def read(name):
    return json.loads((OUT / name).read_text(encoding="utf-8-sig"))


def rows(name):
    with (OUT / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


checks = {}
results, logs, selection = read("research_results.json"), read("experiment_log.json"), read("selection_before_test.json")
assert len(logs) == 45 and all(log["status"] == "completed" for log in logs)
checks["completed_primary_experiments"] = 45
assert digest(OUT / "selection_before_test.json") == results["selection_frozen_sha256"]
capital = read("capital_stress_results.json")
assert len(capital["rows"]) == 4 and all(log["status"] == "completed" for log in capital["logs"])
assert capital["selection_frozen_sha256"] == results["selection_frozen_sha256"]
checks["completed_posthoc_capital_diagnostics"] = 4
checks["selection_unchanged_after_test_and_stress"] = True
for item in read("data_manifest.json")["input_files"]:
    assert digest(OUT / item["path"]) == item["sha256"]
source = OUT / "source" if (OUT / "source/src").exists() else OUT.parent
for path, expected in read("backtest_config.json")["source_sha256"].items():
    assert digest(source / path) == expected, path
checks["input_and_frozen_simulation_source_hashes_match"] = True
equity = rows("05_equity_curve.csv")
assert {row["model"] for row in equity} == set("ABCDEF")
for model in "ABCDEF":
    curve = [row for row in equity if row["model"] == model]
    assert len(curve) == results["test"][model]["trading_sessions"]
    assert [row["date"] for row in curve] == sorted({row["date"] for row in curve})
    peak = results["test"][model]["initial_equity"]
    for row in curve:
        nav, cash, stock = (float(row[key]) for key in ("equity", "cash", "stock_value"))
        assert cash >= 0 and abs(nav - cash - stock) < .01
        assert abs(nav - 100_000_000 - float(row["realized_pnl"]) - float(row["unrealized_pnl"])) < .01
        peak = max(peak, nav)
        assert abs(float(row["drawdown_pct"]) - (nav / peak - 1) * 100) < 1e-8
    assert abs(float(curve[-1]["equity"]) - results["test"][model]["final_equity"]) < .01
checks["daily_nav_cash_pnl_drawdown_and_summary_reconcile"] = len(equity)
trades = rows("06_trade_history.csv")
for trade in trades:
    quantity, amount, fee = (float(trade[key]) for key in ("quantity", "amount", "fees_tax"))
    assert quantity > 0 and int(quantity) == quantity
    assert trade["signal_date"] < trade["datetime"][:10]
    sign = -1 if trade["side"] == "BUY" else 1
    assert abs(float(trade["cash_after"]) - float(trade["cash_before"]) - sign * amount + fee) < .01
    if trade["model"] in "EF" and trade["side"] == "SELL" and trade["reason"].startswith("profit_"):
        assert float(trade["realized_pnl"]) >= 0
checks["trades_integer_next_session_cash_conservation_and_normal_net_profit"] = len(trades)
optimization = rows("04_optimization_results.csv")
assert sum(row["split"] == "test" for row in optimization) == 6
assert all(row["selection_score"] == "not_used_for_selection" for row in optimization if row["split"] == "test")
checks["final_test_rows_excluded_from_parameter_selection"] = True
with sqlite3.connect((OUT / "data/research_daily.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as con:
    assert {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"historical_daily_prices"}
baseline = read("legacy_strategy_settings.json")
assert not baseline["order"]["live_trading"] and "kis_account" not in baseline
checks["shared_data_contains_price_table_only_and_paper_config"] = True
(OUT / "validation_checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(checks, ensure_ascii=False, indent=2))
