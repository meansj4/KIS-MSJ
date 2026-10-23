"""Offline calendar evidence for tests; never makes broker requests."""
from datetime import date, timedelta
import pytest
from kis_msj.trading_calendar import TradingCalendar

REAL_IS_SESSION = TradingCalendar.is_session


@pytest.fixture(autouse=True)
def offline_calendar(monkeypatch, request):
    if request.node.path.name == 'test_trading_policy_v2.py':
        return  # New policy tests provide explicit sessions, including missing data.
    def is_session(self, day):
        if day.isoformat() in self.days:
            return self.days[day.isoformat()]
        # Deliberately synthetic schedule for legacy fixtures, not a KRX claim.
        return day.weekday() < 5
    monkeypatch.setattr(TradingCalendar, 'is_session', is_session)
