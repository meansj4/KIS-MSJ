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
    .readableWrap { overflow: visible; max-height: none; border: 1px solid #e2e7ec; border-radius: 8px; background: white; margin: 8px 0 10px; }
    .readableWrap table { width: 100%; min-width: 0; table-layout: auto; }
    .readableWrap th, .readableWrap td { white-space: normal; overflow-wrap: anywhere; }
    .field .readableWrap { max-height: 320px; overflow-y: auto; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { border-bottom: 1px solid #e6ebf0; padding: 7px; text-align: left; white-space: nowrap; vertical-align: top; }
    th { background: #e8eef5; position: sticky; top: 0; z-index: 1; cursor: pointer; user-select: none; font-weight: 800; min-width: 72px; max-width: 720px; }
    th.resizableTh { position: sticky; overflow: hidden; padding-right: 14px; }
    .colResizeHandle { position: absolute; top: 0; right: 0; bottom: 0; width: 9px; cursor: col-resize; z-index: 3; }
    .colResizeHandle::after { content: ""; position: absolute; top: 20%; bottom: 20%; right: 3px; width: 2px; background: transparent; }
    .colResizeHandle:hover::after, body.resizing-table .colResizeHandle::after { background: #1f6feb; }
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
    .usageBar { height: 9px; border-radius: 999px; background: #e5e7eb; overflow: hidden; margin-top: 8px; }
    .usageFill { height: 100%; border-radius: 999px; background: #16a34a; width: 0%; }
    .usageFill.warning { background: #f59e0b; }
    .usageFill.danger { background: #dc2626; }
    .usageFill.over { background: #7f1d1d; }
    .usageFill.unlimited { background: #64748b; }
    .dashboardCard { border: 1px solid #e2e7ec; border-radius: 8px; padding: 12px; background: #fff; }
    .dashboardCard h3 { margin: 0 0 8px; font-size: 14px; }
    .dashboardValue { font-size: 22px; font-weight: 800; font-variant-numeric: tabular-nums; }
    .pos { color: #15803d; font-weight: 700; }
    .neg { color: #b91c1c; font-weight: 700; }
    .muted { color: #7b8794; }
    .controlCard { border: 1px solid #d7dde3; border-radius: 8px; padding: 12px; background: #fbfcfe; }
    .controlCard.dangerZone { border-color: #ef9a9a; background: #fff5f5; }
    .manualBox { border: 1px dashed #b8c2cc; border-radius: 8px; padding: 12px; background: #fbfcfe; margin: 10px 0; }
    .warn { color: #a15c00; font-weight: 700; }
    .bad { color: #b42318; font-weight: 700; }
    .field { display: grid; grid-template-columns: minmax(150px, var(--config-label-width, 230px)) 8px minmax(260px, 1fr) 8px minmax(190px, var(--config-current-width, 320px)); gap: 10px; align-items: stretch; border-bottom: 1px solid #e6ebf0; padding: 12px 0; }
    .field label { font-weight: 700; }
    .field small { display: block; color: #59636e; margin-top: 4px; line-height: 1.35; }
    .field input, .field textarea, .field select { width: 100%; box-sizing: border-box; }
    .field textarea { min-height: 90px; }
    .sectionNav { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 12px; }
    .critical, .dangerText { color: #b42318; font-weight: 700; }
    .changed { background: #fff7db; }
    .configActions { position: sticky; bottom: 0; background: white; border-top: 1px solid #d7dde3; padding: 10px 0; }
    .configLayoutControls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; background: #fbfcfe; border: 1px solid #e2e7ec; border-radius: 8px; padding: 10px; margin: 8px 0 12px; }
    .configResizeHandle { cursor: col-resize; min-width: 8px; align-self: stretch; position: relative; border-radius: 5px; }
    .configResizeHandle::before { content: ""; position: absolute; top: 0; bottom: 0; left: 3px; width: 2px; background: #d7dde3; }
    .configResizeHandle:hover::before, body.resizing-config .configResizeHandle::before { background: #1f6feb; width: 3px; }
    body.resizing-config, body.resizing-table { cursor: col-resize; user-select: none; }
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
    @media (max-width: 980px) {
      .field { grid-template-columns: 1fr; }
      .field > div { min-width: 0; }
      .configResizeHandle { display: none; }
    }
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
    <button onclick="loadPortfolioDashboard()">운용 현황 Portfolio/Risk</button>
    <button onclick="loadDashboard()">대시보드 Dashboard</button>
    <button onclick="loadStocks()">종목 Stocks</button>
    <button onclick="loadLots()">LOT Lots</button>
    <button onclick="loadOrders()">주문/체결 Orders/Fills</button>
    <button onclick="loadLogs()">로그 Logs</button>
    <button onclick="loadConfig()">설정 Config</button>
    <button onclick="loadRuntime()">실행 제어 Runtime</button>
    <button onclick="loadManualOrders()">수동 주문 Manual</button>
    <button onclick="loadNewSeason()">새 시즌 New Season</button>
    <button onclick="loadReviewRequired()">수동검토 Review</button>
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
  processing_started_at:'처리 시작시각', processing_claimed_by:'처리 claim 주체',
  claim_attempt_count:'claim 시도 횟수', processing_age_minutes:'처리 경과(분)',
  processing_stale:'처리 멈춤 가능성', stale_processing_reason:'처리 멈춤 사유',
  safe_requeue_allowed:'재시도 가능', safe_cancel_allowed:'차단 처리 가능',
  last_processing_error:'마지막 처리 오류',
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
  if (typeof value === 'number') return esc(formatNumber(value));
  const translated = valueLabel(value);
  if (/state|status|reason|side|dedupe|flag|enabled|paused|candidate|stale|duplicate|reflected/i.test(key)) {
    return `<span class="badge ${badgeClass(value)}">${esc(translated)}</span><span class="key">${esc(value)}</span>`;
  }
  if (/^-?\d+\.\d{4,}$/.test(String(value))) return esc(formatNumber(Number(value)));
  return esc(value);
}
function formatNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  if (Number.isInteger(n)) return String(n);
  return String(Math.round(n * 1000) / 1000);
}
const sortState = {};
const DEFAULT_COLUMNS = {
  stocks: ['code','name','enabled','position_state','current_price','open_lot_count','lot_unit_amount','max_symbol_amount','max_lots_per_symbol','lot_sizing_bucket','invested_amount','profit_loss_pct','risk_block_reasons','skip_reason','final_block_reason'],
  lots: ['lot_id','code','name','status','buy_price','remaining_quantity','current_price','unrealized_pnl','unrealized_pnl_rate','age_weeks','effective_target_profit_rate','sell_trigger_price','cleanup_candidate','stale_lot','last_sell_reason'],
  stockLots: ['lot_id','code','name','status','buy_price','remaining_quantity','current_price','unrealized_pnl','unrealized_pnl_rate','age_weeks','effective_target_profit_rate','sell_trigger_price','cleanup_candidate','stale_lot','last_sell_reason'],
  orders: ['order_id','code','name','side','status','quantity','limit_price','reason','requested_at','updated_at','lot_id','sell_reason','reentry_type'],
  fills: ['fill_id','execution_id','dedupe_key_type','order_id','code','name','side','price','quantity','filled_at','lot_id','sell_reason','reentry_type'],
  manualRequests: ['request_id','code','side','quantity','amount','lot_id','status','processing_stale','processing_age_minutes','claim_attempt_count','last_processing_error','stale_processing_reason','recovery_block_reason','block_reason','linked_order_id','requested_at','updated_at'],
  reviewRequired: ['code','name','position_state','review_reason','current_pnl_rate','open_lot_count','stale_lot_count','sync_status','lot_quantity_mismatch','profitable_lot_count'],
  portfolioRealizedDetail: ['code','name','lot_id','buy_filled_at','sell_filled_at','sell_quantity','buy_price','sell_price','buy_amount','sell_amount','realized_pnl','realized_pnl_rate','fee_tax_estimate','sell_reason','holding_days'],
  portfolioUnrealizedDetail: ['code','name','lot_id','buy_filled_at','remaining_quantity','buy_price','remaining_buy_amount','current_price','current_market_value','unrealized_pnl','unrealized_pnl_rate','target_price','target_amount','target_remaining_amount','target_remaining_rate','stale_lot','cleanup_candidate','price_snapshot_at']
};
const columnPrefs = {};
let portfolioDetailState = null;
let configOriginal = null;
let configDraft = null;
let configSchema = null;
let tableColumnWidths = (() => {
  try { return JSON.parse(localStorage.getItem('kisTableColumnWidths') || '{}'); } catch (err) { return {}; }
})();
let tableResizeState = null;
let configLayout = (() => {
  try { return JSON.parse(localStorage.getItem('kisConfigLayout') || '{}'); } catch (err) { return {}; }
})();
let configResizeState = null;
function clampNumber(value, minValue, maxValue) {
  return Math.max(minValue, Math.min(maxValue, Number(value)));
}
function normalizedConfigLayout() {
  return {
    label: clampNumber(configLayout.label || 230, 150, 460),
    current: clampNumber(configLayout.current || 320, 190, 720)
  };
}
function saveConfigLayout() {
  localStorage.setItem('kisConfigLayout', JSON.stringify(normalizedConfigLayout()));
}
function applyConfigLayout() {
  configLayout = normalizedConfigLayout();
  document.documentElement.style.setProperty('--config-label-width', configLayout.label + 'px');
  document.documentElement.style.setProperty('--config-current-width', configLayout.current + 'px');
}
function startConfigColumnResize(event, target) {
  event.preventDefault();
  const layout = normalizedConfigLayout();
  configResizeState = {target, startX: event.clientX, startLabel: layout.label, startCurrent: layout.current};
  document.body.classList.add('resizing-config');
  window.addEventListener('mousemove', onConfigColumnResize);
  window.addEventListener('mouseup', stopConfigColumnResize, {once:true});
}
function onConfigColumnResize(event) {
  if (!configResizeState) return;
  const dx = event.clientX - configResizeState.startX;
  if (configResizeState.target === 'label') {
    configLayout.label = clampNumber(configResizeState.startLabel + dx, 150, 460);
  } else if (configResizeState.target === 'current') {
    configLayout.current = clampNumber(configResizeState.startCurrent - dx, 190, 720);
  }
  applyConfigLayout();
  saveConfigLayout();
}
function stopConfigColumnResize() {
  window.removeEventListener('mousemove', onConfigColumnResize);
  document.body.classList.remove('resizing-config');
  configResizeState = null;
}
function resetConfigLayout() {
  configLayout = {label:230, current:320};
  applyConfigLayout();
  saveConfigLayout();
  renderConfig(window.configSection);
}
applyConfigLayout();
function tableColumnWidth(tableId, key) {
  const width = tableColumnWidths[tableId] && tableColumnWidths[tableId][key];
  return width ? clampNumber(width, 72, 720) : null;
}
function tableColumnStyle(tableId, key) {
  const width = tableColumnWidth(tableId, key);
  return width ? `width:${width}px;min-width:${width}px;max-width:${width}px;` : '';
}
function saveTableColumnWidths() {
  localStorage.setItem('kisTableColumnWidths', JSON.stringify(tableColumnWidths));
}
function startTableColumnResize(event, tableId, key) {
  event.preventDefault();
  event.stopPropagation();
  const th = event.target.closest('th');
  tableResizeState = {tableId, key, startX: event.clientX, startWidth: th ? th.offsetWidth : (tableColumnWidth(tableId, key) || 120)};
  document.body.classList.add('resizing-table');
  window.addEventListener('mousemove', onTableColumnResize);
  window.addEventListener('mouseup', stopTableColumnResize, {once:true});
}
function onTableColumnResize(event) {
  if (!tableResizeState) return;
  const width = clampNumber(tableResizeState.startWidth + (event.clientX - tableResizeState.startX), 72, 720);
  const {tableId, key} = tableResizeState;
  if (!tableColumnWidths[tableId]) tableColumnWidths[tableId] = {};
  tableColumnWidths[tableId][key] = width;
  saveTableColumnWidths();
  document.querySelectorAll(`[data-table-id="${tableId}"] [data-col-key="${key}"]`).forEach(el => {
    el.style.width = width + 'px';
    el.style.minWidth = width + 'px';
    el.style.maxWidth = width + 'px';
  });
}
function stopTableColumnResize() {
  window.removeEventListener('mousemove', onTableColumnResize);
  document.body.classList.remove('resizing-table');
  tableResizeState = null;
}
function table(rows, tableId='default', opts={}) {
  if (!rows || !rows.length) return '<p>No data</p>';
  const keys = Object.keys(rows[0]);
  const visibleKeys = visibleColumns(tableId, keys);
  const state = sortState[tableId] || opts.defaultSort || null;
  const sorted = state ? sortRows(rows, state.key, state.dir) : [...rows];
  const actionHeader = opts.actions ? `<th class="resizableTh" data-col-key="__actions" style="${tableColumnStyle(tableId, '__actions')}">작업<span class="key">actions</span><span class="colResizeHandle" title="컬럼 폭 조절" onmousedown="startTableColumnResize(event, '${esc(tableId)}', '__actions')"></span></th>` : '';
  const actionCells = (row) => opts.actions ? `<td>${rowActions(tableId, row)}</td>` : '';
  const tableKeys = opts.actions ? ['__actions', ...visibleKeys] : visibleKeys;
  const colgroup = '<colgroup>' + tableKeys.map(k => `<col data-col-key="${esc(k)}" style="${tableColumnStyle(tableId, k)}">`).join('') + '</colgroup>';
  return columnControls(tableId, keys, visibleKeys) + '<div class="sortHint">컬럼 헤더를 클릭하면 정렬됩니다. 헤더 오른쪽 경계를 드래그하면 컬럼 폭을 조절할 수 있습니다.</div><div class="tableWrap"><table data-table-id="'+esc(tableId)+'">' +
    colgroup + '<thead><tr>' +
    actionHeader +
    visibleKeys.map(k => `<th class="resizableTh" data-col-key="${esc(k)}" style="${tableColumnStyle(tableId, k)}" onclick="sortTable('${esc(tableId)}','${esc(k)}')">${headerLabel(k)}${state && state.key === k ? (state.dir === 'asc' ? ' ▲' : ' ▼') : ''}<span class="colResizeHandle" title="컬럼 폭 조절" onmousedown="startTableColumnResize(event, '${esc(tableId)}', '${esc(k)}')"></span></th>`).join('') +
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
  if ((tableId === 'portfolioRealizedDetail' || tableId === 'portfolioUnrealizedDetail') && portfolioDetailState) { renderPortfolioDetailPanel(portfolioDetailState); return; }
  reloadCurrent();
}
function showAllColumns(tableId) {
  const rows = rowsForTable(tableId);
  if (rows.length) columnPrefs[tableId] = new Set(Object.keys(rows[0]));
  if (tableId === 'stockLots' && window.selectedStockCode) { openStockLots(window.selectedStockCode); return; }
  if ((tableId === 'portfolioRealizedDetail' || tableId === 'portfolioUnrealizedDetail') && portfolioDetailState) { renderPortfolioDetailPanel(portfolioDetailState); return; }
  reloadCurrent();
}
function toggleColumn(tableId, key, checked) {
  const rows = rowsForTable(tableId);
  const allKeys = rows.length ? Object.keys(rows[0]) : [];
  const current = new Set(visibleColumns(tableId, allKeys));
  if (checked) current.add(key); else current.delete(key);
  columnPrefs[tableId] = current;
  if (tableId === 'stockLots' && window.selectedStockCode) { openStockLots(window.selectedStockCode); return; }
  if ((tableId === 'portfolioRealizedDetail' || tableId === 'portfolioUnrealizedDetail') && portfolioDetailState) { renderPortfolioDetailPanel(portfolioDetailState); return; }
  reloadCurrent();
}
function rowsForTable(tableId) {
  if (tableId === 'stocks') return window.stockRows || [];
  if (tableId === 'lots') return window.lotRows || [];
  if (tableId === 'stockLots') return window.stockLotRows || [];
  if (tableId === 'orders') return window.orderRows || [];
  if (tableId === 'fills') return window.fillRows || [];
  if (tableId === 'manualRequests') return window.manualRequestRows || [];
  if (tableId === 'reviewRequired') return window.reviewRequiredRows || [];
  if (tableId === 'portfolioRealizedDetail' || tableId === 'portfolioUnrealizedDetail') return window.portfolioDetailRows || [];
  return [];
}function rowActions(tableId, row) {
  if (tableId === 'stocks') {
    return `<div class="rowActions"><button onclick="openStockLots('${esc(row.code)}')">LOT 보기</button><button onclick="openManualBuy('${esc(row.code)}')">수동 매수</button></div>`;
  }
  if (tableId === 'lots' || tableId === 'stockLots') {
    const disabled = Number(row.remaining_quantity || 0) <= 0 || String(row.status || '') === 'CLOSED' ? 'disabled' : '';
    return `<div class="rowActions"><button ${disabled} onclick="openManualSell('${esc(row.code)}','${esc(row.lot_id)}',${Number(row.remaining_quantity || 0)})">수동 매도</button></div>`;
  }
  if (tableId === 'reviewRequired') {
    return `<div class="rowActions"><button onclick="reviewRecheck('${esc(row.code)}')">상태 재평가</button><button onclick="reviewAck('${esc(row.code)}')">확인/메모</button><button onclick="openStockLots('${esc(row.code)}')">수익권 LOT 보기</button></div>`;
  }
  if (tableId === 'manualRequests') {
    const requeueDisabled = row.safe_requeue_allowed ? '' : 'disabled';
    const cancelDisabled = row.safe_cancel_allowed ? '' : 'disabled';
    return `<div class="rowActions"><button ${requeueDisabled} onclick="manualRequestRequeue('${esc(row.request_id)}')">Requeue</button><button ${cancelDisabled} onclick="manualRequestCancel('${esc(row.request_id)}')">Block</button></div>`;
  }
  return '';
}
function sortTable(tableId, key) {
  const current = sortState[tableId];
  if (!current || current.key !== key) sortState[tableId] = {key, dir:'asc'};
  else if (current.dir === 'asc') sortState[tableId] = {key, dir:'desc'};
  else delete sortState[tableId];
  if ((tableId === 'portfolioRealizedDetail' || tableId === 'portfolioUnrealizedDetail') && portfolioDetailState) { renderPortfolioDetailPanel(portfolioDetailState); return; }
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
  if (currentView === 'portfolioDashboard') return loadPortfolioDashboard();
  if (currentView === 'stocks') return loadStocks();
  if (currentView === 'lots') return loadLots();
  if (currentView === 'orders') return loadOrders();
  if (currentView === 'runtime') return loadRuntime();
  if (currentView === 'manual') return loadManualOrders();
  if (currentView === 'newSeason') return loadNewSeason();
  if (currentView === 'reviewRequired') return loadReviewRequired();
  if (currentView === 'logs') return loadLogs();
  if (currentView === 'config') return renderConfig();
  return loadDashboard();
}
function metrics(obj) {
  return '<div class="grid">' + Object.entries(obj || {}).map(([k,v]) => `<div class="metric"><strong>${esc(labelFor(k))}<span class="key">${esc(k)}</span></strong>${typeof v === 'object' && v !== null ? renderReadableObject(v) : displayCell(k, v)}</div>`).join('') + '</div>';
}
function renderReadableObject(value, opts={}) {
  if (value === null || value === undefined || value === '') return '<span class="empty">-</span>';
  if (Array.isArray(value)) {
    if (!value.length) return '<span class="empty">-</span>';
    if (value.every(item => item && typeof item === 'object' && !Array.isArray(item))) {
      const keys = Array.from(new Set(value.flatMap(item => Object.keys(item))));
      const rows = value.map(item => '<tr>' + keys.map(k => `<td class="${cellClass(k, item[k])}">${displayCell(k, item[k])}</td>`).join('') + '</tr>').join('');
      return `<div class="tableWrap"><table><thead><tr>${keys.map(k => `<th>${headerLabel(k)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>${opts.raw ? rawDetails(value) : ''}`;
    }
    return '<div>' + value.map(v => `<span class="badge neutral">${esc(v)}</span>`).join(' ') + '</div>' + (opts.raw ? rawDetails(value) : '');
  }
  if (typeof value === 'object') {
    const rows = Object.entries(value).map(([k,v]) => `<tr><th>${headerLabel(k)}</th><td>${typeof v === 'object' && v !== null ? renderReadableObject(v) : displayCell(k, v)}</td></tr>`).join('');
    return `<div class="tableWrap"><table><tbody>${rows}</tbody></table></div>${opts.raw ? rawDetails(value) : ''}`;
  }
  return displayCell('', value);
}
function rawDetails(value) {
  return `<details><summary>원본 JSON 보기</summary><pre>${esc(JSON.stringify(value, null, 2))}</pre></details>`;
}
function renderResult(targetId, value) {
  const el = document.getElementById(targetId);
  if (!el) return;
  el.innerHTML = renderReadableObject(value, {raw:true});
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
  document.getElementById('content').innerHTML = `<h2>대시보드</h2><h3>핵심 요약</h3>${metrics(top)}<h3>Analysis data status</h3>${metrics(s.analysis_status || {})}<h3>봇 상태</h3>${metrics(s.bot)}<h3>계좌/리스크</h3>${metrics(s.account_risk)}<h3>보유 상태별 종목 수</h3>${metrics(s.position_state_counts)}<h3>주문 상태</h3>${metrics(s.order_status_counts)}<h3>경고</h3>${table(s.warnings, 'warnings')}<h3>런타임 제어</h3>${metrics(s.runtime_control)}`;
}
async function loadPortfolioDashboard() {
  currentView = 'portfolioDashboard';
  const d = await api('/api/portfolio-dashboard');
  const summary = d.overall_summary || {};
  document.getElementById('content').innerHTML = `
    <h2>운용 현황</h2>
    <div class="manualBox"><strong>Read-only</strong><p class="muted">이 탭은 DB 조회만 수행하며 주문 API 호출, LOT/position/fill 변경, DB reset을 하지 않습니다. ${esc(d.pre_after_night_status?.message || '')}</p></div>
    <h3>전체 요약</h3>
    <div class="grid">
      ${summaryCard('총 매수 금액', summary.total_buy_amount, 'KRW', 'BUY fill 누적 체결금액')}
      ${summaryCard('총 매수 LOT', summary.total_buy_lot_count, 'LOTS', 'BUY fill 기준 unique LOT')}
      ${summaryCard('현재 보유 원금', summary.current_holding_buy_amount, 'KRW', 'OPEN LOT remaining cost')}
      ${summaryCard('현재 OPEN LOT', summary.current_holding_lot_count, 'LOTS', 'remaining_quantity > 0')}
      ${summaryCard('실현 수익', summary.realized_pnl, 'KRW', 'SELL fill net estimate')}
      ${summaryCard('실현 수익률', summary.realized_pnl_rate, 'RATE', 'realized_pnl / sold cost')}
      ${summaryCard('평가 수익', summary.unrealized_pnl, 'KRW', 'saved current price basis')}
      ${summaryCard('평가 수익률', summary.unrealized_pnl_rate, 'RATE', 'unrealized_pnl / open cost')}
    </div>
    <div class="manualBox">
      <strong>손익 상세 drill-down</strong>
      <p class="muted">상세 내역은 버튼을 누를 때만 별도 read-only API로 가져옵니다.</p>
      <button onclick="loadPortfolioDetail('realized')">전체 실현수익 상세</button>
      <button onclick="loadPortfolioDetail('unrealized')">전체 평가수익 상세</button>
      <div id="portfolioDetailPanel"></div>
    </div>
    <h3>한도 사용률</h3>
    <div class="grid">${(d.limit_usage || []).map(usageCard).join('')}</div>
    <h3>날짜별 성과</h3>
    ${portfolioDailyTable(d.daily_summary || [])}
    <h3>종목별 사용률 Top</h3>
    ${table(d.top_symbol_exposures || [], 'portfolioSymbols')}
    <h3>위험/검토 필요 요약</h3>
    ${metrics(d.risk_status_counts || {})}
    <h3>데이터 품질</h3>
    ${metrics(d.data_quality || {})}
    <details><summary>지표 정의 보기</summary>${renderReadableObject(d.definitions || {})}</details>
  `;
}
function summaryCard(title, value, unit, help) {
  const cls = Number(value || 0) < 0 ? 'neg' : (Number(value || 0) > 0 && title.includes('수익') ? 'pos' : '');
  return `<div class="dashboardCard"><h3>${esc(title)}</h3><div class="dashboardValue ${cls}">${formatDashboardValue(value, unit)}</div><p class="muted">${esc(help)}</p></div>`;
}
function usageCard(row) {
  const pct = row.usage_pct == null ? 0 : Math.max(0, Math.min(100, Number(row.usage_pct)));
  const label = row.unlimited ? '무제한/비활성' : `${Number(row.usage_pct || 0).toFixed(1)}%`;
  return `<div class="dashboardCard">
    <h3>${esc(labelFor(row.key || ''))}</h3>
    <div><strong>${displayCell('', row.current)} / ${row.unlimited ? '∞' : displayCell('', row.limit)}</strong> <span class="badge ${usageBadgeClass(row.level)}">${esc(label)}</span></div>
    <div class="usageBar"><div class="usageFill ${esc(row.level || '')}" style="width:${pct}%"></div></div>
    <p class="muted">${esc(row.basis || '')}</p>
  </div>`;
}
function usageBadgeClass(level) {
  if (level === 'over' || level === 'danger') return 'bad';
  if (level === 'warning') return 'warn';
  if (level === 'normal') return 'good';
  return 'neutral';
}
function formatDashboardValue(value, unit) {
  if (unit === 'RATE') return `${(Number(value || 0) * 100).toFixed(2)}%`;
  if (unit === 'KRW') return Number(value || 0).toLocaleString();
  return displayCell('', value);
}
function portfolioDailyTable(rows) {
  const body = (rows || []).map(row => `<tr>
    <td>${esc(row.date || '')}</td>
    <td class="num">${displayCell('', row.buy_amount)}</td>
    <td class="num">${displayCell('', row.sell_amount)}</td>
    <td class="num ${Number(row.realized_pnl || 0) < 0 ? 'neg' : 'pos'}">${displayCell('', row.realized_pnl)}</td>
    <td class="num ${Number(row.unrealized_pnl || 0) < 0 ? 'neg' : 'pos'}">${displayCell('', row.unrealized_pnl)}</td>
    <td><button onclick="loadPortfolioDetail('realized','${esc(row.date || '')}')">실현 상세</button> <button onclick="loadPortfolioDetail('unrealized','${esc(row.date || '')}')">평가 상세</button></td>
  </tr>`).join('');
  return `<div class="tableWrap"><table data-table-id="portfolioDaily"><thead><tr><th>date</th><th>buy_amount</th><th>sell_amount</th><th>realized_pnl</th><th>unrealized_pnl</th><th>detail</th></tr></thead><tbody>${body}</tbody></table></div><p class="muted">날짜별 평가손익은 현재 기준 참고값입니다. 과거 장마감 기준 평가손익이 아닙니다.</p>`;
}
async function loadPortfolioDetail(kind, date='', offset=0) {
  const panel = document.getElementById('portfolioDetailPanel') || document.getElementById('content');
  const endpoint = date ? `/api/portfolio-dashboard/daily/${encodeURIComponent(date)}/${kind}-detail` : `/api/portfolio-dashboard/${kind}-detail`;
  const result = await api(`${endpoint}?limit=100&offset=${offset}`);
  const title = `${date ? date + ' ' : '전체 '}${kind === 'realized' ? '실현수익' : '평가수익'} 상세`;
  panel.innerHTML = `<div class="detailPanel"><h3>${esc(title)}</h3><p class="muted">${esc(result.calculation_basis || '')}</p>${table(result.rows || [], 'portfolioDetail')}<p class="muted">total=${esc(result.total_count || 0)} / limit=100 / offset=${offset}</p><p>${offset > 0 ? `<button onclick="loadPortfolioDetail('${kind}','${esc(date)}',${Math.max(0, offset-100)})">이전</button>` : ''} ${(offset + 100) < Number(result.total_count || 0) ? `<button onclick="loadPortfolioDetail('${kind}','${esc(date)}',${offset+100})">다음</button>` : ''}</p>${renderReadableObject(result.data_quality_notes || [])}</div>`;
}
async function loadPortfolioDetail(kind, date='', offset=0) {
  const panel = document.getElementById('portfolioDetailPanel') || document.getElementById('content');
  const key = `${kind}|${date || ''}|${offset}`;
  if (portfolioDetailState && portfolioDetailState.key === key) {
    portfolioDetailState = null;
    window.portfolioDetailRows = [];
    panel.innerHTML = '';
    return;
  }
  const endpoint = date ? `/api/portfolio-dashboard/daily/${encodeURIComponent(date)}/${kind}-detail` : `/api/portfolio-dashboard/${kind}-detail`;
  const result = await api(`${endpoint}?limit=100&offset=${offset}`);
  const title = `${date ? date + ' ' : '전체 '}${kind === 'realized' ? '실현수익' : '평가수익'} 상세`;
  portfolioDetailState = {key, kind, date, offset, title, result};
  window.portfolioDetailRows = result.rows || [];
  renderPortfolioDetailPanel(portfolioDetailState);
}
function renderPortfolioDetailPanel(state) {
  const panel = document.getElementById('portfolioDetailPanel') || document.getElementById('content');
  const {kind, date, offset, title, result} = state;
  const tableId = kind === 'realized' ? 'portfolioRealizedDetail' : 'portfolioUnrealizedDetail';
  window.portfolioDetailRows = result.rows || [];
  panel.innerHTML = `<div class="detailPanel"><h3>${esc(title)}</h3><p class="muted">${esc(result.calculation_basis || '')}</p><p><button onclick="loadPortfolioDetail('${kind}','${esc(date)}',${offset})">상세 닫기</button></p>${table(result.rows || [], tableId)}<p class="muted">total=${esc(result.total_count || 0)} / limit=100 / offset=${offset}</p><p>${offset > 0 ? `<button onclick="loadPortfolioDetail('${kind}','${esc(date)}',${Math.max(0, offset-100)})">이전</button>` : ''} ${(offset + 100) < Number(result.total_count || 0) ? `<button onclick="loadPortfolioDetail('${kind}','${esc(date)}',${offset+100})">다음</button>` : ''}</p>${renderReadableObject(result.data_quality_notes || [])}</div>`;
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
  window.manualRequestRows = requests || [];
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
  <h3>미리보기 / 생성 결과</h3><div id="manualResult"></div>
  <h3>수동 주문 요청 목록</h3>${table(window.manualRequestRows, 'manualRequests')}`;
}
async function manualRequestRequeue(requestId) {
  const confirmText = prompt("재처리하려면 '수동요청 재처리 확인'을 입력하세요.");
  if (confirmText === null) return;
  const operatorNote = prompt("운영자 메모(선택)") || "";
  const r = await api('/api/manual-order-requests/requeue', {method:'POST', body:JSON.stringify({request_id: requestId, confirm_text: confirmText, operator_note: operatorNote})});
  alert(r.requeued ? '재시도 대기 상태로 되돌렸습니다.' : ('재시도 대기로 변경하지 못했습니다. 사유: ' + (r.block_reason || 'linked_order_id 또는 상태를 확인하세요.')));
  await loadManualOrders();
}
async function manualRequestCancel(requestId) {
  const confirmText = prompt("차단 처리하려면 '수동요청 차단 확인'을 입력하세요.");
  if (confirmText === null) return;
  const operatorNote = prompt("운영자 메모(선택)") || "";
  const r = await api('/api/manual-order-requests/cancel', {method:'POST', body:JSON.stringify({request_id: requestId, reason:'operator_cancel_stale_processing', confirm_text: confirmText, operator_note: operatorNote})});
  alert(r.canceled ? '차단 처리했습니다.' : ('차단 처리하지 못했습니다. 사유: ' + (r.block_reason || 'linked_order_id 또는 상태를 확인하세요.')));
  await loadManualOrders();
}
async function loadNewSeason() {
  currentView = 'newSeason';
  const s = await api('/api/new-season/status');
  const msg = s.guidance || {};
  const blockedGuide = s.block_reason_ko || (s.reset_block_reasons_ko || [])[0] || msg.reason || '';
  const needsBalance = (s.open_lot_count || 0) > 0;
  const snapshotWarnings = Array.isArray(s.snapshot_warnings) ? s.snapshot_warnings : [];
  const snapshotErrors = Array.isArray(s.snapshot_errors) ? s.snapshot_errors : [];
  const snapshotStatus = s.current_plan_exists ? `<div class="manualBox">
    <h3>KIS 잔고 snapshot 검증</h3>
    <p><strong>전량매도 예정표 미리보기</strong>: ${s.current_plan_exists ? '가능' : '없음'}</p>
    <p><strong>전량매도 요청 생성</strong>: ${s.request_creation_allowed ? '가능' : '불가'}</p>
    ${s.request_creation_block_reason ? `<p class="bad"><strong>요청 생성 차단 사유</strong>: ${esc(s.request_creation_block_reason_ko || s.request_creation_block_reason)} <span class="key">${esc(s.request_creation_block_reason)}</span></p>` : ''}
    ${snapshotWarnings.length ? `<p class="warn"><strong>미리보기 경고</strong>: ${esc(snapshotWarnings.join(', '))}</p>` : ''}
    ${snapshotErrors.length ? `<p class="bad"><strong>strict 검증 오류</strong>: ${esc(snapshotErrors.join(', '))}</p>` : ''}
    <p class="muted">미리보기에서는 generated_at 누락이나 sellable_quantity 누락을 경고로 보여줄 수 있지만, 실제 manual SELL request 생성에는 최신 generated_at과 실제 sellable_quantity가 필요합니다.</p>
  </div>` : '';
  const inputHelp = needsBalance ? `<div class="controlCard">
      <h3>필요한 입력</h3>
      <label>KIS 잔고 snapshot JSON 경로<span class="key">kis_balance_json_path</span></label>
      <input id="kisBalancePath" placeholder="예: exports/kis_balance_20260526.json" value="${esc(window.kisBalancePath || '')}">
      <p><button onclick="newSeasonGenerateSnapshot()">KIS 잔고 snapshot 생성</button></p>
      <p class="muted">읽기 전용 KIS 잔고 조회만 사용합니다. 주문 API는 호출하지 않습니다.</p>
      <p><button onclick="newSeasonValidateSnapshot()">snapshot 검증</button></p>
      <div id="snapshotValidationResult"></div>
      <label>전량매도 예정표 경로<span class="key">liquidation_plan_path</span></label>
      <input id="liquidationPlanPath" placeholder="자동 생성 후 채워집니다" value="${esc(window.liquidationPlanPath || s.plan_path || '')}">
      <label>전량매도 요청 확인 문구<span class="key">confirm</span></label>
      <input id="liquidationConfirm" placeholder="전량매도 요청 확인" value="${esc(window.liquidationConfirm || '')}">
    </div>` : '';
  document.getElementById('content').innerHTML = `<h2>새 시즌 준비</h2>
  <div class="manualBox">
    <strong>${esc(msg.status || '현재 상태 확인')}</strong>
    <p>${esc(msg.description || '')}</p>
    ${blockedGuide ? `<p class="bad"><strong>막힌 이유</strong>: ${esc(blockedGuide)}</p>` : ''}
    <p><strong>다음에 할 일</strong>: ${esc(msg.next_action || '아래 버튼을 눌러 다음 가능한 단계를 진행하세요.')}</p>
    <p><button class="primary" onclick="prepareNewSeasonNext()">새 시즌 전량매도/DB초기화 다음 단계 진행</button></p>
    <p class="muted">이 버튼은 현재 상태를 다시 확인한 뒤 안전하게 가능한 다음 단계만 실행합니다. 전량매도는 manual_order_requests 요청만 만들고, 실제 주문 처리는 실행 중인 Bot Core가 담당합니다. DB 초기화는 전량매도 체결, reconciliation, OPEN LOT 0개 조건이 끝난 뒤에만 가능합니다.</p>
  </div>
  ${s.new_season_ready ? '<div class="danger" style="background:#166534">새 시즌 시작 준비 완료</div>' : ''}
  <div class="grid">
    <div class="metric"><strong>OPEN LOT<span class="key">open_lot_count</span></strong>${displayCell('open_lot_count', s.open_lot_count)}</div>
    <div class="metric"><strong>미체결 주문<span class="key">pending_order_count</span></strong>${displayCell('pending_order_count', s.pending_order_count)}</div>
    <div class="metric"><strong>미처리 수동 요청<span class="key">pending_manual_request_count</span></strong>${displayCell('pending_manual_request_count', s.pending_manual_request_count)}</div>
    <div class="metric"><strong>현재 예정표<span class="key">plan_status</span></strong>${displayCell('plan_status', s.plan_status || '없음')}</div>
  </div>
  ${inputHelp}
  ${snapshotStatus}
  <details class="manualBox"><summary>고급 작업 / 내부 진단 열기</summary>
    <p><button onclick="newSeasonArchive(false)">백업 dry-run</button> <button onclick="newSeasonArchive(true)">백업 생성</button></p>
    <p><input id="planMaxAge" type="number" value="${esc(window.planMaxAge || 60)}" min="1" style="width:90px"> 분 유효
    <button onclick="newSeasonPlan(false)">예정표 dry-run</button> <button onclick="newSeasonPlan(true)">예정표 생성</button></p>
    <p><button onclick="newSeasonRequests(false)">전량매도 요청 dry-run</button> <button onclick="newSeasonRequests(true)">manual SELL request 생성</button></p>
    <p><input id="resetConfirm" placeholder="RESET 확인" value="${esc(window.resetConfirm || '')}">
    <button onclick="newSeasonReset(false)">초기화 가능 여부 확인</button> <button class="dangerBtn" onclick="newSeasonReset(true)">DB 테이블 비우기 실행</button></p>
    <div id="newSeasonResult">${window.newSeasonLastResultObject ? renderReadableObject(window.newSeasonLastResultObject, {raw:true}) : ''}</div>
    ${metrics(s)}
  </details>`;
  return;
  const steps = (s.wizard_steps || []).map(step => `
    <div class="controlCard ${step.status === '차단됨' || step.status === '아직 불가' ? 'dangerZone' : ''}">
      <h3>${esc(step.step)}단계. ${esc(step.title)}</h3>
      <p><span class="badge ${step.status === '가능' || step.status === '최신' || step.status === '적용됨' || step.status === '준비 완료' || step.status === '검증 완료' ? 'good' : (step.status === '차단됨' || step.status === '아직 불가' ? 'bad' : 'warn')}">${esc(step.status)}</span></p>
      <p>${esc(step.description)}</p>
      <p><strong>다음 행동</strong><br>${esc(step.next_action || '')}</p>
      ${step.disabled_reason ? `<p class="warn"><strong>비활성 이유</strong><br>${esc(step.disabled_reason)}</p>` : ''}
      <button ${step.button_enabled ? '' : 'disabled'}>${esc(step.button_label || '확인')}</button>
    </div>`).join('');
  document.getElementById('content').innerHTML = `<h2>새 시즌 준비 마법사</h2>
  <div class="manualBox"><strong>${esc(msg.status || '')}</strong>
    <p>${esc(msg.description || '')}</p>
    ${msg.reason ? `<p class="bad"><strong>이유</strong>: ${esc(msg.reason)}</p>` : ''}
    <p><strong>다음 단계</strong>: ${esc(msg.next_action || '')}</p>
    <details class="muted"><summary>고급 진단값 보기</summary><p>request_creation_possible=${esc(s.request_creation_possible)} / block_reason=${esc(s.block_reason || '-')} / plan_status=${esc(s.plan_status || '-')}</p></details>
  </div>
  ${s.new_season_ready ? '<div class="danger" style="background:#166534">새 시즌 시작 준비 완료</div>' : '<div class="manualBox"><strong>아직 준비가 끝나지 않았습니다.</strong><p>아래 단계 중 차단된 항목을 먼저 처리하세요.</p></div>'}
  <div class="manualBox">
    <h3>UI에서 진행하기</h3>
    <p class="muted">아래 버튼은 KIS 주문 API를 직접 호출하지 않습니다. 전량매도 요청 생성은 manual_order_requests 큐에 요청만 만들고, 실제 주문은 실행 중인 Bot Core가 기존 안전장치를 거쳐 처리합니다.</p>
    <p><button class="primary" onclick="prepareNewSeasonNext()">새 시즌 전량매도/DB초기화 다음 단계 진행</button></p>
    <p class="muted">이 버튼은 현재 상태를 읽고 다음으로 가능한 안전 단계만 진행합니다. 막힌 경우에는 무엇을 입력하거나 확인해야 하는지 알려줍니다.</p>
    <div class="grid">
      <div class="controlCard">
        <h4>1. 이전 시즌 백업</h4>
        <button onclick="newSeasonArchive(false)">백업 dry-run</button>
        <button onclick="newSeasonArchive(true)">백업 생성</button>
      </div>
      <div class="controlCard">
        <h4>2~3. 잔고 snapshot으로 전량매도 예정표 생성</h4>
        <input id="kisBalancePath" placeholder="KIS 잔고 snapshot JSON 경로" value="${esc(window.kisBalancePath || '')}">
      <p><button onclick="newSeasonGenerateSnapshot()">KIS 잔고 snapshot 생성</button></p>
      <p class="muted">읽기 전용 KIS 잔고 조회만 사용합니다. 주문 API는 호출하지 않습니다.</p>
        <input id="planMaxAge" type="number" value="${esc(window.planMaxAge || 60)}" min="1" style="width:90px"> 분 유효
        <p><button onclick="newSeasonPlan(false)">예정표 dry-run</button>
        <button onclick="newSeasonPlan(true)">예정표 생성</button></p>
      </div>
      <div class="controlCard">
        <h4>4. 전량매도 요청 생성</h4>
        <input id="liquidationPlanPath" placeholder="exports/liquidation_plan_...json" value="${esc(window.liquidationPlanPath || s.plan_path || '')}">
        <input id="liquidationConfirm" placeholder="전량매도 요청 확인" value="${esc(window.liquidationConfirm || '')}">
        <p><button onclick="newSeasonRequests(false)">요청 dry-run</button>
        <button onclick="newSeasonRequests(true)">manual SELL request 생성</button></p>
      </div>
      <div class="controlCard dangerZone">
        <h4>6. DB 초기화</h4>
        <input id="resetConfirm" placeholder="RESET 확인" value="${esc(window.resetConfirm || '')}">
        <p><button onclick="newSeasonReset(false)">초기화 가능 여부 확인</button>
        <button class="dangerBtn" onclick="newSeasonReset(true)">DB 테이블 비우기 실행</button></p>
        <p class="bad">OPEN LOT, 미체결, 미처리 요청, SYNC_REQUIRED가 있으면 차단됩니다.</p>
      </div>
    </div>
    <h3>실행 결과</h3><pre id="newSeasonResult">${esc(window.newSeasonLastResult || '')}</pre>
  </div>
  <div class="grid">${steps}</div>
  <h3>상세 진단값</h3>${metrics(s)}`;
}
async function newSeasonArchive(execute) {
  if (execute && !confirm('현재 config/DB/log를 archive에 백업합니다. 계속할까요?')) return;
  const r = await api('/api/new-season/archive', {method:'POST', body:JSON.stringify({execute})});
  window.newSeasonArchiveDone = execute || window.newSeasonArchiveDone;
  window.newSeasonLastResult = JSON.stringify(r, null, 2);
  window.newSeasonLastResultObject = r;
  await loadNewSeason();
}
function rememberNewSeasonInputs() {
  window.kisBalancePath = document.getElementById('kisBalancePath')?.value || window.kisBalancePath || '';
  window.planMaxAge = document.getElementById('planMaxAge')?.value || window.planMaxAge || 60;
  window.liquidationPlanPath = document.getElementById('liquidationPlanPath')?.value || window.liquidationPlanPath || '';
  window.liquidationConfirm = document.getElementById('liquidationConfirm')?.value || window.liquidationConfirm || '';
  window.resetConfirm = document.getElementById('resetConfirm')?.value || window.resetConfirm || '';
}
async function newSeasonPlan(execute) {
  rememberNewSeasonInputs();
  const kis_balance_json_path = document.getElementById('kisBalancePath').value;
  const max_age_minutes = Number(document.getElementById('planMaxAge').value || 60);
  if (execute && !kis_balance_json_path) { alert('KIS 잔고 snapshot JSON 경로가 필요합니다.'); return; }
  const r = await api('/api/new-season/liquidation-plan', {method:'POST', body:JSON.stringify({execute, kis_balance_json_path, max_age_minutes})});
  window.newSeasonLastResult = JSON.stringify(r, null, 2);
  window.newSeasonLastResultObject = r;
  if (r.result && r.result.plan_path) window.liquidationPlanPath = r.result.plan_path;
  await loadNewSeason();
  if (r.result && r.result.plan_path) document.getElementById('liquidationPlanPath').value = r.result.plan_path;
}
async function newSeasonValidateSnapshot() {
  rememberNewSeasonInputs();
  const target = document.getElementById('snapshotValidationResult');
  if (target) target.innerHTML = '검증 중...';
  const r = await api('/api/new-season/validate-snapshot', {method:'POST', body:JSON.stringify({kis_balance_json_path: window.kisBalancePath, max_age_minutes: Number(window.planMaxAge || 60)})});
  const guide = r.guide || {};
  if (target) target.innerHTML = `<div class="manualBox">
    <p><strong>전량매도 예정표 미리보기</strong>: ${r.snapshot_valid_for_preview ? '가능' : '불가'}</p>
    <p><strong>전량매도 요청 생성</strong>: ${r.snapshot_valid_for_request ? '가능' : '불가'}</p>
    <p><strong>생성시각</strong>: ${displayCell('snapshot_generated_at', r.snapshot_generated_at || '-')} / <strong>나이</strong>: ${displayCell('snapshot_age_minutes', r.snapshot_age_minutes)}</p>
    ${(r.snapshot_warnings || []).length ? `<p class="warn"><strong>경고</strong>: ${esc((r.snapshot_warnings || []).join(', '))}</p>` : ''}
    ${(r.snapshot_errors || []).length ? `<p class="bad"><strong>오류</strong>: ${esc((r.snapshot_errors || []).join(', '))}</p>` : ''}
    ${r.request_creation_block_reason ? `<p class="bad"><strong>요청 생성 차단</strong>: ${esc(guide.title || r.request_creation_block_reason)} <span class="key">${esc(r.request_creation_block_reason)}</span></p>` : ''}
    <p><strong>다음 행동</strong>: ${esc(guide.next_action || '')}</p>
    <p class="muted">매칭 ${esc(r.matched_positions_count || 0)}개 / 불일치 ${esc(r.mismatched_positions_count || 0)}개 / snapshot 누락 ${esc((r.missing_in_snapshot_codes || []).join(', ') || '-')} / snapshot 초과 ${esc((r.extra_in_snapshot_codes || []).join(', ') || '-')}</p>
  </div>`;
}

async function newSeasonGenerateSnapshot() {
  rememberNewSeasonInputs();
  const target = document.getElementById('snapshotValidationResult') || document.getElementById('newSeasonResult');
  if (!confirm('읽기 전용 KIS 잔고 조회로 snapshot을 생성합니다. 주문 API는 호출하지 않습니다. 계속할까요?')) return;
  const r = await api('/api/new-season/kis-balance-snapshot', {method:'POST', body:JSON.stringify({max_age_minutes: Number(window.planMaxAge || 60)})});
  if (r.created && r.path) {
    window.kisBalancePath = r.path;
    const input = document.getElementById('kisBalancePath');
    if (input) input.value = r.path;
  }
  window.newSeasonLastResult = JSON.stringify(r, null, 2);
  window.newSeasonLastResultObject = r;
  if (target) target.innerHTML = renderReadableObject(r);
}

async function newSeasonRequests(execute) {
  rememberNewSeasonInputs();
  const kis_balance_json_path = document.getElementById('kisBalancePath').value;
  const plan_path = document.getElementById('liquidationPlanPath').value;
  const confirmText = document.getElementById('liquidationConfirm').value;
  if (execute && confirmText !== '전량매도 요청 확인') { alert('confirm text로 "전량매도 요청 확인"을 입력해야 합니다.'); return; }
  const r = await api('/api/new-season/liquidation-requests', {method:'POST', body:JSON.stringify({execute, kis_balance_json_path, plan_path, confirm: confirmText})});
  window.newSeasonLastResult = JSON.stringify(r, null, 2);
  window.newSeasonLastResultObject = r;
  await loadNewSeason();
}
async function newSeasonReset(execute) {
  rememberNewSeasonInputs();
  const confirmText = document.getElementById('resetConfirm').value;
  if (execute && confirmText !== 'RESET 확인') { alert('confirm text로 "RESET 확인"을 입력해야 합니다.'); return; }
  if (execute && !confirm('DB 초기화는 되돌리기 어렵습니다. archive와 전량매도/reconciliation 완료를 확인했나요?')) return;
  const r = await api('/api/new-season/reset-db', {method:'POST', body:JSON.stringify({execute, confirm: confirmText})});
  window.newSeasonLastResult = JSON.stringify(r, null, 2);
  window.newSeasonLastResultObject = r;
  await loadNewSeason();
}
async function prepareNewSeasonNext() {
  rememberNewSeasonInputs();
  const s = await api('/api/new-season/status');
  if (!window.newSeasonArchiveDone) {
    if (!confirm('1단계로 현재 config/DB/log 백업을 생성합니다. 계속할까요?')) return;
    await newSeasonArchive(true);
    return;
  }
  const needsPlan = !s.current_plan_exists || ['liquidation_plan_missing','liquidation_plan_not_active','liquidation_plan_db_changed','liquidation_plan_snapshot_expired','liquidation_plan_stale'].includes(s.block_reason);
  if (needsPlan && (s.open_lot_count || 0) > 0) {
    if (!window.kisBalancePath) { alert('2단계에서 KIS 잔고 snapshot JSON 경로를 입력한 뒤 다시 진행하세요.'); return; }
    if (!confirm('현재 DB와 KIS 잔고 snapshot 기준으로 전량매도 예정표를 새로 생성합니다. 계속할까요?')) return;
    await newSeasonPlan(true);
    return;
  }
  if (s.request_creation_possible && (s.open_lot_count || 0) > 0) {
    if (!window.liquidationPlanPath && s.plan_path) window.liquidationPlanPath = s.plan_path;
    if (!window.kisBalancePath) { alert('전량매도 요청 생성에는 KIS 잔고 snapshot 경로가 필요합니다.'); return; }
    if (!window.liquidationConfirm) { alert('확인 문구 "전량매도 요청 확인"을 입력한 뒤 다시 진행하세요.'); return; }
    await newSeasonRequests(true);
    return;
  }
  if ((s.open_lot_count || 0) > 0 || (s.pending_order_count || 0) > 0 || (s.pending_manual_request_count || 0) > 0) {
    alert('전량매도 요청 처리, 체결, reconciliation 완료가 먼저 필요합니다. 현재 차단 사유: ' + (s.block_reason_ko || s.block_reason || '-'));
    return;
  }
  if (s.reset_possible) {
    if (!window.resetConfirm) { alert('DB 초기화를 실행하려면 확인 문구 "RESET 확인"을 입력하세요.'); return; }
    await newSeasonReset(true);
    return;
  }
  alert(s.guidance?.next_action || '현재 단계에서 자동으로 진행할 수 있는 작업이 없습니다. 화면의 차단 이유를 확인하세요.');
}
async function loadReviewRequired() {
  currentView = 'reviewRequired';
  const r = await api('/api/review-required');
  const rows = r.items || [];
  window.reviewRequiredRows = rows;
  document.getElementById('content').innerHTML = `<h2>수동검토 필요</h2>
  <div class="manualBox"><strong>처리 원칙</strong><p>${(r.guide || []).map(esc).join('<br>')}</p></div>
  ${table(window.reviewRequiredRows, 'reviewRequired', {actions:true})}
  <div id="reviewResult"></div>`;
}
async function reviewRecheck(code) {
  const r = await api('/api/review-required/' + encodeURIComponent(code) + '/recheck', {method:'POST', body:JSON.stringify({})});
  await loadReviewRequired();
  renderResult('reviewResult', r);
}
async function reviewAck(code) {
  const note = prompt('확인 메모를 입력하세요. acknowledge는 BUY 차단을 해제하지 않습니다.', '') || '';
  const r = await api('/api/review-required/' + encodeURIComponent(code) + '/acknowledge', {method:'POST', body:JSON.stringify({note, acknowledged_by:'local_ui'})});
  await loadReviewRequired();
  renderResult('reviewResult', r);
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
  renderResult('manualResult', r);
}
async function createManual(side) {
  const preview = await api('/api/manual-orders/preview', {method:'POST', body:JSON.stringify(manualPayload(side))});
  if (!preview.can_create) { renderResult('manualResult', preview); return; }
  if (!confirm('manual order request를 생성합니다. 실제 주문은 Bot Core가 별도로 처리합니다. 계속할까요?')) return;
  const r = await api('/api/manual-orders', {method:'POST', body:JSON.stringify(manualPayload(side))});
  await loadManualOrders();
  renderResult('manualResult', r);
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
  applyConfigLayout();
  const sections = Object.keys(configSchema.sections);
  const selected = sectionName || window.configSection || sections[0];
  window.configSection = selected;
  const nav = '<div class="sectionNav">' + sections.map(s => `<button class="${s===selected?'primary':''}" onclick="renderConfig('${esc(s)}')">${esc(s)}</button>`).join('') + '</div>';
  const layoutControls = `<div class="configLayoutControls">
    <strong>컬럼 폭 조절</strong>
    <span class="muted">항목명과 현재값 사이의 세로선을 마우스로 드래그하면 폭을 조절할 수 있습니다. 조절값은 이 브라우저에 저장됩니다.</span>
    <button onclick="resetConfigLayout()">기본값</button>
  </div>`;
  const fields = (configSchema.sections[selected] || []).map(renderConfigField).join('');
  const raw = `<details><summary>고급 / 원본 JSON 보기</summary><p class="warn">원본 JSON 직접 편집도 같은 validation, diff, backup, atomic save를 거칩니다.</p><textarea id="rawConfig" oninput="rawConfigChanged()">${esc(JSON.stringify(configDraft, null, 2))}</textarea></details>`;
  document.getElementById('content').innerHTML = `<h2>Config</h2>${nav}${layoutControls}<div>${fields}</div>${raw}<div class="configActions"><button onclick="previewConfigChanges()">변경사항 확인</button> <button class="primary" onclick="saveConfigForm()">백업 후 저장</button> <button onclick="loadConfig()">되돌리기</button><div id="cfgResult"></div></div>`;
}
function renderConfigField(meta) {
  const current = getPath(configDraft, meta.key);
  const original = getPath(configOriginal, meta.key);
  const changed = JSON.stringify(current) !== JSON.stringify(original);
  const danger = meta.danger_confirm_required ? '<span class="critical"> 이중 확인 필요</span>' : '';
  let input = '';
  if (meta.type === 'boolean') input = `<select onchange="configInputChanged('${esc(meta.key)}', this.value, '${esc(meta.config_format)}')"><option value="true" ${current===true?'selected':''}>true</option><option value="false" ${current===false?'selected':''}>false</option></select>`;
  else if (meta.type === 'json' && Array.isArray(current)) input = renderStructuredJsonEditor(meta, current);
  else if (meta.type === 'json') input = `<textarea onchange="configInputChanged('${esc(meta.key)}', this.value, 'json')">${esc(JSON.stringify(current, null, 2))}</textarea>`;
  else input = `<input type="${meta.type === 'time' ? 'time' : 'text'}" value="${esc(toDisplay(current, meta))}" onchange="configInputChanged('${esc(meta.key)}', this.value, '${esc(meta.config_format)}')">`;
  return `<div class="field ${changed ? 'changed' : ''}"><div><label>${esc(meta.label_ko)}</label><small>${esc(meta.key)}</small></div><div class="configResizeHandle" title="항목명 폭 조절" onmousedown="startConfigColumnResize(event, 'label')"></div><div>${input}<small>${esc(meta.description_ko || '')}${danger}<br>단위: ${esc(meta.unit || '')} / 저장 형식: ${esc(meta.config_format || '')} / 재시작 필요: ${meta.requires_restart ? '예' : '아니오'}</small></div><div class="configResizeHandle" title="현재값 폭 조절" onmousedown="startConfigColumnResize(event, 'current')"></div><div><small>현재값</small>${renderConfigOriginalValue(original, meta)}</div></div>`;
}
function renderConfigOriginalValue(value, meta) {
  if (meta.type === 'json') return renderReadableObject(value, {raw:true});
  const displayed = toDisplay(value, meta);
  return displayCell(meta.key, displayed);
}
const STRUCTURED_JSON_TEMPLATES = {
  'strategy.price_lot_bands': {keys:['min_price','max_price','lot_unit_amount','max_symbol_amount','max_lots','enabled','note'], row:{min_price:0,max_price:0,lot_unit_amount:0,max_symbol_amount:0,enabled:true,note:''}},
  'strategy.add_buy_lot_bands': {keys:['min_lots','max_lots','drop_rate','add_lot_count'], row:{min_lots:1,max_lots:1,drop_rate:0.04,add_lot_count:1}},
  'strategy.target_profit_lot_bands': {keys:['min_lots','max_lots','target_profit_rate'], row:{min_lots:1,max_lots:1,target_profit_rate:0.06}},
};
function structuredJsonKeys(path, rows) {
  const template = STRUCTURED_JSON_TEMPLATES[path];
  if (template) return template.keys;
  const keys = new Set();
  (rows || []).forEach(row => Object.keys(row || {}).forEach(k => keys.add(k)));
  return Array.from(keys);
}
function renderStructuredJsonEditor(meta, rows) {
  const path = meta.key;
  const keys = structuredJsonKeys(path, rows);
  const body = (rows || []).map((row, idx) => `<tr>${keys.map(k => `<td>${structuredJsonInput(path, idx, k, row ? row[k] : undefined)}</td>`).join('')}<td><button onclick="removeStructuredJsonRow('${esc(path)}', ${idx})">행 삭제</button></td></tr>`).join('');
  return `<div class="structuredEditor">
    <p class="muted">배열형 설정은 표에서 바로 수정합니다. 원본 JSON은 아래 고급 보기에서 확인할 수 있습니다.</p>
    <div class="tableWrap"><table><thead><tr>${keys.map(k => `<th>${headerLabel(k)}</th>`).join('')}<th>작업<span class="key">actions</span></th></tr></thead><tbody>${body || `<tr><td colspan="${keys.length + 1}"><span class="empty">행 없음</span></td></tr>`}</tbody></table></div>
    <button onclick="addStructuredJsonRow('${esc(path)}')">행 추가</button>
    <details><summary>이 항목 원본 JSON 보기</summary><textarea onchange="configInputChanged('${esc(path)}', this.value, 'json')">${esc(JSON.stringify(rows || [], null, 2))}</textarea></details>
  </div>`;
}
function structuredJsonInput(path, rowIndex, key, value) {
  if (typeof value === 'boolean') {
    return `<select onchange="structuredJsonInputChanged('${esc(path)}', ${rowIndex}, '${esc(key)}', this.value, 'boolean')"><option value="true" ${value===true?'selected':''}>true</option><option value="false" ${value===false?'selected':''}>false</option></select>`;
  }
  const type = typeof value === 'number' ? 'number' : 'text';
  return `<input type="${type}" value="${esc(value ?? '')}" onchange="structuredJsonInputChanged('${esc(path)}', ${rowIndex}, '${esc(key)}', this.value, '${type}')">`;
}
function structuredJsonInputChanged(path, rowIndex, key, value, type) {
  const rows = getPath(configDraft, path) || [];
  const row = rows[rowIndex] || {};
  let parsed = value;
  if (type === 'boolean') parsed = value === 'true';
  else if (type === 'number') parsed = value === '' ? null : Number(value);
  row[key] = parsed;
  rows[rowIndex] = row;
  setPath(configDraft, path, rows);
  renderConfig(window.configSection);
}
function addStructuredJsonRow(path) {
  const rows = getPath(configDraft, path) || [];
  const template = STRUCTURED_JSON_TEMPLATES[path];
  rows.push(template ? JSON.parse(JSON.stringify(template.row)) : {});
  setPath(configDraft, path, rows);
  renderConfig(window.configSection);
}
function removeStructuredJsonRow(path, rowIndex) {
  const rows = getPath(configDraft, path) || [];
  rows.splice(rowIndex, 1);
  setPath(configDraft, path, rows);
  renderConfig(window.configSection);
}
function getPath(obj, path) { return path.split('.').reduce((acc,k) => acc == null ? undefined : acc[k], obj); }
function setPath(obj, path, value) { const parts = path.split('.'); let target = obj; parts.slice(0,-1).forEach(k => { if (!target[k]) target[k] = {}; target = target[k]; }); target[parts.at(-1)] = value; }
function toDisplay(value, meta) {
  if (value === null || value === undefined) return '';
  if (meta.display_format === 'decimal_percent') return formatNumber(Number(value) * 100);
  if (meta.type === 'json') return JSON.stringify(value, null, 2);
  if (typeof value === 'number') return formatNumber(value);
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
    document.getElementById('cfgResult').innerHTML = `<p class="bad">입력값 오류: ${esc(err.message)}</p>`;
  }
}
function rawConfigChanged() {
  try {
    configDraft = JSON.parse(document.getElementById('rawConfig').value);
  } catch (err) {
    document.getElementById('cfgResult').innerHTML = `<p class="bad">JSON 오류: ${esc(err.message)}</p>`;
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
  renderResult('cfgResult', {valid: validation.valid, errors: validation.errors, danger_confirm_required: danger, changes});
}
async function saveConfigForm() {
  const changes = diffConfig(configOriginal, configDraft);
  const dangerKeys = new Set(configSchema.danger_confirm_keys || []);
  const danger = changes.filter(c => dangerKeys.has(c.key)).map(c => c.key);
  const validation = await api('/api/config/validate', {method:'POST', body:JSON.stringify(configDraft)});
  if (!validation.valid) { renderResult('cfgResult', validation); return; }
  if (danger.length && !confirm('위험 설정 변경이 포함되어 있습니다: ' + danger.join(', ') + '\n계속할까요?')) return;
  if (!confirm('config/backups에 백업을 만들고 atomic save를 수행합니다. 계속할까요?')) return;
  const r = await api('/api/config', {method:'PATCH', body:JSON.stringify(configDraft)});
  renderResult('cfgResult', {saved:r, changes});
  configOriginal = await api('/api/config');
  configDraft = JSON.parse(JSON.stringify(configOriginal));
}
async function collectMarketDataFromDashboard(execute) {
  const resultId = 'marketDataCollectResult';
  const target = document.getElementById(resultId);
  if (target) target.innerHTML = '<span class="muted">Collecting read-only market data...</span>';
  const payload = {execute, symbols_from_config:true, snapshot:true, daily:true};
  const result = await api('/api/market-data/collect', {method:'POST', body:JSON.stringify(payload)});
  renderResult(resultId, result);
  if (execute) await loadDashboard();
}
function attachMarketDataControls() {
  const content = document.getElementById('content');
  if (!content || document.getElementById('marketDataCollectPanel')) return;
  const panel = document.createElement('div');
  panel.id = 'marketDataCollectPanel';
  panel.className = 'manualBox';
  panel.innerHTML = `
    <strong>Market data collection</strong>
    <p class="muted">Read-only quote collection for analysis. This does not call KIS order APIs and does not reset DB.</p>
    <button onclick="collectMarketDataFromDashboard(false)">Dry-run market data collection</button>
    <button class="primary" onclick="collectMarketDataFromDashboard(true)">Save market data now</button>
    <div id="marketDataCollectResult" style="margin-top:8px"></div>
  `;
  content.insertBefore(panel, content.children[2] || null);
}
const originalLoadDashboard = loadDashboard;
loadDashboard = async function() {
  await originalLoadDashboard();
  attachMarketDataControls();
};
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
                "/api/portfolio-dashboard": self.service.portfolio_dashboard,
                "/api/portfolio/summary": self.service.portfolio_dashboard,
                "/api/decisions": self.service.parse_decision_logs,
                "/api/risk/summary": lambda: self.service.status()["account_risk"],
                "/api/execution-mapping/status": self.service.execution_mapping_status,
                "/api/reconciliation/status": lambda: self.service.status()["reconciliation"],
                "/api/runtime": self.service.runtime_status,
                "/api/new-season/status": self.service.new_season_status,
                "/api/review-required": self.service.review_required_list,
            }
            if parsed.path.startswith("/api/stocks/"):
                code = parsed.path.rsplit("/", 1)[-1]
                self._send_json(self.service.stock_detail(code))
                return
            if parsed.path.startswith("/api/portfolio-dashboard/"):
                self._send_json(self._portfolio_dashboard_detail(parsed.path, query))
                return
            if parsed.path.startswith("/api/positions/") and parsed.path.endswith("/review-status"):
                code = parsed.path.split("/")[3].zfill(6)
                self._send_json(self.service.review_status(code))
                return
            if parsed.path.startswith("/api/review-required/") and parsed.path.endswith("/actions"):
                code = parsed.path.split("/")[3].zfill(6)
                self._send_json(self.service.review_status(code))
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

    def _portfolio_dashboard_detail(self, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        parts = [part for part in path.split("/") if part]
        filters = {key: values[0] for key, values in query.items() if values}
        if parts[-1] == "realized-detail":
            if len(parts) >= 5 and parts[-3] == "daily":
                filters["date"] = parts[-2]
            return self.service.portfolio_realized_detail(filters)
        if parts[-1] == "unrealized-detail":
            if len(parts) >= 5 and parts[-3] == "daily":
                filters["date"] = parts[-2]
            return self.service.portfolio_unrealized_detail(filters)
        return {"error": "not_found"}

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
            if parsed.path == "/api/manual-order-requests/requeue":
                self._send_json(self.service.requeue_manual_order_request(str(data.get("request_id", "")), str(data.get("confirm_text", "")), str(data.get("operator_note", ""))))
                return
            if parsed.path == "/api/manual-order-requests/cancel":
                self._send_json(self.service.cancel_manual_order_request(str(data.get("request_id", "")), str(data.get("reason", "operator_cancel_stale_processing")), str(data.get("confirm_text", "")), str(data.get("operator_note", ""))))
                return
            if parsed.path == "/api/new-season/archive":
                self._send_json(self.service.new_season_archive(bool(data.get("execute", False))))
                return
            if parsed.path == "/api/new-season/liquidation-plan":
                self._send_json(self.service.new_season_create_plan(data.get("kis_balance_json_path", ""), bool(data.get("execute", False)), int(data.get("max_age_minutes") or 60)))
                return
            if parsed.path == "/api/new-season/validate-snapshot":
                self._send_json(self.service.new_season_validate_snapshot(data.get("kis_balance_json_path", ""), int(data.get("max_age_minutes") or 60)))
                return
            if parsed.path == "/api/new-season/kis-balance-snapshot":
                self._send_json(self.service.new_season_generate_kis_balance_snapshot(data.get("output_dir", "exports"), int(data.get("max_age_minutes") or 60)))
                return
            if parsed.path == "/api/new-season/liquidation-requests":
                self._send_json(self.service.new_season_create_liquidation_requests(data.get("plan_path", ""), data.get("kis_balance_json_path", ""), data.get("confirm", ""), bool(data.get("execute", False))))
                return
            if parsed.path == "/api/new-season/reset-db":
                self._send_json(self.service.new_season_reset_db(data.get("confirm", ""), bool(data.get("execute", False))))
                return
            if parsed.path == "/api/market-data/collect":
                self._send_json(
                    self.service.collect_market_data(
                        execute=bool(data.get("execute")),
                        symbols_from_config=bool(data.get("symbols_from_config", True)),
                        snapshot=bool(data.get("snapshot", True)),
                        daily=bool(data.get("daily", True)),
                    )
                )
                return
            if parsed.path.startswith("/api/positions/") and parsed.path.endswith("/review/recheck"):
                code = parsed.path.split("/")[3].zfill(6)
                self._send_json(self.service.review_recheck(code))
                return
            if parsed.path.startswith("/api/positions/") and parsed.path.endswith("/review/acknowledge"):
                code = parsed.path.split("/")[3].zfill(6)
                self._send_json(self.service.review_acknowledge(code, data.get("note", ""), data.get("acknowledged_by", "local_ui")))
                return
            if parsed.path.startswith("/api/review-required/") and parsed.path.endswith("/recheck"):
                code = parsed.path.split("/")[3].zfill(6)
                self._send_json(self.service.review_recheck(code))
                return
            if parsed.path.startswith("/api/review-required/") and parsed.path.endswith("/acknowledge"):
                code = parsed.path.split("/")[3].zfill(6)
                self._send_json(self.service.review_acknowledge(code, data.get("note", ""), data.get("acknowledged_by", "local_ui")))
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


