"""Read-only display enrichment; never infer venue from clock or legacy defaults."""
import sqlite3
from contextlib import closing
from .order_identity import broker_order_id


def annotate_sessions(path, rows, kind):
    for row in rows:
        row.update(order_session="UNKNOWN", order_venue="UNKNOWN", execution_session="UNKNOWN",
                   actual_execution_venue="UNKNOWN", order_division="", session_attribution="unrecorded",
                   buy_order_session="UNKNOWN", buy_order_venue="UNKNOWN", broker_order_id="")
        number = str(row.get("order_id") or "")
        if number.startswith("INTENT-"):
            message = str(row.get("message") or "")
            row["broker_order_id"] = message.split(":")[1] if message.startswith("broker_acknowledged:") else "UNKNOWN"
        else:
            row["broker_order_id"] = broker_order_id(number)
        row["market_session"] = "UNKNOWN"
    if not rows or not path.exists():
        return rows
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'order_session_metadata' not in tables:
            return rows
        for start in range(0, len(rows), 400):
            chunk = rows[start:start + 400]
            key = 'lot_id' if kind == 'lots' else 'id' if kind == 'fills' else 'order_id'
            ids = [r.get(key, r.get('fill_id', '')) for r in chunk]
            placeholders = ','.join('?' for _ in ids)
            if kind == 'lots':
                if not {'fill_lot_attribution', 'fill_session_metadata'} <= tables:
                    continue
                data = c.execute(f"""SELECT a.lot_id, m.order_session, m.order_venue FROM fill_lot_attribution a
                    JOIN fills f ON f.id=a.fill_id JOIN fill_session_metadata m ON m.fill_id=f.id
                    WHERE f.side='BUY' AND a.lot_id IN ({placeholders})""", ids).fetchall()
                grouped = {}
                for item in data:
                    grouped.setdefault(item['lot_id'], set()).add((item['order_session'], item['order_venue']))
                for row in chunk:
                    matches = grouped.get(row['lot_id'], set())
                    if len(matches) == 1:
                        row['buy_order_session'], row['buy_order_venue'] = next(iter(matches))
                        row['market_session'] = row['buy_order_session']
            elif kind == 'fills':
                if 'fill_session_metadata' not in tables:
                    continue
                data = {r['fill_id']: dict(r) for r in c.execute(f'SELECT * FROM fill_session_metadata WHERE fill_id IN ({placeholders})', ids)}
                for row in chunk:
                    m = data.get(row.get('id', row.get('fill_id')))
                    if m:
                        row.update(order_session=m['order_session'], order_venue=m['order_venue'],
                                   execution_session=m['session'], actual_execution_venue=m['actual_execution_venue'] or 'UNKNOWN',
                                   session_attribution=m['attribution'], market_session=m['session'])
            else:
                data = {(r['order_id'], r['code'], r['side']): dict(r) for r in c.execute(
                    f'SELECT * FROM order_session_metadata WHERE order_id IN ({placeholders})', ids)}
                for row in chunk:
                    m = data.get((row['order_id'], row['code'], row['side']))
                    if m and str(m['requested_at'])[:10] == str(row.get('requested_at', ''))[:10]:
                        row.update(order_session=m['session'], order_venue=m['venue'], order_division=m['order_division'],
                                   session_attribution='recorded_order_route', market_session=m['session'])
    return rows
