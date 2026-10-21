from scripts.run_arcg_research import candidate_settings, selection_score


def test_selection_uses_documented_percentage_metrics():
    assert selection_score({"cagr_pct": 10, "max_drawdown_pct": -20}) == 0
    assert selection_score({"cagr_pct": 10, "max_drawdown_pct": -10}) > selection_score(
        {"cagr_pct": 12, "max_drawdown_pct": -20})
    assert selection_score({"cagr_pct": 20, "minimum_cash": -1}) == -1e9


def test_predeclared_controls_include_anchor_and_stabilization_comparisons():
    controls = dict(candidate_settings())
    assert controls["anchor_legacy_min"]["reference_mode"] == "legacy_average"
    assert controls["anchor_last"]["reference_mode"] == "last_buy"
    assert controls["immediate_addition"]["stabilization_required"] is False
    assert controls["rotation_only"]["trend_fraction"] == 0
