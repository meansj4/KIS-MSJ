"""Read-only dashboard and guarded-control service for the local web UI."""

from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import re
import shutil
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, time as day_time
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG_PATH, BotConfig, config_hash, load_config
from .kis_client import KisClient
from .lot_manager import LotManager, round_price
from .models import OrderSide, PositionLifecycle, PositionState
from .runtime_control import DEFAULT_RUNTIME_CONTROL_PATH, RuntimeControl, load_runtime_control, runtime_block_reason, save_runtime_control
from .strategy import LotGridStrategy, StrategyAction


SENSITIVE_PARTS = ("account", "acct", "cano", "acnt", "appkey", "appsecret", "token", "authorization", "auth")
RISK_FLAGS = ("trading_halted", "administrative_issue", "investment_alert", "audit_opinion_issue", "delisting_risk", "accounting_issue", "liquidity_warning")
MANUAL_PROCESSING_STALE_MINUTES = 10
MANUAL_REQUEUE_CONFIRM_TEXT = "수동요청 재처리 확인"
MANUAL_CANCEL_CONFIRM_TEXT = "수동요청 차단 확인"
MANUAL_PENDING_STATUSES = {"REQUESTED", "PROCESSING", "ACCEPTED", "SUBMITTED", "PENDING", "OPEN", "NEW", "CREATED", "RETRYING"}
ORDER_PENDING_STATUSES = {"REQUESTED", "PARTIAL", "CANCEL_REJECTED", "SUBMITTED", "ACCEPTED", "PENDING", "OPEN", "NEW"}

NEW_SEASON_REASON_GUIDE: dict[str, dict[str, str]] = {
    "": {"title": "진행 가능", "description": "현재 단계의 조건을 만족했습니다.", "next_action": "다음 단계로 진행하세요."},
    "liquidation_plan_missing": {"title": "전량매도 예정표가 없습니다.", "description": "현재 DB와 실제 계좌 잔고를 기준으로 만든 전량매도 예정표가 아직 없습니다.", "next_action": "3단계에서 전량매도 예정표를 생성하세요."},
    "liquidation_plan_not_active": {"title": "현재 예정표가 유효하지 않습니다.", "description": "예정표가 ACTIVE 상태가 아니어서 전량매도 요청 생성에 사용할 수 없습니다.", "next_action": "전량매도 예정표를 새로 생성하세요."},
    "liquidation_plan_stale": {"title": "예정표가 오래되었습니다.", "description": "예정표 생성 후 시간이 지나 최신 상태라고 보기 어렵습니다.", "next_action": "최신 DB/잔고 기준으로 예정표를 다시 생성하세요."},
    "liquidation_plan_db_changed": {"title": "예정표 생성 후 보유 LOT이 변경되었습니다.", "description": "DB의 OPEN LOT 상태가 예정표 생성 시점과 달라졌습니다.", "next_action": "전량매도 예정표를 다시 생성하세요."},
    "liquidation_plan_snapshot_expired": {"title": "KIS 잔고 확인 자료가 만료되었습니다.", "description": "실제 계좌 잔고 확인 자료가 오래되어 현재 상태를 보장할 수 없습니다.", "next_action": "최신 KIS 잔고 snapshot을 준비한 뒤 예정표를 다시 생성하세요."},
    "liquidation_kis_balance_fetch_required": {"title": "실제 계좌 잔고 확인 자료가 필요합니다.", "description": "DB 수량만으로는 전량매도 요청을 만들 수 없습니다.", "next_action": "KIS 잔고 snapshot을 준비하거나 선택하세요."},
    "liquidation_kis_balance_mismatch": {"title": "DB 보유수량과 실제 계좌 잔고가 다릅니다.", "description": "전량매도 요청 수량이 실제 계좌와 맞지 않을 수 있습니다.", "next_action": "reconciliation/sync 상태를 먼저 확인하세요."},
    "liquidation_sellable_quantity_insufficient": {"title": "매도가능수량이 부족합니다.", "description": "실제 계좌의 매도가능수량이 전량매도 요청 수량보다 적습니다.", "next_action": "미체결 주문 또는 계좌 상태를 확인하세요."},
    "liquidation_open_order_exists": {"title": "미체결 주문이 있습니다.", "description": "이미 진행 중인 주문이 있어 전량매도 요청을 만들 수 없습니다.", "next_action": "주문이 체결/취소될 때까지 기다리거나 주문 상태를 확인하세요."},
    "liquidation_pending_manual_sell_exists": {"title": "처리 중인 수동 매도 요청이 있습니다.", "description": "같은 종목 또는 LOT에 대한 수동 매도 요청이 아직 끝나지 않았습니다.", "next_action": "수동 요청 처리가 끝난 뒤 다시 확인하세요."},
    "liquidation_plan_pending_work_created": {"title": "예정표 생성 후 진행 중 작업이 생겼습니다.", "description": "예정표 생성 이후 미체결 주문 또는 미처리 수동 요청이 생겼습니다.", "next_action": "진행 중 작업을 정리하고 예정표를 다시 생성하세요."},
    "liquidation_plan_sync_required": {"title": "동기화가 필요합니다.", "description": "SYNC_REQUIRED 상태가 있어 전량매도 요청 생성이 차단됩니다.", "next_action": "reconciliation을 먼저 완료하세요."},
    "liquidation_plan_lot_mismatch": {"title": "LOT 수량 불일치가 있습니다.", "description": "DB LOT 수량과 포지션/실제 계좌 수량이 맞지 않을 가능성이 있습니다.", "next_action": "수량 불일치를 해결한 뒤 다시 시도하세요."},
    "liquidation_kis_balance_snapshot_missing_generated_at": {"title": "잔고 snapshot 생성시각이 없습니다.", "description": "실제 전량매도 요청 생성에는 최신성 검증을 위한 generated_at 값이 필요합니다.", "next_action": "generated_at이 포함된 최신 KIS 잔고 snapshot을 준비하세요."},
    "liquidation_kis_balance_snapshot_invalid_generated_at": {"title": "잔고 snapshot 생성시각을 읽을 수 없습니다.", "description": "generated_at 값이 ISO 시간 형식이 아니어서 최신성 검증을 할 수 없습니다.", "next_action": "generated_at을 예: 2026-05-27T09:30:00+09:00 형식으로 수정한 snapshot을 준비하세요."},
    "liquidation_kis_balance_snapshot_stale": {"title": "잔고 snapshot이 오래되었습니다.", "description": "snapshot 생성시각이 허용 유효시간을 초과했습니다.", "next_action": "최신 KIS 잔고 snapshot을 다시 준비하고 전량매도 예정표를 재생성하세요."},
    "liquidation_kis_sellable_quantity_missing": {"title": "매도가능수량이 없습니다.", "description": "실제 전량매도 요청 생성에는 sellable_quantity 또는 ord_psbl_qty 같은 매도가능수량 필드가 필요합니다.", "next_action": "매도가능수량이 포함된 최신 KIS 잔고 snapshot을 준비하세요."},
    "reset_open_lot_exists": {"title": "아직 보유 LOT이 남아 있습니다.", "description": "OPEN LOT이 남아 있어 DB 초기화를 할 수 없습니다.", "next_action": "전량매도와 체결 동기화를 완료하세요."},
    "reset_pending_order_exists": {"title": "미체결 주문이 있습니다.", "description": "진행 중 주문이 있어 DB 초기화를 할 수 없습니다.", "next_action": "주문 상태를 확인하고 종결될 때까지 기다리세요."},
    "reset_pending_manual_request_exists": {"title": "미처리 수동 요청이 있습니다.", "description": "manual_order_requests에 아직 진행 중인 요청이 있습니다.", "next_action": "수동 요청 처리를 완료하세요."},
    "reset_sync_required": {"title": "DB와 실제 계좌 동기화가 필요합니다.", "description": "SYNC_REQUIRED 상태에서는 DB 초기화가 위험합니다.", "next_action": "reconciliation을 먼저 완료하세요."},
}

PLAN_STATUS_GUIDE: dict[str, str] = {
    "ACTIVE": "현재 전량매도 예정표가 유효합니다.",
    "EXPIRED": "예정표가 오래되어 새로 만들어야 합니다.",
    "SUPERSEDED": "더 최신 예정표가 있어 이 예정표는 사용할 수 없습니다.",
    "USED": "이미 전량매도 요청 생성에 사용된 예정표입니다.",
    "BLOCKED": "차단 사유가 있어 사용할 수 없습니다.",
    "": "전량매도 예정표가 아직 없습니다.",
}


CONFIG_METADATA: tuple[dict[str, Any], ...] = (
    {"section": "Strategy", "key": "strategy.review_loss_pct", "label_ko": "검토 필요 손실률", "description_ko": "종목 손실이 이 수준에 도달하면 REVIEW_REQUIRED 후보가 됩니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.max_open_lots_before_review", "label_ko": "검토 전 최대 OPEN LOT 수", "description_ko": "OPEN LOT이 많아진 종목을 재검토 대상으로 보기 위한 기준입니다.", "type": "number", "unit": "개", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.pnl_minus_threshold", "label_ko": "MINUS 모드 기준", "description_ko": "포지션 손익률이 이 값 이하이면 MINUS 모드입니다. UI는 %로 표시하지만 config에는 소수로 저장됩니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "normal", "requires_restart": True},
    {"section": "Strategy", "key": "strategy.pnl_plus_threshold", "label_ko": "PLUS 모드 기준", "description_ko": "포지션 손익률이 이 값 이상이면 PLUS 모드입니다. UI는 %로 표시하지만 config에는 소수로 저장됩니다.", "type": "number", "unit": "%", "display_format": "decimal_percent", "config_format": "decimal_rate", "warning_level": "normal", "requires_restart": True},
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
    {"section": "Risk", "key": "risk.max_new_buy_amount_per_day", "label_ko": "일일 신규 매수 총액 제한", "description_ko": "하루 initial_buy 주문 금액 합계 제한입니다. 100종목 확장 운용에서 고가 LOT 종목이 몰릴 때 하루 투입액이 과도해지는 것을 막습니다.", "type": "number", "unit": "원", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Risk", "key": "risk.max_total_initial_buy_amount_per_day", "label_ko": "일일 최초 매수 총액 제한", "description_ko": "max_new_buy_amount_per_day와 같은 목적의 호환 필드입니다. 값이 있으면 initial_buy 하루 총액 제한으로 우선 사용됩니다.", "type": "number", "unit": "원", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Risk", "key": "risk.profile", "label_ko": "리스크 프로파일", "description_ko": "현재 운용 한도 묶음 이름입니다. 예: expansion_100_safe, expansion_100_medium, expansion_100_aggressive.", "type": "text", "unit": "profile", "display_format": "text", "config_format": "text", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.live_trading", "label_ko": "실거래 모드", "description_ko": "true이면 실제 KIS 주문 API를 통해 주문이 나갈 수 있습니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "critical", "requires_restart": True, "danger_confirm_required": True},
    {"section": "Order", "key": "order.emergency_market_order", "label_ko": "비상 시장가 주문", "description_ko": "true이면 비상 상황에서 시장가 주문을 허용할 수 있어 매우 위험합니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "critical", "requires_restart": True, "danger_confirm_required": True},
    {"section": "Order", "key": "order.buy_limit_markup_pct", "label_ko": "매수 지정가 가산율", "description_ko": "BUY 주문을 낼 때 현재가 그대로 주문하지 않고, 체결 가능성을 높이기 위해 현재가보다 이 비율만큼 높은 지정가를 계산합니다. 예: 현재가 10,000원, 값 0.3이면 매수 지정가는 약 10,030원입니다. 시장가가 아니라 지정가 주문 정책입니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.sell_limit_markdown_pct", "label_ko": "매도 지정가 할인율", "description_ko": "SELL 주문을 낼 때 현재가 그대로 주문하지 않고, 체결 가능성을 높이기 위해 현재가보다 이 비율만큼 낮은 지정가를 계산합니다. 예: 현재가 10,000원, 값 0.3이면 매도 지정가는 약 9,970원입니다. 시장가가 아니라 지정가 주문 정책입니다.", "type": "number", "unit": "%", "display_format": "percent_value", "config_format": "percent_value", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.price_sample_count", "label_ko": "가격 샘플 수", "description_ko": "주문을 요청하기 직전에 현재가를 몇 번 확인할지 정합니다. 봇은 BUY/SELL action이 만들어진 뒤 바로 주문하지 않고, 이 횟수만큼 가격을 읽어 주문 직전 가격이 너무 흔들리지 않는지 확인합니다. 예: 값이 5이면 가격을 5번 읽고, 샘플들이 허용 변동성 안에 있을 때만 지정가 주문을 계산합니다.", "type": "number", "unit": "개", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.price_sample_interval_seconds", "label_ko": "가격 샘플 간격", "description_ko": "가격 샘플을 여러 번 읽을 때 각 읽기 사이에 기다리는 시간입니다. 예: 가격 샘플 수가 5이고 간격이 0.2초이면, 주문 직전 약 0.8초 동안 가격을 5번 확인합니다. 샘플 간 변동성이 risk.max_price_sample_volatility_pct를 넘으면 주문을 피합니다.", "type": "number", "unit": "초", "display_format": "number", "config_format": "number", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.limit_order_timeout_seconds", "label_ko": "지정가 주문 타임아웃", "description_ko": "지정가 주문을 낸 뒤 체결 확인을 기다리는 최대 시간입니다. 이 시간 안에 전량 체결되지 않으면 주문 상태를 다시 확인하고, 설정/상황에 따라 미체결 또는 부분체결 상태로 남겨 reconciliation이 계속 추적합니다.", "type": "number", "unit": "초", "display_format": "integer", "config_format": "integer", "warning_level": "warning", "requires_restart": True},
    {"section": "Order", "key": "order.order_cooldown_seconds", "label_ko": "종목별 주문 쿨다운", "description_ko": "같은 종목에 대해 너무 짧은 시간 안에 연속 주문이 나가지 않도록 막는 대기 시간입니다. 특히 재진입/추가매수 직후 같은 종목에 다시 BUY가 몰리는 것을 줄이는 안전장치입니다.", "type": "number", "unit": "초", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
    {"section": "Order", "key": "order.min_order_request_interval_seconds", "label_ko": "전체 최소 주문 요청 간격", "description_ko": "종목과 무관하게 봇 전체에서 주문 요청 사이에 최소로 띄울 시간입니다. 여러 종목이 동시에 조건을 만족해도 주문 API를 짧은 시간에 연속 호출하지 않도록 하는 전역 주문 난사 방지 장치입니다.", "type": "number", "unit": "초", "display_format": "integer", "config_format": "integer", "warning_level": "normal", "requires_restart": True},
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
    {"section": "UI / Manual Orders", "key": "ui_manual_trading_enabled", "label_ko": "수동 주문 요청 활성화", "description_ko": "true이면 UI가 manual order request를 생성할 수 있습니다. UI가 직접 주문 API를 호출하지는 않습니다.", "type": "boolean", "unit": "bool", "display_format": "boolean", "config_format": "boolean", "warning_level": "critical", "requires_restart": True, "danger_confirm_required": True},
)


HIDDEN_CONFIG_KEYS = {
    "strategy.reentry_drop_rate",
    "strategy.initial_buy_amount",
    "strategy.auto_buy_limit",
    "strategy.absolute_max_investment",
    "strategy.exposure_buy_bands",
    "strategy.exposure_sell_bands",
}


DETAILED_CONFIG_DESCRIPTIONS: dict[str, str] = {
    "strategy.review_loss_pct": "종목 전체 손실률을 사람이 보기 시작해야 하는 기준입니다. 예를 들어 -20이면 해당 종목의 전체 평가손익률이 -20% 근처가 될 때 REVIEW_REQUIRED 후보가 됩니다. REVIEW_REQUIRED에서는 신규 BUY는 막고, 수익권 LOT의 PROFIT_TAKE SELL만 허용하는 보수 정책을 씁니다.",
    "strategy.max_open_lots_before_review": "한 종목에 OPEN LOT이 너무 많이 쌓이면 전략이 복잡해지고 수동 확인이 필요합니다. 이 값은 그런 종목을 REVIEW_REQUIRED 후보로 표시하기 위한 LOT 개수 기준입니다. LOT을 강제로 팔지는 않고, 추가 BUY를 보수적으로 막는 데 사용됩니다.",
    "strategy.pnl_minus_threshold": "보유 중 추가매수 기준가격을 정할 때 쓰는 포지션 손익 모드 기준입니다. 포지션 손익률이 이 값 이하이면 MINUS 모드가 되고, reference_buy_price는 OPEN LOT의 VWAP과 median 중 낮은 값으로 잡아 더 보수적으로 추가매수합니다. UI에는 %로 보이지만 config에는 -0.01처럼 소수로 저장됩니다.",
    "strategy.pnl_plus_threshold": "포지션 손익률이 이 값 이상이면 PLUS 모드입니다. PLUS 모드에서는 포지션에 여유가 있다고 보고 reference_buy_price를 OPEN LOT의 VWAP과 median 중 높은 값으로 잡습니다. 그래도 예전처럼 최고가 LOT 하나를 직접 기준으로 쓰지는 않습니다.",
    "strategy.normal_reentry_drop_rate": "PROFIT_TAKE 전량 매도 후 WAIT_REENTRY 상태에서만 쓰는 일반 재진입 하락률입니다. 기준 가격인 normal_exit_anchor_price는 해당 보유 사이클의 SELL 체결가들로 계산한 cycle_sell_vwap_price와 cycle_sell_median_price 중 낮은 값입니다. 현재가가 normal_exit_anchor_price에서 이 비율 이상 하락하면 NORMAL_REENTRY 후보가 됩니다.",
    "strategy.trailing_activation_gain": "TRAILING_REENTRY를 켜기 위한 상승 조건입니다. 기준 가격인 trailing_exit_anchor_price는 전량 매도 사이클의 SELL 체결 VWAP과 median 중 높은 값입니다. 전량 매도 후 post_exit_high_price가 trailing_exit_anchor_price보다 이 비율 이상 올라간 적이 있어야, 이후 고점 대비 조정 매수 조건을 평가합니다.",
    "strategy.trailing_reentry_drop_rate": "TRAILING_REENTRY의 고점 대비 조정폭입니다. 전량 매도 후 새 고점(post_exit_high_price)이 형성되고 trailing_activation_gain 조건을 만족한 뒤, 현재가가 그 고점에서 이 비율 이상 내려오면 TRAILING_REENTRY 후보가 됩니다. 이 조건은 판 가격보다 비싸게 다시 사는 상황도 허용하므로 initial_buy_amount 1회분으로 제한됩니다.",
    "strategy.min_reentry_wait_minutes": "전량 PROFIT_TAKE 후 바로 다시 사는 것을 막기 위한 최소 대기시간입니다. WAIT_REENTRY가 된 뒤 이 시간이 지나야 NORMAL_REENTRY 또는 TRAILING_REENTRY가 실제 매수 후보가 될 수 있습니다.",
    "strategy.max_trailing_reentry_per_day": "TRAILING_REENTRY는 상승 후 눌림목을 사는 장치라 과하면 추격매수가 될 수 있습니다. 이 값은 하루에 허용할 trailing 재진입 횟수입니다. NORMAL_REENTRY와는 별도로 관리됩니다.",
    "strategy.reentry_buy_cooldown_minutes": "재진입 매수 체결 후 바로 추가매수가 이어지는 것을 막는 쿨다운입니다. NORMAL_REENTRY 또는 TRAILING_REENTRY로 새 LOT이 생긴 뒤 이 시간 동안 해당 종목의 추가 BUY를 막습니다.",
    "strategy.age_decay_rate": "오래된 LOT이 목표수익률 때문에 영구 보존되는 문제를 줄이기 위한 주간 목표수익률 감소폭입니다. effective_target_profit_rate = base_target_profit_rate - lot_age_weeks * age_decay_rate로 계산됩니다. 예: base 6%, 값 0.5%p/주이면 12주 후 목표가 0% 근처까지 내려갑니다.",
    "strategy.cleanup_enabled": "오래된 손실 LOT을 제한적으로 정리하는 CLEANUP_SELL 기능입니다. 켜도 아무 손실이나 팔지 않습니다. LOT 나이, effective target, 실제 손실률, cleanup_min_target_rate, 당일 실현수익 기반 cleanup_loss_budget, open order 없음, HOLDING 상태 조건을 모두 만족해야 합니다.",
    "strategy.cleanup_min_age_weeks": "CLEANUP_SELL 후보가 되기 위한 LOT 최소 보유 주수입니다. 이보다 젊은 손실 LOT은 cleanup 대상이 아니며, 기존 전략대로 보유하거나 수익권 회복을 기다립니다.",
    "strategy.cleanup_min_target_rate": "자동 cleanup으로 허용할 최대 손실률입니다. 예: -4%이면 -4%보다 더 큰 손실(-5%, -10% 등)은 자동 매도하지 않습니다. 0% 이상 매도는 cleanup이 아니라 PROFIT_TAKE입니다.",
    "strategy.cleanup_profit_offset_ratio": "당일 실현수익 중 cleanup 손실로 상쇄해도 되는 비율입니다. 예: 당일 실현수익 10,000원, 값 30%이면 cleanup 손실 예산은 3,000원입니다. 예상 손실이 예산을 넘으면 CLEANUP_SELL은 차단됩니다.",
    "strategy.cleanup_buy_cooldown_days": "일부 LOT만 CLEANUP_SELL 된 뒤 같은 종목을 바로 다시 사는 모순을 막는 캘린더 일수입니다. 이 기간 동안 해당 종목의 BUY는 막고 SELL 판단은 유지합니다.",
    "strategy.cleanup_reentry_cooldown_days": "CLEANUP_SELL로 전량 매도되어 OPEN LOT이 0이 된 뒤 모든 BUY를 막는 캘린더 일수입니다. 기본 정책은 쿨다운 종료 후 자동 재진입이 아니라 REVIEW_REQUIRED로 보내 사람이 다시 볼 수 있게 하는 것입니다.",
    "strategy.cleanup_auto_return_to_wait_reentry": "전량 CLEANUP_SELL 후 쿨다운이 끝났을 때 WAIT_REENTRY로 자동 복귀할지 정합니다. 기본 false가 안전합니다. true이면 손실 정리 직후 시간이 지나면 다시 자동 재진입 조건을 볼 수 있어 공격적입니다.",
    "strategy.stale_lot_loss_rate": "STALE LOT 표시를 위한 손실률 기준입니다. 이 기준 이하 손실인 오래된 LOT은 주의가 필요한 LOT으로 표시됩니다. STALE은 즉시 손절 신호가 아니며, cleanup 조건과 loss budget이 맞을 때만 CLEANUP_SELL 후보가 됩니다.",
    "strategy.stale_lot_min_age_weeks": "STALE LOT으로 보기 위한 최소 보유 주수입니다. 손실이 커도 너무 최근에 산 LOT은 바로 stale로 보지 않고, 일정 기간 지나도 회복하지 못한 LOT만 표시합니다.",
    "strategy.stale_lot_price_gap_rate": "매수가 대비 현재가가 얼마나 낮아져야 STALE LOT 후보가 되는지 정합니다. 예: -10%이면 현재가가 buy_price의 90% 이하일 때 stale 조건 중 하나를 만족합니다.",
    "strategy.review_symbol_loss_rate": "종목 전체 평가손실률이 이 기준 이하이면 REVIEW_REQUIRED 후보가 됩니다. REVIEW_REQUIRED는 더 사지 말고 사람이 확인하라는 상태이며, PROFIT_TAKE SELL은 허용하지만 CLEANUP_SELL은 기본적으로 차단합니다.",
    "strategy.stale_lot_review_age_weeks": "STALE LOT이 너무 오래 지속되면 자동 로직만으로 계속 끌고 가기 어렵습니다. 이 주수 이상 오래된 stale LOT은 REVIEW_REQUIRED 후보가 됩니다.",
    "strategy.high_exposure_partial_sell_pct": "고투입 구간에서 전체 포지션을 줄이기 위해 부분 매도를 할 때 사용할 비율입니다. 포지션 축소 목적의 보수 장치이며, LOT 단위 매도 원칙과 체결 기준 DB 반영 원칙은 그대로 유지됩니다.",
    "strategy.estimated_fee_tax_pct": "UI 미리보기와 손익 추정에서 사용하는 예상 수수료/세금 비율입니다. 실제 KIS 정산값과 완전히 같지는 않을 수 있지만, PROFIT_TAKE/CLEANUP 판단에서 net 추정치를 볼 때 보수적으로 참고합니다.",
    "risk.market_risk_mode": "시장 전체가 위험하다고 판단될 때 켜는 수동 안전 플래그입니다. true이면 신규 BUY를 보수적으로 제한합니다. 급락장, 시스템 이상, 사람이 잠시 자동매매를 줄이고 싶을 때 쓰는 전역 위험 모드입니다.",
    "risk.daily_account_loss_limit_pct": "하루 기준 계좌 손실률 제한입니다. 값은 % 단위이고 보통 음수입니다. 당일 손실이 이 기준을 넘으면 신규 주문을 줄이거나 차단해 하루 손실 확대를 막는 용도입니다.",
    "risk.total_account_loss_limit_pct": "전체 계좌 기준 손실률 제한입니다. 하루 변동이 아니라 계좌 전체 손실이 커질 때 자동매매 노출을 줄이기 위한 전역 안전장치입니다.",
    "risk.max_review_positions": "REVIEW_REQUIRED 종목이 너무 많아졌는지 확인하는 기준입니다. 검토 필요 종목이 많다는 것은 자동 운용 상태가 복잡해졌다는 뜻이므로, UI 경고와 운용 점검에 사용됩니다.",
    "risk.min_cash_available": "계좌에 최소로 남겨둘 현금입니다. 이보다 현금이 적으면 신규 BUY를 막아 주문 실패와 현금 부족을 예방합니다. SELL은 이 제한 때문에 막지 않습니다.",
    "risk.max_consecutive_api_errors": "KIS 조회/주문 관련 API 오류가 연속으로 몇 번까지 허용되는지 정합니다. 연속 오류가 많으면 가격, 잔고, 체결 상태를 믿기 어려워지므로 운용을 보수적으로 봅니다.",
    "risk.max_price_sample_volatility_pct": "주문 직전 price_sample_count만큼 읽은 가격들이 이 비율보다 크게 흔들리면 주문을 피합니다. 예: 샘플 사이 가격이 급변하면 지정가 계산이 의미 없어질 수 있어 안전하게 차단합니다.",
    "risk.block_on_lot_mismatch": "KIS 실제 잔고와 내부 OPEN LOT 합계가 맞지 않을 때 주문을 막을지 정합니다. false는 위험합니다. mismatch 상태에서 주문하면 lots/positions가 더 꼬일 수 있으므로 true가 기본 안전값입니다.",
    "risk.max_active_symbols": "활성 종목 수가 이 값 이상이면 신규 initial_buy를 막습니다. 활성 종목은 OPEN LOT, open order, WAIT_REENTRY, COOLDOWN, REVIEW/RISK/SYNC 상태 등을 포함합니다. 기존 보유 종목의 SELL은 이 제한 때문에 막지 않습니다.",
    "risk.max_total_open_lots": "계좌 전체 OPEN LOT 개수 제한입니다. 초과하면 신규 BUY와 추가매수를 막아 관리해야 할 LOT 수가 과도하게 늘어나는 것을 방지합니다. SELL은 계속 허용합니다.",
    "risk.max_total_invested_amount": "계좌 전체 OPEN LOT 기준 총 투입금 제한입니다. 초과하면 모든 BUY를 막아 계좌 노출을 제한합니다. 이미 보유한 LOT을 줄이는 SELL은 허용합니다.",
    "risk.max_new_buy_per_day": "하루에 허용할 신규 initial_buy 주문 수입니다. 체결 기준이 아니라 주문 요청 기준이므로 거절/취소도 포함합니다. 주문 난사를 막기 위한 보수 정책이며 reentry_buy는 여기에 포함하지 않습니다.",
    "order.live_trading": "true이면 실제 KIS 주문 API로 주문 요청이 나갈 수 있습니다. UI 테스트나 설정 확인만 할 때는 이 값이 매우 중요합니다. live_trading=true에서는 수동 주문 요청에도 강한 확인 문구가 필요합니다.",
    "order.emergency_market_order": "비상 상황에서 시장가 주문을 허용할 수 있는 플래그입니다. 현재 기본 주문 정책은 지정가입니다. 이 값을 true로 두면 예외적 상황에서 시장가 주문 가능성이 생기므로 매우 신중해야 합니다.",
    "order.buy_limit_markup_pct": "BUY 주문을 낼 때 현재가 그대로 주문하지 않고, 체결 가능성을 높이기 위해 현재가보다 이 비율만큼 높은 지정가를 계산합니다. 예: 현재가 10,000원, 값 0.3이면 매수 지정가는 약 10,030원입니다. 시장가가 아니라 지정가 주문 정책입니다.",
    "order.sell_limit_markdown_pct": "SELL 주문을 낼 때 현재가 그대로 주문하지 않고, 체결 가능성을 높이기 위해 현재가보다 이 비율만큼 낮은 지정가를 계산합니다. 예: 현재가 10,000원, 값 0.3이면 매도 지정가는 약 9,970원입니다. 시장가가 아니라 지정가 주문 정책입니다.",
    "order.price_sample_count": "주문을 요청하기 직전에 현재가를 몇 번 확인할지 정합니다. 봇은 BUY/SELL action이 만들어진 뒤 바로 주문하지 않고, 이 횟수만큼 가격을 읽어 주문 직전 가격이 너무 흔들리지 않는지 확인합니다. 예: 값이 5이면 가격을 5번 읽고, 샘플들이 허용 변동성 안에 있을 때만 지정가 주문을 계산합니다.",
    "order.price_sample_interval_seconds": "가격 샘플을 여러 번 읽을 때 각 읽기 사이에 기다리는 시간입니다. 예: 가격 샘플 수가 5이고 간격이 0.2초이면, 주문 직전 약 0.8초 동안 가격을 5번 확인합니다. 샘플 간 변동성이 risk.max_price_sample_volatility_pct를 넘으면 주문을 피합니다.",
    "order.limit_order_timeout_seconds": "지정가 주문을 낸 뒤 체결 확인을 기다리는 최대 시간입니다. 이 시간 안에 전량 체결되지 않으면 주문 상태를 다시 확인하고, 미체결/부분체결 상태로 남겨 reconciliation이 계속 추적합니다.",
    "order.order_cooldown_seconds": "같은 종목에 대해 너무 짧은 시간 안에 연속 주문이 나가지 않도록 막는 대기 시간입니다. 특히 재진입/추가매수 직후 같은 종목에 다시 BUY가 몰리는 것을 줄이는 안전장치입니다.",
    "order.min_order_request_interval_seconds": "종목과 무관하게 봇 전체에서 주문 요청 사이에 최소로 띄울 시간입니다. 여러 종목이 동시에 조건을 만족해도 주문 API를 짧은 시간에 연속 호출하지 않도록 하는 전역 주문 난사 방지 장치입니다.",
    "order.cancel_unfilled_on_start": "봇 시작 시 기존 미체결 주문을 취소하려고 시도할지 정합니다. 실제 주문 상태에 영향을 줄 수 있으므로 live_trading=true 환경에서는 특히 조심해야 합니다.",
    "order.execution_query_buffer_minutes": "open REQUESTED/PARTIAL order가 있을 때, 그 주문 요청시각보다 이 시간만큼 더 과거부터 체결내역을 조회합니다. 봇 재시작이나 API 지연으로 체결을 놓치는 위험을 줄이기 위한 reconciliation 범위 버퍼입니다.",
    "order.include_previous_day_for_open_orders": "open order가 있으면 오늘 체결만 보지 않고 최소 전일 00:00부터 조회를 시도할지 정합니다. 장마감 직전 주문, 다음날 재시작 같은 상황에서 전일 주문 체결을 놓치지 않기 위한 옵션입니다.",
    "order.enable_execution_raw_log": "KIS 체결 응답 원본 필드 mapping을 확인하기 위한 임시 디버그 옵션입니다. 켜면 마스킹된 raw execution sample이 로그에 남습니다. 실체결 필드 검증 후에는 개인정보/로그량 관리를 위해 false로 되돌리는 것을 권장합니다.",
    "order.reconcile_recent_executions_on_startup": "봇 시작 직후 open order 유무와 관계없이 최근 체결내역을 1회 조회해 미반영 봇 주문 체결을 복구할 기회를 제공합니다. manual/unmatched execution은 자동 LOT에 섞지 않습니다.",
    "order.startup_execution_lookup_days": "시작 시 최근 며칠치 체결을 조회할지 정합니다. 값이 1이면 오늘 기준 최근 1일 범위를 봅니다. dedupe가 있으므로 이미 반영된 fill은 중복 반영하지 않습니다.",
    "market_hours.open_time": "정규장 시작 시간입니다. 이 시간 전에는 신규 주문 판단을 하지 않거나 outside_trade_window로 차단합니다.",
    "market_hours.close_time": "정규장 종료 시간입니다. 이 시간 이후에는 신규 주문이 차단됩니다. 장마감 전 차단 구간은 block_before_close_minutes가 별도로 적용됩니다.",
    "market_hours.block_after_open_minutes": "장 시작 직후 변동성이 큰 구간을 피하기 위해 open_time 이후 이 분 수 동안 주문을 차단합니다. 예: 09:00 시작, 값 5이면 09:05 전까지 outside_trade_window가 될 수 있습니다.",
    "market_hours.block_before_close_minutes": "장 마감 직전 체결/취소/동기화 리스크를 줄이기 위해 close_time 이전 이 분 수 동안 신규 주문을 차단합니다.",
    "storage_path": "봇이 orders, fills, lots, positions, manual_order_requests를 저장하는 SQLite DB 경로입니다. UI는 이 DB를 읽어 상태를 보여주며, DB 파일을 바꾸면 다른 운용 상태를 보게 됩니다.",
    "log_path": "자동거래 판단, 주문 요청, 체결 반영, reconciliation, raw execution mapping 로그를 기록하는 파일 경로입니다. UI Logs 화면과 진단 화면은 이 파일을 읽습니다.",
    "kis_account.account_number_env": "실제 계좌번호 값을 config에 직접 쓰지 않고, 계좌번호가 들어 있는 환경변수 이름만 지정합니다. UI에도 실제 계좌번호는 표시하지 않습니다.",
    "kis_account.account_product_code_env": "계좌 상품코드가 들어 있는 환경변수 이름입니다. 실제 값은 환경변수에서 읽고, config/UI에는 key 이름만 둡니다.",
    "kis_account.customer_type": "KIS API 호출에 전달하는 고객 타입 코드입니다. 개인/법인 등 계정 유형에 맞는 값을 써야 하며, 잘못되면 API 요청이 실패할 수 있습니다.",
    "upstream_watch.enabled": "KIS Open Trading API upstream 변경 감시 기능을 켤지 정합니다. 매매 판단 자체와는 별개이며, API 문서/레포 변경을 주기적으로 확인하는 보조 기능입니다.",
    "upstream_watch.interval_seconds": "upstream_watch가 켜져 있을 때 변경 확인을 몇 초마다 할지 정합니다. 너무 짧게 두면 불필요한 fetch/확인이 많아질 수 있습니다.",
    "upstream_watch.repo_path": "Open Trading API repository가 로컬에 있는 경로입니다. upstream 변경 감시 기능이 이 경로를 기준으로 동작합니다.",
    "upstream_watch.fetch": "upstream 확인 시 git fetch 같은 원격 확인을 시도할지 정합니다. 네트워크/권한 영향을 받을 수 있으므로 기본은 보수적으로 둡니다.",
    "loop_interval_seconds": "자동매매 메인 루프가 한 바퀴 돈 뒤 다음 루프까지 기다리는 시간입니다. 짧게 하면 반응은 빨라지지만 API 호출과 로그가 많아집니다. 길게 하면 안정적이지만 기회 포착이 느려집니다.",
    "max_loop_count": "테스트 실행 때 루프를 몇 번만 돌리고 멈출지 정하는 값입니다. 비어 있으면 제한 없이 계속 돕니다. 실운용에서는 보통 null입니다.",
    "ui_manual_trading_enabled": "UI에서 수동 매수/매도 요청을 생성할 수 있게 할지 정합니다. false이면 버튼/API 모두 요청 생성을 차단합니다. true여도 UI는 KIS 주문 API를 직접 호출하지 않고 manual_order_requests 큐에 요청만 넣습니다.",
}


def _lot_sizing_config_metadata() -> list[dict[str, Any]]:
    return [
        {
            "section": "Strategy",
            "key": "strategy.lot_sizing_mode",
            "label_ko": "LOT 금액 결정 방식",
            "description_ko": "cycle_locked_by_entry_price이면 새 보유 사이클의 최초 진입 가격으로 1 LOT 금액과 종목당 최대금액을 정하고, OPEN LOT이 남아 있는 동안에는 현재가가 다른 가격대로 이동해도 이 기준을 다시 계산하지 않습니다.",
            "type": "text",
            "unit": "mode",
            "display_format": "text",
            "config_format": "text",
            "warning_level": "warning",
            "requires_restart": True,
        },
        {
            "section": "Strategy",
            "key": "strategy.price_lot_bands",
            "label_ko": "가격대별 1 LOT 금액",
            "description_ko": "새 사이클 최초 진입 또는 재진입 시 현재가가 어느 가격대에 있는지 보고 1 LOT 금액, 종목당 최대금액, 최대 LOT 수를 결정합니다. enabled=false 구간은 자동매수와 수동 BUY 요청 모두 차단됩니다.",
            "type": "json",
            "unit": "bands",
            "display_format": "json",
            "config_format": "json",
            "warning_level": "warning",
            "requires_restart": True,
        },
        {
            "section": "Strategy",
            "key": "strategy.add_buy_lot_bands",
            "label_ko": "LOT 수 기준 추가매수 구간",
            "description_ko": "cycle locked LOT sizing 모드에서 누적투입금 절대금액 대신 현재 OPEN LOT 수를 기준으로 추가매수 하락률과 추가 LOT 개수를 결정합니다.",
            "type": "json",
            "unit": "bands",
            "display_format": "json",
            "config_format": "json",
            "warning_level": "warning",
            "requires_restart": True,
        },
        {
            "section": "Strategy",
            "key": "strategy.target_profit_lot_bands",
            "label_ko": "LOT 수 기준 매도 목표수익률 구간",
            "description_ko": "cycle locked LOT sizing 모드에서는 LOT을 매수했을 때 저장된 목표수익률이 아니라, 매도 판단 시점의 현재 OPEN LOT 수 구간으로 모든 OPEN LOT의 기본 목표수익률을 다시 계산합니다. 예를 들어 현재 5~6 LOT 구간이면 예전에 산 LOT도 현재 구간 target_profit_rate를 기준으로 age_decay_rate를 적용합니다.",
            "type": "json",
            "unit": "bands",
            "display_format": "json",
            "config_format": "json",
            "warning_level": "warning",
            "requires_restart": True,
        },
        {
            "section": "Strategy",
            "key": "strategy.max_lots_per_symbol_default",
            "label_ko": "종목당 기본 최대 LOT 수",
            "description_ko": "가격대별 band에 max_lots가 따로 없을 때 적용하는 기본 최대 OPEN LOT 수입니다. 이 수에 도달하면 BUY는 max_lots_per_symbol_reached로 차단되고 SELL은 계속 허용됩니다.",
            "type": "number",
            "unit": "개",
            "display_format": "integer",
            "config_format": "integer",
            "warning_level": "warning",
            "requires_restart": True,
        },
    ]


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
        lot_sizing_metadata = _lot_sizing_config_metadata()
        for item in CONFIG_METADATA:
            if item["key"] in HIDDEN_CONFIG_KEYS:
                continue
            enriched = dict(item)
            if enriched["key"] in DETAILED_CONFIG_DESCRIPTIONS:
                enriched["description_ko"] = DETAILED_CONFIG_DESCRIPTIONS[enriched["key"]]
            sections.setdefault(str(enriched["section"]), []).append(enriched)
        metadata = [dict(item) for item in CONFIG_METADATA if item["key"] not in HIDDEN_CONFIG_KEYS]
        for item in lot_sizing_metadata:
            sections.setdefault(str(item["section"]), []).append(dict(item))
            metadata.append(dict(item))
        for item in metadata:
            if item["key"] in DETAILED_CONFIG_DESCRIPTIONS:
                item["description_ko"] = DETAILED_CONFIG_DESCRIPTIONS[item["key"]]
        return {
            "sections": sections,
            "metadata": metadata,
            "restart_required_by_default": True,
            "conversion_notes": {
                "decimal_rate": "UI에서는 percent로 보여주고 저장 시 100으로 나눈 소수로 변환합니다. 예: 4.0% -> 0.04",
                "percent_value": "config 값 자체가 percent입니다. 예: 4.0 -> 4.0%",
                "json": "배열/복합 구조는 JSON으로 편집하며 동일한 validation/backup/atomic save를 거칩니다.",
            },
            "danger_confirm_keys": [item["key"] for item in metadata if item.get("danger_confirm_required")],
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
            "analysis_status": self.analysis_status(positions, lots, fills),
            "loop_performance": self.loop_performance_summary(logs),
        }

    def loop_performance_summary(self, log_lines: list[str]) -> dict[str, Any]:
        loop_rows = []
        for line in log_lines:
            if "loop_profile " not in line:
                continue
            payload = _parse_key_values(line.split("loop_profile", 1)[1])
            if payload:
                loop_rows.append(payload)
        latest = loop_rows[-1] if loop_rows else {}
        durations = [_safe_float(row.get("loop_duration_ms")) for row in loop_rows[-10:] if row.get("loop_duration_ms") not in (None, "")]
        p95 = 0.0
        if durations:
            ordered = sorted(durations)
            index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
            p95 = ordered[index]
        return {
            "last_loop_duration_ms": latest.get("loop_duration_ms", ""),
            "avg_loop_duration_10_ms": round(sum(durations) / len(durations), 2) if durations else "",
            "p95_loop_duration_10_ms": round(p95, 2) if durations else "",
            "slowest_symbol_last_loop": latest.get("slowest_symbol_last_loop", ""),
            "bottleneck_stage": latest.get("bottleneck_stage", ""),
            "symbols_processed": latest.get("symbols_processed", ""),
            "loop_over_interval": latest.get("loop_over_interval", ""),
        }

    def analysis_status(self, positions: list[dict[str, Any]], lots: list[dict[str, Any]], fills: list[dict[str, Any]]) -> dict[str, Any]:
        config_snapshots = self._table("config_snapshots")
        decisions = self._table("decisions")
        price_snapshots = self._table("price_snapshots")
        daily_prices = self._table("daily_prices")
        liquidity_snapshots = self._table("liquidity_snapshots")
        collection_runs = self._table("market_data_collection_runs")
        closed_lots = [lot for lot in lots if str(lot.get("status") or "") == "CLOSED" or int(lot.get("remaining_quantity") or 0) <= 0]
        open_lots = [lot for lot in lots if str(lot.get("status") or "") != "CLOSED" and int(lot.get("remaining_quantity") or 0) > 0]
        dates = {str(fill.get("filled_at") or "")[:10] for fill in fills if str(fill.get("filled_at") or "")}
        price_codes = {str(row.get("code") or "") for row in price_snapshots if row.get("code")}
        configured_codes = {stock.code for stock in self.config.stocks if stock.enabled and not stock.manual_only}
        latest_market_at = max((str(row.get("collected_at") or row.get("sampled_at") or "") for row in price_snapshots + daily_prices + liquidity_snapshots), default="")
        latest_daily_date = max((str(row.get("date") or "") for row in daily_prices), default="")
        daily_codes = {str(row.get("code") or "") for row in daily_prices if row.get("code")}
        daily_dates = {str(row.get("date") or "") for row in daily_prices if row.get("date")}
        latest_run = max(collection_runs, key=lambda row: str(row.get("ended_at") or row.get("started_at") or ""), default={})
        current_hash = config_hash(self.config)
        run_id = self.config.experiment.run_id or self.config.run_id or f"{self.config.risk.profile}_{current_hash}"
        experiment_name = self.config.experiment.experiment_name or self.config.experiment_name or self.config.risk.profile
        readiness_level = 0
        what_if_level = "Level 0: trade results only"
        if price_snapshots:
            readiness_level = 1
            what_if_level = "Level 1: decision-time price context"
        if daily_prices:
            readiness_level = 2
            what_if_level = "Level 2: daily follow-up possible"
        if price_snapshots and daily_prices:
            readiness_level = 2
            what_if_level = "config_comparison_and_limited_what_if"
        return {
            "fill_count": len(fills),
            "closed_lot_count": len(closed_lots),
            "open_lot_count": len(open_lots),
            "stale_lot_count": sum(1 for lot in open_lots if lot.get("cleanup_candidate")),
            "review_required_count": sum(1 for position in positions if position.get("needs_review") or position.get("position_state") == PositionLifecycle.REVIEW_REQUIRED.value),
            "trading_day_count": len(dates),
            "config_snapshot_count": len(config_snapshots),
            "decision_record_count": len(decisions),
            "current_config_hash": current_hash,
            "current_run_id": run_id,
            "current_experiment_name": experiment_name,
            "price_snapshots_count": len(price_snapshots),
            "daily_prices_count": len(daily_prices),
            "liquidity_snapshots_count": len(liquidity_snapshots),
            "symbols_with_price_data_count": len(price_codes),
            "symbols_with_daily_prices_count": len(daily_codes),
            "market_data_missing_symbols_count": len(configured_codes - price_codes),
            "latest_market_data_collected_at": latest_market_at,
            "latest_daily_price_date": latest_daily_date,
            "days_with_market_data_count": len(daily_dates),
            "market_data_collection_run_count": len(collection_runs),
            "latest_market_data_collection_run_id": latest_run.get("run_id", ""),
            "latest_market_data_collection_mode": latest_run.get("mode", ""),
            "latest_market_data_collection_errors": latest_run.get("error_count", 0),
            "tuning_readiness_level": readiness_level,
            "what_if_analysis_level": what_if_level,
            "analysis_export_ready": bool(fills or decisions),
        }

    def collect_market_data(self, *, execute: bool = False, symbols_from_config: bool = True, snapshot: bool = True, daily: bool = True) -> dict[str, Any]:
        from scripts.collect_market_data import collect_market_data

        return collect_market_data(
            self.config_path,
            symbols_from_config=symbols_from_config,
            snapshot=snapshot,
            daily=daily,
            execute=execute,
        )

    def new_season_status(self) -> dict[str, Any]:
        positions = self.positions()
        lots = self.lots()
        orders = self.orders()
        manual_requests = self.manual_order_requests()
        open_lots = [lot for lot in lots if int(lot.get("remaining_quantity") or 0) > 0 and lot.get("status") != "CLOSED"]
        pending_orders = [order for order in orders if str(order.get("status") or "") in ORDER_PENDING_STATUSES]
        pending_manual = [request for request in manual_requests if str(request.get("status") or "") in MANUAL_PENDING_STATUSES]
        sync_required = [position for position in positions if position.get("sync_status") == PositionLifecycle.SYNC_REQUIRED.value or position.get("position_state") == PositionLifecycle.SYNC_REQUIRED.value]
        lot_mismatch = [position for position in positions if position.get("lot_quantity_mismatch")]
        db_hash = _stable_hash(
            [
                {
                    "lot_id": lot.get("lot_id"),
                    "code": lot.get("code"),
                    "remaining_quantity": lot.get("remaining_quantity"),
                    "buy_price": lot.get("buy_price"),
                    "status": lot.get("status"),
                    "buy_filled_at": lot.get("buy_filled_at"),
                }
                for lot in sorted(open_lots, key=lambda item: (str(item.get("code")), str(item.get("lot_id"))))
            ]
        )
        latest_plan = self._latest_liquidation_plan()
        plan = latest_plan.get("plan") or {}
        db_matches = bool(plan) and plan.get("db_open_lot_hash") == db_hash
        plan_expired = False
        if plan.get("expires_at"):
            try:
                plan_expired = datetime.now() > datetime.fromisoformat(str(plan["expires_at"]))
            except ValueError:
                plan_expired = True
        block_reason = ""
        if not plan:
            block_reason = "liquidation_plan_missing"
        elif plan.get("status") != "ACTIVE":
            block_reason = "liquidation_plan_not_active"
        elif not db_matches:
            block_reason = "liquidation_plan_db_changed"
        elif plan_expired:
            block_reason = "liquidation_plan_snapshot_expired"
        elif plan.get("request_creation_allowed") is False and plan.get("request_creation_block_reason"):
            block_reason = str(plan.get("request_creation_block_reason"))
        elif pending_orders or pending_manual:
            block_reason = "liquidation_plan_pending_work_created"
        elif sync_required:
            block_reason = "liquidation_plan_sync_required"
        elif lot_mismatch:
            block_reason = "liquidation_plan_lot_mismatch"
        reset_reasons = []
        if open_lots:
            reset_reasons.append("reset_open_lot_exists")
        if pending_orders:
            reset_reasons.append("reset_pending_order_exists")
        if pending_manual:
            reset_reasons.append("reset_pending_manual_request_exists")
        if sync_required:
            reset_reasons.append("reset_sync_required")
        if lot_mismatch:
            reset_reasons.append("liquidation_plan_lot_mismatch")
        ready = not reset_reasons and self.config.risk.profile == "expansion_100_safe" and len(self.config.stocks) >= 100
        user_message = self._new_season_user_message(block_reason, reset_reasons)
        plan_status = str(plan.get("status") or "")
        return {
            "open_lot_count": len(open_lots),
            "pending_order_count": len(pending_orders),
            "pending_manual_request_count": len(pending_manual),
            "sync_required_count": len(sync_required),
            "lot_mismatch_count": len(lot_mismatch),
            "risk_profile": self.config.risk.profile,
            "db_open_lot_hash": db_hash,
            "current_plan_exists": bool(plan),
            "plan_path": latest_plan.get("path", ""),
            "plan_id": plan.get("plan_id", ""),
            "plan_created_at": plan.get("created_at", ""),
            "plan_db_snapshot_at": plan.get("db_snapshot_at", ""),
            "plan_kis_balance_snapshot_at": plan.get("kis_balance_snapshot_at", ""),
            "plan_status": plan.get("status", ""),
            "plan_expires_at": plan.get("expires_at", ""),
            "plan_expired": plan_expired,
            "plan_db_matches_current": db_matches,
            "plan_kis_snapshot_hash": plan.get("kis_snapshot_hash", ""),
            "snapshot_warnings": plan.get("snapshot_warnings", []),
            "snapshot_errors": plan.get("snapshot_errors", []),
            "snapshot_generated_at": plan.get("snapshot_generated_at", ""),
            "snapshot_age_minutes": plan.get("snapshot_age_minutes"),
            "snapshot_validation_mode": plan.get("snapshot_validation_mode", ""),
            "request_creation_allowed": plan.get("request_creation_allowed", False),
            "request_creation_block_reason": plan.get("request_creation_block_reason", ""),
            "request_creation_block_reason_ko": _reason_guide(str(plan.get("request_creation_block_reason") or ""))["title"] if plan.get("request_creation_block_reason") else "",
            "request_creation_possible": block_reason == "",
            "block_reason": block_reason,
            "block_reason_ko": _reason_guide(block_reason)["title"] if block_reason else "",
            "block_reason_description_ko": _reason_guide(block_reason)["description"] if block_reason else "",
            "next_action_ko": _reason_guide(block_reason)["next_action"] if block_reason else "전량매도 요청을 생성할 수 있습니다.",
            "plan_status_description_ko": PLAN_STATUS_GUIDE.get(plan_status, plan_status),
            "reset_possible": not reset_reasons,
            "reset_block_reasons": reset_reasons,
            "reset_block_guides": [_reason_guide(reason) for reason in reset_reasons],
            "new_season_ready": ready,
            "new_season_ready_message": "새 시즌 시작 준비 완료" if ready else "새 시즌 시작 준비가 아직 완료되지 않았습니다.",
            "wizard_steps": self._new_season_wizard_steps(block_reason, reset_reasons, plan_status, bool(plan), plan_expired, db_matches, ready),
            "guidance": user_message,
        }

    def new_season_archive(self, execute: bool = False) -> dict[str, Any]:
        module = _prepare_new_season_module()
        result = module.archive_current_state(self.config_path, Path("archive"), dry_run=not execute)
        self._append_audit_log("new_season_archive_requested", {"execute": execute, "result": result})
        return {"executed": execute, "order_api_called": False, "db_reset_executed": False, "result": result}

    def new_season_create_plan(self, kis_balance_json_path: str = "", execute: bool = False, max_age_minutes: int = 60) -> dict[str, Any]:
        module = _prepare_new_season_module()
        balance_path = Path(kis_balance_json_path) if kis_balance_json_path else None
        try:
            balances = module.load_kis_balance_json(balance_path) if balance_path else None
        except (OSError, json.JSONDecodeError, ValueError) as error:
            return {"created": False, "reason": "liquidation_kis_balance_fetch_failed", "message": str(error), "order_api_called": False}
        result = module.liquidation_plan(
            self.config_path,
            Path("exports"),
            dry_run=not execute,
            kis_balances=balances,
            kis_balance_path=balance_path,
            max_age_minutes=max_age_minutes,
        )
        self._append_audit_log("new_season_liquidation_plan_requested", {"execute": execute, "kis_balance_json_path": str(balance_path or ""), "result": {key: result.get(key) for key in ("plan_path", "status", "item_count", "source")}})
        return {"created": execute, "order_api_called": False, "db_reset_executed": False, "result": result}

    def new_season_validate_snapshot(self, kis_balance_json_path: str = "", max_age_minutes: int = 60) -> dict[str, Any]:
        module = _prepare_new_season_module()
        if not kis_balance_json_path:
            return _snapshot_validation_response("liquidation_kis_balance_fetch_required")
        path = Path(kis_balance_json_path)
        try:
            preview = module.validate_kis_balance_snapshot(path, mode="preview", max_age_minutes=max_age_minutes)
            strict = module.validate_kis_balance_snapshot(path, mode="create_request", max_age_minutes=max_age_minutes)
        except Exception as error:  # noqa: BLE001
            response = _snapshot_validation_response("liquidation_kis_balance_fetch_failed")
            response["message"] = f"{type(error).__name__}: {error}"
            return response
        db_snapshot = module.db_open_lot_snapshot(Path(self.config.storage_path))
        db_qty: dict[str, int] = {}
        for row in db_snapshot.get("rows", []):
            code = str(row.get("code", "")).zfill(6)
            db_qty[code] = db_qty.get(code, 0) + int(row.get("remaining_quantity", 0) or 0)
        balances = preview.get("balances", {})
        matched = 0
        mismatched: list[dict[str, Any]] = []
        missing: list[str] = []
        for code, quantity in sorted(db_qty.items()):
            kis_quantity = int((balances.get(code) or {}).get("holding_quantity", 0))
            if code not in balances:
                missing.append(code)
            elif kis_quantity == quantity:
                matched += 1
            else:
                mismatched.append({"code": code, "db_open_lot_quantity": quantity, "kis_holding_quantity": kis_quantity})
        extra = sorted(code for code, item in balances.items() if code not in db_qty and int(item.get("holding_quantity", 0) or 0) > 0)
        errors = list(strict.get("errors", []))
        if missing or extra or mismatched:
            errors.append("liquidation_kis_balance_mismatch")
        block_reason = errors[0] if errors else ""
        missing_fields = []
        if "liquidation_kis_balance_snapshot_missing_generated_at" in errors:
            missing_fields.append("generated_at")
        if "liquidation_kis_sellable_quantity_missing" in errors:
            missing_fields.append("sellable_quantity")
        return {
            "snapshot_valid_for_preview": bool(preview.get("valid")),
            "snapshot_valid_for_request": bool(strict.get("valid")) and not (missing or extra or mismatched),
            "snapshot_warnings": list(preview.get("warnings", [])),
            "snapshot_errors": errors,
            "snapshot_generated_at": preview.get("generated_at", ""),
            "snapshot_age_minutes": preview.get("age_minutes"),
            "missing_required_fields": missing_fields,
            "matched_positions_count": matched,
            "mismatched_positions_count": len(mismatched),
            "mismatched_positions": mismatched,
            "missing_in_snapshot_codes": missing,
            "extra_in_snapshot_codes": extra,
            "request_creation_allowed": not block_reason,
            "request_creation_block_reason": block_reason,
            "guide": _reason_guide(block_reason) if block_reason else {"title": "전량매도 요청 생성 가능", "description": "snapshot이 request 생성 조건을 만족합니다.", "next_action": "전량매도 예정표를 생성하거나 요청 생성 단계로 진행하세요."},
        }

    def new_season_generate_kis_balance_snapshot(self, output_dir: str = "exports", max_age_minutes: int = 60) -> dict[str, Any]:
        try:
            client = KisClient(self.config.kis_account, enable_execution_raw_log=False)
            rows = list(client.balance_snapshot_rows())
            generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
            output_path = Path(output_dir or "exports")
            output_path.mkdir(parents=True, exist_ok=True)
            path = output_path / f"kis_balance_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            account_number = getattr(client, "account_number", "")
            masked = f"****{account_number[-4:]}" if account_number else ""
            payload = {
                "generated_at": generated_at,
                "source": "local_ui_kis_balance_snapshot",
                "account_id_masked": masked,
                "positions": rows,
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            validation = self.new_season_validate_snapshot(str(path), max_age_minutes=max_age_minutes)
            result = {
                "created": True,
                "path": str(path),
                "position_count": len(rows),
                "generated_at": generated_at,
                "validation": validation,
                "order_api_called": False,
                "kis_order_api_called": False,
            }
        except Exception as error:
            result = {
                "created": False,
                "reason": "liquidation_kis_balance_fetch_failed",
                "message": str(error),
                "order_api_called": False,
                "kis_order_api_called": False,
            }
        self._append_audit_log("new_season_kis_balance_snapshot_generated", {key: result.get(key) for key in ("created", "path", "position_count", "reason", "message")})
        return result

    def new_season_create_liquidation_requests(self, plan_path: str = "", kis_balance_json_path: str = "", confirm: str = "", execute: bool = False) -> dict[str, Any]:
        module = _prepare_new_season_module()
        result = module.create_liquidation_manual_requests(
            self.config_path,
            confirm,
            dry_run=not execute,
            kis_balance_path=Path(kis_balance_json_path) if kis_balance_json_path else None,
            plan_path=Path(plan_path) if plan_path else None,
        )
        self._append_audit_log("new_season_liquidation_requests_requested", {"execute": execute, "plan_path": plan_path, "kis_balance_json_path": kis_balance_json_path, "result": result})
        return {"executed": execute, "order_api_called": False, "db_reset_executed": False, "result": result}

    def new_season_reset_db(self, confirm: str = "", execute: bool = False) -> dict[str, Any]:
        module = _prepare_new_season_module()
        result = module.reset_db(self.config_path, confirm, dry_run=not execute)
        self._append_audit_log("new_season_reset_requested", {"execute": execute, "result": result})
        return {"executed": execute and bool(result.get("reset")), "order_api_called": False, "result": result}

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
            "max_new_buy_amount_per_day": config.risk.max_new_buy_amount_per_day,
            "max_total_initial_buy_amount_per_day": config.risk.max_total_initial_buy_amount_per_day,
            "new_buy_amount_today": sum(int(order.get("quantity") or 0) * int(order.get("limit_price") or 0) for order in orders if order.get("side") == "BUY" and order.get("reason") == "initial_buy" and str(order.get("requested_at", "")).startswith(today)),
            "risk_profile": config.risk.profile,
            "candidate_stock_count": len(config.stocks),
            "enabled_stock_count": sum(1 for stock in config.stocks if stock.enabled),
            "today_fill_count": sum(1 for fill in fills if str(fill.get("filled_at", "")).startswith(today)),
        }

    def portfolio_dashboard(self) -> dict[str, Any]:
        """Read-only portfolio and risk usage dashboard built from local DB rows."""
        config = self.config
        positions = self.positions()
        lots = self.lots()
        orders = self.orders()
        fills = self.fills()
        price_snapshots = self._table("price_snapshots")
        positions_by_code = {str(row.get("code") or ""): row for row in positions}
        latest_prices = self._latest_prices(price_snapshots, positions_by_code)
        open_lots = [lot for lot in lots if _to_int(lot.get("remaining_quantity")) > 0 and str(lot.get("status") or "") != "CLOSED"]
        buy_fills = [fill for fill in fills if str(fill.get("side") or "") == OrderSide.BUY.value]
        sell_fills = [fill for fill in fills if str(fill.get("side") or "") == OrderSide.SELL.value]
        lot_by_id = {str(lot.get("lot_id") or ""): lot for lot in lots}
        buy_amount = sum(_to_int(fill.get("quantity")) * _to_int(fill.get("price")) for fill in buy_fills)
        buy_lot_count = len(_unique_lot_keys(buy_fills))
        holding_buy_amount = sum(_to_int(lot.get("remaining_quantity")) * _to_int(lot.get("buy_price")) for lot in open_lots)
        holding_market_value = sum(_to_int(lot.get("remaining_quantity")) * _current_price_for_lot(lot, latest_prices) for lot in open_lots)
        realized = self._realized_pnl_from_sell_fills(sell_fills, lot_by_id)
        if realized["sold_cost"] <= 0:
            realized["realized_pnl"] = sum(_to_int(lot.get("realized_profit_loss")) for lot in lots)
            realized["sold_cost"] = sum(max(0, _to_int(lot.get("buy_quantity")) - _to_int(lot.get("remaining_quantity"))) * _to_int(lot.get("buy_price")) for lot in lots)
        unrealized_pnl = sum((_current_price_for_lot(lot, latest_prices) - _to_int(lot.get("buy_price"))) * _to_int(lot.get("remaining_quantity")) for lot in open_lots)
        today = datetime.now().date().isoformat()
        active_codes = self._active_symbol_codes(positions, open_lots, orders)
        overall = {
            "total_buy_amount": buy_amount,
            "total_buy_lot_count": buy_lot_count,
            "current_holding_buy_amount": holding_buy_amount,
            "current_holding_market_value": holding_market_value,
            "current_holding_lot_count": len(open_lots),
            "holding_symbol_count": len({str(lot.get("code") or "") for lot in open_lots}),
            "realized_pnl": realized["realized_pnl"],
            "realized_pnl_rate": _safe_rate(realized["realized_pnl"], realized["sold_cost"]),
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_rate": _safe_rate(unrealized_pnl, holding_buy_amount),
            "today_buy_fill_count": sum(1 for fill in buy_fills if _date_key(fill.get("filled_at")) == today),
            "today_sell_fill_count": sum(1 for fill in sell_fills if _date_key(fill.get("filled_at")) == today),
            "today_realized_pnl": self._daily_realized(sell_fills, lot_by_id).get(today, {}).get("realized_pnl", 0),
            "fee_tax_basis": "estimated_fee_tax_pct",
            "fee_tax_rate_pct": config.strategy.estimated_fee_tax_pct,
        }
        limit_usage = self._limit_usage(config, positions, open_lots, orders, active_codes, holding_buy_amount, today)
        daily_summary = self._daily_summary(buy_fills, sell_fills, open_lots, lot_by_id, latest_prices)
        symbol_rows = self._symbol_exposures(positions, open_lots, latest_prices)
        risk_counts = self._risk_status_counts(positions, open_lots)
        latest_price_at = max((str(item.get("timestamp") or "") for item in latest_prices.values()), default="")
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "pre_after_night_status": {
                "status": "on_hold",
                "message": "Pre/After/Night expansion is on hold; this dashboard uses the existing REGULAR-session DB data only.",
            },
            "overall_summary": overall,
            "limit_usage": limit_usage,
            "daily_summary": daily_summary,
            "top_symbol_exposures": symbol_rows[:10],
            "top_symbol_lot_counts": sorted(symbol_rows, key=lambda row: row["open_lot_count"], reverse=True)[:10],
            "risk_status_counts": risk_counts,
            "data_quality": {
                "latest_price_source_at": latest_price_at,
                "price_snapshot_count": len(price_snapshots),
                "fee_tax_estimated": True,
                "notes": self._portfolio_data_quality_notes(price_snapshots, latest_prices, open_lots),
            },
            "definitions": {
                "total_buy_amount": "Sum of BUY fill quantity * price.",
                "total_buy_lot_count": "Unique LOT ids created by BUY fills; falls back to fill/order key if lot_id is missing.",
                "current_holding_buy_amount": "OPEN LOT remaining_quantity * buy_price.",
                "realized_pnl": "SELL fill PnL minus estimated fee/tax when sell fill and lot cost are available; otherwise stored lot realized_profit_loss.",
                "unrealized_pnl": "OPEN LOT current saved price minus buy price, multiplied by remaining quantity.",
                "daily_unrealized_pnl": "Current-basis unrealized PnL for open LOTs bought on that date, not a historical close snapshot.",
            },
        }

    def portfolio_realized_detail(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = filters or {}
        lots = self.lots()
        fills = self.fills()
        lot_by_id = {str(lot.get("lot_id") or ""): lot for lot in lots}
        rows = []
        for fill in fills:
            if str(fill.get("side") or "") != OrderSide.SELL.value:
                continue
            lot = lot_by_id.get(str(fill.get("lot_id") or ""))
            if not lot:
                continue
            quantity = _to_int(fill.get("quantity"))
            buy_price = _to_int(lot.get("buy_price"))
            sell_price = _to_int(fill.get("price"))
            buy_amount = buy_price * quantity
            sell_amount = sell_price * quantity
            fee_tax = int(round(sell_amount * self.config.strategy.estimated_fee_tax_pct / 100.0))
            realized_pnl = sell_amount - buy_amount - fee_tax
            row = {
                "date": _date_key(fill.get("filled_at")),
                "code": fill.get("code") or lot.get("code"),
                "name": fill.get("name") or "",
                "lot_id": fill.get("lot_id") or "",
                "buy_filled_at": lot.get("buy_filled_at", ""),
                "buy_quantity": lot.get("buy_quantity", 0),
                "buy_price": buy_price,
                "buy_amount": buy_amount,
                "sell_filled_at": fill.get("filled_at", ""),
                "sell_quantity": quantity,
                "sell_price": sell_price,
                "sell_amount": sell_amount,
                "gross_realized_pnl": sell_amount - buy_amount,
                "fee_tax_estimate": fee_tax,
                "realized_pnl": realized_pnl,
                "realized_pnl_rate": _safe_rate(realized_pnl, buy_amount),
                "pnl_basis": "sell_fill_net_estimate",
                "sell_reason": fill.get("sell_reason") or lot.get("last_sell_reason") or "",
                "market_session": fill.get("market_session") or lot.get("buy_market_session") or "REGULAR",
                "holding_days": _holding_days(lot.get("buy_filled_at"), fill.get("filled_at")),
                "config_hash": fill.get("config_hash", ""),
                "run_id": fill.get("run_id", ""),
                "experiment_name": fill.get("experiment_name", ""),
                "execution_id": fill.get("execution_id", ""),
                "order_id": fill.get("order_id", ""),
            }
            rows.append(row)
        rows = self._filter_detail_rows(rows, filters)
        total = len(rows)
        rows = self._sort_and_page(rows, filters, default_sort="-realized_pnl")
        return {
            "rows": rows,
            "total_count": total,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "calculation_basis": "SELL fill 기준. 부분매도는 LOT 전체가 아니라 각 SELL fill 수량 기준으로 계산합니다.",
            "data_quality_notes": [
                f"수수료/세금은 strategy.estimated_fee_tax_pct={self.config.strategy.estimated_fee_tax_pct}% 기준 추정치입니다.",
                "SELL fill에 lot_id가 없거나 LOT row가 없으면 정확한 원가 계산이 어려워 상세에서 제외됩니다.",
            ],
            "price_source_info": {"required": False, "source": "sell_fill_and_lot_cost"},
            "read_only": True,
            "order_api_called": False,
            "db_reset_executed": False,
        }

    def portfolio_unrealized_detail(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        filters = filters or {}
        positions = self.positions()
        lots = self.lots()
        price_snapshots = self._table("price_snapshots")
        positions_by_code = {str(row.get("code") or ""): row for row in positions}
        latest_prices = self._latest_prices(price_snapshots, positions_by_code)
        open_lots = [lot for lot in lots if _to_int(lot.get("remaining_quantity")) > 0 and str(lot.get("status") or "") != "CLOSED"]
        lot_states = {str(lot.get("lot_id") or ""): _lot_state_from_row(lot) for lot in open_lots}
        lot_manager = LotManager(self.config.strategy, lot_states)
        rows = []
        for lot in open_lots:
            code = str(lot.get("code") or "")
            position = positions_by_code.get(code, {})
            current_price = _current_price_for_lot(lot, latest_prices)
            price_info = latest_prices.get(code, {})
            quantity = _to_int(lot.get("remaining_quantity"))
            buy_price = _to_int(lot.get("buy_price"))
            buy_amount = quantity * buy_price
            market_value = quantity * current_price if current_price else 0
            unrealized_pnl = market_value - buy_amount if current_price else 0
            state = lot_states.get(str(lot.get("lot_id") or ""))
            current_base_rate, target_band, target_source = lot_manager.current_target_profit_info(code)
            effective_rate = float(lot.get("effective_target_profit_rate") or 0)
            if state is not None:
                lot_manager.update_lot_target_metadata(state, current_price, current_base_target_profit_rate=current_base_rate)
                effective_rate = state.effective_target_profit_rate
            target_price = round_price(buy_price * (1.0 + effective_rate)) if buy_price else 0
            target_amount = target_price * quantity
            row = {
                "date": _date_key(lot.get("buy_filled_at")),
                "code": code,
                "name": position.get("name", ""),
                "lot_id": lot.get("lot_id", ""),
                "buy_filled_at": lot.get("buy_filled_at", ""),
                "buy_quantity": lot.get("buy_quantity", 0),
                "remaining_quantity": quantity,
                "buy_price": buy_price,
                "remaining_buy_amount": buy_amount,
                "current_price": current_price,
                "current_market_value": market_value,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_rate": _safe_rate(unrealized_pnl, buy_amount),
                "target_price": target_price,
                "target_amount": target_amount,
                "target_profit_lot_band": target_band,
                "target_source": target_source,
                "current_base_target_profit_rate": current_base_rate,
                "effective_target_profit_rate": effective_rate,
                "target_remaining_amount": max(0, target_amount - market_value) if current_price else 0,
                "target_remaining_rate": _safe_rate(target_price - current_price, current_price) if current_price else 0.0,
                "stale_lot": bool(lot.get("stale_lot")),
                "cleanup_candidate": bool(lot.get("cleanup_candidate")),
                "position_state": position.get("position_state", ""),
                "market_session": lot.get("buy_market_session") or "REGULAR",
                "price_snapshot_at": price_info.get("timestamp", ""),
                "current_price_source": price_info.get("source", ""),
                "data_quality_note": "현재 기준 참고값입니다. target 가격/금액은 실제 주문 예정가가 아니라 현재 로직 기준 목표값입니다.",
                "run_id": lot.get("run_id", ""),
                "experiment_name": lot.get("experiment_name", ""),
                "config_hash": lot.get("config_hash", ""),
            }
            rows.append(row)
        rows = self._filter_detail_rows(rows, filters)
        total = len(rows)
        rows = self._sort_and_page(rows, filters, default_sort="unrealized_pnl")
        return {
            "rows": rows,
            "total_count": total,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "calculation_basis": "OPEN LOT 현재 기준 평가손익. 날짜별 조회는 해당 날짜에 매수되어 현재 OPEN 상태인 LOT만 보여줍니다.",
            "data_quality_notes": self._portfolio_data_quality_notes(price_snapshots, latest_prices, open_lots) + [
                "target_price/target_amount는 실제 주문 예정가가 아니라 target_profit_lot_bands와 age_decay가 반영된 참고 목표값입니다.",
            ],
            "price_source_info": {code: latest_prices.get(code, {}) for code in sorted({str(lot.get("code") or "") for lot in open_lots})},
            "read_only": True,
            "order_api_called": False,
            "db_reset_executed": False,
        }

    def _filter_detail_rows(self, rows: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
        code = str(filters.get("code") or "").zfill(6) if filters.get("code") else ""
        lot_id = str(filters.get("lot_id") or "")
        date = str(filters.get("date") or "")
        date_from = str(filters.get("date_from") or "")
        date_to = str(filters.get("date_to") or "")
        query = str(filters.get("q") or "").lower()
        result = []
        for row in rows:
            row_date = str(row.get("date") or "")
            if code and str(row.get("code") or "").zfill(6) != code:
                continue
            if lot_id and str(row.get("lot_id") or "") != lot_id:
                continue
            if date and row_date != date:
                continue
            if date_from and row_date < date_from:
                continue
            if date_to and row_date > date_to:
                continue
            if query and query not in json.dumps(row, ensure_ascii=False).lower():
                continue
            result.append(row)
        return result

    def _sort_and_page(self, rows: list[dict[str, Any]], filters: dict[str, Any], *, default_sort: str) -> list[dict[str, Any]]:
        sort = str(filters.get("sort") or default_sort)
        reverse = sort.startswith("-")
        key = sort[1:] if reverse else sort
        rows = sorted(rows, key=lambda row: _sort_value(row.get(key)), reverse=reverse)
        limit = min(max(_to_int(filters.get("limit")) or 100, 1), 1000)
        offset = max(_to_int(filters.get("offset")), 0)
        return rows[offset:offset + limit]

    def _latest_prices(self, price_snapshots: list[dict[str, Any]], positions_by_code: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for code, position in positions_by_code.items():
            price = _to_int(position.get("current_price"))
            if price > 0:
                latest[code] = {"price": price, "source": "positions.current_price", "timestamp": str(position.get("last_update_time") or "")}
        for snapshot in price_snapshots:
            code = str(snapshot.get("code") or "")
            price = _to_int(snapshot.get("current_price"))
            timestamp = str(snapshot.get("sampled_at") or snapshot.get("collected_at") or "")
            if not code or price <= 0:
                continue
            current = latest.get(code, {})
            if not current.get("timestamp") or timestamp >= str(current.get("timestamp") or ""):
                latest[code] = {"price": price, "source": "price_snapshots.current_price", "timestamp": timestamp}
        return latest

    def _realized_pnl_from_sell_fills(self, sell_fills: list[dict[str, Any]], lot_by_id: dict[str, dict[str, Any]]) -> dict[str, int]:
        realized_pnl = 0
        sold_cost = 0
        for fill in sell_fills:
            lot = lot_by_id.get(str(fill.get("lot_id") or ""))
            if not lot:
                continue
            quantity = _to_int(fill.get("quantity"))
            sell_price = _to_int(fill.get("price"))
            buy_price = _to_int(lot.get("buy_price"))
            fee_tax = int(round(sell_price * quantity * self.config.strategy.estimated_fee_tax_pct / 100.0))
            realized_pnl += (sell_price - buy_price) * quantity - fee_tax
            sold_cost += buy_price * quantity
        return {"realized_pnl": realized_pnl, "sold_cost": sold_cost}

    def _daily_realized(self, sell_fills: list[dict[str, Any]], lot_by_id: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
        daily: dict[str, dict[str, int]] = defaultdict(lambda: {"realized_pnl": 0, "sold_cost": 0})
        for fill in sell_fills:
            day = _date_key(fill.get("filled_at"))
            lot = lot_by_id.get(str(fill.get("lot_id") or ""))
            if not day or not lot:
                continue
            quantity = _to_int(fill.get("quantity"))
            sell_price = _to_int(fill.get("price"))
            buy_price = _to_int(lot.get("buy_price"))
            fee_tax = int(round(sell_price * quantity * self.config.strategy.estimated_fee_tax_pct / 100.0))
            daily[day]["realized_pnl"] += (sell_price - buy_price) * quantity - fee_tax
            daily[day]["sold_cost"] += buy_price * quantity
        return daily

    def _daily_summary(
        self,
        buy_fills: list[dict[str, Any]],
        sell_fills: list[dict[str, Any]],
        open_lots: list[dict[str, Any]],
        lot_by_id: dict[str, dict[str, Any]],
        latest_prices: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "date": "",
            "buy_amount": 0,
            "buy_lot_count": 0,
            "sell_amount": 0,
            "sell_lot_count": 0,
            "realized_pnl": 0,
            "realized_pnl_rate": 0.0,
            "unrealized_pnl": 0,
            "unrealized_pnl_rate": 0.0,
            "basis": "fills_by_filled_at_kst",
        })
        buy_lots_by_day: dict[str, set[str]] = defaultdict(set)
        sell_lots_by_day: dict[str, set[str]] = defaultdict(set)
        for fill in buy_fills:
            day = _date_key(fill.get("filled_at"))
            if not day:
                continue
            rows[day]["date"] = day
            rows[day]["buy_amount"] += _to_int(fill.get("quantity")) * _to_int(fill.get("price"))
            buy_lots_by_day[day].add(_lot_key(fill))
        for fill in sell_fills:
            day = _date_key(fill.get("filled_at"))
            if not day:
                continue
            rows[day]["date"] = day
            rows[day]["sell_amount"] += _to_int(fill.get("quantity")) * _to_int(fill.get("price"))
            sell_lots_by_day[day].add(_lot_key(fill))
        daily_realized = self._daily_realized(sell_fills, lot_by_id)
        for day, values in daily_realized.items():
            rows[day]["date"] = day
            rows[day]["realized_pnl"] = values["realized_pnl"]
            rows[day]["realized_pnl_rate"] = _safe_rate(values["realized_pnl"], values["sold_cost"])
        for lot in open_lots:
            day = _date_key(lot.get("buy_filled_at"))
            if not day:
                continue
            rows[day]["date"] = day
            cost = _to_int(lot.get("remaining_quantity")) * _to_int(lot.get("buy_price"))
            pnl = (_current_price_for_lot(lot, latest_prices) - _to_int(lot.get("buy_price"))) * _to_int(lot.get("remaining_quantity"))
            rows[day]["unrealized_pnl"] += pnl
            rows[day]["_unrealized_cost"] = _to_int(rows[day].get("_unrealized_cost")) + cost
        for day, row in rows.items():
            row["buy_lot_count"] = len(buy_lots_by_day[day])
            row["sell_lot_count"] = len(sell_lots_by_day[day])
            row["unrealized_pnl_rate"] = _safe_rate(_to_int(row.get("unrealized_pnl")), _to_int(row.get("_unrealized_cost")))
            row.pop("_unrealized_cost", None)
        return sorted(rows.values(), key=lambda row: str(row.get("date") or ""), reverse=True)

    def _limit_usage(
        self,
        config: BotConfig,
        positions: list[dict[str, Any]],
        open_lots: list[dict[str, Any]],
        orders: list[dict[str, Any]],
        active_codes: set[str],
        holding_buy_amount: int,
        today: str,
    ) -> list[dict[str, Any]]:
        today_initial_orders = [order for order in orders if order.get("side") == "BUY" and order.get("reason") == "initial_buy" and str(order.get("requested_at", "")).startswith(today)]
        today_initial_amount = sum(_to_int(order.get("quantity")) * _to_int(order.get("limit_price")) for order in today_initial_orders)
        return [
            _usage("max_total_invested_amount", holding_buy_amount, config.risk.max_total_invested_amount, "KRW", "OPEN LOT remaining cost / risk.max_total_invested_amount"),
            _usage("max_total_open_lots", len(open_lots), config.risk.max_total_open_lots, "lots", "OPEN LOT count / risk.max_total_open_lots"),
            _usage("max_active_symbols", len(active_codes), config.risk.max_active_symbols, "symbols", "Active holding/order/review symbols / risk.max_active_symbols"),
            _usage("max_new_buy_per_day", len(today_initial_orders), config.risk.max_new_buy_per_day, "orders", "Today initial BUY orders / risk.max_new_buy_per_day"),
            _usage("max_new_buy_amount_per_day", today_initial_amount, config.risk.max_new_buy_amount_per_day, "KRW", "Today initial BUY order amount / risk.max_new_buy_amount_per_day"),
            _usage("max_total_initial_buy_amount_per_day", today_initial_amount, config.risk.max_total_initial_buy_amount_per_day, "KRW", "Today initial BUY order amount / risk.max_total_initial_buy_amount_per_day"),
        ]

    def _symbol_exposures(self, positions: list[dict[str, Any]], open_lots: list[dict[str, Any]], latest_prices: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        by_code: dict[str, dict[str, Any]] = {}
        positions_by_code = {str(position.get("code") or ""): position for position in positions}
        for lot in open_lots:
            code = str(lot.get("code") or "")
            row = by_code.setdefault(code, {"code": code, "name": positions_by_code.get(code, {}).get("name", ""), "open_lot_count": 0, "holding_buy_amount": 0, "market_value": 0, "unrealized_pnl": 0})
            quantity = _to_int(lot.get("remaining_quantity"))
            buy_price = _to_int(lot.get("buy_price"))
            current_price = _current_price_for_lot(lot, latest_prices)
            row["open_lot_count"] += 1
            row["holding_buy_amount"] += quantity * buy_price
            row["market_value"] += quantity * current_price
            row["unrealized_pnl"] += (current_price - buy_price) * quantity
        for code, row in by_code.items():
            position = positions_by_code.get(code, {})
            row["unrealized_pnl_rate"] = _safe_rate(_to_int(row.get("unrealized_pnl")), _to_int(row.get("holding_buy_amount")))
            row["max_symbol_amount"] = _to_int(position.get("max_symbol_amount"))
            row["max_symbol_amount_usage_pct"] = _safe_percent(_to_int(row.get("holding_buy_amount")), _to_int(position.get("max_symbol_amount")))
            row["max_lots_per_symbol"] = _to_int(position.get("max_lots_per_symbol"))
            row["max_lots_per_symbol_usage_pct"] = _safe_percent(_to_int(row.get("open_lot_count")), _to_int(position.get("max_lots_per_symbol")))
            row["usage_level"] = _usage_level(max(row["max_symbol_amount_usage_pct"] or 0, row["max_lots_per_symbol_usage_pct"] or 0))
        return sorted(by_code.values(), key=lambda row: max(row["max_symbol_amount_usage_pct"] or 0, row["max_lots_per_symbol_usage_pct"] or 0), reverse=True)

    def _risk_status_counts(self, positions: list[dict[str, Any]], open_lots: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "review_required_count": sum(1 for position in positions if position.get("needs_review") or position.get("position_state") == PositionLifecycle.REVIEW_REQUIRED.value),
            "sync_required_count": sum(1 for position in positions if position.get("sync_status") == PositionLifecycle.SYNC_REQUIRED.value or position.get("position_state") == PositionLifecycle.SYNC_REQUIRED.value),
            "risk_blocked_count": sum(1 for position in positions if position.get("danger_state") or position.get("position_state") == PositionLifecycle.RISK_BLOCKED.value),
            "stale_lot_count": sum(1 for lot in open_lots if lot.get("stale_lot")),
            "cleanup_candidate_count": sum(1 for lot in open_lots if lot.get("cleanup_candidate")),
            "lot_quantity_mismatch_count": sum(1 for position in positions if position.get("lot_quantity_mismatch")),
        }

    def _active_symbol_codes(self, positions: list[dict[str, Any]], open_lots: list[dict[str, Any]], orders: list[dict[str, Any]]) -> set[str]:
        return {
            str(lot.get("code") or "") for lot in open_lots
        } | {str(order.get("code") or "") for order in orders if order.get("status") in {"REQUESTED", "PARTIAL"}} | {
            str(item.get("code") or "") for item in positions if item.get("position_state") in {
                PositionLifecycle.HOLDING.value,
                PositionLifecycle.WAIT_REENTRY.value,
                PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value,
                PositionLifecycle.REVIEW_REQUIRED.value,
                PositionLifecycle.RISK_BLOCKED.value,
                PositionLifecycle.SYNC_REQUIRED.value,
            }
        }

    def _portfolio_data_quality_notes(self, price_snapshots: list[dict[str, Any]], latest_prices: dict[str, dict[str, Any]], open_lots: list[dict[str, Any]]) -> list[str]:
        notes = [
            "평가손익은 DB에 저장된 positions.current_price 또는 최신 price_snapshots.current_price 기준입니다.",
            "일자별 평가손익은 현재 기준 보조값이며, 장마감 snapshot 기준 과거 평가손익이 아닙니다.",
            f"수수료/세금은 strategy.estimated_fee_tax_pct={self.config.strategy.estimated_fee_tax_pct}% 기준 추정치입니다.",
        ]
        if not price_snapshots:
            notes.append("price_snapshots가 없어 positions.current_price 중심으로 평가손익을 계산했습니다.")
        missing_price_codes = sorted({str(lot.get("code") or "") for lot in open_lots if _current_price_for_lot(lot, latest_prices) <= 0})
        if missing_price_codes:
            notes.append("현재가가 없어 평가손익이 0으로 처리된 종목: " + ", ".join(missing_price_codes[:10]))
        latest_ts = max((str(item.get("timestamp") or "") for item in latest_prices.values()), default="")
        if latest_ts:
            notes.append(f"최신 가격 기준 시각: {latest_ts}")
        return notes

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
            "review_status": self.review_status(code),
        }

    def review_status(self, code: str) -> dict[str, Any]:
        code = str(code).zfill(6)
        position = next((item for item in self.positions() if item.get("code") == code), {"code": code})
        triggers = self._review_triggers(position)
        return {
            "code": code,
            "position_state": position.get("position_state", ""),
            "needs_review": bool(position.get("needs_review")),
            "review_reason": position.get("review_reason", ""),
            "review_created_at": position.get("review_created_at", ""),
            "review_trigger_values": position.get("review_trigger_values", ""),
            "review_acknowledged_at": position.get("review_acknowledged_at", ""),
            "review_acknowledged_by": position.get("review_acknowledged_by", ""),
            "review_note": position.get("review_note", ""),
            "active_reasons": triggers["reasons"],
            "trigger_values": triggers["values"],
            "still_active": bool(triggers["reasons"]),
            "recommended_actions": [
                "추가매수는 중단하고 수익권 LOT의 PROFIT_TAKE SELL 가능 여부를 먼저 확인하세요.",
                "수동매도 후 KIS 잔고와 내부 LOT 수량이 맞는지 reconciliation 상태를 확인하세요.",
                "조건이 해소되었다면 상태 재평가를 실행하세요. 강제 해제는 기본 제공하지 않습니다.",
            ],
        }

    def review_required_list(self) -> dict[str, Any]:
        rows = []
        for position in self.positions():
            if position.get("position_state") != PositionLifecycle.REVIEW_REQUIRED.value and not position.get("needs_review"):
                continue
            status = self.review_status(str(position.get("code") or ""))
            lots = [lot for lot in self.lots() if lot.get("code") == position.get("code") and int(lot.get("remaining_quantity") or 0) > 0 and lot.get("status") != "CLOSED"]
            stale = [lot for lot in lots if lot.get("stale_lot")]
            profit_lots = [lot for lot in lots if float(lot.get("unrealized_pnl_rate") or 0) >= 0]
            rows.append(
                {
                    "code": position.get("code", ""),
                    "name": position.get("name", ""),
                    "position_state": position.get("position_state", ""),
                    "review_reason": position.get("review_reason", ""),
                    "review_created_at": position.get("review_created_at", ""),
                    "review_trigger_values": position.get("review_trigger_values", ""),
                    "current_pnl_rate": position.get("profit_loss_pct", 0),
                    "open_lot_count": len(lots),
                    "stale_lot_count": len(stale),
                    "db_quantity": position.get("quantity", 0),
                    "sync_status": position.get("sync_status", ""),
                    "lot_quantity_mismatch": bool(position.get("lot_quantity_mismatch")),
                    "can_recheck": True,
                    "profitable_lot_count": len(profit_lots),
                    "active_reasons": status["active_reasons"],
                    "trigger_values": status["trigger_values"],
                    "recommended_actions": status["recommended_actions"],
                    "release_requirements": self._review_release_requirements(status),
                }
            )
        self._append_audit_log("review_required_list_viewed", {"count": len(rows)})
        return {
            "items": rows,
            "count": len(rows),
            "force_clear_available": False,
            "guide": [
                "REVIEW_REQUIRED는 조건을 무시하고 강제 해제하지 않습니다.",
                "수동매도 후에는 체결 동기화/reconciliation 확인 뒤 상태 재평가를 실행하세요.",
                "acknowledge는 확인 기록만 남기며 BUY 차단을 해제하지 않습니다.",
            ],
        }

    def review_recheck(self, code: str) -> dict[str, Any]:
        code = str(code).zfill(6)
        from .storage import StateStore

        store = StateStore(self.config.storage_path)
        positions = store.load_positions()
        position = positions.get(code, PositionState(code=code))
        triggers = self._review_triggers(asdict(position))
        now = datetime.now().isoformat(timespec="seconds")
        if "sync_required" in triggers["reasons"]:
            position.sync_status = PositionLifecycle.SYNC_REQUIRED.value
            position.position_state = PositionLifecycle.SYNC_REQUIRED.value
            position.trading_paused = True
            position.auto_buy_enabled = False
            position.skip_reason = "sync_required"
            event = "review_required_still_active"
        elif triggers["reasons"]:
            position.needs_review = True
            position.auto_buy_enabled = False
            position.position_state = PositionLifecycle.REVIEW_REQUIRED.value
            position.review_reason = triggers["reasons"][0]
            position.review_created_at = position.review_created_at or now
            position.review_trigger_values = json.dumps(triggers["values"], ensure_ascii=False)
            event = "review_required_still_active"
        else:
            position.needs_review = False
            position.auto_buy_enabled = True
            position.review_reason = ""
            position.review_trigger_values = ""
            position.skip_reason = ""
            open_lots = [lot for lot in self.lots() if lot.get("code") == code and int(lot.get("remaining_quantity") or 0) > 0 and lot.get("status") != "CLOSED"]
            if open_lots:
                position.position_state = PositionLifecycle.HOLDING.value
            elif position.position_state == PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value and position.cleanup_reentry_cooldown_until:
                position.position_state = PositionLifecycle.COOLDOWN_AFTER_CLEANUP.value
            elif position.last_fill_side == OrderSide.SELL.value or any(lot.get("code") == code for lot in self.lots()):
                position.position_state = PositionLifecycle.WAIT_REENTRY.value
            else:
                position.position_state = PositionLifecycle.NEVER_BOUGHT.value
            event = "review_required_cleared"
        store.save_position(position)
        payload = {"code": code, "event": event, "active_reasons": triggers["reasons"], "trigger_values": triggers["values"]}
        self._append_audit_log("review_status_rechecked", payload)
        self._append_audit_log(event, payload)
        return {"rechecked": True, **payload, "position_state": position.position_state}

    def review_acknowledge(self, code: str, note: str = "", acknowledged_by: str = "local_ui") -> dict[str, Any]:
        code = str(code).zfill(6)
        from .storage import StateStore

        store = StateStore(self.config.storage_path)
        positions = store.load_positions()
        position = positions.get(code, PositionState(code=code))
        position.review_acknowledged_at = datetime.now().isoformat(timespec="seconds")
        position.review_acknowledged_by = acknowledged_by or "local_ui"
        position.review_note = note
        store.save_position(position)
        payload = {
            "code": code,
            "review_acknowledged_at": position.review_acknowledged_at,
            "review_acknowledged_by": position.review_acknowledged_by,
            "review_note": position.review_note,
            "position_state": position.position_state,
            "buy_block_still_active": position.position_state == PositionLifecycle.REVIEW_REQUIRED.value or position.needs_review,
        }
        self._append_audit_log("review_acknowledged", payload)
        return payload

    def _review_triggers(self, position: dict[str, Any]) -> dict[str, Any]:
        code = str(position.get("code") or "").zfill(6)
        open_lots = [lot for lot in self.lots() if lot.get("code") == code and int(lot.get("remaining_quantity") or 0) > 0 and lot.get("status") != "CLOSED"]
        exposure = sum(int(lot.get("remaining_quantity") or 0) * int(lot.get("buy_price") or 0) for lot in open_lots)
        open_lot_count = len(open_lots)
        stale_lot_ids = [
            str(lot.get("lot_id") or "")
            for lot in open_lots
            if bool(lot.get("stale_lot")) and float(lot.get("age_weeks") or 0) >= self.config.strategy.stale_lot_review_age_weeks
        ]
        reasons = []
        pnl_rate = float(position.get("profit_loss_pct") or 0.0) / 100.0
        if position.get("sync_status") == PositionLifecycle.SYNC_REQUIRED.value or position.get("lot_quantity_mismatch") or position.get("trading_paused"):
            reasons.append("sync_required")
        if self.config.strategy.lot_sizing_mode != "cycle_locked_by_entry_price" and exposure > self.config.strategy.auto_buy_limit:
            reasons.append("auto_buy_limit_exceeded")
        if pnl_rate <= self.config.strategy.review_symbol_loss_rate and exposure > 0:
            reasons.append("symbol_loss_review")
        max_lots = int(position.get("max_lots_per_symbol") or self.config.strategy.max_open_lots_before_review)
        if max_lots and open_lot_count > max_lots:
            reasons.append("too_many_open_lots")
        if stale_lot_ids:
            reasons.append("stale_lot_review_age")
        values = {
            "position_pnl_rate": pnl_rate,
            "review_symbol_loss_rate": self.config.strategy.review_symbol_loss_rate,
            "open_lot_count": open_lot_count,
            "max_lots": max_lots,
            "exposure": exposure,
            "auto_buy_limit": self.config.strategy.auto_buy_limit,
            "stale_lot_ids": stale_lot_ids,
            "stale_lot_review_age_weeks": self.config.strategy.stale_lot_review_age_weeks,
        }
        return {"reasons": reasons, "values": values}

    def _review_release_requirements(self, status: dict[str, Any]) -> list[str]:
        reasons = set(status.get("active_reasons") or [])
        values = status.get("trigger_values") or {}
        requirements = []
        if "symbol_loss_review" in reasons:
            requirements.append(f"현재 손익률이 기준({values.get('review_symbol_loss_rate')})보다 회복되어야 합니다.")
        if "too_many_open_lots" in reasons:
            requirements.append(f"OPEN LOT 수가 허용 기준({values.get('max_lots')}) 이하가 되어야 합니다.")
        if "stale_lot_review_age" in reasons:
            requirements.append("장기 STALE LOT 조건이 해소되거나 수동 정리 후 reconciliation이 완료되어야 합니다.")
        if "sync_required" in reasons:
            requirements.append("DB와 KIS 잔고 동기화 불일치를 먼저 해결해야 합니다.")
        if not requirements:
            requirements.append("현재 review trigger는 해소된 것으로 보입니다. 상태 재평가를 실행할 수 있습니다.")
        return requirements

    def _latest_liquidation_plan(self) -> dict[str, Any]:
        export_dir = Path("exports")
        candidates = sorted(export_dir.glob("liquidation_plan_*.json")) if export_dir.exists() else []
        for path in reversed(candidates):
            try:
                return {"path": str(path), "plan": json.loads(path.read_text(encoding="utf-8"))}
            except (OSError, json.JSONDecodeError):
                continue
        return {"path": "", "plan": {}}

    def _new_season_guidance(self, block_reason: str) -> str:
        messages = {
            "": "전량매도 예정표가 현재 보유 상태와 일치합니다.",
            "liquidation_plan_missing": "전량매도 예정표를 새로 생성해야 합니다.",
            "liquidation_plan_not_active": "전량매도 예정표가 ACTIVE 상태가 아닙니다. 새로 생성해주세요.",
            "liquidation_plan_db_changed": "전량매도 예정표 생성 후 보유 LOT이 변경되었습니다. 새로 생성해야 합니다.",
            "liquidation_plan_snapshot_expired": "KIS 잔고 확인 자료가 만료되었습니다. 다시 확인해주세요.",
            "liquidation_plan_pending_work_created": "예정표 생성 후 미체결 주문 또는 미처리 수동 요청이 생겼습니다.",
            "liquidation_plan_sync_required": "SYNC_REQUIRED 종목이 있어 전량매도 요청 생성이 차단됩니다.",
            "liquidation_plan_lot_mismatch": "LOT 수량 불일치가 있어 전량매도 요청 생성이 차단됩니다.",
            "liquidation_kis_balance_snapshot_missing_generated_at": "KIS 잔고 snapshot에 generated_at이 없어 실제 요청 생성이 차단됩니다.",
            "liquidation_kis_balance_snapshot_invalid_generated_at": "KIS 잔고 snapshot의 generated_at 형식을 읽을 수 없습니다.",
            "liquidation_kis_balance_snapshot_stale": "KIS 잔고 snapshot이 오래되어 최신 상태를 보장할 수 없습니다.",
            "liquidation_kis_sellable_quantity_missing": "KIS 잔고 snapshot에 실제 매도가능수량이 없어 요청 생성이 차단됩니다.",
        }
        return messages.get(block_reason, block_reason)

    def _new_season_user_message(self, block_reason: str, reset_reasons: list[str]) -> dict[str, Any]:
        if block_reason:
            guide = _reason_guide(block_reason)
            return {
                "status": "전량매도 요청 생성 불가",
                "reason": guide["title"],
                "description": guide["description"],
                "next_action": guide["next_action"],
            }
        if reset_reasons:
            first = _reason_guide(reset_reasons[0])
            return {
                "status": "DB 초기화 준비 미완료",
                "reason": first["title"],
                "description": first["description"],
                "next_action": first["next_action"],
            }
        return {
            "status": "새 시즌 시작 준비 완료",
            "reason": "",
            "description": "DB 초기화를 막는 보유/미체결/동기화 문제가 없습니다.",
            "next_action": "새 시즌 config와 봇 실행 설정을 최종 확인하세요.",
        }

    def _new_season_wizard_steps(
        self,
        block_reason: str,
        reset_reasons: list[str],
        plan_status: str,
        plan_exists: bool,
        plan_expired: bool,
        db_matches: bool,
        ready: bool,
    ) -> list[dict[str, Any]]:
        snapshot_ok = bool(plan_exists and plan_status == "ACTIVE" and not plan_expired and not block_reason.startswith("liquidation_kis"))
        plan_ok = bool(plan_exists and plan_status == "ACTIVE" and not plan_expired and db_matches)
        request_ok = block_reason == ""
        reset_ok = not reset_reasons
        config_ok = self.config.risk.profile == "expansion_100_safe" and len(self.config.stocks) >= 100
        return [
            {
                "step": 1,
                "title": "이전 시즌 백업",
                "status": "확인 필요",
                "description": "현재 DB/config/log를 archive에 백업합니다.",
                "button_label": "백업 생성",
                "button_enabled": True,
                "disabled_reason": "",
                "next_action": "UI의 백업 생성 버튼 또는 scripts/prepare_new_season.py --archive 명령으로 백업을 생성하세요.",
            },
            {
                "step": 2,
                "title": "실제 계좌 잔고 확인",
                "status": "검증 완료" if snapshot_ok else "snapshot 필요",
                "description": "DB 보유수량과 실제 KIS 잔고가 일치하는지 확인하기 위한 자료가 필요합니다.",
                "button_label": "잔고 snapshot 선택",
                "button_enabled": False,
                "disabled_reason": "" if snapshot_ok else "최신 KIS 잔고 snapshot을 준비해야 합니다.",
                "next_action": "주문이 아닌 잔고 조회 자료를 준비한 뒤 예정표를 생성하세요.",
            },
            {
                "step": 3,
                "title": "전량매도 예정표 생성",
                "status": "최신" if plan_ok else ("만료됨" if plan_expired else "새로 생성 필요"),
                "description": "현재 DB와 KIS 잔고를 기준으로 어떤 LOT을 전량매도해야 하는지 계산합니다.",
                "button_label": "전량매도 예정표 생성",
                "button_enabled": False,
                "disabled_reason": "UI에서는 안전상 직접 생성하지 않고 스크립트/CLI 절차를 사용합니다.",
                "next_action": "KIS 잔고 snapshot을 포함해 liquidation plan을 새로 생성하세요." if not plan_ok else "다음 단계로 진행할 수 있습니다.",
            },
            {
                "step": 4,
                "title": "전량매도 요청 생성",
                "status": "생성 가능" if request_ok else "차단됨",
                "description": "예정표 기준으로 봇에게 전량매도를 요청합니다. UI는 직접 주문하지 않고 manual_order_requests 큐만 사용합니다.",
                "button_label": "전량매도 요청 생성",
                "button_enabled": False,
                "disabled_reason": "" if request_ok else _reason_guide(block_reason)["title"],
                "next_action": "confirm text '전량매도 요청 확인'으로 request 생성 절차를 실행하세요." if request_ok else _reason_guide(block_reason)["next_action"],
            },
            {
                "step": 5,
                "title": "체결 및 동기화 확인",
                "status": "완료" if reset_ok else "확인 필요",
                "description": "전량매도 요청이 체결되고 DB와 KIS 잔고가 맞는지 확인합니다.",
                "button_label": "동기화 상태 새로고침",
                "button_enabled": True,
                "disabled_reason": "",
                "next_action": "미체결 주문/수동 요청/SYNC_REQUIRED가 사라졌는지 확인하세요.",
            },
            {
                "step": 6,
                "title": "DB 초기화",
                "status": "가능" if reset_ok else "차단됨",
                "description": "OPEN LOT 0개, 미체결 주문 0개, 미처리 요청 0개, sync mismatch 없음일 때만 가능합니다.",
                "button_label": "DB 초기화",
                "button_enabled": False,
                "disabled_reason": ", ".join(_reason_guide(reason)["title"] for reason in reset_reasons),
                "next_action": "RESET 확인 문구는 모든 차단 조건이 해소된 뒤에만 사용하세요." if reset_ok else _reason_guide(reset_reasons[0])["next_action"],
            },
            {
                "step": 7,
                "title": "새 100종목 config 적용 확인",
                "status": "적용됨" if config_ok else "확인 필요",
                "description": "expansion_100_safe profile과 KOSPI 100 후보군이 적용되었는지 확인합니다.",
                "button_label": "config 확인",
                "button_enabled": True,
                "disabled_reason": "",
                "next_action": "Config 탭에서 profile, stocks, risk limit을 확인하세요.",
            },
            {
                "step": 8,
                "title": "새 시즌 시작 준비 완료",
                "status": "준비 완료" if ready else "아직 불가",
                "description": "모든 차단 조건이 사라지면 봇을 새 시즌 설정으로 운용할 수 있습니다.",
                "button_label": "봇 실행 가이드 보기",
                "button_enabled": ready,
                "disabled_reason": "" if ready else "앞 단계의 차단 조건을 먼저 해결해야 합니다.",
                "next_action": "runtime/config를 최종 확인하고 봇을 시작하세요." if ready else "wizard에서 차단된 단계를 먼저 처리하세요.",
            },
        ]

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
        rows = self._table("orders")
        fill_totals: dict[str, dict[str, int]] = {}
        for fill in self.fills():
            order_id = str(fill.get("order_id") or "")
            if not order_id:
                continue
            item = fill_totals.setdefault(order_id, {"quantity": 0, "count": 0})
            item["quantity"] += _to_int(fill.get("quantity"))
            item["count"] += 1
        for row in rows:
            order_id = str(row.get("order_id") or "")
            totals = fill_totals.get(order_id, {"quantity": 0, "count": 0})
            filled_quantity = totals["quantity"]
            quantity = _to_int(row.get("quantity"))
            row["filled_quantity"] = filled_quantity
            row["fill_count"] = totals["count"]
            row["fill_exists"] = filled_quantity > 0
            row["remaining_quantity"] = max(0, quantity - filled_quantity)
            status = str(row.get("status") or "")
            row["post_cancel_execution_checked"] = bool(row.get("post_cancel_execution_checked_at"))
            if status in {"CANCELED", "CANCELED_NO_FILL"} and filled_quantity > 0:
                row["order_sync_warning"] = "canceled_status_with_fill"
            elif status in {"CANCELED", "CANCELED_NO_FILL"} and not row["post_cancel_execution_checked"]:
                row["order_sync_warning"] = "canceled_without_post_cancel_execution_check"
            elif status == "CANCEL_REJECTED":
                row["order_sync_warning"] = "cancel_rejected_reconciliation_required"
            elif status in {"FILLED_AFTER_CANCEL_REQUEST", "CANCELED_AFTER_PARTIAL_FILL"}:
                row["order_sync_warning"] = "fill_confirmed_after_cancel_request"
            else:
                row["order_sync_warning"] = ""
        return rows

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

    def manual_order_requests(self) -> list[dict[str, Any]]:
        rows = self._table("manual_order_requests")
        for row in rows:
            age_minutes = self._processing_age_minutes(row)
            row["processing_age_minutes"] = round(age_minutes, 3) if age_minutes is not None else None
            row["processing_stale"] = bool(
                row.get("status") == "PROCESSING"
                and not row.get("linked_order_id")
                and age_minutes is not None
                and age_minutes >= MANUAL_PROCESSING_STALE_MINUTES
            )
            if row["processing_stale"] and not row.get("stale_processing_reason"):
                row["stale_processing_reason"] = "manual_request_processing_stale_no_linked_order"
            recovery_block = self._manual_recovery_block_reason(row)
            row["recovery_block_reason"] = recovery_block
            row["safe_requeue_allowed"] = bool(row["processing_stale"] and not recovery_block)
            row["safe_cancel_allowed"] = bool(row["processing_stale"] and not recovery_block)
        return rows

    def requeue_manual_order_request(self, request_id: str, confirm_text: str = "", operator_note: str = "") -> dict[str, Any]:
        from .storage import StateStore

        row = self._manual_request_by_id(request_id)
        block_reason = self._manual_recovery_block_reason(row)
        if confirm_text != MANUAL_REQUEUE_CONFIRM_TEXT:
            block_reason = "manual_request_requeue_confirm_text_required"
        if block_reason:
            self._append_audit_log(
                "manual_order_request_requeued",
                self._manual_recovery_audit_payload(row, request_id, False, block_reason, operator_note),
            )
            return {"requeued": False, "request_id": request_id, "block_reason": block_reason, "order_api_called": False, "lots_positions_fills_changed": False}
        ok = StateStore(self.config.storage_path).requeue_stale_manual_order_request(request_id)
        self._append_audit_log(
            "manual_order_request_requeued",
            self._manual_recovery_audit_payload(row, request_id, ok, "operator_requeue_stale_processing", operator_note),
        )
        return {"requeued": ok, "request_id": request_id, "block_reason": "" if ok else "manual_request_requeue_failed", "order_api_called": False, "lots_positions_fills_changed": False}

    def cancel_manual_order_request(self, request_id: str, reason: str = "operator_cancel_stale_processing", confirm_text: str = "", operator_note: str = "") -> dict[str, Any]:
        from .storage import StateStore

        row = self._manual_request_by_id(request_id)
        block_reason = self._manual_recovery_block_reason(row)
        if confirm_text != MANUAL_CANCEL_CONFIRM_TEXT:
            block_reason = "manual_request_block_confirm_text_required"
        if block_reason:
            self._append_audit_log(
                "manual_order_request_blocked_by_operator",
                self._manual_recovery_audit_payload(row, request_id, False, block_reason, operator_note),
            )
            return {"canceled": False, "request_id": request_id, "block_reason": block_reason, "order_api_called": False, "lots_positions_fills_changed": False}
        ok = StateStore(self.config.storage_path).cancel_stale_manual_order_request(request_id, reason)
        self._append_audit_log(
            "manual_order_request_blocked_by_operator",
            self._manual_recovery_audit_payload(row, request_id, ok, reason, operator_note),
        )
        return {"canceled": ok, "request_id": request_id, "block_reason": "" if ok else "manual_request_block_failed", "order_api_called": False, "lots_positions_fills_changed": False}

    def _manual_request_by_id(self, request_id: str) -> dict[str, Any] | None:
        for row in self._table("manual_order_requests"):
            if str(row.get("request_id") or "") == request_id:
                return row
        return None

    def _processing_age_minutes(self, row: dict[str, Any]) -> float | None:
        started_at = _parse_datetime(str(row.get("processing_started_at") or ""))
        if not started_at:
            return None
        return max(0.0, (datetime.now() - started_at.replace(tzinfo=None)).total_seconds() / 60.0)

    def _manual_recovery_block_reason(self, row: dict[str, Any] | None) -> str:
        if row is None:
            return "manual_request_not_found"
        if row.get("status") != "PROCESSING":
            return "manual_request_not_processing"
        if row.get("linked_order_id"):
            return "manual_request_linked_order_exists"
        age_minutes = self._processing_age_minutes(row)
        if age_minutes is None or age_minutes < MANUAL_PROCESSING_STALE_MINUTES:
            return "manual_request_processing_not_stale"
        code = str(row.get("code") or "").zfill(6)
        side = str(row.get("side") or "")
        lot_id = str(row.get("lot_id") or "")
        request_id = str(row.get("request_id") or "")
        for order in self.orders():
            if str(order.get("status") or "") not in ORDER_PENDING_STATUSES:
                continue
            if str(order.get("code") or "").zfill(6) != code:
                continue
            if lot_id and str(order.get("lot_id") or "") not in {"", lot_id}:
                continue
            return "manual_request_open_order_exists"
        for request in self._table("manual_order_requests"):
            if str(request.get("request_id") or "") == request_id:
                continue
            if str(request.get("status") or "") not in MANUAL_PENDING_STATUSES:
                continue
            same_code = str(request.get("code") or "").zfill(6) == code
            same_lot = bool(lot_id and str(request.get("lot_id") or "") == lot_id)
            if same_code or same_lot:
                return "manual_request_pending_request_exists"
        positions = {str(row.get("code") or "").zfill(6): row for row in self.positions()}
        position = positions.get(code, {})
        if position.get("sync_status") == PositionLifecycle.SYNC_REQUIRED.value or position.get("position_state") == PositionLifecycle.SYNC_REQUIRED.value:
            return "sync_required"
        if position.get("position_state") == PositionLifecycle.RISK_BLOCKED.value:
            return "risk_blocked_buy_sell_blocked"
        if side == OrderSide.SELL.value:
            lots = {str(lot.get("lot_id") or ""): lot for lot in self.lots()}
            lot = lots.get(lot_id)
            if not lot:
                return "lot_not_found"
            remaining = int(lot.get("remaining_quantity") or 0)
            if str(lot.get("status") or "") == "CLOSED" or remaining <= 0:
                return "closed_lot"
            quantity = int(row.get("quantity") or 0)
            if quantity > remaining:
                return "quantity_exceeds_remaining"
        return ""

    def _manual_recovery_audit_payload(self, row: dict[str, Any] | None, request_id: str, success: bool, reason: str, operator_note: str) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "success": success,
            "reason": reason,
            "previous_status": row.get("status") if row else "",
            "previous_processing_started_at": row.get("processing_started_at") if row else "",
            "claim_attempt_count": row.get("claim_attempt_count") if row else 0,
            "linked_order_id": row.get("linked_order_id") if row else "",
            "operator_note": operator_note,
        }

    def manual_order_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = self.config
        side = str(payload.get("side", "")).upper()
        code = str(payload.get("code", "")).zfill(6)
        quantity = int(payload.get("quantity") or 0)
        amount = int(payload.get("amount") or 0)
        lot_id = str(payload.get("lot_id") or "")
        positions = {row["code"]: row for row in self.positions()}
        position = positions.get(code, {"code": code, "position_state": PositionLifecycle.NEVER_BOUGHT.value})
        lots = {row["lot_id"]: row for row in self.lots()}
        lot = lots.get(lot_id, {})
        current_price = int(payload.get("current_price") or position.get("current_price") or lot.get("current_price") or 0)
        open_lots_for_code = [
            row for row in lots.values()
            if str(row.get("code") or "").zfill(6) == code and int(row.get("remaining_quantity") or 0) > 0 and str(row.get("status") or "") != "CLOSED"
        ]
        fallback_entry_price = int(open_lots_for_code[0].get("buy_price") or 0) if open_lots_for_code else current_price
        sizing_preview = self._manual_buy_lot_sizing_preview(position, current_price, len(open_lots_for_code), fallback_entry_price)
        runtime = self.runtime_status()
        block_reasons: list[str] = []
        if not config.ui_manual_trading_enabled:
            block_reasons.append("ui_manual_trading_disabled")
        if side not in {OrderSide.BUY.value, OrderSide.SELL.value}:
            block_reasons.append("invalid_side")
        if runtime.get("all_orders_paused"):
            block_reasons.append("runtime_all_orders_paused")
        if position.get("sync_status") == PositionLifecycle.SYNC_REQUIRED.value or position.get("position_state") == PositionLifecycle.SYNC_REQUIRED.value:
            block_reasons.append("sync_required")
        if position.get("position_state") == PositionLifecycle.RISK_BLOCKED.value:
            block_reasons.append("risk_blocked_buy_sell_blocked")
        if current_price <= 0:
            block_reasons.append("current_price_missing")
        if side == OrderSide.BUY.value:
            if runtime.get("buy_paused"):
                block_reasons.append("runtime_buy_paused")
            if self._has_open_order(code, OrderSide.BUY.value):
                block_reasons.append("open_buy_order_exists")
            if quantity <= 0 and amount > 0 and current_price > 0:
                quantity = amount // current_price
            if amount <= 0 and quantity > 0 and current_price > 0:
                amount = quantity * current_price
            if amount <= 0 and sizing_preview["lot_unit_amount"] > 0:
                amount = int(sizing_preview["lot_unit_amount"])
                if quantity <= 0 and current_price > 0:
                    quantity = amount // current_price
            if quantity <= 0:
                block_reasons.append("quantity_below_one")
            if amount <= 0:
                block_reasons.append("amount_missing")
            sizing_block = self._lot_sizing_block_reason(position, current_price, sizing_preview, amount, len(open_lots_for_code))
            if sizing_block:
                block_reasons.append(sizing_block)
        if side == OrderSide.SELL.value:
            if runtime.get("sell_paused"):
                block_reasons.append("runtime_sell_paused")
            if not lot_id or not lot:
                block_reasons.append("lot_not_found")
            remaining = int(lot.get("remaining_quantity") or 0)
            if str(lot.get("status") or "") == "CLOSED" or remaining <= 0:
                block_reasons.append("closed_lot")
            if self._has_open_order(code, OrderSide.SELL.value, lot_id):
                block_reasons.append("open_sell_order_exists")
            if quantity <= 0:
                quantity = remaining
            if quantity > remaining:
                block_reasons.append("quantity_exceeds_remaining")
            amount = quantity * current_price if current_price > 0 else 0
        estimated_pnl = 0
        estimated_pnl_rate = 0.0
        estimated_fee_tax = 0
        if side == OrderSide.SELL.value and lot and current_price > 0:
            buy_price = int(lot.get("buy_price") or 0)
            estimated_pnl = (current_price - buy_price) * quantity
            estimated_pnl_rate = (current_price - buy_price) / buy_price if buy_price else 0.0
            estimated_fee_tax = int(round(amount * config.strategy.estimated_fee_tax_pct / 100.0))
        confirm_text = str(payload.get("confirm_text") or "")
        confirm_required = bool(config.order.live_trading)
        confirm_text_verified = (not confirm_required) or confirm_text == "수동주문 확인"
        if confirm_required and not confirm_text_verified:
            block_reasons.append("confirm_text_required")
        preview = {
            "can_create": not block_reasons,
            "block_reasons": block_reasons,
            "source": "local_ui_manual",
            "requested_by": str(payload.get("requested_by") or "local_ui"),
            "code": code,
            "name": position.get("name") or lot.get("name", ""),
            "side": side,
            "quantity": quantity,
            "amount": amount,
            "lot_id": lot_id,
            "current_price": current_price,
            "price_lot_band": sizing_preview["lot_sizing_bucket"],
            "entry_price_for_lot_sizing": sizing_preview["entry_price_for_lot_sizing"],
            "lot_unit_amount": sizing_preview["lot_unit_amount"],
            "max_symbol_amount": sizing_preview["max_symbol_amount"],
            "max_lots_per_symbol": sizing_preview["max_lots_per_symbol"],
            "current_open_lot_count": len(open_lots_for_code),
            "lot_sizing_locked": sizing_preview["lot_sizing_locked"],
            "lot_sizing_mode": sizing_preview["lot_sizing_mode"],
            "remaining_buy_capacity_amount": max(0, int(sizing_preview["max_symbol_amount"]) - int(position.get("cumulative_invested_amount") or 0)) if sizing_preview["max_symbol_amount"] else 0,
            "estimated_order_amount": amount,
            "estimated_realized_pnl": estimated_pnl,
            "estimated_realized_pnl_rate": estimated_pnl_rate,
            "estimated_fee_tax": estimated_fee_tax,
            "runtime_snapshot": runtime,
            "live_trading": config.order.live_trading,
            "confirm_required": confirm_required,
            "confirm_text_verified": confirm_text_verified,
            "position_state": position.get("position_state", ""),
            "open_order_exists": self._has_open_order(code, side, lot_id if side == OrderSide.SELL.value else ""),
        }
        self._append_audit_log("manual_order_request_previewed", preview)
        return preview

    def create_manual_order_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        preview = self.manual_order_preview(payload)
        if not preview["can_create"]:
            self._append_audit_log("manual_order_request_blocked", preview)
            return {"created": False, "preview": preview, "errors": preview["block_reasons"]}
        request_id = f"MANUAL-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat(timespec="seconds")
        request = {
            "request_id": request_id,
            "source": "local_ui_manual",
            "requested_by": preview["requested_by"],
            "requested_at": now,
            "code": preview["code"],
            "side": preview["side"],
            "amount": preview["amount"],
            "quantity": preview["quantity"],
            "current_price": preview["current_price"],
            "lot_id": preview["lot_id"],
            "order_type": "LIMIT_POLICY",
            "preview_json": json.dumps(preview, ensure_ascii=False),
            "runtime_snapshot_json": json.dumps(preview["runtime_snapshot"], ensure_ascii=False),
            "live_trading": preview["live_trading"],
            "confirm_text_verified": preview["confirm_text_verified"],
            "status": "REQUESTED",
            "config_hash": config_hash(self.config),
            "config_version": config_hash(self.config),
            "run_id": self.config.experiment.run_id or self.config.run_id or f"{self.config.risk.profile}_{config_hash(self.config)}",
            "experiment_name": self.config.experiment.experiment_name or self.config.experiment_name or self.config.risk.profile,
        }
        from .storage import StateStore

        StateStore(self.config.storage_path).create_manual_order_request(request)
        self._append_audit_log("manual_order_request_created", request)
        return {"created": True, "request_id": request_id, "preview": preview}

    def _manual_buy_lot_sizing_preview(self, position: dict[str, Any], current_price: int, open_lot_count: int, fallback_entry_price: int = 0) -> dict[str, Any]:
        strategy = self.config.strategy
        if strategy.lot_sizing_mode != "cycle_locked_by_entry_price":
            return {
                "enabled": True,
                "entry_price_for_lot_sizing": 0,
                "lot_unit_amount": strategy.initial_buy_amount,
                "max_symbol_amount": strategy.auto_buy_limit,
                "max_lots_per_symbol": strategy.max_open_lots_before_review,
                "lot_sizing_bucket": "legacy_exposure_bands",
                "lot_sizing_mode": strategy.lot_sizing_mode,
                "lot_sizing_locked": False,
            }
        locked_lot_unit = int(position.get("lot_unit_amount") or 0)
        locked_max_amount = int(position.get("max_symbol_amount") or 0)
        if open_lot_count > 0 and locked_lot_unit > 0 and locked_max_amount > 0:
            return {
                "enabled": True,
                "entry_price_for_lot_sizing": int(position.get("entry_price_for_lot_sizing") or 0),
                "lot_unit_amount": locked_lot_unit,
                "max_symbol_amount": locked_max_amount,
                "max_lots_per_symbol": int(position.get("max_lots_per_symbol") or strategy.max_lots_per_symbol_default),
                "lot_sizing_bucket": str(position.get("lot_sizing_bucket") or ""),
                "lot_sizing_mode": str(position.get("lot_sizing_mode") or strategy.lot_sizing_mode),
                "lot_sizing_locked": True,
            }
        band_price = fallback_entry_price if open_lot_count > 0 and fallback_entry_price > 0 else current_price
        for band in strategy.price_lot_bands:
            if band.min_price <= band_price <= band.max_price:
                return {
                    "enabled": band.enabled,
                    "entry_price_for_lot_sizing": band_price,
                    "lot_unit_amount": band.lot_unit_amount,
                    "max_symbol_amount": band.max_symbol_amount,
                    "max_lots_per_symbol": band.max_lots or strategy.max_lots_per_symbol_default,
                    "lot_sizing_bucket": f"{band.min_price}-{band.max_price}",
                    "lot_sizing_mode": strategy.lot_sizing_mode,
                    "lot_sizing_locked": False,
                }
        return {
            "enabled": False,
            "entry_price_for_lot_sizing": current_price,
            "lot_unit_amount": 0,
            "max_symbol_amount": 0,
            "max_lots_per_symbol": 0,
            "lot_sizing_bucket": "",
            "lot_sizing_mode": strategy.lot_sizing_mode,
            "lot_sizing_locked": False,
        }

    def _lot_sizing_block_reason(self, position: dict[str, Any], current_price: int, sizing: dict[str, Any], amount: int, open_lot_count: int) -> str:
        if self.config.strategy.lot_sizing_mode != "cycle_locked_by_entry_price":
            return ""
        if not sizing.get("lot_sizing_bucket"):
            return "price_out_of_lot_sizing_range"
        if not sizing.get("enabled"):
            return "lot_sizing_band_disabled"
        lot_unit = int(sizing.get("lot_unit_amount") or 0)
        max_amount = int(sizing.get("max_symbol_amount") or 0)
        max_lots = int(sizing.get("max_lots_per_symbol") or 0)
        if lot_unit <= 0 or max_amount <= 0:
            return "lot_sizing_band_disabled"
        if current_price > lot_unit:
            return "lot_unit_amount_below_price"
        if max_lots and open_lot_count >= max_lots:
            return "max_lots_per_symbol_reached"
        if max_amount and int(position.get("cumulative_invested_amount") or 0) + (amount or lot_unit) > max_amount:
            return "max_symbol_amount_reached"
        return ""

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
        if min(risk.min_cash_available, risk.max_active_symbols, risk.max_total_open_lots, risk.max_total_invested_amount, risk.max_new_buy_per_day, risk.max_new_buy_amount_per_day, risk.max_total_initial_buy_amount_per_day) < 0:
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
        if updates.get("config_reload_requested"):
            current["config_reload_requested_at"] = datetime.now().isoformat(timespec="seconds")
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

    def _has_open_order(self, code: str, side: str, lot_id: str = "") -> bool:
        for order in self.orders():
            if order.get("code") != code or order.get("side") != side or order.get("status") not in {"REQUESTED", "PARTIAL"}:
                continue
            if side == OrderSide.SELL.value and lot_id and order.get("lot_id") != lot_id:
                continue
            return True
        return False

    def _append_audit_log(self, event: str, payload: dict[str, Any]) -> None:
        log_path = Path(self.config.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        item = " ".join(f"{key}={json.dumps(value, ensure_ascii=False, default=str)}" for key, value in payload.items())
        with log_path.open("a", encoding="utf-8") as output:
            output.write(f"{datetime.now().isoformat(timespec='seconds')} INFO {event} {item}\n")

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


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _prepare_new_season_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "prepare_new_season.py"
    spec = importlib.util.spec_from_file_location("prepare_new_season_ui", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("prepare_new_season.py not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reason_guide(reason: str) -> dict[str, str]:
    return NEW_SEASON_REASON_GUIDE.get(reason, {"title": reason or "진행 가능", "description": reason, "next_action": "상태를 다시 확인하세요."})


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    for candidate in (normalized, normalized.replace(" ", "T")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _snapshot_validation_response(reason: str) -> dict[str, Any]:
    return {
        "snapshot_valid_for_preview": False,
        "snapshot_valid_for_request": False,
        "snapshot_warnings": [],
        "snapshot_errors": [reason],
        "snapshot_age_minutes": None,
        "missing_required_fields": ["path"] if reason == "liquidation_kis_balance_fetch_required" else [],
        "matched_positions_count": 0,
        "mismatched_positions_count": 0,
        "missing_in_snapshot_codes": [],
        "extra_in_snapshot_codes": [],
        "request_creation_allowed": False,
        "request_creation_block_reason": reason,
        "guide": _reason_guide(reason),
    }


def _parse_key_values(body: str) -> dict[str, str]:
    return dict(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=([^ ]+)", body))


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key, "UNKNOWN"))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_rate(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _safe_percent(value: int | float, limit: int | float) -> float | None:
    return round(float(value) / float(limit) * 100.0, 3) if limit else None


def _usage(key: str, current: int, limit: int, unit: str, basis: str) -> dict[str, Any]:
    pct = _safe_percent(current, limit)
    return {
        "key": key,
        "current": current,
        "limit": limit,
        "usage_pct": pct,
        "level": _usage_level(pct),
        "unit": unit,
        "basis": basis,
        "unlimited": limit <= 0,
    }


def _usage_level(pct: float | None) -> str:
    if pct is None:
        return "unlimited"
    if pct >= 100:
        return "over"
    if pct >= 80:
        return "danger"
    if pct >= 50:
        return "warning"
    return "normal"


def _date_key(value: Any) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else ""


def _lot_key(row: dict[str, Any]) -> str:
    return str(row.get("lot_id") or row.get("fill_id") or row.get("execution_id") or row.get("order_id") or "")


def _unique_lot_keys(rows: list[dict[str, Any]]) -> set[str]:
    return {key for key in (_lot_key(row) for row in rows) if key}


def _current_price_for_lot(lot: dict[str, Any], latest_prices: dict[str, dict[str, Any]]) -> int:
    code = str(lot.get("code") or "")
    price = _to_int(latest_prices.get(code, {}).get("price"))
    return price or _to_int(lot.get("current_price"))


def _holding_days(start: Any, end: Any) -> float:
    start_at = _parse_datetime(str(start or ""))
    end_at = _parse_datetime(str(end or ""))
    if not start_at or not end_at:
        return 0.0
    return round(max(0.0, (end_at.replace(tzinfo=None) - start_at.replace(tzinfo=None)).total_seconds() / 86400), 3)


def _sort_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    if value is None:
        return ""
    text = str(value)
    numeric = _to_int(text)
    if text.strip() and str(numeric) == text.strip():
        return numeric
    return text


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    for key, value in list(row.items()):
        if key in {"needs_review", "auto_buy_enabled", "danger_state", "lot_quantity_mismatch", "trading_paused", "sell_completed", "partial_sold", "cleanup_candidate", "cleanup_flag", "anchor_single_fill", "live_trading", "confirm_text_verified"}:
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
