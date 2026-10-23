from datetime import date, datetime, timedelta, timezone
import math
from statistics import stdev

import pytest

from kis_msj.kis_client import ADJUSTED_DAILY_CLOSE_PATH, KisClient, MockKisClient
from kis_msj.volatility import (
    DailyVolatilityCache, calculate_volatility, completed_close_cutoff,
    volatility_multiplier,
)


def _prices():
    prices = [100.0]
    for index in range(60):
        prices.append(prices[-1] * math.exp(((index % 7) - 3) * 0.006))
    return prices


def _bars(end_day):
    return [(end_day - timedelta(days=60 - index), close) for index, close in enumerate(_prices())]


def test_volatility_reference_examples_and_volatility_only_clamp():
    assert volatility_multiplier(14.7 * 0.3 + 20.0 * 0.7) == pytest.approx(1.0)
    assert volatility_multiplier(125 * 0.3 + 117 * 0.7) == pytest.approx(2.0, rel=1e-9)
    assert volatility_multiplier(0) == 0.75
    assert volatility_multiplier(10_000) == 2.5
    assert 4 * volatility_multiplier(10_000) * 3.2 == 32


def test_sample_log_return_std_uses_latest_61_chronological_closes():
    prices = _prices()
    returns = [math.log(b / a) for a, b in zip(prices, prices[1:])]
    estimate = calculate_volatility([0.001] + prices)
    assert estimate.reason == "ok"
    assert estimate.close_count == 61
    assert estimate.vol20 == pytest.approx(stdev(returns[-20:]) * math.sqrt(252) * 100)
    assert estimate.vol60 == pytest.approx(stdev(returns) * math.sqrt(252) * 100)
    assert estimate.weighted_volatility == pytest.approx(estimate.vol20 * .3 + estimate.vol60 * .7)
    assert estimate.gap_pct == estimate.multiplier * 4


@pytest.mark.parametrize("closes", [[], [100.0] * 60, [100.0] * 60 + [0], [100.0] * 60 + [float("nan")]])
def test_missing_or_invalid_history_uses_base_gap(closes):
    estimate = calculate_volatility(closes)
    assert estimate.multiplier == 1.0
    assert estimate.gap_pct == 4.0


def test_completed_cutoff_excludes_intraday_bar_and_handles_korean_time_weekends():
    assert completed_close_cutoff(datetime(2026, 10, 23, 15, 29)) == date(2026, 10, 22)
    assert completed_close_cutoff(datetime(2026, 10, 23, 15, 30)) == date(2026, 10, 23)
    assert completed_close_cutoff(datetime(2026, 10, 26, 9)) == date(2026, 10, 23)
    assert completed_close_cutoff(datetime(2026, 10, 24, 10)) == date(2026, 10, 23)
    assert completed_close_cutoff(datetime(2026, 10, 23, 6, 30, tzinfo=timezone.utc)) == date(2026, 10, 23)
    assert completed_close_cutoff(datetime(2026, 10, 23, 15, 30), "16:30") == date(2026, 10, 22)


def test_cache_frozen_intraday_restart_and_refreshed_after_close(tmp_path):
    class Provider:
        calls = []
        def adjusted_daily_closes(self, code, *, end_date, count):
            self.calls.append((code, end_date, count))
            return _bars(end_date)

    provider = Provider()
    path = tmp_path / "state.volatility.json"
    cache = DailyVolatilityCache(path)
    first = cache.refresh("005930", provider, datetime(2026, 10, 23, 9))
    assert first.as_of == "2026-10-22"
    assert first.reason == "ok"
    assert cache.refresh("005930", provider, datetime(2026, 10, 23, 15, 29)) == first
    restarted = DailyVolatilityCache(path)
    assert restarted.multiplier("005930") == first.multiplier
    assert restarted.refresh("005930", provider, datetime(2026, 10, 23, 14)) == first
    assert len(provider.calls) == 1
    after = restarted.refresh("005930", provider, datetime(2026, 10, 23, 15, 30))
    assert len(provider.calls) == 2
    assert after.as_of == "2026-10-23"
    restarted.refresh("005930", provider, datetime(2026, 10, 26, 9))
    assert len(provider.calls) == 2


def test_cache_filters_future_dates_deduplicates_and_fallback_survives_restart(tmp_path):
    class Provider:
        calls = 0
        def adjusted_daily_closes(self, code, *, end_date, count):
            self.calls += 1
            return _bars(end_date)[1:] + [(end_date, 10)] + [(end_date + timedelta(days=1), 10)]

    provider = Provider()
    path = tmp_path / "cache.json"
    first = DailyVolatilityCache(path).refresh("005930", provider, datetime(2026, 10, 23, 9))
    assert first.close_count == 60
    assert first.multiplier == 1
    assert DailyVolatilityCache(path).refresh("005930", provider, datetime(2026, 10, 23, 10)) == first
    assert provider.calls == 1


def test_failed_request_has_persistent_base_gap_and_does_not_retry_intraday(tmp_path):
    class Provider:
        calls = 0
        def adjusted_daily_closes(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("unavailable")

    provider = Provider()
    path = tmp_path / "cache.json"
    cache = DailyVolatilityCache(path)
    estimate = cache.refresh("005930", provider, datetime(2026, 10, 23, 9))
    assert estimate.reason == "daily_data_unavailable"
    assert estimate.multiplier == 1
    assert DailyVolatilityCache(path).refresh("005930", provider, datetime(2026, 10, 23, 10)) == estimate
    assert provider.calls == 1


@pytest.mark.parametrize("contents", ["invalid json", "[]", '{"version":1,"symbols":{"005930":{}}}'])
def test_invalid_cache_file_safely_uses_default(tmp_path, contents):
    path = tmp_path / "cache.json"
    path.write_text(contents, encoding="utf-8")
    assert DailyVolatilityCache(path).multiplier("005930") == 1


def test_kis_daily_provider_uses_adjusted_krx_data_and_paginates_with_prior_date():
    client = KisClient.__new__(KisClient)
    end = date(2026, 10, 22)
    bars = _bars(end)
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        selected = bars[-30:] if len(calls) == 1 else bars[:-30]
        rows = [{"stck_bsop_date": day.strftime("%Y%m%d"), "stck_clpr": str(close)} for day, close in reversed(selected)]
        rows += [{"stck_bsop_date": "20261023", "stck_clpr": "100000"}, {"stck_bsop_date": "", "stck_clpr": ""}]
        return {"output2": rows}
    client._request = request
    result = client.adjusted_daily_closes("005930", end_date=end)
    assert result == bars
    assert len(calls) == 2
    method, path, kwargs = calls[0]
    assert (method, path, kwargs["tr_id"]) == ("GET", ADJUSTED_DAILY_CLOSE_PATH, "FHKST03010100")
    assert kwargs["params"]["FID_ORG_ADJ_PRC"] == "0"
    assert kwargs["params"]["FID_PERIOD_DIV_CODE"] == "D"
    assert kwargs["params"]["FID_COND_MRKT_DIV_CODE"] == "J"
    assert kwargs["params"]["FID_INPUT_DATE_2"] == "20261022"
    assert calls[1][2]["params"]["FID_INPUT_DATE_2"] == (bars[-30][0] - timedelta(days=1)).strftime("%Y%m%d")


def test_kis_daily_provider_does_not_fill_invalid_prices_or_loop_forever():
    client = KisClient.__new__(KisClient)
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return {"output2": [
            {"stck_bsop_date": "20261022", "stck_clpr": "0"},
            {"stck_bsop_date": "20261021", "stck_clpr": "NaN"},
        ]}
    client._request = request
    assert client.adjusted_daily_closes("005930", end_date=date(2026, 10, 22)) == []
    assert len(calls) == 2


def test_mock_daily_history_stays_offline():
    assert MockKisClient().adjusted_daily_closes("005930", end_date=date(2026, 10, 22)) == []


def test_closed_market_bot_loop_still_refreshes_completed_volatility(tmp_path, monkeypatch):
    from kis_msj import main as trader_module
    from kis_msj.config import BotConfig, OrderConfig, StockConfig, StrategyConfig
    from kis_msj.models import AccountSnapshot

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 23, 16, tzinfo=tz)

    config = BotConfig(
        stocks=(StockConfig("005930", "Test"),),
        strategy=StrategyConfig(adaptive_lot_enabled=True),
        order=OrderConfig(price_sample_interval_seconds=0),
        storage_path=str(tmp_path / "state.db"), log_path=str(tmp_path / "bot.log"),
    )
    trader = trader_module.AutoTrader(config, use_mock_client=True)
    requests = []
    trader.client.adjusted_daily_closes = lambda code, **kwargs: requests.append(kwargs) or _bars(kwargs["end_date"])
    trader.runtime_interrupt_reason = lambda stage: ""
    trader.reconcile_recent_executions_on_startup = lambda: None
    trader.reconcile_open_orders = lambda: None
    trader.startup_sync = lambda: AccountSnapshot(1_000_000, 1_000_000, 0, 0, ())
    trader.process_manual_order_requests = lambda *args: None
    monkeypatch.setattr(trader_module, "datetime", Clock)
    monkeypatch.setattr(trader_module, "in_trade_window", lambda config: False)
    monkeypatch.setattr(trader_module, "extended_window", lambda config: False)
    trader.run_once()
    trader.run_once()
    assert len(requests) == 1
    assert requests[0]["end_date"] == date(2026, 10, 23)
    assert trader.lot_manager.volatility_multiplier_for("005930") == calculate_volatility(_prices()).multiplier
