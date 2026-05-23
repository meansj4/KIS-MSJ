"""Local-only HTTP UI for monitoring and controlling the lot auto-trader."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import DEFAULT_CONFIG_PATH
from .ui_service import UIService


INDEX_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KIS LOT Bot Control</title>
  <style>
    :root { font-family: system-ui, -apple-system, Segoe UI, sans-serif; color: #20242a; background: #f5f7f9; }
    body { margin: 0; }
    header { position: sticky; top: 0; z-index: 2; background: #18202a; color: white; padding: 12px 18px; display: flex; align-items: center; justify-content: space-between; }
    main { padding: 18px; display: grid; gap: 16px; }
    .danger { background: #b42318; color: white; padding: 12px 18px; font-weight: 700; }
    .tabs { display: flex; gap: 8px; flex-wrap: wrap; }
    button { border: 1px solid #b8c2cc; background: white; padding: 8px 10px; border-radius: 6px; cursor: pointer; }
    button.primary { background: #1f6feb; color: white; border-color: #1f6feb; }
    button.dangerBtn { background: #b42318; color: white; border-color: #b42318; }
    section { background: white; border: 1px solid #d7dde3; border-radius: 8px; padding: 14px; overflow: auto; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
    .metric { border: 1px solid #e2e7ec; padding: 10px; border-radius: 6px; }
    .metric strong { display: block; font-size: 12px; color: #59636e; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { border-bottom: 1px solid #e6ebf0; padding: 7px; text-align: left; white-space: nowrap; }
    th { background: #f1f4f7; position: sticky; top: 0; }
    .warn { color: #a15c00; font-weight: 700; }
    .bad { color: #b42318; font-weight: 700; }
    pre { background: #0b1020; color: #d6e2ff; padding: 12px; border-radius: 6px; overflow: auto; max-height: 420px; }
    input, textarea, select { padding: 7px; border: 1px solid #b8c2cc; border-radius: 6px; }
    textarea { width: 100%; min-height: 240px; font-family: ui-monospace, Consolas, monospace; }
  </style>
</head>
<body>
<header><div>KIS LOT Bot Control</div><div>localhost read/control UI - 주문 API 없음</div></header>
<div id="banner"></div>
<main>
  <div class="tabs">
    <button onclick="loadDashboard()">Dashboard</button>
    <button onclick="loadStocks()">Stocks</button>
    <button onclick="loadLots()">Lots</button>
    <button onclick="loadOrders()">Orders/Fills</button>
    <button onclick="loadLogs()">Logs</button>
    <button onclick="loadConfig()">Config</button>
    <button onclick="loadExecution()">Execution Check</button>
    <button onclick="loadRuntime()">Runtime Control</button>
  </div>
  <section id="content">Loading...</section>
</main>
<script>
async function api(path, options={}) {
  const res = await fetch(path, {headers: {'content-type': 'application/json'}, ...options});
  const data = await res.json();
  if (!res.ok) throw new Error(JSON.stringify(data));
  return data;
}
function esc(v) { return String(v ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function table(rows) {
  if (!rows || !rows.length) return '<p>No data</p>';
  const keys = Object.keys(rows[0]);
  return '<table><thead><tr>' + keys.map(k => `<th>${esc(k)}</th>`).join('') + '</tr></thead><tbody>' +
    rows.map(r => '<tr>' + keys.map(k => `<td>${esc(r[k])}</td>`).join('') + '</tr>').join('') + '</tbody></table>';
}
function metrics(obj) {
  return '<div class="grid">' + Object.entries(obj || {}).map(([k,v]) => `<div class="metric"><strong>${esc(k)}</strong>${esc(typeof v === 'object' ? JSON.stringify(v) : v)}</div>`).join('') + '</div>';
}
async function refreshBanner() {
  const s = await api('/api/status');
  const msgs = s.risk_banner.messages || [];
  document.getElementById('banner').innerHTML = msgs.length ? `<div class="danger">${msgs.map(esc).join('<br>')}</div>` : '';
}
async function loadDashboard() {
  const s = await api('/api/status');
  document.getElementById('content').innerHTML = `<h2>Dashboard</h2><h3>Bot</h3>${metrics(s.bot)}<h3>Risk</h3>${metrics(s.account_risk)}<h3>Position States</h3>${metrics(s.position_state_counts)}<h3>Orders</h3>${metrics(s.order_status_counts)}<h3>Warnings</h3>${table(s.warnings)}<h3>Runtime</h3>${metrics(s.runtime_control)}`;
}
async function loadStocks() { document.getElementById('content').innerHTML = '<h2>Stocks</h2>' + table(await api('/api/stocks')); }
async function loadLots() { document.getElementById('content').innerHTML = '<h2>Lots</h2>' + table(await api('/api/lots')); }
async function loadOrders() { const o=await api('/api/orders'), f=await api('/api/fills'); document.getElementById('content').innerHTML = '<h2>Orders</h2>'+table(o)+'<h2>Fills</h2>'+table(f); }
async function loadLogs() { const l=await api('/api/logs/tail?limit=300'); document.getElementById('content').innerHTML = '<h2>Logs</h2><pre>'+esc(l.lines.join('\n'))+'</pre>'; }
async function loadExecution() { const e=await api('/api/execution-mapping/status'); document.getElementById('content').innerHTML = '<h2>Execution Mapping Check</h2>'+metrics(e)+'<pre>'+esc(e.raw_log_line || '')+'</pre>'; }
async function loadRuntime() {
  const r=await api('/api/runtime');
  document.getElementById('content').innerHTML = `<h2>Runtime Control</h2>${metrics(r)}
  <p><button class="dangerBtn" onclick="runtime('/api/runtime/emergency-stop')">Emergency Stop</button>
  <button onclick="runtime('/api/runtime/pause-buy')">Pause Buy</button>
  <button onclick="runtime('/api/runtime/pause-sell')">Pause Sell</button>
  <button onclick="runtime('/api/runtime/resume')">Resume</button></p>`;
}
async function runtime(path) { await api(path, {method:'POST'}); await loadRuntime(); }
async function loadConfig() {
  const c = await api('/api/config');
  document.getElementById('content').innerHTML = `<h2>Config</h2><p><button onclick="validateConfig()">Validate</button> <button onclick="saveConfig()">Save edited JSON</button></p><textarea id="cfg">${esc(JSON.stringify(c, null, 2))}</textarea><pre id="cfgResult"></pre>`;
}
async function validateConfig() { const body = document.getElementById('cfg').value; const r = await api('/api/config/validate', {method:'POST', body}); document.getElementById('cfgResult').textContent = JSON.stringify(r, null, 2); }
async function saveConfig() { if (!confirm('config backup 후 atomic 저장합니다. 계속할까요?')) return; const body = document.getElementById('cfg').value; const r = await api('/api/config', {method:'PATCH', body}); document.getElementById('cfgResult').textContent = JSON.stringify(r, null, 2); }
refreshBanner().then(loadDashboard);
</script>
</body>
</html>
"""


class UIHandler(BaseHTTPRequestHandler):
    service: UIService

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_text(INDEX_HTML, "text/html; charset=utf-8")
                return
            query = parse_qs(parsed.query)
            routes = {
                "/api/status": self.service.status,
                "/api/config": self.service.raw_config,
                "/api/config/schema": lambda: {"sections": ["strategy", "risk", "order", "market_hours", "stocks"], "restart_required_by_default": True},
                "/api/stocks": self.service.stocks,
                "/api/positions": self.service.positions,
                "/api/lots": self.service.lots,
                "/api/orders": self.service.orders,
                "/api/fills": self.service.fills,
                "/api/decisions": self.service.parse_decision_logs,
                "/api/risk/summary": lambda: self.service.status()["account_risk"],
                "/api/execution-mapping/status": self.service.execution_mapping_status,
                "/api/reconciliation/status": lambda: self.service.status()["reconciliation"],
                "/api/runtime": self.service.runtime_status,
            }
            if parsed.path.startswith("/api/stocks/"):
                code = parsed.path.rsplit("/", 1)[-1]
                self._send_json(self.service.stock_detail(code))
                return
            if parsed.path.startswith("/api/positions/"):
                code = parsed.path.rsplit("/", 1)[-1].zfill(6)
                self._send_json(next((item for item in self.service.positions() if item.get("code") == code), {}))
                return
            if parsed.path == "/api/logs/tail":
                self._send_json(self.service.logs_tail(int(query.get("limit", ["300"])[0]), query.get("keyword", [""])[0], query.get("level", [""])[0], query.get("event", [""])[0]))
                return
            if parsed.path in routes:
                self._send_json(routes[parsed.path]())
                return
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as error:  # noqa: BLE001
            self._send_json({"error": type(error).__name__, "message": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        self._write_route()

    def do_PATCH(self) -> None:  # noqa: N802
        self._write_route()

    def _write_route(self) -> None:
        try:
            parsed = urlparse(self.path)
            data = self._read_json()
            if parsed.path == "/api/config/validate":
                valid, errors = self.service.validate_config_data(data)
                self._send_json({"valid": valid, "errors": errors})
                return
            if parsed.path == "/api/config/backup":
                self._send_json({"backup_path": str(self.service.backup_config())})
                return
            if parsed.path == "/api/config":
                self._send_json(self.service.save_config_patch(data))
                return
            if parsed.path.startswith("/api/stocks/"):
                code = parsed.path.rsplit("/", 1)[-1]
                self._send_json(self.service.patch_stock(code, data))
                return
            if parsed.path == "/api/runtime/pause-all":
                self._send_json(self.service.runtime_set(all_orders_paused=True, reason=data.get("reason", "ui_pause_all")))
                return
            if parsed.path == "/api/runtime/pause-buy":
                self._send_json(self.service.runtime_set(buy_paused=True, reason=data.get("reason", "ui_pause_buy")))
                return
            if parsed.path == "/api/runtime/pause-sell":
                self._send_json(self.service.runtime_set(sell_paused=True, reason=data.get("reason", "ui_pause_sell")))
                return
            if parsed.path == "/api/runtime/pause-cleanup":
                self._send_json(self.service.runtime_set(cleanup_paused=True, reason=data.get("reason", "ui_pause_cleanup")))
                return
            if parsed.path == "/api/runtime/pause-reentry":
                self._send_json(self.service.runtime_set(reentry_paused=True, reason=data.get("reason", "ui_pause_reentry")))
                return
            if parsed.path == "/api/runtime/resume":
                self._send_json(self.service.runtime_set(all_orders_paused=False, buy_paused=False, sell_paused=False, cleanup_paused=False, reentry_paused=False, reason=data.get("reason", "ui_resume")))
                return
            if parsed.path == "/api/runtime/emergency-stop":
                self._send_json(self.service.runtime_set(all_orders_paused=True, buy_paused=True, sell_paused=True, cleanup_paused=True, reentry_paused=True, reason=data.get("reason", "emergency_stop")))
                return
            if parsed.path == "/api/decision-preview":
                self._send_json(self.service.decision_preview(data.get("code"), data.get("current_price")))
                return
            if parsed.path == "/api/reconciliation/dry-run":
                self._send_json({"dry_run": True, "order_api_called": False, "status": self.service.status()["reconciliation"]})
                return
            if parsed.path == "/api/reconciliation/apply":
                self._send_json({"applied": False, "message": "DB apply is intentionally disabled in this UI phase."})
                return
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as error:  # noqa: BLE001
            self._send_json({"error": type(error).__name__, "message": str(error)}, HTTPStatus.BAD_REQUEST)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length") or 0)
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body) if body.strip() else {}

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, payload: str, content_type: str) -> None:
        body = payload.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def build_server(config_path: Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    service = UIService(config_path)

    class BoundHandler(UIHandler):
        pass

    BoundHandler.service = service
    return ThreadingHTTPServer((host, port), BoundHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local KIS LOT bot monitoring UI.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Refusing to bind non-localhost host. Use 127.0.0.1.")
    server = build_server(args.config, args.host, args.port)
    print(f"KIS LOT UI running at http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
