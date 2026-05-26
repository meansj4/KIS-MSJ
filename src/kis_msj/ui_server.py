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
from .runtime_control import DEFAULT_RUNTIME_CONTROL_PATH
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
    section { background: white; border: 1px solid #d7dde3; border-radius: 8px; padding: 14px; overflow: visible; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
    .metric { border: 1px solid #e2e7ec; padding: 10px; border-radius: 6px; }
    .metric strong { display: block; font-size: 12px; color: #59636e; }
    .tableWrap { overflow: auto; max-height: 68vh; border: 1px solid #e2e7ec; border-radius: 8px; background: white; margin: 10px 0 16px; }
    .tableWrap table { min-width: max-content; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { border-bottom: 1px solid #e6ebf0; padding: 7px; text-align: left; white-space: nowrap; vertical-align: top; }
    th { background: #e8eef5; position: sticky; top: 0; z-index: 1; cursor: pointer; user-select: none; font-weight: 800; }
    th:hover { background: #e5ebf1; }
    tr:hover td { background: #f8fbff; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .key { display: block; color: #7b8794; font-family: ui-monospace, Consolas, monospace; font-size: 11px; font-weight: 400; margin-top: 2px; }
    .empty { color: #9aa5b1; }
    .badge { display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; font-weight: 700; background: #edf2f7; color: #334155; }
    .badge.good { background: #dcfce7; color: #166534; }
    .badge.warn { background: #ffedd5; color: #9a3412; }
    .badge.bad { background: #fee2e2; color: #991b1b; }
    .badge.neutral { background: #e0f2fe; color: #075985; }
    .pos { color: #15803d; font-weight: 700; }
    .neg { color: #b91c1c; font-weight: 700; }
    .muted { color: #7b8794; }
    .controlCard { border: 1px solid #d7dde3; border-radius: 8px; padding: 12px; background: #fbfcfe; }
    .controlCard.dangerZone { border-color: #ef9a9a; background: #fff5f5; }
    .manualBox { border: 1px dashed #b8c2cc; border-radius: 8px; padding: 12px; background: #fbfcfe; margin: 10px 0; }
    .warn { color: #a15c00; font-weight: 700; }
    .bad { color: #b42318; font-weight: 700; }
    .field { display: grid; grid-template-columns: minmax(180px, 260px) 1fr minmax(80px, 120px); gap: 10px; align-items: start; border-bottom: 1px solid #e6ebf0; padding: 10px 0; }
    .field label { font-weight: 700; }
    .field small { display: block; color: #59636e; margin-top: 4px; line-height: 1.35; }
    .field input, .field textarea, .field select { width: 100%; box-sizing: border-box; }
    .field textarea { min-height: 90px; }
    .sectionNav { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
    .critical, .dangerText { color: #b42318; font-weight: 700; }
    .changed { background: #fff7db; }
    .configActions { position: sticky; bottom: 0; background: white; border-top: 1px solid #d7dde3; padding: 10px 0; }
    .sortHint { color: #59636e; font-size: 12px; margin: 4px 0 10px; }
    .rowActions { display: flex; gap: 6px; align-items: center; }
    .rowActions button { padding: 5px 8px; font-size: 12px; }
    .detailPanel { border: 1px solid #d7dde3; border-radius: 8px; padding: 12px; background: #fbfcfe; margin-top: 12px; }
    .columnControls { border: 1px solid #e2e7ec; border-radius: 8px; padding: 10px; background: #fbfcfe; margin: 8px 0; }
    .columnControls summary { cursor: pointer; font-weight: 800; }
    .columnControls .checks { display: flex; flex-wrap: wrap; gap: 8px 12px; margin-top: 10px; }
    .columnControls label { display: inline-flex; gap: 4px; align-items: center; font-size: 12px; }
    .refreshBar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; background: #eef5ff; border-bottom: 1px solid #d7dde3; padding: 8px 18px; }
    pre { background: #0b1020; color: #d6e2ff; padding: 12px; border-radius: 6px; overflow: auto; max-height: 420px; }
    input, textarea, select { padding: 7px; border: 1px solid #b8c2cc; border-radius: 6px; }
    textarea { width: 100%; min-height: 240px; font-family: ui-monospace, Consolas, monospace; }
  </style>
</head>
<body>
<header><div>KIS LOT Bot Control</div><div>localhost read/control UI - 주문 API 없음</div></header>
<div id="banner"></div>
<div class="refreshBar">
  <button onclick="manualRefresh()">새로고침</button>
  <label>자동 갱신 <input id="autoRefreshEnabled" type="checkbox" checked onchange="setupAutoRefresh()"></label>
  <label>간격(초) <input id="autoRefreshSeconds" type="number" min="3" value="10" style="width:70px" onchange="setupAutoRefresh()"></label>
  <span class="muted" id="lastRefreshAt">-</span>
</div>
<main>
  <div class="tabs">
    <button onclick="loadDashboard()">Dashboard</button>
    <button onclick="loadStocks()">Stocks</button>
    <button onclick="loadLots()">Lots</button>
    <button onclick="loadOrders()">Orders/Fills</button>
    <button onclick="loadLogs()">Logs</button>
    <button onclick="loadConfig()">Config</button>
    <button onclick="loadRuntime()">Runtime Control</button>
    <button onclick="loadManualOrders()">수동 주문 요청</button>
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
let autoRefreshTimer = null;
function setupAutoRefresh() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  const enabled = document.getElementById('autoRefreshEnabled')?.checked;
  const seconds = Math.max(3, Number(document.getElementById('autoRefreshSeconds')?.value || 10));
  if (enabled) autoRefreshTimer = setInterval(() => { if (currentView !== 'config') manualRefresh(); }, seconds * 1000);
}
async function manualRefresh() {
  await refreshBanner();
  await reloadCurrent();
  const target = document.getElementById('lastRefreshAt');
  if (target) target.textContent = '마지막 갱신: ' + new Date().toLocaleTimeString();
}
const LABELS = {
  code:'종목코드', name:'종목명', enabled:'사용 여부', position_state:'보유 상태',
  current_price:'현재가', open_lot_count:'OPEN LOT 수', invested_amount:'투입금',
  profit_loss_pct:'평가손익률', risk_block_reasons:'위험 사유', last_decision:'최근 판단',
  skip_reason:'스킵 사유', final_block_reason:'최종 차단 사유',
  lot_id:'LOT ID', status:'상태', buy_price:'매수가', buy_quantity:'매수 수량',
  remaining_quantity:'잔여 수량', buy_amount:'매수 금액', buy_filled_at:'매수 체결시각',
  lot_age_days:'LOT 나이(일)', age_weeks:'LOT 나이(주)', unrealized_pnl:'평가손익',
  unrealized_pnl_rate:'평가손익률', base_target_profit_rate:'기본 목표수익률',
  effective_target_profit_rate:'실효 목표수익률', cleanup_candidate:'Cleanup 후보',
  stale_lot:'STALE LOT', last_sell_reason:'최근 매도 사유', order_id:'주문 ID',
  side:'매수/매도', quantity:'수량', limit_price:'지정가', reason:'사유',
  requested_at:'요청시각', updated_at:'갱신시각', cleanup_flag:'Cleanup 여부',
  fill_id:'체결 ID', execution_id:'KIS 체결번호', price:'체결가', filled_at:'체결시각',
  dedupe_key_type:'중복방지 키', is_duplicate:'중복 여부', apply_fill:'체결 반영 여부',
  position_lots_reflected:'포지션/LOT 반영 여부',
  all_orders_paused:'전체 주문 일시정지', buy_paused:'매수 일시정지', sell_paused:'매도 일시정지',
  cleanup_paused:'Cleanup 일시정지', reentry_paused:'재진입 일시정지', updated_at:'갱신시각',
  updated_by:'수정자', expires_at:'만료시각', source:'출처', requested_by:'요청자',
  requested_at:'요청시각', amount:'금액', order_type:'주문 유형', preview_json:'미리보기 JSON',
  runtime_snapshot_json:'런타임 상태 JSON', live_trading:'실거래 여부', confirm_text_verified:'확인 문구 검증',
  block_reason:'차단 사유', linked_order_id:'연결 주문 ID', created_at:'생성시각',
  market_value:'평가금액', total_quantity:'총 수량', realized_pnl:'실현손익',
  target_sell_price:'목표 매도가', target_profit_pct:'목표수익률', sell_completed:'매도 완료',
  partial_sold:'부분 매도', estimated_fee_tax:'예상 수수료/세금', last_order_id:'최근 주문 ID',
  last_order_status:'최근 주문 상태', sync_status:'동기화 상태', review_reason:'검토 사유'
};
const VALUE_LABELS = {
  HOLDING:'보유 중', NEVER_BOUGHT:'미매수', WAIT_REENTRY:'재진입 대기',
  COOLDOWN_AFTER_CLEANUP:'Cleanup 후 쿨다운', REVIEW_REQUIRED:'수동 검토 필요',
  RISK_BLOCKED:'위험 차단', SYNC_REQUIRED:'동기화 필요',
  PROFIT_TAKE:'본전/수익 매도', CLEANUP_SELL:'손실 정리 매도', UNKNOWN:'알 수 없음',
  BUY:'매수', SELL:'매도', REQUESTED:'요청됨', PARTIAL:'부분체결', FILLED:'체결완료',
  CANCELED:'취소됨', REJECTED:'거절됨', execution_id:'체결번호 기준', fallback:'보조 키 기준',
  runtime_all_orders_paused:'전체 주문 일시정지로 차단', runtime_buy_paused:'매수 일시정지로 차단',
  runtime_sell_paused:'매도 일시정지로 차단', runtime_cleanup_paused:'Cleanup 일시정지로 차단',
  runtime_reentry_paused:'재진입 일시정지로 차단', open_order_exists_for_cleanup:'미체결 주문이 있어 cleanup 매도 차단',
  risk_blocked_buy_sell_blocked:'위험 차단 상태라 매수/매도 모두 차단', sync_required:'동기화 필요로 차단',
  review_required:'수동 검토 필요로 매수 차단'
};
Object.assign(LABELS, {
  code:'종목코드', name:'종목명', enabled:'사용 여부',
  trading_halted:'거래정지', administrative_issue:'관리종목 이슈', investment_alert:'투자주의/경고',
  audit_opinion_issue:'감사의견 이슈', delisting_risk:'상장폐지 위험', accounting_issue:'회계 이슈',
  liquidity_warning:'유동성 경고', position_state:'보유 상태', current_price:'현재가',
  open_lot_count:'OPEN LOT 수', invested_amount:'투입금', profit_loss_pct:'평가손익률',
  risk_block_reasons:'위험 차단 사유', last_decision:'최근 판단', skip_reason:'스킵 사유',
  final_block_reason:'최종 차단 사유', lot_id:'LOT ID', status:'상태', buy_price:'매수가',
  buy_quantity:'매수 수량', remaining_quantity:'잔여 수량', buy_amount:'매수 금액',
  buy_filled_at:'매수 체결시각', buy_time:'매수시각', lot_age_days:'LOT 경과 일수',
  age_weeks:'LOT 경과 주수', lot_age_weeks:'LOT 경과 주수', unrealized_pnl:'평가손익',
  unrealized_pnl_rate:'평가손익률', base_target_profit_rate:'기본 목표수익률',
  effective_target_profit_rate:'적용 목표수익률', sell_trigger_price:'매도 트리거 가격',
  cleanup_candidate:'손실정리 후보', stale_lot:'STALE LOT', last_sell_reason:'최근 매도 사유',
  order_id:'주문 ID', side:'매수/매도', order_type:'주문 유형', order_status:'주문 상태',
  requested_price:'주문 요청가', requested_quantity:'주문 요청수량', quantity:'수량',
  limit_price:'지정가', reason:'사유', requested_at:'주문 요청시각', updated_at:'갱신시각',
  cleanup_flag:'Cleanup 여부', fill_id:'체결 ID', execution_id:'KIS 체결번호',
  price:'체결가', filled_at:'체결시각', dedupe_key_type:'중복방지 키',
  is_duplicate:'중복 여부', apply_fill:'체결 반영 여부',
  position_lots_reflected:'포지션/LOT 반영 여부', source:'출처', requested_by:'요청자',
  amount:'금액', live_trading:'실거래 여부', confirm_text_verified:'확인 문구 검증',
  block_reason:'차단 사유', linked_order_id:'연결 주문 ID', created_at:'생성시각',
  review_reason:'검토 사유', sync_status:'동기화 상태',
  entry_price_for_lot_sizing:'LOT 기준 진입가', lot_unit_amount:'현재 사이클 1 LOT 금액',
  max_symbol_amount:'종목당 최대 금액', max_lots_per_symbol:'종목당 최대 LOT 수',
  lot_sizing_bucket:'LOT 가격 구간', lot_sizing_locked_at:'LOT 기준 고정시각',
  lot_sizing_mode:'LOT 금액 모드', lot_sizing_locked:'LOT 기준 고정 여부',
  current_open_lot_count:'현재 OPEN LOT 수', remaining_buy_capacity_amount:'남은 매수 가능 금액',
  price_lot_band:'가격대 LOT 구간'
});
Object.assign(VALUE_LABELS, {
  HOLDING:'보유 중', NEVER_BOUGHT:'미매수', WAIT_REENTRY:'재진입 대기',
  COOLDOWN_AFTER_CLEANUP:'Cleanup 후 쿨다운', REVIEW_REQUIRED:'수동 검토 필요',
  RISK_BLOCKED:'위험 차단', SYNC_REQUIRED:'동기화 필요', OPEN:'미청산',
  CLOSED:'청산 완료', REQUESTED:'요청됨', PARTIAL:'부분체결', FILLED:'체결완료',
  CANCELED:'취소됨', REJECTED:'거절됨', BUY:'매수', SELL:'매도',
  PROFIT_TAKE:'본전/수익 매도', CLEANUP_SELL:'손실 정리 매도', UNKNOWN:'알 수 없음',
  execution_id:'체결번호 기준', fallback:'보조 키 기준',
  ui_manual_trading_disabled:'수동 주문 요청 비활성', confirm_text_required:'실거래 확인 문구 필요',
  current_price_missing:'현재가 없음', open_buy_order_exists:'미체결 매수 주문 존재',
  open_sell_order_exists:'해당 LOT 미체결 매도 주문 존재', quantity_below_one:'수량 1 미만',
  quantity_exceeds_remaining:'잔여 수량 초과', closed_lot:'청산 완료 LOT', lot_not_found:'LOT 없음'
});
function labelFor(key) { return LABELS[key] || humanizeKey(key); }
function humanizeKey(key) {
  const parts = String(key).split('_').map(p => ({
    code:'종목코드', name:'종목명', current:'현재', price:'가격', quantity:'수량',
    remaining:'잔여', buy:'매수', sell:'매도', order:'주문', fill:'체결',
    filled:'체결', requested:'요청', created:'생성', updated:'갱신', status:'상태',
    reason:'사유', amount:'금액', rate:'비율', pct:'비율', pnl:'손익',
    profit:'수익', loss:'손실', lot:'LOT', id:'ID', flag:'여부', count:'수',
    time:'시각', date:'일자', side:'매수/매도'
  }[p] || p));
  return parts.join(' ');
}
function valueLabel(v) { return VALUE_LABELS[String(v)] || String(v); }
function headerLabel(key) { return `${esc(labelFor(key))}<span class="key">${esc(key)}</span>`; }
function isNumericKey(key) { return /(price|amount|quantity|count|pct|rate|pnl|loss|profit|age|limit|total|cash|value)/i.test(key); }
function cellClass(key, value) {
  const classes = [];
  if (isNumericKey(key)) classes.push('num');
  if (Number(value) > 0 && /(pnl|profit|loss|rate|pct)/i.test(key)) classes.push('pos');
  if (Number(value) < 0 && /(pnl|profit|loss|rate|pct)/i.test(key)) classes.push('neg');
  return classes.join(' ');
}
function badgeClass(value) {
  const v = String(value);
  if (/PROFIT|FILLED|HOLDING|true/i.test(v)) return 'good';
  if (/WAIT|REVIEW|PARTIAL|REQUESTED|fallback/i.test(v)) return 'warn';
  if (/RISK|SYNC|CLEANUP|REJECTED|CANCELED|BLOCKED|false/i.test(v)) return 'bad';
  return 'neutral';
}
function displayCell(key, value) {
  if (value === null || value === undefined || value === '') return '<span class="empty">-</span>';
  const translated = valueLabel(value);
  if (/state|status|reason|side|dedupe|flag|enabled|paused|candidate|stale|duplicate|reflected/i.test(key)) {
    return `<span class="badge ${badgeClass(value)}">${esc(translated)}</span><span class="key">${esc(value)}</span>`;
  }
  return esc(value);
}
const sortState = {};
const DEFAULT_COLUMNS = {
  stocks: ['code','name','enabled','position_state','current_price','open_lot_count','lot_unit_amount','max_symbol_amount','max_lots_per_symbol','lot_sizing_bucket','invested_amount','profit_loss_pct','risk_block_reasons','skip_reason','final_block_reason'],
  lots: ['lot_id','code','name','status','buy_price','remaining_quantity','current_price','unrealized_pnl','unrealized_pnl_rate','age_weeks','effective_target_profit_rate','sell_trigger_price','cleanup_candidate','stale_lot','last_sell_reason'],
  stockLots: ['lot_id','code','name','status','buy_price','remaining_quantity','current_price','unrealized_pnl','unrealized_pnl_rate','age_weeks','effective_target_profit_rate','sell_trigger_price','cleanup_candidate','stale_lot','last_sell_reason'],
  orders: ['order_id','code','name','side','status','quantity','limit_price','reason','requested_at','updated_at','lot_id','sell_reason','reentry_type'],
  fills: ['fill_id','execution_id','dedupe_key_type','order_id','code','name','side','price','quantity','filled_at','lot_id','sell_reason','reentry_type']
};
const columnPrefs = {};
let configOriginal = null;
let configDraft = null;
let configSchema = null;
function table(rows, tableId='default', opts={}) {
  if (!rows || !rows.length) return '<p>No data</p>';
  const keys = Object.keys(rows[0]);
  const visibleKeys = visibleColumns(tableId, keys);
  const state = sortState[tableId] || opts.defaultSort || null;
  const sorted = state ? sortRows(rows, state.key, state.dir) : [...rows];
  const actionHeader = opts.actions ? '<th>작업<span class="key">actions</span></th>' : '';
  const actionCells = (row) => opts.actions ? `<td>${rowActions(tableId, row)}</td>` : '';
  return columnControls(tableId, keys, visibleKeys) + '<div class="sortHint">컬럼 헤더를 클릭하면 정렬됩니다. 기본은 핵심 컬럼만 표시하며, 컬럼 선택에서 숨긴 정보를 다시 볼 수 있습니다.</div><div class="tableWrap"><table data-table-id="'+esc(tableId)+'"><thead><tr>' +
    actionHeader +
    visibleKeys.map(k => `<th onclick="sortTable('${esc(tableId)}','${esc(k)}')">${headerLabel(k)}${state && state.key === k ? (state.dir === 'asc' ? ' ▲' : ' ▼') : ''}</th>`).join('') +
    '</tr></thead><tbody>' +
    sorted.map(r => '<tr>' + actionCells(r) + visibleKeys.map(k => `<td class="${cellClass(k, r[k])}">${displayCell(k, r[k])}</td>`).join('') + '</tr>').join('') + '</tbody></table></div>';
}
function visibleColumns(tableId, keys) {
  if (columnPrefs[tableId]) return keys.filter(k => columnPrefs[tableId].has(k));
  const defaults = DEFAULT_COLUMNS[tableId];
  return defaults ? keys.filter(k => defaults.includes(k)) : keys;
}
function columnControls(tableId, keys, visibleKeys) {
  if (!DEFAULT_COLUMNS[tableId]) return '';
  const visible = new Set(visibleKeys);
  return `<details class="columnControls"><summary>컬럼 선택: ${visibleKeys.length}/${keys.length}개 표시</summary>
    <div class="rowActions"><button onclick="showDefaultColumns('${esc(tableId)}')">핵심 컬럼</button><button onclick="showAllColumns('${esc(tableId)}')">전체보기</button></div>
    <div class="checks">${keys.map(k => `<label><input type="checkbox" ${visible.has(k) ? 'checked' : ''} onchange="toggleColumn('${esc(tableId)}','${esc(k)}',this.checked)"> ${esc(labelFor(k))}<span class="key">${esc(k)}</span></label>`).join('')}</div>
  </details>`;
}
function showDefaultColumns(tableId) {
  delete columnPrefs[tableId];
  if (tableId === 'stockLots' && window.selectedStockCode) { openStockLots(window.selectedStockCode); return; }
  reloadCurrent();
}
function showAllColumns(tableId) {
  const rows = rowsForTable(tableId);
  if (rows.length) columnPrefs[tableId] = new Set(Object.keys(rows[0]));
  if (tableId === 'stockLots' && window.selectedStockCode) { openStockLots(window.selectedStockCode); return; }
  reloadCurrent();
}
function toggleColumn(tableId, key, checked) {
  const rows = rowsForTable(tableId);
  const allKeys = rows.length ? Object.keys(rows[0]) : [];
  const current = new Set(visibleColumns(tableId, allKeys));
  if (checked) current.add(key); else current.delete(key);
  columnPrefs[tableId] = current;
  if (tableId === 'stockLots' && window.selectedStockCode) { openStockLots(window.selectedStockCode); return; }
  reloadCurrent();
}
function rowsForTable(tableId) {
  if (tableId === 'stocks') return window.stockRows || [];
  if (tableId === 'lots') return window.lotRows || [];
  if (tableId === 'stockLots') return window.stockLotRows || [];
  if (tableId === 'orders') return window.orderRows || [];
  if (tableId === 'fills') return window.fillRows || [];
  return [];
}function rowActions(tableId, row) {
  if (tableId === 'stocks') {
    return `<div class="rowActions"><button onclick="openStockLots('${esc(row.code)}')">LOT 보기</button><button onclick="openManualBuy('${esc(row.code)}')">수동 매수</button></div>`;
  }
  if (tableId === 'lots' || tableId === 'stockLots') {
    const disabled = Number(row.remaining_quantity || 0) <= 0 || String(row.status || '') === 'CLOSED' ? 'disabled' : '';
    return `<div class="rowActions"><button ${disabled} onclick="openManualSell('${esc(row.code)}','${esc(row.lot_id)}',${Number(row.remaining_quantity || 0)})">수동 매도</button></div>`;
  }
  return '';
}
function sortTable(tableId, key) {
  const current = sortState[tableId];
  if (!current || current.key !== key) sortState[tableId] = {key, dir:'asc'};
  else if (current.dir === 'asc') sortState[tableId] = {key, dir:'desc'};
  else delete sortState[tableId];
  reloadCurrent();
}
function sortRows(rows, key, dir) {
  const multiplier = dir === 'desc' ? -1 : 1;
  return [...rows].sort((a,b) => {
    const av = sortValue(a[key]), bv = sortValue(b[key]);
    if (av.empty && bv.empty) return 0;
    if (av.empty) return 1;
    if (bv.empty) return -1;
    if (av.value < bv.value) return -1 * multiplier;
    if (av.value > bv.value) return 1 * multiplier;
    return 0;
  });
}
function sortValue(v) {
  if (v === null || v === undefined || v === '') return {empty:true, value:''};
  if (typeof v === 'boolean') return {empty:false, value:v ? 1 : 0};
  if (typeof v === 'number') return {empty:false, value:v};
  const s = String(v).trim();
  if (/^(true|false)$/i.test(s)) return {empty:false, value:/^true$/i.test(s) ? 1 : 0};
  if (/^-?\d+(\.\d+)?$/.test(s)) return {empty:false, value:Number(s)};
  const t = Date.parse(s);
  if (!Number.isNaN(t) && /\d{4}-\d{2}-\d{2}/.test(s)) return {empty:false, value:t};
  return {empty:false, value:s.toLowerCase()};
}
let currentView = 'dashboard';
async function reloadCurrent() {
  if (currentView === 'stocks') return loadStocks();
  if (currentView === 'lots') return loadLots();
  if (currentView === 'orders') return loadOrders();
  if (currentView === 'runtime') return loadRuntime();
  if (currentView === 'manual') return loadManualOrders();
  if (currentView === 'logs') return loadLogs();
  if (currentView === 'config') return renderConfig();
  return loadDashboard();
}
function metrics(obj) {
  return '<div class="grid">' + Object.entries(obj || {}).map(([k,v]) => `<div class="metric"><strong>${esc(labelFor(k))}<span class="key">${esc(k)}</span></strong>${displayCell(k, typeof v === 'object' ? JSON.stringify(v) : v)}</div>`).join('') + '</div>';
}
async function refreshBanner() {
  const s = await api('/api/status');
  const msgs = s.risk_banner.messages || [];
  document.getElementById('banner').innerHTML = msgs.length ? `<div class="danger">${msgs.map(esc).join('<br>')}</div>` : '';
}
async function loadDashboard() {
  currentView = 'dashboard';
  const s = await api('/api/status');
  const top = {
    live_trading: s.risk_banner.live_trading,
    all_orders_paused: s.runtime_control.all_orders_paused,
    buy_paused: s.runtime_control.buy_paused,
    sell_paused: s.runtime_control.sell_paused,
    total_open_lot_count: s.account_risk.total_open_lot_count,
    new_buy_count_today: s.account_risk.new_buy_count_today,
    risk_blocked_count: s.position_state_counts.RISK_BLOCKED || 0,
    review_required_count: s.position_state_counts.REVIEW_REQUIRED || 0
  };
  document.getElementById('content').innerHTML = `<h2>대시보드</h2><h3>핵심 요약</h3>${metrics(top)}<h3>봇 상태</h3>${metrics(s.bot)}<h3>계좌/리스크</h3>${metrics(s.account_risk)}<h3>보유 상태별 종목 수</h3>${metrics(s.position_state_counts)}<h3>주문 상태</h3>${metrics(s.order_status_counts)}<h3>경고</h3>${table(s.warnings, 'warnings')}<h3>런타임 제어</h3>${metrics(s.runtime_control)}`;
}
async function loadStocks() {
  currentView = 'stocks';
  const rows = await api('/api/stocks');
  if (!sortState.stocks) sortState.stocks = {key:'position_state', dir:'asc'};
  document.getElementById('content').innerHTML = '<h2>종목</h2><input id="stockFilter" placeholder="종목코드/종목명/상태 검색" oninput="renderStockTable()" value="'+esc(window.stockFilterValue || '')+'"><div class="manualBox"><strong>종목별 LOT 확인 / 수동 요청</strong><p class="muted">LOT 보기로 종목별 보유 LOT과 LOT별 수익률을 확인할 수 있습니다. 수동 매수/매도 버튼은 manual_order_requests 큐에 요청만 만들며 UI가 KIS 주문 API를 직접 호출하지 않습니다.</p></div><div id="stockTable"></div><div id="stockLotPanel"></div>';
  window.stockRows = rows;
  renderStockTable();
}
function renderStockTable() {
  window.stockFilterValue = document.getElementById('stockFilter')?.value || '';
  const q = window.stockFilterValue.toLowerCase();
  const rows = (window.stockRows || []).filter(r => !q || JSON.stringify(r).toLowerCase().includes(q));
  document.getElementById('stockTable').innerHTML = table(rows, 'stocks', {actions:true});
}
async function openStockLots(code) {
  window.selectedStockCode = code;
  const detail = await api('/api/stocks/' + encodeURIComponent(code));
  const lots = detail.lots || [];
  const stock = detail.stock || {};
  window.stockLotRows = lots;
  document.getElementById('stockLotPanel').innerHTML = `<div class="detailPanel"><h3>${esc(stock.name || '')} ${esc(code)} 보유 LOT</h3><p class="muted">종목별 LOT 수익률, 잔여 수량, cleanup/stale 상태를 확인하고 OPEN LOT은 수동 매도 요청 화면으로 보낼 수 있습니다.</p>${table(lots, 'stockLots', {actions:true})}</div>`;
}
async function openManualBuy(code) {
  await loadManualOrders();
  document.getElementById('manualBuyCode').value = code;
  document.getElementById('manualBuyAmount').focus();
}
async function openManualSell(code, lotId, remainingQty) {
  await loadManualOrders();
  document.getElementById('manualSellCode').value = code;
  document.getElementById('manualSellLot').value = lotId;
  document.getElementById('manualSellQty').value = remainingQty || '';
  document.getElementById('manualSellQty').focus();
}
async function loadLots() {
  currentView = 'lots';
  const rows = await api('/api/lots');
  window.lotRows = rows;
  if (!sortState.lots) sortState.lots = {key:'unrealized_pnl_rate', dir:'asc'};
  document.getElementById('content').innerHTML = '<h2>LOT</h2><div class="manualBox"><strong>LOT별 수동 매도 요청</strong><p class="muted">OPEN LOT 행의 수동 매도 버튼으로 LOT ID와 잔여 수량을 자동 입력할 수 있습니다. CLOSED LOT, open SELL order, RISK_BLOCKED, SYNC_REQUIRED, runtime sell pause 상태에서는 요청 생성도 차단됩니다.</p></div>' + table(rows, 'lots', {actions:true});
}
async function loadOrders() {
  currentView = 'orders';
  const o=await api('/api/orders'), f=await api('/api/fills');
  window.orderRows = o;
  window.fillRows = f;
  if (!sortState.orders) sortState.orders = {key:'requested_at', dir:'desc'};
  if (!sortState.fills) sortState.fills = {key:'filled_at', dir:'desc'};
  document.getElementById('content').innerHTML = '<h2>주문</h2>'+table(o, 'orders')+'<h2>체결</h2>'+table(f, 'fills');
}
async function loadLogs() { currentView = 'logs'; const l=await api('/api/logs/tail?limit=300'); document.getElementById('content').innerHTML = '<h2>로그</h2><div class="manualBox"><strong>주요 차단 사유</strong><p>open_order_exists_for_cleanup: 미체결 주문이 있어 cleanup 매도 차단<br>risk_blocked_buy_sell_blocked: 위험 차단으로 매수/매도 모두 차단<br>sync_required: DB/KIS 동기화 필요</p></div><pre>'+esc(l.lines.join('\n'))+'</pre>'; }
async function loadExecution() { currentView = 'execution'; const e=await api('/api/execution-mapping/status'); document.getElementById('content').innerHTML = '<h2>체결 필드 검증</h2>'+metrics(e)+'<pre>'+esc(e.raw_log_line || '')+'</pre>'; }
async function loadRuntime() {
  currentView = 'runtime';
  const r=await api('/api/runtime');
  document.getElementById('content').innerHTML = `<h2>런타임 제어</h2>${metrics(r)}
  <div class="manualBox"><strong>봇 루프 제어</strong><p class="muted">봇 프로세스가 이미 실행 중일 때 적용됩니다. UI가 새 프로세스를 띄우거나 KIS 주문 API를 직접 호출하지 않습니다.</p>
    <button onclick="runtimePost('/api/runtime/start-loop','ui_start_loop')">Start / 루프 재개</button>
    <button onclick="runtimePost('/api/runtime/pause-loop','ui_pause_loop')">Loop Pause</button>
    <button onclick="runtimePost('/api/runtime/reload-config','ui_reload_config')">Reset / Config 다시 읽기</button>
    <p class="muted">Config 저장 후 Reset을 누르면 실행 중인 봇이 다음 루프에서 최신 config를 다시 읽습니다.</p>
  </div>
  <div class="grid">
    <div class="controlCard"><button onclick="runtime('/api/runtime/pause-all')">전체 주문 일시정지</button><p class="muted">모든 신규 주문 요청을 차단합니다.</p></div>
    <div class="controlCard"><button onclick="runtime('/api/runtime/pause-buy')">매수 일시정지</button><p class="muted">신규 매수, 추가매수, 재진입 매수를 차단합니다.</p></div>
    <div class="controlCard"><button onclick="runtime('/api/runtime/pause-sell')">매도 일시정지</button><p class="muted">자동 매도 요청을 차단합니다.</p></div>
    <div class="controlCard"><button onclick="runtime('/api/runtime/pause-cleanup')">Cleanup 일시정지</button><p class="muted">손실 정리 매도만 별도로 차단합니다.</p></div>
    <div class="controlCard"><button onclick="runtime('/api/runtime/pause-reentry')">재진입 일시정지</button><p class="muted">NORMAL/TRAILING 재진입 매수를 차단합니다.</p></div>
    <div class="controlCard"><button onclick="runtime('/api/runtime/resume')">일시정지 해제</button><p class="muted">runtime pause 플래그를 해제합니다.</p></div>
    <div class="controlCard dangerZone"><button class="dangerBtn" onclick="runtime('/api/runtime/emergency-stop')">Emergency Stop 비상정지</button><p class="bad">즉시 모든 주문 요청을 차단하는 비상 정지입니다.</p></div>
  </div>`;
}
async function runtime(path) { await api(path, {method:'POST'}); await loadRuntime(); }
async function runtimePost(path, reason) { await api(path, {method:'POST', body:JSON.stringify({reason})}); await loadRuntime(); }
async function loadManualOrders() {
  currentView = 'manual';
  const cfg = await api('/api/config');
  const requests = await api('/api/manual-order-requests');
  document.getElementById('content').innerHTML = `<h2>수동 주문 요청</h2>
  <div class="manualBox"><strong>현재 기능 상태</strong><p>ui_manual_trading_enabled=${esc(cfg.ui_manual_trading_enabled)}. UI는 KIS 주문 API를 직접 호출하지 않고 manual_order_requests 큐에 요청만 생성합니다.</p></div>
  <div class="manualBox"><strong>수동 주문 테스트 방법</strong><p class="muted">1) Config에서 ui_manual_trading_enabled=true로 저장합니다. 2) Runtime Control에서 Reset / Config 다시 읽기를 누릅니다. 3) 여기서 미리보기 후 요청 생성을 누르면 DB의 manual_order_requests에 REQUESTED로 저장되고, 실행 중인 봇이 다음 루프에서 기존 order_manager 경로로 처리합니다.</p></div>
  <div class="grid">
    <div class="controlCard">
      <h3>수동 매수 요청</h3>
      <input id="manualBuyCode" placeholder="종목코드 예: 005930">
      <input id="manualBuyAmount" placeholder="주문금액">
      <input id="manualBuyQty" placeholder="수량 선택 입력">
      <input id="manualBuyPrice" placeholder="미리보기 현재가, 선택 입력">
      <input id="manualBuyConfirm" placeholder="live trading이면 '수동주문 확인' 입력">
      <p><button ${cfg.ui_manual_trading_enabled ? '' : 'disabled'} onclick="previewManual('BUY')">매수 미리보기</button>
      <button ${cfg.ui_manual_trading_enabled ? '' : 'disabled'} onclick="createManual('BUY')">요청 생성</button></p>
      <small class="muted">비활성 상태에서는 설정에서 수동 주문 요청 기능이 비활성화되어 있습니다.</small>
    </div>
    <div class="controlCard">
      <h3>LOT 수동 매도 요청</h3>
      <input id="manualSellCode" placeholder="종목코드">
      <input id="manualSellLot" placeholder="LOT ID">
      <input id="manualSellQty" placeholder="매도 수량, 비우면 전량">
      <input id="manualSellPrice" placeholder="미리보기 현재가, 선택 입력">
      <input id="manualSellConfirm" placeholder="live trading이면 '수동주문 확인' 입력">
      <p><button ${cfg.ui_manual_trading_enabled ? '' : 'disabled'} onclick="previewManual('SELL')">매도 미리보기</button>
      <button ${cfg.ui_manual_trading_enabled ? '' : 'disabled'} onclick="createManual('SELL')">요청 생성</button></p>
      <small class="muted">CLOSED LOT, open SELL order, RISK_BLOCKED, SYNC_REQUIRED, runtime sell pause 상태에서는 차단됩니다.</small>
    </div>
  </div>
  <h3>미리보기 / 생성 결과</h3><pre id="manualResult"></pre>
  <h3>수동 주문 요청 목록</h3>${table(requests, 'manualRequests')}`;
}
function manualPayload(side) {
  if (side === 'BUY') return {
    side:'BUY',
    code:document.getElementById('manualBuyCode').value,
    amount:Number(document.getElementById('manualBuyAmount').value || 0),
    quantity:Number(document.getElementById('manualBuyQty').value || 0),
    current_price:Number(document.getElementById('manualBuyPrice')?.value || 0),
    confirm_text:document.getElementById('manualBuyConfirm').value,
    requested_by:'local_ui'
  };
  return {
    side:'SELL',
    code:document.getElementById('manualSellCode').value,
    lot_id:document.getElementById('manualSellLot').value,
    quantity:Number(document.getElementById('manualSellQty').value || 0),
    current_price:Number(document.getElementById('manualSellPrice')?.value || 0),
    confirm_text:document.getElementById('manualSellConfirm').value,
    requested_by:'local_ui'
  };
}
async function previewManual(side) {
  const r = await api('/api/manual-orders/preview', {method:'POST', body:JSON.stringify(manualPayload(side))});
  document.getElementById('manualResult').textContent = JSON.stringify(r, null, 2);
}
async function createManual(side) {
  const preview = await api('/api/manual-orders/preview', {method:'POST', body:JSON.stringify(manualPayload(side))});
  if (!preview.can_create) { document.getElementById('manualResult').textContent = JSON.stringify(preview, null, 2); return; }
  if (!confirm('manual order request를 생성합니다. 실제 주문은 Bot Core가 별도로 처리합니다. 계속할까요?')) return;
  const r = await api('/api/manual-orders', {method:'POST', body:JSON.stringify(manualPayload(side))});
  await loadManualOrders();
  document.getElementById('manualResult').textContent = JSON.stringify(r, null, 2);
}
async function loadConfig() {
  currentView = 'config';
  configOriginal = await api('/api/config');
  configDraft = JSON.parse(JSON.stringify(configOriginal));
  configSchema = await api('/api/config/schema');
  renderConfig();
}
function renderConfig(sectionName) {
  if (!configSchema || !configDraft) return;
  const sections = Object.keys(configSchema.sections);
  const selected = sectionName || window.configSection || sections[0];
  window.configSection = selected;
  const nav = '<div class="sectionNav">' + sections.map(s => `<button class="${s===selected?'primary':''}" onclick="renderConfig('${esc(s)}')">${esc(s)}</button>`).join('') + '</div>';
  const fields = (configSchema.sections[selected] || []).map(renderConfigField).join('');
  const raw = `<details><summary>고급 / 원본 JSON 보기</summary><p class="warn">원본 JSON 직접 편집도 같은 validation, diff, backup, atomic save를 거칩니다.</p><textarea id="rawConfig" oninput="rawConfigChanged()">${esc(JSON.stringify(configDraft, null, 2))}</textarea></details>`;
  document.getElementById('content').innerHTML = `<h2>Config</h2>${nav}<div>${fields}</div>${raw}<div class="configActions"><button onclick="previewConfigChanges()">변경사항 확인</button> <button class="primary" onclick="saveConfigForm()">백업 후 저장</button> <button onclick="loadConfig()">되돌리기</button><pre id="cfgResult"></pre></div>`;
}
function renderConfigField(meta) {
  const current = getPath(configDraft, meta.key);
  const original = getPath(configOriginal, meta.key);
  const changed = JSON.stringify(current) !== JSON.stringify(original);
  const danger = meta.danger_confirm_required ? '<span class="critical"> 이중 확인 필요</span>' : '';
  let input = '';
  if (meta.type === 'boolean') input = `<select onchange="configInputChanged('${esc(meta.key)}', this.value, '${esc(meta.config_format)}')"><option value="true" ${current===true?'selected':''}>true</option><option value="false" ${current===false?'selected':''}>false</option></select>`;
  else if (meta.type === 'json') input = `<textarea onchange="configInputChanged('${esc(meta.key)}', this.value, 'json')">${esc(JSON.stringify(current, null, 2))}</textarea>`;
  else input = `<input type="${meta.type === 'time' ? 'time' : 'text'}" value="${esc(toDisplay(current, meta))}" onchange="configInputChanged('${esc(meta.key)}', this.value, '${esc(meta.config_format)}')">`;
  return `<div class="field ${changed ? 'changed' : ''}"><div><label>${esc(meta.label_ko)}</label><small>${esc(meta.key)}</small></div><div>${input}<small>${esc(meta.description_ko || '')}${danger}<br>단위: ${esc(meta.unit || '')} / 저장 형식: ${esc(meta.config_format || '')} / 재시작 필요: ${meta.requires_restart ? '예' : '아니오'}</small></div><div><small>현재값</small>${esc(toDisplay(original, meta))}</div></div>`;
}
function getPath(obj, path) { return path.split('.').reduce((acc,k) => acc == null ? undefined : acc[k], obj); }
function setPath(obj, path, value) { const parts = path.split('.'); let target = obj; parts.slice(0,-1).forEach(k => { if (!target[k]) target[k] = {}; target = target[k]; }); target[parts.at(-1)] = value; }
function toDisplay(value, meta) {
  if (value === null || value === undefined) return '';
  if (meta.display_format === 'decimal_percent') return Number(value) * 100;
  if (meta.type === 'json') return JSON.stringify(value, null, 2);
  return value;
}
function fromDisplay(value, format) {
  if (format === 'boolean') return value === true || value === 'true';
  if (format === 'json') return JSON.parse(value);
  if (format === 'decimal_rate') return Number(value) / 100;
  if (format === 'integer') return value === '' ? null : parseInt(value, 10);
  if (format === 'number' || format === 'percent_value') return value === '' ? null : Number(value);
  if (format === 'nullable_integer') return value === '' ? null : parseInt(value, 10);
  return value;
}
function configInputChanged(path, value, format) {
  try {
    setPath(configDraft, path, fromDisplay(value, format));
    renderConfig(window.configSection);
  } catch (err) {
    document.getElementById('cfgResult').textContent = '입력값 오류: ' + err.message;
  }
}
function rawConfigChanged() {
  try {
    configDraft = JSON.parse(document.getElementById('rawConfig').value);
  } catch (err) {
    document.getElementById('cfgResult').textContent = 'JSON 오류: ' + err.message;
  }
}
function diffConfig(before, after, prefix='') {
  const keys = new Set([...Object.keys(before || {}), ...Object.keys(after || {})]);
  const out = [];
  keys.forEach(k => {
    const path = prefix ? prefix + '.' + k : k;
    const a = before ? before[k] : undefined, b = after ? after[k] : undefined;
    if (a && b && typeof a === 'object' && typeof b === 'object' && !Array.isArray(a) && !Array.isArray(b)) out.push(...diffConfig(a,b,path));
    else if (JSON.stringify(a) !== JSON.stringify(b)) out.push({key:path, before:a, after:b});
  });
  return out;
}
async function previewConfigChanges() {
  const changes = diffConfig(configOriginal, configDraft);
  const validation = await api('/api/config/validate', {method:'POST', body:JSON.stringify(configDraft)});
  const dangerKeys = new Set(configSchema.danger_confirm_keys || []);
  const danger = changes.filter(c => dangerKeys.has(c.key)).map(c => c.key);
  document.getElementById('cfgResult').textContent = JSON.stringify({valid: validation.valid, errors: validation.errors, danger_confirm_required: danger, changes}, null, 2);
}
async function saveConfigForm() {
  const changes = diffConfig(configOriginal, configDraft);
  const dangerKeys = new Set(configSchema.danger_confirm_keys || []);
  const danger = changes.filter(c => dangerKeys.has(c.key)).map(c => c.key);
  const validation = await api('/api/config/validate', {method:'POST', body:JSON.stringify(configDraft)});
  if (!validation.valid) { document.getElementById('cfgResult').textContent = JSON.stringify(validation, null, 2); return; }
  if (danger.length && !confirm('위험 설정 변경이 포함되어 있습니다: ' + danger.join(', ') + '\n계속할까요?')) return;
  if (!confirm('config/backups에 백업을 만들고 atomic save를 수행합니다. 계속할까요?')) return;
  const r = await api('/api/config', {method:'PATCH', body:JSON.stringify(configDraft)});
  document.getElementById('cfgResult').textContent = JSON.stringify({saved:r, changes}, null, 2);
  configOriginal = await api('/api/config');
  configDraft = JSON.parse(JSON.stringify(configOriginal));
}
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
                "/api/config/schema": self.service.config_schema,
                "/api/stocks": self.service.stocks,
                "/api/positions": self.service.positions,
                "/api/lots": self.service.lots,
                "/api/orders": self.service.orders,
                "/api/fills": self.service.fills,
                "/api/manual-order-requests": self.service.manual_order_requests,
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
            if parsed.path == "/api/runtime/start-loop":
                self._send_json(self.service.runtime_set(bot_paused=False, reason=data.get("reason", "ui_start_loop")))
                return
            if parsed.path == "/api/runtime/pause-loop":
                self._send_json(self.service.runtime_set(bot_paused=True, reason=data.get("reason", "ui_pause_loop")))
                return
            if parsed.path == "/api/runtime/reload-config":
                self._send_json(self.service.runtime_set(config_reload_requested=True, reason=data.get("reason", "ui_reload_config")))
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
            if parsed.path == "/api/manual-orders/preview":
                self._send_json(self.service.manual_order_preview(data))
                return
            if parsed.path == "/api/manual-orders":
                self._send_json(self.service.create_manual_order_request(data))
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


def build_server(config_path: Path, host: str = "127.0.0.1", port: int = 8765, runtime_path: Path | None = None) -> ThreadingHTTPServer:
    service = UIService(config_path, runtime_path or DEFAULT_RUNTIME_CONTROL_PATH)

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


