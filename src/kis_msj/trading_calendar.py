"""KRX dated sessions from KIS holiday responses; no weekday approximations."""
from datetime import datetime, timedelta, timezone, time
from pathlib import Path
import json

KST = timezone(timedelta(hours=9))


class CalendarUnavailable(ValueError):
    pass


def local(value):
    return value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)


class TradingCalendar:
    def __init__(self, days=None, client=None, path=None, close_time='15:30'):
        self.days = dict(days or {})
        self.client, self.path = client, Path(path) if path else None
        self.close_time = time.fromisoformat(close_time)
        if self.path and self.path.exists():
            try:
                cached = json.loads(self.path.read_text(encoding='utf-8'))
                self.days.update({key: value for key,value in cached.items() if type(value) is bool})
            except (ValueError, AttributeError, OSError):
                pass  # Missing evidence remains unavailable; never guess weekdays.

    def is_session(self, day):
        key = day.isoformat()
        if key not in self.days:
            if self.client is None or not hasattr(self.client, '_request'):
                raise CalendarUnavailable('krx_calendar_unavailable:' + key)
            try:
                data = self.client._request('GET', '/uapi/domestic-stock/v1/quotations/chk-holiday',
                    tr_id='CTCA0903R', params={'BASS_DT':day.strftime('%Y%m%d'), 'CTX_AREA_FK':'', 'CTX_AREA_NK':''})
            except Exception as error:
                raise CalendarUnavailable('krx_calendar_query_failed:' + key) from error
            rows = data.get('output') or []
            if isinstance(rows, dict): rows = [rows]
            for row in rows:
                raw = str(row.get('bass_dt', ''))
                if len(raw) == 8 and row.get('opnd_yn') in ('Y', 'N'):
                    self.days[datetime.strptime(raw, '%Y%m%d').date().isoformat()] = row['opnd_yn'] == 'Y'
            if key not in self.days:
                raise CalendarUnavailable('krx_calendar_missing_date:' + key)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.days, sort_keys=True), encoding='utf-8')
                temporary.replace(self.path)
        return self.days[key]

    def window(self, now, size=5):
        day = local(now).date()
        result = []
        for _ in range(60):
            if self.is_session(day): result.append(day.isoformat())
            if len(result) == size: return set(result)
            day -= timedelta(days=1)
        raise CalendarUnavailable('krx_calendar_window_incomplete')

    def completed_since(self, bought, now):
        bought = datetime.fromisoformat(bought.replace('Z', '+00:00')) if isinstance(bought, str) else bought
        now = local(now)
        day = local(bought).date() + timedelta(days=1)
        last = now.date() if now.time().replace(tzinfo=None) >= self.close_time else now.date()-timedelta(days=1)
        count = 0
        while day <= last:
            count += self.is_session(day)
            day += timedelta(days=1)
        return count


def trend_score(events, calendar, now):
    window = calendar.window(now)
    seen, score = set(), 0
    for event in sorted(events, key=lambda e: e['at']):
        at = local(datetime.fromisoformat(event['at']))
        if at > local(now) or at.date().isoformat() not in window or event['order'] in seen:
            continue
        seen.add(event['order'])
        score += 1 if event['side'] == 'BUY' else -1
    return max(-12, min(12, score))
