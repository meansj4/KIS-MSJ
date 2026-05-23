"""Read-only dashboard and guarded-control service for the local web UI."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
from dataclasses import asdict
from datetime import datetime, time as day_time
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG_PATH, BotConfig, load_config
from .lot_manager import LotManager
from .models import OrderSide, PositionLifecycle, PositionState
from .runtime_control import DEFAULT_RUNTIME_CONTROL_PATH, RuntimeControl, load_runtime_control, runtime_block_reason, save_runtime_control
from .strategy import LotGridStrategy, StrategyAction


SENSITIVE_PARTS = ("account", "acct", "cano", "acnt", "appkey", "appsecret", "token", "authorization", "auth")
RISK_FLAGS = ("trading_halted", "administrative_issue", "investment_alert", "audit_opinion_issue", "delisting_risk", "accounting_issue", "liquidity_warning")


CONFIG_METADATA: tuple[dict[str, Any], ...] = (
    {"section": "Strategy", "key": "strategy.initial_buy_amount", "label_ko": "최초 매수 금액", "description_ko": "NEVER_BOUGHT 상태의 신규 종목을 처음 매수할 때 사용하는 금액입니다.", "type": "number", "unit": "원", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.auto_buy_limit", "label_ko": "종목별 자동매수 한도", "description_ko": "이 금액을 넘으면 자동 추가매수를 제한하고 REVIEW_REQUIRED 후보가 됩니다.", "type": "number", "unit": "원", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.absolute_max_investment", "label_ko": "종목별 절대 최대 투입금", "description_ko": "종목별 투입금의 절대 상한입니다.", "type": "number", "unit": "원", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.review_loss_pct", "label_ko": "검토 필요 손실률", "description_ko": "종목 손실이 이 수준에 도달하면 REVIEW_REQUIRED 후보가 됩니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.max_open_lots_before_review", "label_ko": "검토 전 최대 OPEN LOT 수", "description_ko": "OPEN LOT이 많아진 종목을 재검토 대상으로 보기 위한 기준입니다.", "type": "number", "unit": "개", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.pnl_minus_threshold", "label_ko": "MINUS 모드 기준", "description_ko": "포지션 손익률이 이 값 이하이면 MINUS 모드입니다. UI는 %로 표시하지만 config에는 소수로 저장됩니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.pnl_plus_threshold", "label_ko": "PLUS 모드 기준", "description_ko": "포지션 손익률이 이 값 이상이면 PLUS 모드입니다. UI는 %로 표시하지만 config에는 소수로 저장됩니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.reentry_drop_rate", "label_ko": "기존 재진입 하락률", "description_ko": "호환용 설정입니다. normal_reentry_drop_rate를 우선 확인하세요.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.normal_reentry_drop_rate", "label_ko": "NORMAL_REENTRY 하락률", "description_ko": "normal_exit_anchor_price 대비 이 비율 이상 하락하면 재진입 후보가 됩니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.trailing_activation_gain", "label_ko": "TRAILING 활성화 상승률", "description_ko": "trailing_exit_anchor_price 대비 이 비율 이상 오른 뒤에만 trailing 재진입을 검토합니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.trailing_reentry_drop_rate", "label_ko": "TRAILING 고점 대비 하락률", "description_ko": "post_exit_high_price 대비 이 비율 이상 조정받으면 trailing 재진입 후보가 됩니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.min_reentry_wait_minutes", "label_ko": "최소 재진입 대기시간", "description_ko": "전량 매도 후 재진입을 평가하기 전 기다릴 최소 시간입니다.", "type": "number", "unit": "분", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.max_trailing_reentry_per_day", "label_ko": "일일 trailing 재진입 제한", "description_ko": "하루에 허용할 TRAILING_REENTRY 횟수입니다.", "type": "number", "unit": "회", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.reentry_buy_cooldown_minutes", "label_ko": "재진입 후 매수 쿨다운", "description_ko": "재진입 매수 후 추가매수를 막는 시간입니다.", "type": "number", "unit": "분", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.age_decay_rate", "label_ko": "LOT 목표수익률 주간 감소폭", "description_ko": "오래된 LOT의 목표수익률을 주마다 낮추는 값입니다. UI는 %p/주로 표시하지만 config에는 소수로 저장됩니다.", "type": "number", "unit": "%p/주", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.cleanup_enabled", "label_ko": "Cleanup 손실 정리 활성화", "description_ko": "조건을 만족하는 오래된 손실 LOT의 제한적 CLEANUP_SELL을 허용합니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "danger", "requires_restart": True, "danger_confirm_required": True},
    {"section": "Strategy", "key": "strategy.cleanup_min_age_weeks", "label_ko": "Cleanup 최소 보유기간", "description_ko": "이 주수 이상 오래된 LOT만 cleanup 후보가 됩니다.", "type": "number", "unit": "주", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.cleanup_min_target_rate", "label_ko": "Cleanup 최대 손실 허용률", "description_ko": "이 손실률보다 더 큰 손실은 자동 cleanup하지 않습니다. UI는 %로 표시하지만 config에는 소수로 저장됩니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "danger", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.cleanup_profit_offset_ratio", "label_ko": "Cleanup 수익 상쇄 비율", "description_ko": "당일 실현수익 중 cleanup 손실에 사용할 비율입니다. UI는 %로 표시하지만 config에는 0~1 소수로 저장됩니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "danger", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.cleanup_buy_cooldown_days", "label_ko": "Cleanup 후 매수 쿨다운", "description_ko": "일부 cleanup 후 같은 종목 매수를 막는 캘린더 일수입니다.", "type": "number", "unit": "일", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.cleanup_reentry_cooldown_days", "label_ko": "전량 cleanup 후 재진입 쿨다운", "description_ko": "전량 cleanup 후 모든 BUY를 막는 캘린더 일수입니다.", "type": "number", "unit": "일", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.cleanup_auto_return_to_wait_reentry", "label_ko": "Cleanup 후 자동 WAIT_REENTRY 복귀", "description_ko": "false가 기본입니다. true이면 cleanup cooldown 후 자동 재진입 대기로 갈 수 있습니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "danger", "requires_restart": True, "danger_confirm_required": True},
    {"section": "Strategy", "key": "strategy.stale_lot_loss_rate", "label_ko": "STALE LOT 손실률 기준", "description_ko": "오래된 손실 LOT을 stale로 표시할 손실률입니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.stale_lot_min_age_weeks", "label_ko": "STALE LOT 최소 보유기간", "description_ko": "stale LOT 판정에 필요한 최소 보유 주수입니다.", "type": "number", "unit": "주", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.stale_lot_price_gap_rate", "label_ko": "STALE LOT 가격 하락 기준", "description_ko": "매수가 대비 이 비율 이상 낮아진 LOT을 stale 후보로 봅니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.review_symbol_loss_rate", "label_ko": "REVIEW_REQUIRED 종목 손실률", "description_ko": "종목 전체 손실률이 이 수준 이하면 REVIEW_REQUIRED 후보가 됩니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "danger", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.stale_lot_review_age_weeks", "label_ko": "STALE 장기 지속 검토 주수", "description_ko": "stale LOT이 이 주수 이상 오래되면 REVIEW_REQUIRED 후보가 됩니다.", "type": "number", "unit": "주", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.exposure_buy_bands", "label_ko": "누적투입금별 추가매수 구간", "description_ko": "drop_pct는 이미 % 단위 값입니다. amount는 해당 구간의 추가매수 금액입니다.", "type": "json", "unit": "bands", "display_format": "json", "config_format": "json", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.exposure_sell_bands", "label_ko": "누적투입금별 매도 목표수익률 구간", "description_ko": "target_profit_pct는 이미 % 단위 값입니다.", "type": "json", "unit": "bands", "display_format": "json", "config_format": "json", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.high_exposure_partial_sell_pct", "label_ko": "고투입 구간 부분매도 비율", "description_ko": "고투입 구간에서 포지션 축소 목적의 부분매도 비율입니다. config 값은 이미 %입니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.estimated_fee_tax_pct", "label_ko": "예상 수수료/세금", "description_ko": "손익 추정에 사용하는 예상 비용률입니다. config 값은 이미 %입니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "normal", "requires_restart": True},
    {"section": "Risk", "key": "risk.market_risk_mode", "label_ko": "시장 리스크 모드", "description_ko": "true이면 시장 위험 상태로 보고 신규 주문을 제한합니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "danger", "requires_restart": True, "danger_confirm_required": True},
    {"section": "Risk", "key": "risk.daily_account_loss_limit_pct", "label_ko": "일일 계좌 손실 제한", "description_ko": "일일 손실률 제한입니다. config 값은 이미 %입니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "danger", "requires_restart": True},
    {"section": "Risk", "key": "risk.total_account_loss_limit_pct", "label_ko": "전체 계좌 손실 제한", "description_ko": "전체 계좌 기준 손실률 제한입니다. config 값은 이미 %입니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "danger", "requires_restart": True},
    {"section": "Risk", "key": "risk.max_review_positions", "label_ko": "최대 검토 필요 종목 수", "description_ko": "REVIEW_REQUIRED가 너무 많아지면 운용 리스크가 커집니다.", "type": "number", "unit": "개", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Risk", "key": "risk.min_cash_available", "label_ko": "최소 현금 보유액", "description_ko": "이 현금보다 적으면 신규 BUY를 제한합니다.", "type": "number", "unit": "원", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Risk", "key": "risk.max_consecutive_api_errors", "label_ko": "연속 API 오류 제한", "description_ko": "연속 API 오류가 이 값을 넘으면 운용을 보수적으로 봅니다.", "type": "number", "unit": "회", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Risk", "key": "risk.max_price_sample_volatility_pct", "label_ko": "가격 샘플 변동성 제한", "description_ko": "짧은 샘플 중 가격 변동성이 큰 경우 주문을 피하기 위한 기준입니다. config 값은 이미 %입니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "warning", "requires_restart": True},
    {"section": "Risk", "key": "risk.block_on_lot_mismatch", "label_ko": "LOT 불일치 시 차단", "description_ko": "false이면 DB/KIS 잔고 불일치에도 주문이 가능해질 수 있어 위험합니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "danger", "requires_restart": True, "danger_confirm_required": True},
    {"section": "Risk", "key": "risk.max_active_symbols", "label_ko": "최대 활성 종목 수", "description_ko": "신규 initial_buy를 제한하는 활성 종목 수 상한입니다.", "type": "number", "unit": "개", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Risk", "key": "risk.max_total_open_lots", "label_ko": "최대 전체 OPEN LOT 수", "description_ko": "초과 시 모든 BUY를 차단합니다. SELL은 허용됩니다.", "type": "number", "unit": "개", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Risk", "key": "risk.max_total_invested_amount", "label_ko": "최대 전체 투입금", "description_ko": "초과 시 모든 BUY를 차단합니다. SELL은 허용됩니다.", "type": "number", "unit": "원", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Risk", "key": "risk.max_new_buy_per_day", "label_ko": "일일 신규 매수 주문 제한", "description_ko": "initial_buy 주문 수 기준입니다. 거절/취소도 주문 난사 방지 목적으로 포함됩니다.", "type": "number", "unit": "회", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Order", "key": "order.live_trading", "label_ko": "실거래 모드", "description_ko": "true이면 실제 KIS 주문 API를 통해 주문이 나갈 수 있습니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "critical", "requires_restart": True, "danger_confirm_required": True},
    {"section": "Order", "key": "order.emergency_market_order", "label_ko": "비상 시장가 주문", "description_ko": "true이면 비상 상황에서 시장가 주문을 허용할 수 있어 매우 위험합니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "critical", "requires_restart": True, "danger_confirm_required": True},
    {"section": "Order", "key": "order.buy_limit_markup_pct", "label_ko": "매수 지정가 가산율", "description_ko": "현재가 대비 매수 지정가를 얼마나 높일지 정합니다. config 값은 이미 %입니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.sell_limit_markdown_pct", "label_ko": "매도 지정가 할인율", "description_ko": "현재가 대비 매도 지정가를 얼마나 낮출지 정합니다. config 값은 이미 %입니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.price_sample_count", "label_ko": "가격 샘플 수", "description_ko": "주문 전 가격 안정성을 보기 위해 수집하는 샘플 수입니다.", "type": "number", "unit": "개", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.price_sample_interval_seconds", "label_ko": "가격 샘플 간격", "description_ko": "가격 샘플 사이의 대기 시간입니다.", "type": "number", "unit": "초", "display_format": "number", "config_format": "number", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.limit_order_timeout_seconds", "label_ko": "지정가 주문 타임아웃", "description_ko": "미체결 지정가 주문을 취소/재확인하기 전 대기 시간입니다.", "type": "number", "unit": "초", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Order", "key": "order.order_cooldown_seconds", "label_ko": "주문 쿨다운", "description_ko": "같은 종목 주문 사이의 기본 대기 시간입니다.", "type": "number", "unit": "초", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.min_order_request_interval_seconds", "label_ko": "최소 주문 요청 간격", "description_ko": "주문 난사를 막기 위한 전체 주문 요청 최소 간격입니다.", "type": "number", "unit": "초", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.cancel_unfilled_on_start", "label_ko": "시작 시 미체결 취소", "description_ko": "봇 시작 시 미체결 주문 취소를 시도할 수 있어 주의가 필요합니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "danger", "requires_restart": True, "danger_confirm_required": True},
    {"section": "Order", "key": "order.execution_query_buffer_minutes", "label_ko": "체결 조회 버퍼", "description_ko": "open order requested_at 이전으로 체결 조회 범위를 넓히는 시간입니다.", "type": "number", "unit": "분", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.include_previous_day_for_open_orders", "label_ko": "open order 전일 체결 포함", "description_ko": "open order가 있으면 최소 전일 00:00부터 체결 조회를 시도합니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "warning", "requires_restart": True},
    {"section": "Order", "key": "order.enable_execution_raw_log", "label_ko": "체결 raw log 활성화", "description_ko": "첫 실체결 필드 검증용 임시 옵션입니다. 확인 후 끄는 것을 권장합니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "danger", "requires_restart": True, "danger_confirm_required": True},
    {"section": "Order", "key": "order.reconcile_recent_executions_on_startup", "label_ko": "시작 시 최근 체결 reconciliation", "description_ko": "봇 재시작 시 최근 체결을 조회해 미반영 체결 복구 기회를 제공합니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.startup_execution_lookup_days", "label_ko": "시작 시 체결 조회 일수", "description_ko": "시작 시 최근 며칠의 체결을 조회할지 정합니다.", "type": "number", "unit": "일", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Market Hours", "key": "market_hours.open_time", "label_ko": "장 시작 시간", "description_ko": "정규장 시작 시간입니다.", "type": "time", "unit": "HH:MM", "display_format": "time", "config_format": "string", "warning_level": "normal", "requires_restart": True},
    {"section": "Market Hours", "key": "market_hours.close_time", "label_ko": "장 종료 시간", "description_ko": "정규장 종료 시간입니다.", "type": "time", "unit": "HH:MM", "display_format": "time", "config_format": "string", "warning_level": "normal", "requires_restart": True},
    {"section": "Market Hours", "key": "market_hours.block_after_open_minutes", "label_ko": "장 시작 후 차단 시간", "description_ko": "장 시작 직후 변동성이 큰 시간대의 주문을 막습니다.", "type": "number", "unit": "분", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Market Hours", "key": "market_hours.block_before_close_minutes", "label_ko": "장 마감 전 차단 시간", "description_ko": "장 마감 직전 주문을 막습니다.", "type": "number", "unit": "분", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "storage_path", "label_ko": "DB 경로", "description_ko": "SQLite 상태 DB 경로입니다.", "type": "text", "unit": "path", "display_format": "text", "config_format": "string", "warning_level": "normal", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "log_path", "label_ko": "로그 경로", "description_ko": "자동거래 로그 파일 경로입니다.", "type": "text", "unit": "path", "display_format": "text", "config_format": "string", "warning_level": "normal", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "kis_account.account_number_env", "label_ko": "계좌번호 env key", "description_ko": "실제 계좌번호 값이 아니라 환경변수 이름만 표시합니다.", "type": "text", "unit": "env", "display_format": "text", "config_format": "string", "warning_level": "warning", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "kis_account.account_product_code_env", "label_ko": "계좌 상품코드 env key", "description_ko": "실제 값이 아니라 환경변수 이름만 표시합니다.", "type": "text", "unit": "env", "display_format": "text", "config_format": "string", "warning_level": "warning", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "kis_account.customer_type", "label_ko": "고객 타입", "description_ko": "KIS API 고객 타입입니다.", "type": "text", "unit": "code", "display_format": "text", "config_format": "string", "warning_level": "normal", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "upstream_watch.enabled", "label_ko": "upstream 감시 활성화", "description_ko": "Open Trading API upstream 변경 감시 여부입니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "normal", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "upstream_watch.interval_seconds", "label_ko": "upstream 감시 간격", "description_ko": "upstream 확인 간격입니다.", "type": "number", "unit": "초", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "upstream_watch.repo_path", "label_ko": "upstream repo 경로", "description_ko": "Open Trading API repository 경로입니다.", "type": "text", "unit": "path", "display_format": "text", "config_format": "string", "warning_level": "normal", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "upstream_watch.fetch", "label_ko": "upstream fetch 수행", "description_ko": "true이면 upstream 확인 시 fetch를 시도할 수 있습니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "warning", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "loop_interval_seconds", "label_ko": "봇 루프 간격", "description_ko": "자동매매 메인 루프 반복 간격입니다.", "type": "number", "unit": "초", "display_format": "number", "config_format": "number", "warning_level": "normal", "requires_restart": True},
    {"section": "Paths / Account / Upstream", "key": "max_loop_count", "label_ko": "최대 루프 수", "description_ko": "테스트용 최대 루프 수입니다. 비어 있으면 제한이 없습니다.", "type": "number", "unit": "회", "display_format": "nullable_integer", "config_format": "nullable_integer", "warning_level": "normal", "requires_restart": True},
)


class UIService:
    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH, runtime_path: str | Path = DEFAULT_RUNTIME_CONTROL_PATH) -> None:
        self.config_path = Path(config_path)
        self.runtime_path = Path(runtime_path)

    @property
    def config(self) -> BotConfig:
        return load_config(self.config_path)

    def raw_config(self) -> dict[str, Any]:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def config_schema(self) -> dict[str, Any]:
        sections: dict[str, list[dict[str, Any]]] = {}
        for item in CONFIG_METADATA:
            sections.setdefault(str(item["section"]), []).append(dict(item))
        return {
            "sections": sections,
            "metadata": [dict(item) for item in CONFIG_METADATA],
            "restart_required_by_default": True,
            "conversion_notes": {
                "decimal_rate": "UI에서는 percent로 보여주고 저장 시 100으로 나눈 소수로 변환합니다. 예: 4.0% -> 0.04",
                "percent_value": "config 값 자체가 percent입니다. 예: 4.0 -> 4.0%",
                "json": "배열/복합 구조는 JSON으로 편집하며 동일한 validation/backup/atomic save를 거칩니다.",
            },
            "danger_confirm_keys": [item["key"] for item in CONFIG_METADATA if item.get("danger_confirm_required")],
        }

    def status(self) -> dict[str, Any]:
        config = self.config
        positions = self.positions()
        lots = self.lots()
        orders = self.orders()
        fills = self.fills()
        logs = self.parse_log_events(400)
        runtime = asdict(load_runtime_control(self.runtime_path))
        raw_mapping = self.execution_mapping_status()
        return {
            "bot": {
                "state": "UNKNOWN",
                "last_loop_at": self._latest_log_time(),
                "next_loop_estimate": "",
                "loop_interval_seconds": config.loop_interval_seconds,
                "max_loop_count": config.max_loop_count,
                "uptime": "",
                "recent_exception": self._latest_error(),
                "consecutive_api_errors": "unknown",
                "market_status": self.market_status(),
            },
            "warnings": self.warnings(config, positions, orders, raw_mapping),
            "risk_banner": self.risk_banner(config),
            "runtime_control": runtime,
            "account_risk": self.risk_summary(config, positions, lots, orders, fills),
            "position_state_counts": _count_by(positions, "position_state"),
            "order_status_counts": _count_by(orders, "status"),
            "reconciliation": self.reconciliation_summary(logs),
            "execution_mapping": raw_mapping,
        }

    def risk_banner(self, config: BotConfig) -> dict[str, Any]:
        warnings = []
        if config.order.live_trading:
            warnings.append("현재 live_trading=true입니다. 이 봇은 실거래 주문을 낼 수 있습니다.")
        if config.order.emergency_market_order:
            warnings.append("emergency_market_order=true입니다.")
        if config.order.enable_execution_raw_log:
            warnings.append("enable_execution_raw_log=true입니다. 검증 후 끄는 것을 권장합니다.")
        return {"live_trading": config.order.live_trading, "level": "danger" if warnings else "normal", "messages": warnings}

    def risk_summary(self, config: BotConfig, positions: list[dict[str, Any]], lots: list[dict[str, Any]], orders: list[dict[str, Any]], fills: list[dict[str, Any]]) -> dict[str, Any]:
        open_lots = [lot for lot in lots if lot.get("remaining_quantity", 0) > 0 and lot.get("status") != "CLOSED"]
        active_codes = {
            lot["code"] for lot in open_lots
        } | {order["code"] for order in orders if order.get("status") in {"REQUESTED", "PARTIAL"}} | {
            item["code"] for item in positions if item.get("position_state") in {
                PositionLifecycle.HOLDING.value,
                PositionLifecycle.WAIT_REENTRY.value,
                PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value,
                PositionLifecycle.REVIEW_REQUIRED.value,
                PositionLifecycle.RISK_BLOCKED.value,
                PositionLifecycle.SYNC_REQUIRED.value,
            }
        }
        today = datetime.now().date().isoformat()
        return {
            "cash_available": "unknown_ui_read_only",
            "min_cash_available": config.risk.min_cash_available,
            "daily_profit_loss": "unknown_ui_read_only",
            "daily_account_loss_limit_pct": config.risk.daily_account_loss_limit_pct,
            "total_account_loss_limit_pct": config.risk.total_account_loss_limit_pct,
            "max_active_symbols": config.risk.max_active_symbols,
            "active_symbol_count": len(active_codes),
            "max_total_open_lots": config.risk.max_total_open_lots,
            "total_open_lot_count": len(open_lots),
            "max_total_invested_amount": config.risk.max_total_invested_amount,
            "total_invested_amount": sum(int(lot.get("remaining_quantity", 0)) * int(lot.get("buy_price", 0)) for lot in open_lots),
            "max_new_buy_per_day": config.risk.max_new_buy_per_day,
            "new_buy_count_today": sum(1 for order in orders if order.get("side") == "BUY" and order.get("reason") == "initial_buy" and str(order.get("requested_at", "")).startswith(today)),
            "today_fill_count": sum(1 for fill in fills if str(fill.get("filled_at", "")).startswith(today)),
        }

    def warnings(self, config: BotConfig, positions: list[dict[str, Any]], orders: list[dict[str, Any]], raw_mapping: dict[str, Any]) -> list[dict[str, str]]:
        warnings = []
        for state in ("SYNC_REQUIRED", "RISK_BLOCKED", "REVIEW_REQUIRED"):
            count = sum(1 for item in positions if item.get("position_state") == state or item.get("sync_status") == state)
            if count:
                warnings.append({"level": "danger" if state != "REVIEW_REQUIRED" else "warning", "reason": state, "message": f"{state} 종목 {count}개"})
        if any(item.get("lot_quantity_mismatch") for item in positions):
            warnings.append({"level": "danger", "reason": "lot_mismatch", "message": "lot quantity mismatch 존재"})
        if config.risk.market_risk_mode:
            warnings.append({"level": "warning", "reason": "market_risk_mode", "message": "market_risk_mode=true"})
        if config.strategy.cleanup_enabled:
            warnings.append({"level": "warning", "reason": "cleanup_enabled", "message": "cleanup_enabled=true"})
        if raw_mapping.get("status") != "PASS":
            warnings.append({"level": "warning", "reason": "raw_execution_mapping", "message": raw_mapping.get("message", "raw execution mapping 확인 필요")})
        if any(order.get("status") in {"REQUESTED", "PARTIAL"} for order in orders):
            warnings.append({"level": "warning", "reason": "open_order_exists", "message": "open order 존재"})
        return warnings

    def stocks(self) -> list[dict[str, Any]]:
        config = self.config
        positions = {item["code"]: item for item in self.positions()}
        latest_decisions = self.latest_decisions_by_code()
        lots = self.lots()
        result = []
        for stock in config.stocks:
            position = positions.get(stock.code, {})
            open_lots = [lot for lot in lots if lot.get("code") == stock.code and lot.get("remaining_quantity", 0) > 0 and lot.get("status") != "CLOSED"]
            risk_reasons = [flag for flag in RISK_FLAGS if getattr(stock, flag)]
            decision = latest_decisions.get(stock.code, {})
            result.append({
                **asdict(stock),
                "risk_block_reasons": ",".join(risk_reasons),
                "position_state": position.get("position_state", "UNKNOWN"),
                "current_price": position.get("current_price", 0),
                "open_lot_count": len(open_lots),
                "invested_amount": sum(int(lot.get("buy_price", 0)) * int(lot.get("remaining_quantity", 0)) for lot in open_lots),
                "profit_loss_pct": position.get("profit_loss_pct", 0.0),
                "last_decision": decision.get("action", ""),
                "skip_reason": decision.get("skip_reason", ""),
                "final_block_reason": decision.get("final_block_reason", ""),
            })
        return result

    def stock_detail(self, code: str) -> dict[str, Any]:
        code = str(code).zfill(6)
        config = self.config
        position = next((item for item in self.positions() if item["code"] == code), {"code": code})
        lots = [lot for lot in self.lots() if lot.get("code") == code]
        open_lots = [lot for lot in lots if lot.get("remaining_quantity", 0) > 0 and lot.get("status") != "CLOSED"]
        lot_manager = LotManager(config.strategy)
        lot_manager.lots = {lot["lot_id"]: _lot_state_from_row(lot) for lot in lots}
        strategy = LotGridStrategy(config, lot_manager)
        position_state = _position_state_from_row(position)
        current_price = int(position.get("current_price") or 0)
        context = strategy.context(position_state, current_price) if current_price else None
        return {
            "stock": next((asdict(stock) for stock in config.stocks if stock.code == code), {"code": code}),
            "position": position,
            "lots": lots,
            "open_lot_count": len(open_lots),
            "strategy_context": asdict(context) if context else {},
            "recent_decisions": [item for item in self.parse_decision_logs(300) if item.get("code") == code][-50:],
        }

    def positions(self) -> list[dict[str, Any]]:
        return self._table("positions")

    def lots(self) -> list[dict[str, Any]]:
        rows = self._table("lots")
        now = datetime.now()
        by_code_price = {item["code"]: int(item.get("current_price") or 0) for item in self.positions()}
        for row in rows:
            current_price = by_code_price.get(row["code"], 0)
            row["current_price"] = current_price
            row["lot_age_days"] = _age_days(row.get("buy_filled_at", ""), now)
            row["unrealized_pnl"] = (current_price - int(row.get("buy_price", 0))) * int(row.get("remaining_quantity", 0)) if current_price else 0
            row["unrealized_pnl_rate"] = (current_price - int(row.get("buy_price", 0))) / int(row.get("buy_price", 1)) if current_price and row.get("buy_price") else 0
            row["stale_lot"] = row["unrealized_pnl_rate"] <= self.config.strategy.stale_lot_loss_rate and float(row.get("age_weeks") or 0) >= self.config.strategy.stale_lot_min_age_weeks
            row["sell_trigger_price"] = int(round(int(row.get("buy_price", 0)) * (1 + float(row.get("effective_target_profit_rate") or 0))))
        return rows

    def orders(self) -> list[dict[str, Any]]:
        return self._table("orders")

    def fills(self) -> list[dict[str, Any]]:
        rows = self._table("fills")
        seen = set()
        for row in rows:
            execution_id = row.get("execution_id", "")
            row["dedupe_key_type"] = "fallback" if not execution_id or str(execution_id).startswith("AGG:") else "execution_id"
            key = execution_id or "|".join(str(row.get(part, "")) for part in ("order_id", "code", "side", "lot_id", "price", "quantity", "filled_at"))
            row["is_duplicate"] = key in seen
            seen.add(key)
            row["apply_fill"] = True
            row["position_lots_reflected"] = True
        return rows

    def parse_log_events(self, limit: int = 300) -> list[str]:
        log_path = Path(self.config.log_path)
        if not log_path.exists():
            return []
        return [_mask_sensitive(line.rstrip()) for line in _tail_lines(log_path, limit)]

    def parse_decision_logs(self, limit: int = 500) -> list[dict[str, Any]]:
        decisions = []
        for line in self.parse_log_events(limit):
            if " decision " not in line:
                continue
            timestamp = line[:19]
            body = line.split(" decision ", 1)[1]
            item = {"timestamp": timestamp}
            item.update(_parse_key_values(body))
            decisions.append(item)
        return decisions

    def latest_decisions_by_code(self) -> dict[str, dict[str, Any]]:
        latest = {}
        for decision in self.parse_decision_logs(1000):
            code = str(decision.get("code") or "")
            if code:
                latest[code] = decision
        return latest

    def logs_tail(self, limit: int = 300, keyword: str = "", level: str = "", event: str = "") -> dict[str, Any]:
        lines = self.parse_log_events(limit)
        if keyword:
            lines = [line for line in lines if keyword.lower() in line.lower()]
        if level:
            lines = [line for line in lines if f" {level.upper()} " in line]
        if event:
            lines = [line for line in lines if event in line]
        return {"lines": lines[-limit:], "count": len(lines)}

    def execution_mapping_status(self) -> dict[str, Any]:
        raw_lines = [line for line in self.parse_log_events(1000) if "kis_raw_executions" in line]
        latest = raw_lines[-1] if raw_lines else ""
        reconcile_lines = [line for line in self.parse_log_events(1000) if "startup_execution_reconcile" in line or "reconcile_execution_result" in line]
        parsed = _parse_key_values(latest)
        parsed_reconcile = _parse_key_values(reconcile_lines[-1].split(" ", 3)[-1]) if reconcile_lines else {}
        has_required = all(str(parsed.get(key, "False")) == "True" for key in ("has_order_no", "has_filled_at", "has_side"))
        raw_count = int(parsed.get("raw_execution_count") or 0)
        status = "PASS" if raw_count > 0 and has_required else ("WARN" if raw_count == 0 else "FAIL")
        return {
            "status": status,
            "message": "첫 실제 체결 raw row 확인 필요" if raw_count == 0 else ("필수 필드 확인" if has_required else "필수 필드 누락"),
            **parsed,
            **{key: parsed_reconcile.get(key, "") for key in ("fetched_execution_count", "new_fill_count", "duplicate_fill_count", "ignored_unmatched_execution_count")},
            "raw_log_line": latest,
        }

    def reconciliation_summary(self, log_lines: list[str]) -> dict[str, Any]:
        latest = next((line for line in reversed(log_lines) if "startup_execution_reconcile" in line or "reconcile_execution_result" in line), "")
        return {"latest": latest, **_parse_key_values(latest)}

    def validate_config_data(self, raw: dict[str, Any]) -> tuple[bool, list[str]]:
        errors = []
        try:
            config = _load_config_from_data(raw, self.config_path.parent)
        except Exception as error:  # noqa: BLE001
            return False, [f"config_load_failed: {error}"]
        strategy = config.strategy
        risk = config.risk
        order = config.order
        market = config.market_hours
        if strategy.initial_buy_amount <= 0:
            errors.append("initial_buy_amount must be > 0")
        if strategy.auto_buy_limit < strategy.initial_buy_amount:
            errors.append("auto_buy_limit must be >= initial_buy_amount")
        if strategy.absolute_max_investment < strategy.auto_buy_limit:
            errors.append("absolute_max_investment must be >= auto_buy_limit")
        if strategy.pnl_minus_threshold >= 0 or strategy.pnl_plus_threshold <= 0:
            errors.append("pnl thresholds must straddle zero")
        if strategy.cleanup_min_target_rate > 0:
            errors.append("cleanup_min_target_rate must be <= 0")
        if not 0 <= strategy.cleanup_profit_offset_ratio <= 1:
            errors.append("cleanup_profit_offset_ratio must be between 0 and 1")
        errors.extend(_validate_bands("exposure_buy_bands", [asdict(item) for item in strategy.exposure_buy_bands], "amount"))
        errors.extend(_validate_bands("exposure_sell_bands", [asdict(item) for item in strategy.exposure_sell_bands], "target_profit_pct"))
        if risk.daily_account_loss_limit_pct > 0 or risk.total_account_loss_limit_pct > 0:
            errors.append("loss limits must be <= 0")
        if risk.max_consecutive_api_errors < 1:
            errors.append("max_consecutive_api_errors must be >= 1")
        if min(risk.min_cash_available, risk.max_active_symbols, risk.max_total_open_lots, risk.max_total_invested_amount, risk.max_new_buy_per_day) < 0:
            errors.append("risk limits must be >= 0")
        if order.price_sample_count < 1 or order.price_sample_interval_seconds <= 0 or order.limit_order_timeout_seconds <= 0:
            errors.append("order timing/sample values are invalid")
        if min(order.order_cooldown_seconds, order.min_order_request_interval_seconds, order.execution_query_buffer_minutes, order.startup_execution_lookup_days) < 0:
            errors.append("order nonnegative settings are invalid")
        if market.open_time >= market.close_time:
            errors.append("open_time must be before close_time")
        if min(market.block_after_open_minutes, market.block_before_close_minutes) < 0:
            errors.append("market block minutes must be >= 0")
        return not errors, errors

    def save_config_patch(self, patch: dict[str, Any], updated_by: str = "local_ui") -> dict[str, Any]:
        raw = self.raw_config()
        updated = _deep_merge(raw, patch)
        valid, errors = self.validate_config_data(updated)
        if not valid:
            return {"saved": False, "errors": errors}
        backup_path = self.backup_config()
        temp_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, self.config_path)
        reloaded = self.raw_config()
        if reloaded != updated:
            shutil.copy2(backup_path, self.config_path)
            return {"saved": False, "errors": ["round_trip_verification_failed"], "backup_path": str(backup_path)}
        self._append_config_history(raw, updated, updated_by, backup_path)
        return {"saved": True, "backup_path": str(backup_path), "restart_required": True}

    def backup_config(self) -> Path:
        backup_dir = self.config_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{self.config_path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{self.config_path.suffix}"
        shutil.copy2(self.config_path, backup_path)
        return backup_path

    def patch_stock(self, code: str, updates: dict[str, Any]) -> dict[str, Any]:
        code = str(code).zfill(6)
        raw = self.raw_config()
        stocks = raw.setdefault("stocks", [])
        for stock in stocks:
            if str(stock.get("code", "")).zfill(6) == code:
                for key in ("enabled", *RISK_FLAGS):
                    if key in updates:
                        stock[key] = bool(updates[key])
                return self.save_config_patch({"stocks": stocks})
        return {"saved": False, "errors": [f"stock_not_found: {code}"]}

    def runtime_set(self, **updates: Any) -> dict[str, Any]:
        current = asdict(load_runtime_control(self.runtime_path))
        current.update(updates)
        current["updated_at"] = datetime.now().isoformat(timespec="seconds")
        control = RuntimeControl(**{key: current.get(key) for key in RuntimeControl.__dataclass_fields__})
        save_runtime_control(control, self.runtime_path)
        return asdict(control)

    def runtime_status(self) -> dict[str, Any]:
        return asdict(load_runtime_control(self.runtime_path))

    def decision_preview(self, code: str | None = None, current_price: int | None = None) -> dict[str, Any]:
        config = self.config
        lots = {row["lot_id"]: _lot_state_from_row(row) for row in self.lots()}
        lot_manager = LotManager(config.strategy, lots)
        strategy = LotGridStrategy(config, lot_manager)
        positions = [_position_state_from_row(row) for row in self.positions()]
        if code:
            positions = [position for position in positions if position.code == str(code).zfill(6)]
        previews = []
        for position in positions:
            price = current_price or position.current_price
            context = strategy.context(position, price) if price else None
            action = None if not price else strategy.decide(position, price, _empty_snapshot(), _allowed(), _allowed())
            final_block_reason = runtime_block_reason(load_runtime_control(self.runtime_path), action) if action else ""
            previews.append({
                "code": position.code,
                "current_price": price,
                "dry_run": True,
                "action_created": bool(action),
                "action_blocked_before_request": bool(final_block_reason),
                "action_execution_state": "BLOCKED" if final_block_reason else ("CANDIDATE" if action else "NONE"),
                "action_type": action.reason if action else "NONE",
                "side": action.side.value if action else "",
                "sell_reason": action.sell_reason if action else "",
                "reentry_type": action.reentry_type if action else "",
                "skip_reason": position.skip_reason,
                "final_block_reason": final_block_reason,
                "context": asdict(context) if context else {},
            })
        return {"previews": previews, "dry_run": True, "order_api_called": False}

    def _table(self, table: str) -> list[dict[str, Any]]:
        db_path = Path(self.config.storage_path)
        if not db_path.exists():
            return []
        with sqlite3.connect(db_path) as connection:
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.Error:
                return []
        return [_normalize_row(dict(row)) for row in rows]

    def _latest_log_time(self) -> str:
        for line in reversed(self.parse_log_events(200)):
            if len(line) >= 19:
                return line[:19]
        return ""

    def _latest_error(self) -> str:
        for line in reversed(self.parse_log_events(500)):
            if " ERROR " in line or "auto-trader error" in line:
                return line
        return ""

    def market_status(self) -> str:
        config = self.config
        now = datetime.now()
        open_hour, open_minute = [int(part) for part in config.market_hours.open_time.split(":", 1)]
        close_hour, close_minute = [int(part) for part in config.market_hours.close_time.split(":", 1)]
        open_time = datetime.combine(now.date(), day_time(open_hour, open_minute))
        close_time = datetime.combine(now.date(), day_time(close_hour, close_minute))
        start = open_time.timestamp() + config.market_hours.block_after_open_minutes * 60
        end = close_time.timestamp() - config.market_hours.block_before_close_minutes * 60
        if now < open_time:
            return "장전"
        if now > close_time:
            return "장마감"
        if not (start <= now.timestamp() <= end):
            return "매매 차단 구간"
        return "장중"

    def _append_config_history(self, before: dict[str, Any], after: dict[str, Any], updated_by: str, backup_path: Path) -> None:
        history_path = self.config_path.parent / "config_change_history.jsonl"
        item = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "updated_by": updated_by,
            "backup_path": str(backup_path),
            "changed_top_level_keys": sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key)),
        }
        with history_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(item, ensure_ascii=False) + "\n")


def _load_config_from_data(raw: dict[str, Any], directory: Path) -> BotConfig:
    temp_path = directory / ".ui_config_validate_tmp.json"
    try:
        temp_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        return load_config(temp_path)
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _validate_bands(name: str, bands: list[dict[str, Any]], value_key: str) -> list[str]:
    errors = []
    previous_max = None
    for band in sorted(bands, key=lambda item: item["min_exposure"]):
        if band["min_exposure"] > band["max_exposure"]:
            errors.append(f"{name} min_exposure must be <= max_exposure")
        if previous_max is not None and band["min_exposure"] <= previous_max:
            errors.append(f"{name} must not overlap")
        if band.get(value_key, 0) < 0 or (value_key == "amount" and band.get(value_key, 0) <= 0):
            errors.append(f"{name} {value_key} invalid")
        previous_max = band["max_exposure"]
    return errors


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(base))
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _tail_lines(path: Path, limit: int) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as input_file:
        lines = input_file.readlines()
    return lines[-limit:]


def _mask_sensitive(value: str) -> str:
    masked = value
    for part in SENSITIVE_PARTS:
        masked = re.sub(rf"({part}[^=\s:]*[=:]\s*)[^,\s}}]+", rf"\1***", masked, flags=re.IGNORECASE)
    masked = re.sub(r"\b\d{8,}\b", "***", masked)
    return masked


def _parse_key_values(body: str) -> dict[str, str]:
    return dict(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=([^ ]+)", body))


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key, "UNKNOWN"))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    for key, value in list(row.items()):
        if key in {"needs_review", "auto_buy_enabled", "danger_state", "lot_quantity_mismatch", "trading_paused", "sell_completed", "partial_sold", "cleanup_candidate", "cleanup_flag", "anchor_single_fill"}:
            row[key] = bool(value)
    return row


def _age_days(value: str, now: datetime) -> float:
    if not value:
        return 0.0
    try:
        return max(0.0, (now - datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)).total_seconds() / 86400)
    except ValueError:
        return 0.0


def _lot_state_from_row(row: dict[str, Any]):
    from .models import LotState

    fields = {key: row.get(key) for key in LotState.__dataclass_fields__}
    return LotState(**fields)


def _position_state_from_row(row: dict[str, Any]) -> PositionState:
    fields = {}
    for key, field in PositionState.__dataclass_fields__.items():
        value = row.get(key, field.default)
        fields[key] = value
    return PositionState(**fields)


def _empty_snapshot():
    from .models import AccountSnapshot

    return AccountSnapshot(10_000_000, 10_000_000, 0, 0, ())


def _allowed():
    from .risk_manager import RiskDecision

    return RiskDecision(True)
