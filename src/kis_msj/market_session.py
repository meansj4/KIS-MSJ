"""Exchange/session plumbing; deliberately contains no trading strategy math.

KIS specifications checked 2026-10-02; see docs/addtime.md for sources and
the conservative treatment of undocumented aftermarket status values.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Callable

from .config import BotConfig
from .models import OrderRequest, Quote

KST = timezone(timedelta(hours=9))


def korea_now() -> datetime:
    return datetime.now(KST).replace(tzinfo=None)


class SessionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Route:
    session: str
    venue: str
    quote_market: str
    order_division: str = "00"


REGULAR = Route("REGULAR", "KRX", "J")
NXT_PRE = Route("NXT_PRE", "NXT", "NX")
NXT_AFTER = Route("NXT_AFTER", "NXT", "NX")
KRX_AFTER = Route("KRX_AFTER", "KRX", "J", "41")


def candidates(config: BotConfig, now: datetime) -> tuple[Route, ...]:
    """One selected venue, never a fan-out. Regular routing is unchanged."""
    if now.tzinfo is not None:
        now = now.astimezone(KST).replace(tzinfo=None)
    if not config.extended_hours.enabled or now.weekday() >= 5:
        return ()
    overrides = config.extended_hours.calendar_overrides.get(now.date().isoformat(), {})
    if overrides.get("closed") == "true":
        return ()
    t = now.time()
    if time(8) <= t < time(8, 50):
        routes = (NXT_PRE,)
    elif time(9) <= t < time(15, 30):
        hours = config.market_hours
        start = datetime.combine(now.date(), time.fromisoformat(hours.open_time)) + timedelta(minutes=hours.block_after_open_minutes)
        end = datetime.combine(now.date(), time.fromisoformat(hours.close_time)) - timedelta(minutes=hours.block_before_close_minutes)
        routes = (REGULAR,) if start <= now <= end else ()
    elif time(15, 40) <= t < time(16):
        routes = (NXT_AFTER,)
    elif time(16) <= t < time(20):
        routes = (NXT_AFTER, KRX_AFTER)
    else:
        routes = ()
    return tuple(r for r in routes if overrides.get(r.session) != "closed"
                 and (not overrides.get(r.session) or t >= time.fromisoformat(overrides[r.session])))


def extended_window(config: BotConfig, now: datetime | None = None) -> bool:
    return any(r.session != "REGULAR" for r in candidates(config, now or korea_now()))


class SessionMarketData:
    """Read-only KIS REST adapter. Missing/stale/unknown evidence denies entry.

    No websocket connection is added to the existing REST bot. REST status,
    same-venue dated bars and holiday data must all agree before submission.
    """
    def __init__(self, client, config: BotConfig, clock: Callable[[], datetime] = korea_now):
        self.client = client
        self.config = config
        self.clock = clock
        self._calendar: dict[str, bool] = {}
        self._info: dict[str, tuple[datetime, dict]] = {}
        self._blocked: dict[tuple[str, str, str], str] = {}

    def _get(self, endpoint: str, tr_id: str, **params):
        if getattr(self.client, "is_demo", False) or not hasattr(self.client, "_request"):
            raise SessionUnavailable("extended_market_data_requires_real_quote_api")
        return self.client._request("GET", "/uapi/domestic-stock/v1/quotations/" + endpoint,
                                    params=params, tr_id=tr_id)

    def open_day(self) -> bool:
        day = self.clock().strftime("%Y%m%d")
        if day not in self._calendar:
            # Also cache an unavailable answer: KIS requests at most once/day.
            self._calendar[day] = False
            data = self._get("chk-holiday", "CTCA0903R", BASS_DT=day, CTX_AREA_FK="", CTX_AREA_NK="")
            rows = data.get("output") or []
            if isinstance(rows, dict):
                rows = [rows]
            self._calendar[day] = any(str(r.get("bass_dt")) == day and r.get("opnd_yn") == "Y" for r in rows)
        return self._calendar[day]

    def stock_info(self, code: str, *, force: bool = False) -> dict:
        now = self.clock()
        cached = self._info.get(code)
        if force or cached is None or cached[0].date() != now.date() or (now - cached[0]).total_seconds() >= 600:
            row = self._get("search-stock-info", "CTPF1002R", PRDT_TYPE_CD="300", PDNO=code).get("output") or {}
            self._info[code] = (now, row)
        return self._info[code][1]

    def reject_for_day(self, code: str, route: Route, reason: str) -> None:
        self._blocked[(self.clock().date().isoformat(), code, route.session)] = reason

    def _check_clock(self, route: Route) -> None:
        if route not in candidates(self.config, self.clock()):
            raise SessionUnavailable("session_closed_or_changed")
        if not self.open_day():
            raise SessionUnavailable("holiday_or_calendar_unavailable")

    def quote(self, code: str, name: str, route: Route, *, force_info: bool = False) -> Quote:
        self._check_clock(route)
        blocked = self._blocked.get((self.clock().date().isoformat(), code, route.session))
        if blocked:
            raise SessionUnavailable(blocked)
        params = {"FID_COND_MRKT_DIV_CODE": route.quote_market, "FID_INPUT_ISCD": code}
        if route.session != "REGULAR":
            info = self.stock_info(code, force=force_info)
            if route.venue == "NXT":
                if info.get("cptt_trad_tr_psbl_yn") != "Y" or info.get("nxt_tr_stop_yn") != "N":
                    raise SessionUnavailable("nxt_not_eligible_or_halted_or_unknown")
            elif info.get("stck_kind_cd") != "101" or info.get("scty_grp_id_cd") != "ST":
                # Restrict initial support to positively identified common stock.
                raise SessionUnavailable("krx_after_common_stock_not_confirmed")
        state = self._get("inquire-price-2", "FHPST01010000", **params).get("output") or {}
        if state.get("trht_yn") != "N":
            raise SessionUnavailable("halted_or_halt_state_unknown")
        if route == KRX_AFTER:
            required_n = ("mang_issu_yn", "short_over_yn", "invt_caful_yn", "stange_runup_yn", "low_current_yn", "sltr_yn")
            if any(state.get(k) != "N" for k in required_n) or state.get("mrkt_warn_cls_code") != "00":
                raise SessionUnavailable("krx_after_exclusion_or_unknown")
        book = self._get("inquire-asking-price-exp-ccn", "FHKST01010200", **params).get("output1") or {}
        # Official REST specification: 2=intraday, 0=ordinary. Do not guess
        # that old 40 (single-price) or 30 (closed) now means continuous after.
        if book.get("new_mkop_cls_code") != "20":
            raise SessionUnavailable("continuous_market_status_not_confirmed:" + str(book.get("new_mkop_cls_code", "missing")))
        try:
            book_time = datetime.combine(self.clock().date(), datetime.strptime(str(book["aspr_acpt_hour"]), "%H%M%S").time())
        except (KeyError, ValueError):
            raise SessionUnavailable("book_time_missing") from None
        age = (self.clock() - book_time).total_seconds()
        if not 0 <= age <= self.config.extended_hours.max_quote_age_seconds:
            raise SessionUnavailable(f"book_status_stale:venue={route.venue},session={route.session},book_time={book_time.isoformat()},age_seconds={age:.1f},limit={self.config.extended_hours.max_quote_age_seconds}")
        if route == REGULAR:
            self._check_clock(route)
            return self.client.quote(code, name=name)
        now = self.clock()
        chart = self._get("inquire-time-itemchartprice", "FHKST03010200", **params,
                          FID_INPUT_HOUR_1=now.strftime("%H%M%S"), FID_PW_DATA_INCU_YN="N", FID_ETC_CLS_CODE="")
        bars = chart.get("output2") or []
        valid_times = []
        for row in bars:
            try:
                stamp = datetime.strptime(str(row["stck_bsop_date"]) + str(row["stck_cntg_hour"]), "%Y%m%d%H%M%S")
                if int(row.get("cntg_vol") or 0) > 0 and route in candidates(self.config, stamp):
                    valid_times.append(stamp)
            except (ValueError, KeyError, TypeError):
                continue
        now = self.clock()
        if not valid_times or not 0 <= (now - max(valid_times)).total_seconds() <= self.config.extended_hours.max_quote_age_seconds:
            raise SessionUnavailable("same_venue_current_session_price_stale")
        # Current price from the same response whose dated bars proved freshness.
        # Do not substitute KRX regular close or an expected auction price.
        price = int((chart.get("output1") or {}).get("stck_prpr") or 0)
        # A final halt probe does not change the strategy's sampled price.
        row = self._get("inquire-price", "FHKST01010100", **params).get("output") or {}
        if row.get("temp_stop_yn") != "N":
            raise SessionUnavailable("temporary_halt_or_unknown")
        if price <= 0:
            raise SessionUnavailable("price_missing")
        self._check_clock(route)
        return Quote(code, price, max(valid_times), name)

    def select(self, code: str, name: str = "") -> tuple[Route, Quote]:
        errors = []
        for route in candidates(self.config, self.clock()):
            try:
                return route, self.quote(code, name, route)
            except SessionUnavailable as error:
                errors.append(str(error))
        raise SessionUnavailable("|".join(errors) or "outside_trade_window")

    def validate_request(self, request: OrderRequest) -> None:
        route = Route(request.session, request.venue, request.quote_market, request.order_division)
        if route.session != "REGULAR" and self.config.extended_hours.mode != "live":
            raise SessionUnavailable("extended_shadow_no_orders")
        if request.market_order:
            raise SessionUnavailable("extended_execution_limit_only")
        self.quote(request.code, request.name, route, force_info=True)


class RoutedQuotes:
    def __init__(self, market: SessionMarketData, route: Route):
        self.market, self.route = market, route

    def quote(self, code: str, *, name: str = "") -> Quote:
        if self.route == REGULAR:
            self.market._check_clock(self.route)
            return self.market.client.quote(code, name=name)
        return self.market.quote(code, name, self.route)
