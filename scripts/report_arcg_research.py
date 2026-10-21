"""Render factual Korean ARCG research documents and figures from saved results.

No experiment, broker client, live application or order is executed here.
Missing experiment outputs are an error; the renderer never invents returns.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODELS = "ABCDEF"
MODEL_NAMES = {
    "A": "현재 로컬 LOT 전략",
    "B": "A + 자금배분 가드·공통 분할수량",
    "C": "B + 상태·진입신호·인과적 종목순위",
    "D": "C + 적응형 추가매수",
    "E": "D + 신규 회전·추세·위험매도",
    "F": "E + 신규신호 재진입·장기보유 재평가",
}
LIMITATION = (
    "이 결과는 현재 규칙을 과거에 투영한 연구용 반사실 실험이다. 원시주가의 기업행위·배당, "
    "당시 상장/상장폐지 종목 구성, 역사 업종정보와 실제 주문장 검증이 불완전하므로 "
    "표의 CAGR/MDD를 실거래 기대수익이나 검증된 경제적 성과로 해석할 수 없다. "
    "검증구간에서 고른 후보도 실거래 최적값으로 권장하지 않는다."
)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def number(row: dict, key: str, default=None):
    try:
        value = float(row[key])
        return value if math.isfinite(value) else default
    except (KeyError, ValueError, TypeError):
        return default


def fmt(value, digits=2):
    if value is None:
        return "미산출"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}" if math.isfinite(value) else "미산출"
    return str(value).replace("|", "/").replace("\n", " ")


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines.extend("| " + " | ".join(fmt(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def git_info():
    def query(*args):
        try:
            return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()
        except (OSError, subprocess.CalledProcessError):
            return "Git 정보 미제공"
    return query("branch", "--show-current"), query("rev-parse", "HEAD")


def test_logs(out):
    records = []
    for path in sorted((out / "test_logs").glob("*.log")):
        raw = path.read_bytes()
        text = raw.decode("utf-16") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else raw.decode("utf-8-sig", errors="replace")
        summaries = [line.strip() for line in text.splitlines()
                     if re.search(r"\b\d+ (?:passed|failed|error|errors|skipped)\b", line)
                     and " in " in line]
        if summaries:
            records.append((path.name, summaries[-1].strip("= ")))
    return records


def performance_table(test):
    return table(["모델", "CAGR %", "MDD %", "최종 NAV 원", "거래 수", "평균 현금 %", "최소 현금 %", "현금부족 차단", "경제적 무결성 플래그"], [
        (m, number(test[m], "cagr_pct"), number(test[m], "max_drawdown_pct"), number(test[m], "final_equity"),
         number(test[m], "total_trades"), number(test[m], "average_cash_ratio_pct"), number(test[m], "minimum_cash_ratio_pct"),
         number(test[m], "cash_shortfall_blocks"), test[m].get("economic_valid", "미제공")) for m in MODELS])


def render_figures(out: Path, equities: list[dict], optimization: list[dict]):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from datetime import datetime

    grouped = {m: sorted([r for r in equities if r.get("model") == m and r.get("split", "test") == "test"], key=lambda r: r["date"]) for m in MODELS}
    colors = dict(zip(MODELS, plt.get_cmap("tab10").colors))
    def save(filename, title, ylabel, column, scale=1):
        fig, ax = plt.subplots(figsize=(12, 5))
        for m, rows in grouped.items():
            ax.plot([datetime.fromisoformat(r["date"]) for r in rows], [number(r, column, 0) / scale for r in rows], label=m, color=colors[m], linewidth=1.2)
        ax.set(title=title, ylabel=ylabel, xlabel="Date")
        ax.grid(alpha=.25)
        ax.legend(ncol=6)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(out / filename, dpi=150)
        plt.close(fig)
    save("equity_comparison.png", "Retrospective raw-price scenario: account NAV", "NAV (million KRW)", "equity", 1e6)
    save("drawdown_comparison.png", "Scenario drawdown (negative %)", "Drawdown (%)", "drawdown_pct")
    save("cash_ratio_comparison.png", "Scenario cash / account NAV", "Cash ratio (%)", "cash_ratio_pct")

    months = read_csv(out / "monthly_returns.csv")
    month_keys = sorted({r["period"] for r in months})
    fig, ax = plt.subplots(figsize=(max(12, len(month_keys) * .20), 5))
    for index, m in enumerate(MODELS):
        mapping = {r["period"]: number(r, "return_pct", 0) for r in months if r.get("model") == m}
        ax.bar([i + (index - 2.5) * .13 for i in range(len(month_keys))], [mapping.get(key, 0) for key in month_keys], width=.13, label=m, color=colors[m])
    step = max(1, len(month_keys) // 16)
    ax.set_xticks(list(range(0, len(month_keys), step)), month_keys[::step], rotation=45, ha="right")
    ax.set(title="Monthly NAV returns: common execution assumptions", ylabel="Return (%)")
    ax.axhline(0, color="black", linewidth=.6)
    ax.legend(ncol=6)
    fig.tight_layout()
    fig.savefig(out / "monthly_returns.png", dpi=150)
    plt.close(fig)

    candidates = [r for r in optimization if r.get("split") == "validation" and r.get("status") == "completed"]
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    labels = [r.get("candidate", str(i)) for i, r in enumerate(candidates)]
    axes[0].plot(range(len(candidates)), [number(r, "cagr_pct", 0) for r in candidates], "o-")
    axes[1].plot(range(len(candidates)), [number(r, "max_drawdown_pct", 0) for r in candidates], "o-", color="tab:red")
    axes[0].set(title="Predeclared candidate sensitivity: validation only", ylabel="CAGR (%)")
    axes[1].set(ylabel="MDD (%)")
    axes[1].set_xticks(range(len(labels)), labels, rotation=45, ha="right")
    for ax in axes:
        ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(out / "parameter_sensitivity.png", dpi=150)
    plt.close(fig)

    # Conditions refer to the causal common-index labels saved by the replay,
    # not a retrospective relabeling based on investment outcomes.
    regimes = ("RISK_ON", "NEUTRAL", "RISK_OFF")
    fig, ax = plt.subplots(figsize=(10, 5))
    for index, m in enumerate(MODELS):
        observations = defaultdict(list)
        rows = grouped[m]
        for i in range(1, len(rows)):
            previous, current = number(rows[i - 1], "equity"), number(rows[i], "equity")
            if previous and current is not None:
                observations[rows[i].get("market_regime", "NEUTRAL")].append((current / previous - 1) * 100)
        values = [sum(observations[r]) / len(observations[r]) if observations[r] else float("nan") for r in regimes]
        ax.bar([i + (index - 2.5) * .13 for i in range(3)], values, width=.13, label=m, color=colors[m])
    ax.set_xticks(range(3), regimes)
    ax.set(title="Mean daily NAV return conditioned on recorded market state", ylabel="Arithmetic daily return (%)")
    ax.axhline(0, color="black", linewidth=.6)
    ax.legend(ncol=6)
    fig.tight_layout()
    fig.savefig(out / "market_regime_performance.png", dpi=150)
    plt.close(fig)


def render(out: Path):
    required = ["research_results.json", "backtest_config.json", "03_backtest_comparison.csv", "04_optimization_results.csv", "05_equity_curve.csv", "06_trade_history.csv", "annual_returns.csv", "monthly_returns.csv"]
    missing = [name for name in required if not (out / name).exists()]
    if missing:
        raise FileNotFoundError("실행 결과가 아직 없어 보고서를 만들지 않습니다: " + ", ".join(missing))
    results, config = read_json(out / required[0]), read_json(out / required[1])
    test, validation, selection = results["test"], results.get("validation", {}), results["selection"]
    if set(MODELS) - set(test):
        raise ValueError("A~F의 실제 최종 평가 결과가 모두 필요합니다")
    for model in MODELS:
        for key in ("cagr_pct", "max_drawdown_pct", "final_equity", "total_trades", "average_cash_ratio_pct", "minimum_cash_ratio_pct", "cash_shortfall_blocks"):
            if number(test[model], key) is None:
                raise ValueError(f"실제 성과 필드 누락/비정상: {model} {key}")
    selected_model = selection.get("model_selected_on_validation", "미제공")
    if selected_model not in MODELS or len(selected_model) != 1:
        raise ValueError("최종 test 전에 저장된 validation 선택 모델이 필요합니다")
    selected_candidate = selection.get("candidate", "미제공")
    params = dict(selection.get("arcg", config.get("default_arcg", {})))
    defaults = dict(config.get("default_arcg", params))
    optimization, equities = read_csv(out / "04_optimization_results.csv"), read_csv(out / "05_equity_curve.csv")
    if any(not any(row.get("model") == model for row in equities) for model in MODELS):
        raise ValueError("A~F 각 모델의 실제 일자별 NAV가 필요합니다")
    experiment_log = read_json(out / "experiment_log.json", [])
    # Replay cost overrides are common to A-F and supersede ARCG dataclass
    # defaults. Describe actual evaluated values, not merely selected overrides.
    final_f = next((r for r in reversed(experiment_log) if r.get("tag") == "final" and r.get("model") == "F" and r.get("split") == "test" and r.get("status") == "completed"), {})
    effective = final_f.get("diagnostics", {}).get("effective_arcg_parameters", {})
    if effective:
        params.update(effective)
        for key in ("buy_fee_rate", "sell_fee_rate", "sell_tax_rate", "slippage_rate"):
            defaults[key] = params[key]
    manifest = results.get("data_limitations", {})
    limitations = manifest.get("limitations", [])
    branch, commit = git_info()
    records = test_logs(out)
    tests_table = table(["실행 로그", "실제 결과"], records) if records else "실행 로그가 아직 제공되지 않았다."
    comparison = performance_table(test)
    chosen = test.get(selected_model, test["F"])
    baseline = test["A"]
    delta_cagr = (number(chosen, "cagr_pct", 0) - number(baseline, "cagr_pct", 0))
    delta_mdd = (number(chosen, "max_drawdown_pct", 0) - number(baseline, "max_drawdown_pct", 0))
    caveat = "\n\n" + LIMITATION + "\n"
    annual = read_csv(out / "annual_returns.csv")
    annual_map = {(r.get("model"), r.get("period")): number(r, "return_pct") for r in annual}
    annual_rows = []
    for period in sorted({r.get("period") for r in annual}):
        a, chosen_year = annual_map.get(("A", period)), annual_map.get((selected_model, period))
        annual_rows.append((period, a, chosen_year, chosen_year - a if a is not None and chosen_year is not None else None))
    annual_table = table(["연도", "A NAV 수익 %", "검증선택 모델 NAV 수익 %", "차이 pp"], annual_rows)
    stress = results.get("cost_sensitivity", read_csv(out / "execution_cost_sensitivity.csv"))
    stress_table = table(["모델", "슬리피지 bps", "CAGR %", "MDD %", "최종 NAV 원", "총 실행비용 원"], [
        (r.get("model"), number(r, "slippage_bps"), number(r, "cagr_pct"), number(r, "max_drawdown_pct"), number(r, "final_equity"), number(r, "total_execution_cost")) for r in stress
    ]) if stress else "별도 비용 스트레스 실행결과가 제공되지 않았다."

    spec = f"""# ARCG 최종 연구 명세

ARCG는 기존 실행 엔진과 분리된 결정 모듈이다. 전체 순서는 완료된 시장/종목 관측 → 상태분류 → 현금/예산 검증 → 우선순위가 있는 매매 판단 → 별도 체결 반영이다. 실거래 main에 연결하거나 운영설정을 변경하지 않았다.
{caveat}
## 상태와 최초 진입

현재 선택 후보의 fast/slow 이동평균은 {params.get('fast_period')}/{params.get('slow_period')}개 완료 봉, slow 기울기는 {params.get('slope_period')}봉, ATR은 {params.get('atr_period')}봉 단순평균이다. fast가 slow의 trend_band 위이고 slow 기울기가 양의 한계값을 넘고 종가가 fast 이상이면 UPTREND, 대칭 하락이면 DOWNTREND, 나머지는 RANGE다. 시장은 같은 관측식으로 RISK_ON/NEUTRAL/RISK_OFF를 구분하고 ATR 비율이 시장 변동성 한도를 넘으면 RISK_OFF다. 상태전환은 서로 다른 완료 봉 {params.get('confirmation_bars')}개 확인 후 적용하며 같은 봉 중복관찰은 확인횟수를 올리지 않는다.

core의 timezone 없는 timestamp는 UTC로 해석한다. 실제 한국시간 입력은 Asia/Seoul offset을 포함해 전달해야 한다. replay의 날짜 축은 자체적으로 일관된 완료일봉/다음 시가 시각을 사용하며 실제 장중 broker timestamp 변환을 검증한 것은 아니다.

최초매수는 평균선 상향돌파·직전 고가 돌파·하락 후 안정/반등이라는 인과적 신호 중 하나가 있어야 한다. 과열 상한, 최소 거래대금/가격, 상태, 위험, 전체 사이클 예산 확보를 동시에 검사한다. RISK_OFF 또는 DOWNTREND에서는 신규/추가매수를 막는다. 각 봉에서 위험매도 > 수익매도 > 매수 순서로 하나의 결정을 내고 미결 주문이 있으면 같은 종목 추가 결정을 막는다.

## 자금배분

안전현금은 NAV × safety_cash_fraction, 종목한도는 NAV × symbol_cap_fraction과 선택적 절대액 한도 중 작은 값, 업종한도와 계좌한도는 각각 NAV 비율이다. 신규 사이클은 최초 주문금액뿐 아니라 전체 사이클 예산을 확보해야 시작한다. 이후 해당 예산은 잠그고 잔존 원가·비용·미체결 매수 보류금을 포함해 사용액을 검사한다. 추가매수는 자기 준비금을 사용할 수 있고 다른 종목 준비금과 안전현금을 침범할 수 없다.

Portfolio.buyable_cash는 브로커가 미체결 매수 보류를 반영한 값이다. pending 예약액은 이 값을 다시 차감하는 용도가 아니라 노출·논리적 배정 조정에 사용한다. 오프라인 replay는 원장 cash에서 pending을 한 번 차감해 이 의미를 맞춘다. 미체결 매도는 현금으로 인정하지 않는다. 잔고/주문 상태 불확실, 낡은 스냅샷, LOT 불일치나 불완전 체결 이력은 신규매수를 막는다. 일시적 현금부족만으로 전체 LOT를 일괄 손절하지 않는다.

## 추가매수와 사이클

기본 기준은 사이클의 실제 BUY 체결 최저가다. 일부/최저원가 LOT를 팔아도 값이 올라가지 않으며 완전 청산 후 새 사이클에서만 초기화한다. 마지막 매수가와 현재 평균값은 비교 실험의 별도 기준이다. 누락된 사이클 기록을 추정해 매수를 재개하지 않고 history_complete 검사로 차단한다.

gap = min(max_gap, max(base_gap, ATR/종가 × atr_gap_multiplier) × (1 + utilization_gap_multiplier × 예산사용률) × 중립시장 배수). 현재가가 기준 × (1-gap) 이하이고 선택된 안정화 조건(현재 종가 ≥ 직전 종가), 매수간격, 추세/자금검사를 만족할 때만 추가매수한다. 신호를 만든 봉에서 체결했다고 처리하지 않는다.

## 회전·추세·위험매도

RANGE 및 회전 LOT는 max(profit_target, ATR 비율 × profit_atr_multiplier) 목표와 비용차감 최소순익 조건을 함께 사용한다. UPTREND의 추세 배정 비율은 {fmt(params.get('trend_fraction'))}이다. 1주 LOT처럼 분할 불가능하면 정수 수량 규칙을 적용한다. 추세 LOT는 완료 봉의 최고가를 갱신하고 max(trailing_fraction, ATR 비율 × trailing_atr_multiplier)만큼 되밀리거나 추세이탈·목표를 만족할 때 순익조건 하에 매도한다. 손실 방치 방지는 독립 위험매도가 담당한다.

투자근거 붕괴/기업위험은 전량, 심각한 추세악화나 집중도 초과는 설정 비율 축소를 고려한다. 실제 기업위험 필드는 외부 사실이 주어질 때만 작동하며 과거 가격데이터만으로 해당 사건을 검증한 것은 아니다. 보유기간 경과는 검토를 유발하고 추가매수를 일시정지할 수 있지만 수익목표를 무제한 음수로 낮추지 않는다.

## 재진입과 실행 연결

전량 청산 후 새 관측의 신규 진입신호·상태·자금·재진입 간격을 다시 검사한다. 강제 timeout 매수, 단순 오래 기다린 순서의 무조건 진입은 F에서 사용하지 않는다. 상태는 직렬화 가능하며 재시작 뒤 사이클 최저가/예산/LOT/마지막 관측을 복구해야 한다.

`arcg_state.StateBook`은 사이클/LOT/미결예약/체결ID를 별도 파일에 영속화하고 같은 체결 재적용을 방지한다. 부분체결은 남은 보류금과 정수 추세배정을 유지하고, 취소는 확인된 경우에만 보류를 해제하며, 잔고 불일치는 매수를 막는다. 복구는 완전한 체결이력과 사이클예산 및 브로커 수량의 대사가 충족될 때만 완료 상태로 인정한다. 이 원장을 실제 계좌원장과 연결하는 절차는 production 통합 작업으로 남는다.

`arcg_adapter.py`는 완료 관측과 최신 계좌·세션·runtime 증거를 받아 기존 OrderRequest 형식의 사전검토 요청을 만든다. 매도 LOT가 여러 개면 요청도 여러 개이며 향후 통합 시 한 건씩 체결·잔고·가드를 갱신해야 한다. 이 adapter는 broker 제출이나 production main 연결을 하지 않는다. 기존 지정가·호가단위·부분체결·미체결·durable intent·취소/복구 가드는 기존 모듈에 남아 있고 새 경로의 실제 연결 검증은 미완료다.

미완성 실행 의미도 구분한다. adapter가 최신 증거를 거절하기 전에 core는 해당 봉의 signal timestamp를 반환상태에 기록할 수 있다. 호출자가 이를 바로 저장하면 같은 봉 재시도 신호가 소비될 수 있으므로 향후 실행자는 accepted-intent와 재시도 정책을 설계해야 한다. evidence.quote_price는 신선한 양수 가격인지 확인하는 증거이며 완료봉 기준 주문가를 최신 호가에 재산정하거나 실제 호가장 체결성을 확인하는 기능은 아니다.

## A~F 기능 분해의 해석

{table(['모델', '설계 목표'], [(m, MODEL_NAMES[m]) for m in MODELS])}

A는 현재 로컬 LotGridStrategy/LotManager/PositionManager/RiskManager를 직접 호출한다. B는 예산가드뿐 아니라 이후 단계에 공통으로 유지하는 예산×tranche 비율의 수량크기를 도입한다. B 이후에는 연구용 하위클래스가 기존 가격구간 금액한도를 새 예산/분할크기로 대체하지만 위험·동기화·검토상태 가드는 유지한다. C는 legacy 매수 intent에 상태·최초 진입신호를 필터링하고 현재 관측 slope에 따른 종목순위를 추가한다. D는 적응형 추가매수를 도입하고 과거 재진입 규칙은 유지한다. E는 추세배정과 함께 회전·위험을 포함한 새 매도체계로 전환하며 재진입은 기존 규칙+필터다. B~E의 완전 청산 후 기존 재진입 적격성은 원래 가격구간의 현금배수로 먼저 판단한 뒤 공통 분할크기로 다시 수량을 계산하므로 새 수량 기준의 재진입 현금배수와 정확히 같은 것은 아니다. F에서 새 신호 재진입/장기검토를 적용한다. 따라서 B/C/E 변화량을 각각 단일 매개변수 하나만의 인과효과로 부르면 안 된다. 자세한 선택 분기는 `arcg_backtest.py`가 기준이며 production 전체 루프를 재현하지 않는다. 최종 검증 선택은 모델 {selected_model}, 후보 {selected_candidate}이고 선택 근거와 한계는 07/09 보고서를 따른다.
"""
    (out / "01_strategy_spec.md").write_text(spec, encoding="utf-8")

    change = f"""# 코드 변경 보고서

기존 사용자 수정과 운영설정을 보존한 별도 연구 구현이다. 운영 live flag, runtime pause, 종목목록, 실제 계좌원장과 자동매매 프로세스는 이번 연구의 배포 대상으로 변경하지 않았다.

{table(['파일', '추가한 책임'], [
('src/kis_msj/arcg.py','관측·상태·예산·매매 결정, 체결 및 사이클 상태의 순수 계산'),
('src/kis_msj/arcg_state.py','사이클·예약·체결ID의 영속 상태, 부분체결·취소·재시작·이력복구·대사'),
('src/kis_msj/arcg_adapter.py','최신 실행증거를 검사하는 기존 OrderRequest 사전검토 bridge; 제출 기능 없음'),
('src/kis_msj/arcg_backtest.py','읽기전용 시세 로더, 기존 A 호출, A~F 동일 일봉체결/비용/원장 평가'),
('scripts/run_arcg_research.py','시간순 후보탐색, 검증선택 고정, 최종테스트 및 CSV/JSON 내보내기'),
('scripts/report_arcg_research.py','저장된 실제 결과의 한국어 문서와 PNG 생성'),
('scripts/arcg_pytest_isolation.py','테스트 기본 runtime 중립화와 외부 네트워크 차단'),
('tests/test_arcg_core.py, test_arcg_backtest.py, test_arcg_adapter.py, test_arcg_research_runner.py','새 전략·체결·경계·preview·선택점수 검증; 실제 파일 목록은 패키지 manifest 참조'),
('analysis_output/legacy_strategy_settings.json','계좌정보 없는 현재정책과 공개 code/name/sector; 시행일·현재 업종을 과거 투영'),
])}

기존 코드에서 규칙을 삭제하거나 실거래 경로를 교체하지 않았다. 새 결정 모듈에서는 현금선형 수익목표, 무제한 시간 decay, 고원가 LOT 손실확정 및 강제 재진입을 기본 규칙으로 사용하지 않는다. A 비교에서는 현재 유효값을 유지한다. 소스에 있으나 현재 OFF인 기존 현금연동 매수 등의 기능도 기준선 분석에 구분했다.

보고서 생성 당시 연구 브랜치 `{branch}`, HEAD `{commit}`. 시작 HEAD와 사용자의 미커밋 수정은 00 보고서 및 로컬 baseline 자료로 따로 보존했다. 신규 커밋에는 이번 ARCG 추가 파일만 담고 기존 사용자의 미커밋 소스 변경을 임의로 커밋하지 않는다. 따라서 신규 커밋 하나만 깨끗한 checkout에 적용해서는 현재 로컬 API 의존성을 모두 재현할 수 없다. 정확한 재현물은 패키지의 계좌정보 없는 전체 src snapshot과 sanitized 설정이다. 최종 커밋/patch 범위는 패키지 재현 메타데이터가 기준이다. 민감한 원본 config/계좌 DB/로컬 baseline ZIP은 공유 대상이 아니다.

## 실제 테스트 결과

{tests_table}

비격리 baseline은 운영 pause를 읽어 47 passed 뒤 실패했다. 테스트용 하네스는 해당 파일을 수정하지 않고 테스트 프로세스의 기본 호출만 중립화한다. 명시적 임시 경로와 각 테스트의 pause monkeypatch는 유지한다. 격리 전체 기존 suite는 936 passed/2 failed였으며 남은 실패는 기존 dry-run SQLite 헤더 변경과 과거 커밋 원문 동일성 assertion이다. 새 ARCG 구현으로 인한 회귀로 단정하거나 전체 통과라고 표시하지 않는다. 실제 최종 로그가 추가되면 위 표가 이를 함께 보여준다.

잔여 작업은 실거래 adapter 연결, 실제 계좌 불일치/재시작 reconciliation, 부분체결 및 거래소별 가격·세션 제약의 end-to-end 검증, 데이터 보정과 당시 종목군 확보다. 연구실험 완료와 실거래 적용 준비 완료는 별개의 상태다.

초기 계산에서 발견한 한도/원장 의미 문제는 최종 holdout 전에 무효화하고 수정했다. 무효 시도와 당시 소스·설정은 invalidated_run1 아래 보존하며 최종 03/04 표에 섞지 않는다. 최종 실제 실험 건수는 experiment_log.json/research_results.json을 따른다.
"""
    (out / "02_code_change_report.md").write_text(change, encoding="utf-8")

    tested = defaultdict(set)
    for key, value in defaults.items():
        tested[key].add(json.dumps(value, ensure_ascii=False))
    for candidate in config.get("candidates", []):
        overrides = candidate[1] if isinstance(candidate, list) else candidate.get("parameters", {})
        for key, value in overrides.items():
            tested[key].add(json.dumps(value, ensure_ascii=False))
    parameter_rows = [(key, json.dumps(params.get(key), ensure_ascii=False), ", ".join(sorted(tested[key])),
                       "변경실험 있음" if len(tested[key]) > 1 else "기본값만; 민감도 미검증") for key in sorted(params)]
    candidates = sorted([r for r in optimization if r.get("split") == "validation"], key=lambda r: number(r, "selection_score", -1e99), reverse=True)
    neighbors = table(["후보", "상태", "검증 CAGR %", "검증 MDD %", "현금부족", "선택점수"], [
        (r.get("candidate"), r.get("status"), number(r, "cagr_pct"), number(r, "max_drawdown_pct"), number(r, "cash_shortfall_blocks"), fmt(number(r, "selection_score"), 6)) for r in candidates])
    recommendation = f"""# 파라미터 연구 결과와 후속 검증값

실거래 권장 최적값은 제시하지 않는다. 검증구간에서 선택한 연구후보는 `{selected_candidate}`, 모델은 `{selected_model}`이다. 이 값은 다음 보정 데이터 연구에서 재검증할 시작점이다.
{caveat}
## 탐색 절차

시간구간: {json.dumps(config.get('splits', {}), ensure_ascii=False)}. 후보 {len(config.get('candidates', []))}개를 train/validation에서 평가한 뒤 validation 기준으로 고르고 `selection_before_test.json`에 고정했다. 마지막 test 구간은 탐색 루프에서 사용하지 않는다. 기존 로컬 연구에서 이미 본 과거 자료이므로 완전히 미사용인 전향적 holdout으로 보장할 수 없다. 여러 시점을 이동하는 walk-forward 재학습 검증은 이번 단일 시간분할과 다르며 미완료다.

선택식은 비율단위 CAGR - 0.5×abs(MDD) - 0.000001×현금부족차단 횟수다. CSV의 CAGR/MDD는 퍼센트 수치이므로 선택식에서 100으로 나눈다. train은 진단용이며 test 성과로 재선택하지 않는다. 다수 후보의 1요인 비교는 전체 조합 최적화도 주변 모든 조합 안정성 보장도 아니다.

## 선택 후보의 설정과 실제 시도값

{table(['파라미터', '연구 후보값', '실험에 포함된 값', '해석'], parameter_rows)}

표의 시도값은 완료·실패 여부와 별개로 사전 선언한 실험 범위다. 비용 필드는 replay가 모든 모델에 공통으로 덮어쓴 실제 실행값을 우선 표시하며 dataclass 원래 기본값과 다를 수 있다. 개별 실패/성공과 train/validation 결과 전체는 04 CSV에서 확인한다. 최소 거래비용·유동성·snapshot 신선도·review 기간 등의 미탐색 기본값을 최적화 결과로 부르지 않는다. 실거래 세금과 수수료는 해당 기간·종목·계좌의 실제 조건으로 대체해야 한다.

## 검증구간 주변 후보 비교

{neighbors}

average/legacy_average/last_buy/cycle_low 기준과 안정화 즉시/대기 조건은 별도 후보로 비교했다. 표에 없는 기준 조합의 성과는 계산하지 않았다. 추세 LOT 비율과 trailing 폭도 몇 개 이산값만 검토했으므로 현 설정 주변의 연속적 안정성은 추가 검증이 필요하다. 승자 한 개만 남기지 않고 실패와 열세 후보도 CSV에 보존한다.

선택 후보의 안정화 대기 설정은 `{str(params.get('stabilization_required')).lower()}`이다. false라면 이 표본의 후보선택에서는 강제 반등확인 대기를 채택하지 않았다는 뜻이며, 사용자가 요청한 모든 새 기능이 항상 유리하다고 가정하지 않는다. 원시가격 자료에서 고른 결과이므로 다음 데이터 검증에서도 같은 결론이 유지되는지 확인해야 한다.
"""
    (out / "07_parameter_recommendation.md").write_text(recommendation, encoding="utf-8")

    worst_rows = []
    for m in MODELS:
        rows = sorted([r for r in equities if r.get("model") == m], key=lambda r: r["date"])
        worst = min(rows, key=lambda r: number(r, "drawdown_pct", 0))
        before = [r for r in rows if r["date"] <= worst["date"]]
        prior_peak = max(before, key=lambda r: number(r, "equity", 0))
        recovery = next((r["date"] for r in rows if r["date"] > worst["date"] and number(r, "equity", 0) >= number(prior_peak, "equity", 0)), "기간 내 미회복")
        worst_rows.append((m, prior_peak["date"], worst["date"], recovery, number(worst, "drawdown_pct"), number(test[m], "max_underwater_calendar_days"),
                           number(test[m], "minimum_cash"), number(test[m], "maximum_symbol_investment"),
                           number(test[m], "quarantine_event_count"), number(test[m], "stale_held_code_count")))
    trades = read_csv(out / "06_trade_history.csv")
    reasons = table(["모델", "실제 체결 주요 이유(횟수)"], [(m, ", ".join(f"{k}: {v}" for k, v in Counter(r.get("reason", r.get("trade_reason", "미제공")) for r in trades if r.get("model") == m).most_common(5))) for m in MODELS])
    risk_report = f"""# 위험·데이터 무결성 분석
{caveat}
## 실제 계좌 NAV 기준 결과

{comparison}

{table(['모델','직전 peak','최악 drawdown 일자','peak 회복','MDD %','최장 underwater 달력일','최소 현금 원','종목 최대원가 원','가격격리 사건','낡은 보유시세 사건'], worst_rows)}

가격 급변/누락 종목의 매매를 격리하고 일정 세션 이상 갱신되지 않은 보유주식을 0으로 평가하는 보수적 정책이 있다. 이는 실제 상장폐지 가치 회수나 기업행위를 복원한 처리가 아니다. 이번 모든 모델의 economic_valid는 false이며 경제적으로 완전한 성과를 검증하지 못했음을 뜻한다. 별도 conditional_price_replay_valid는 탐지한 보유 중 가격급변/낡은 시세 노출이 없었는지의 조건부 검사일 뿐이며 원시가격·표본·배당의 한계를 해결하지 않는다. 손실이 크게 나타나거나 후보가 열세이면 그대로 기록한다.

## 현금과 기회비용

현금부족차단은 실제 원장 cash 한도에서 줄이거나 못 산 횟수다. 논리적 예산/안전현금으로 더 일찍 막힌 신호는 `all_buy_blocks`와 diagnostics의 이유별 집계에 따로 나타날 수 있다. 따라서 cash_shortfall_blocks 감소만으로 매수기회 손실이 없었다고 결론내리지 않는다. 평균현금 상승, 총수익·benchmark 격차, 거래 수와 NAV를 함께 읽는다. 현금 0 이상이라는 회계 검증은 급락 손실이나 실제 브로커 유동성 보장과 다르다.

## 선택 후 비용 스트레스

{stress_table}

기본 replay 슬리피지는 backtest_config.json의 공통값이다. 별도 비용 스트레스는 A와 검증선택 모델만 평가하고 결과로 파라미터/모델을 다시 고르지 않는다. 10/20bps 시도 결과가 제공되면 위에 표시한다. 실시간 호가장/부분체결 스트레스와는 다르며, 실제 세율이나 거래가능성을 검증한 실험도 아니다. 선택고정 파일의 해시는 전후 동일성을 검사한다.

## 자료 제한

{chr(10).join('- ' + str(item) for item in limitations)}

공통 세션 {manifest.get('first_common_session', '미제공')}~{manifest.get('last_common_session', '미제공')}, 주식 {manifest.get('stock_rows', '미제공')}행, 취득 종목 {len(manifest.get('stock_codes', []))}개다. 당시 거래 가능 전체 종목군, 상장폐지와 종목변경의 point-in-time 매핑이 없어 생존/선정편향이 남는다. 업종은 현재 2026년 설정의 공개 code/name/sector 문자열만 별도로 투영했다. 이는 표준화한 당시 산업분류가 아니고 세부 테마가 업종을 지나치게 나눌 수 있다. 미등록/업종 없는 종목만 UNKNOWN 공동 그룹에 배정한다. 이 가정으로 예산 계산은 재현할 수 있지만 역사 업종집중도나 분산효과를 정확히 검증했다고 볼 수 없다.

일봉 종가 신호 뒤 다음 공통 거래일 시가 근사로만 체결한다. 장중 high/low의 도달 순서를 가정해 동일봉 수익을 만들지 않는다. 시가 지정가 접근성, 직전 거래량 대비 제한, 슬리피지·매수수수료·매도세금 추정치를 반영하지만 장 시작 경매/실시간 가격샘플/시간외 order book/부분체결/실제 세율·결제가능액은 재현하지 못한다. 미체결 매도대금은 선사용하지 않는다. 일봉은 실제 30초 주문 timeout을 검증할 수 없다.

## 상대 열세 구간과 다음 검증

{annual_table}

{reasons}

모델별 연간·월간 원장 NAV 수익은 annual_returns.csv/monthly_returns.csv를 제공한다. regime 성과 그림은 그날까지 관측한 지수상태별 일수익의 산술평균이며 독립 표본의 유의성이나 인과효과를 뜻하지 않는다. 급락 시 추가매수 제한은 손실을 줄일 수 있지만 강한 반등을 놓칠 수 있고 추세 LOT는 횡보에서 수익확정 지연/whipsaw를 만들 수 있다. 다음 단계는 기업행위/수정주가 및 배당 원장, 당시 종목군/업종, 비용·체결 민감도와 rolling walk-forward 검증이다. 이 작업 전 실거래 연결은 연구 성과만으로 정당화되지 않는다.
"""
    (out / "08_risk_analysis.md").write_text(risk_report, encoding="utf-8")

    ablations = []
    for prior, following in zip(MODELS, MODELS[1:]):
        ablations.append((prior + "→" + following,
            number(test[following], "cagr_pct", 0) - number(test[prior], "cagr_pct", 0),
            number(test[following], "max_drawdown_pct", 0) - number(test[prior], "max_drawdown_pct", 0),
            number(test[following], "final_equity", 0) - number(test[prior], "final_equity", 0)))
    best_step = max(ablations, key=lambda row: row[1])
    weak_steps = [step[0] for step in ablations if step[1] < 0 or step[2] < 0]
    final = f"""# KIS-MSJ ARCG 종합 연구 보고서

**검증된 실거래 우월성 또는 최적 파라미터를 확정하지 않았다.** 실제 코드·오프라인 테스트·A~F 역사 시뮬레이션과 사전선언 후보 탐색을 수행하고 원장 NAV·현금·미실현손실을 포함한 자료를 제공한다. 검증 선택은 `{selected_candidate}` / 모델 `{selected_model}`이며 실거래 배포 권고는 없다.

검증선택 모델은 이번 최종 test에서 A 대비 CAGR {fmt(delta_cagr)}pp, MDD {fmt(delta_mdd)}pp 변화했다. {"CAGR 또는 MDD가 악화되어 이번 개선목표를 달성했다고 판정하지 않는다." if delta_cagr < 0 or delta_mdd < 0 else "표본상의 변화가 좋아도 아래 자료 제한으로 실거래 개선을 확정하지 않는다."} test에서 더 좋아 보이는 다른 모델로 사후 재선택하지 않았다.
{caveat}
## 연구 설계와 원자료

원자료/해시/필터는 data_manifest.json, 재현설정은 backtest_config.json, 전체 실험의 성공·실패는 experiment_log.json에 남긴다. 완료 실험 수 {results.get('completed_experiments', '미제공')}, 실험별 소요시간 합계 {fmt(results.get('runtime_seconds'))}초(병렬 전체 wall time과 다름). 초기자금·비용·기간은 모든 A~F에 공통이고 날짜별 종가까지의 정보만 신호에 쓴다. A는 최신 로컬 기존 결정·LOT·포지션·위험 클래스를 호출하고 현재 현금정책의 시행일만 과거 전기간에 적용한다. 실제 과거 운영계좌나 거래소별 주문루프의 완전재현은 아니다.

## 동일 조건 최종 test 결과

{comparison}

MDD는 음수이며 0에 가까워질수록 낙폭이 작다. CAGR과 MDD는 퍼센트, 차이는 퍼센트포인트(pp)다. 순자산은 현금+보유평가액이며 실현손익만 큰 전략을 우수하다고 선택하지 않았다. benchmark는 비용·배당 없는 지수 가격수익으로 총수익 benchmark와 다르다.

## 요청한 12개 검토 질문

### 1. 기존 전략의 핵심 문제

가격 진입신호 없는 최초매수, 잔존 평균에 따라 바뀌는 추가매수 기준, 미래 추가매수 예산과 업종제한 부재, 현재 LOT 수×전체 보유기간 decay, 저현금에서 최저원가 트리거/최고원가 손실확정, 가격·추세와 무관한 현금용량 재진입이다. 체결원장/중복방지/복구 등 실행 안정기능은 기존 장점으로 보존했다. 00 문서가 코드 근거를 제공한다.

### 2. 무엇을 어떻게 바꿨는가

완료관측 상태분류, 잠긴 사이클 예산과 안전/추가매수/신규자금 분리, 비용을 고려한 자금한도, 사이클 최저체결가와 ATR·예산사용률 적응 gap, 회전/추세 LOT 분리, 위험매도, 신규신호 재진입과 장기보유 재검토를 추가했다. 기존 production main과 운영 config를 대체하지 않고 별도 core·replay·preview adapter로 구현했다.

### 3. 새 기능 중 가장 성과가 좋았던 부분

이번 test 표에서 CAGR 증분이 가장 큰 단계는 {best_step[0]} ({fmt(best_step[1])}pp), 같은 단계 MDD 변화는 {fmt(best_step[2])}pp다. 이는 해당 표본의 사후 기술통계다. 단계마다 경로·체결·보유가 바뀌고 데이터 편향이 남아 단일 기능의 검증된 인과효과로 확정하지 않는다. 검증구간과 후보별 결과를 먼저 함께 검토해야 한다.

{table(['단계','CAGR 변화 pp','MDD 변화 pp','최종 NAV 변화 원'], ablations)}

### 4. 성과를 악화시킨 기능

CAGR 하락 또는 MDD 악화가 관측된 단계는 {', '.join(weak_steps) if weak_steps else '이번 최종 표에서는 해당 조건 없음'}이다. 모든 기능이 개선된다는 전제를 두지 않는다. 기준값/추세비율/안정화 비교의 열세 후보도 04 CSV와 07 후보표에 포함했다. 여러 데이터/기간의 안정성을 확보하기 전 기능 제거·채택을 실거래 결론으로 옮기지 않는다.

### 5. A~F 중 가장 우수한 모델

선택에 사용한 validation 점수로 모델 `{selected_model}`을 test 실행 전에 골랐다. test에서 다른 모델의 수익률이 높아도 그 수치로 재선택하지 않는다. 원시가격과 표본구성 제한으로 경제적으로 가장 우수한 전략을 확정할 수는 없다. `selection_before_test.json`이 선택 시점·점수·설정을 기록한다.

### 6. 기존 대비 CAGR·MDD 변화

검증선택 모델의 이번 test CAGR은 {fmt(number(chosen,'cagr_pct'))}%, A는 {fmt(number(baseline,'cagr_pct'))}%로 변화 {fmt(delta_cagr)}pp다. MDD는 각각 {fmt(number(chosen,'max_drawdown_pct'))}%와 {fmt(number(baseline,'max_drawdown_pct'))}%로 변화 {fmt(delta_mdd)}pp다. 최종 NAV는 각각 {fmt(number(chosen,'final_equity'))}원과 {fmt(number(baseline,'final_equity'))}원이다. 이 차이는 위의 데이터·체결가정 하에 산출된 값이다.

### 7. 현금부족이 개선됐는가

선택 모델 현금부족 차단은 {fmt(number(chosen,'cash_shortfall_blocks'))}회, A는 {fmt(number(baseline,'cash_shortfall_blocks'))}회다. 최소 현금비율은 각각 {fmt(number(chosen,'minimum_cash_ratio_pct'))}%, {fmt(number(baseline,'minimum_cash_ratio_pct'))}%다. 조기 예산차단이 별도 이유로 집계되므로 이 횟수만으로 실제 자금관리 우월성을 결론내리지 않는다. 최대 동시매수 요구금액·모든 buy blocks·평균현금·투자원가를 CSV에서 함께 비교한다.

### 8. 놓친 매수기회가 늘었는가

새 모델은 추세·예산·안정화 조건으로 기회를 제한한다. 거래 수는 선택 모델 {fmt(number(chosen,'total_trades'))}, A {fmt(number(baseline,'total_trades'))}이며 기회비용의 직접 인과추정치는 아니다. 거절 신호가 이후 얼마의 이익이 됐을지 미래가격으로 선별해 매수하지 않았다. 별도 체계적인 거절신호 outcome 연구는 미완료다.

선택 후 실행비용 민감도도 별도 기록했다. 아래 결과로 선택을 다시 바꾸지 않았으며 가격경로·비용 변화만의 스트레스 시나리오다.

{stress_table}

### 9. 어느 시장상황에서 유리한가

기대 메커니즘은 하락장 신규/추가매수 억제와 상승장 일부 추세보유다. 실제 상태별 수익은 market_regime_performance.png를 제공하되 조건부 일수익 산술평균이며 표본수/경로/기업행위 왜곡을 고려해야 한다. 급락 후 V자 반등과 횡보 whipsaw에서는 기존 방식보다 나쁠 수 있다. 연간·월간 파일로 열세 기간을 숨기지 않는다.

{annual_table}

### 10. 다음에 가장 먼저 개선할 부분

먼저 수정주가·권리/배당 원장과 당시 종목군/업종으로 경제적 무결성을 확보한다. 이후 실제 빈도·거래량·비용·호가/세션 기반 체결검증과 walk-forward를 수행한다. 파라미터 추가탐색보다 이 자료 검증이 현재 결론을 바꿀 가능성이 크다.

### 11. 실거래 적용 준비가 됐는가

아니다. production main에 ARCG를 연결하거나 운영설정·프로세스를 배포하지 않았다. preview adapter는 기존 주문형식과 안전증거를 검사하는 연결점까지다. 실제 브로커 제출, 다중 LOT 순차체결, restart 상태 reconciliation, 실패/부분체결/세션전환 end-to-end 검증이 필요하다. 기존 운영 안전장치는 그대로 유지된다.

### 12. 추가 데이터·검증은 무엇인가

당시 상장/상장폐지·합병·분할·감자·권리락, 배당/거래정지, 역사 업종·유동성, 수정주가의 조정정의, 실제 세금/수수료, 정규/시간외 주문장과 미체결/부분체결 자료가 필요하다. 동일 feature engine의 더 긴 다양한 기간, rolling 검증, 설정 주변 조합/비용 스트레스, 계좌/LOT 불일치 복구와 장시간 paper 실행을 추가한다.

## 테스트와 재현

{tests_table}

기존 전체 suite의 남은 2개 실패는 그대로 기록하며 신규 ARCG 테스트와 구분한다. 격리 하네스 사용 여부를 숨기지 않는다. 비격리 로그의 운영 pause를 해제하지 않았고 외부 네트워크는 테스트 프로세스에서 차단했다. 실험 재현 명령·환경·해시·파일구성은 REPRODUCE.md와 manifest를 따른다.

## 공유 패키지 읽는 순서

이 문서 → 00 기존분석 → 01 명세 → 03 성과표/05 NAV/06 체결 → 04 후보검색/07 후보근거 → 08 위험 → 코드 patch/테스트 로그 순으로 확인한다. 모든 CSV는 실제 실행 산출물이며 수행하지 못한 체결·자료 검증을 가상의 성과로 채우지 않았다. 실거래 자격증명·원래 계좌 DB와 민감 baseline ZIP은 포함하지 않는다.
"""
    (out / "09_final_research_report.md").write_text(final, encoding="utf-8")

    reproduce = fr"""# 재현 안내

기준 사용자 날짜: 2026-10-22 Asia/Seoul. 실행 당시 브랜치 `{branch}`, HEAD `{commit}`. 정확한 버전·입력 해시와 파라미터는 data_manifest.json/backtest_config.json/선택고정 JSON을 우선한다. 표의 수익은 실제 계좌 실적이 아니다.

공유 패키지를 풀었을 때 연구자료 최상위에 data/, source/, legacy_strategy_settings.json이 있다. 로컬 저장소에서는 아래 첫 명령으로 같은 source 작업위치에 들어간다. 패키지를 푼 위치에서는 해당 source 디렉터리로 이동한다. 가상환경 자체는 포함하지 않으므로 준비된 Python 환경에서 실행한다.

```powershell
cd analysis_output/source
python -m pytest -p scripts.arcg_pytest_isolation tests -q --basetemp ../tmp/arcg_package_tests
python -m scripts.run_arcg_research --database ../data/research_daily.sqlite3 --index ../data/kospi_index.csv --output .. --jobs 3
python -m scripts.report_arcg_research --output ..
```

source에는 전체 실행 src와 신규 ARCG 관련 테스트/연구 스크립트가 들어 있다. 첫 pytest 명령은 패키지에 포함한 신규 테스트만 검증하며 원래 저장소 전체 938개 기존 테스트를 다시 실행하는 명령이 아니다. 전체 기존 suite는 제공한 로그로 검토하고 재실행하려면 원래 저장소의 전체 tests/scripts가 필요하다. 원래 저장소 루트의 전체 suite 명령은 아래와 같다.

```powershell
.\.venv\Scripts\python.exe -m pytest -p scripts.arcg_pytest_isolation tests -q --disable-warnings --continue-on-collection-errors --basetemp tmp/arcg_pytest_reproduce
```

압축을 별도 환경에 풀었으면 연구 source 디렉터리 구조를 유지하고 현재 환경의 Python 실행경로를 사용한다. 의존성/환경은 environment.json이 있으면 이를 확인한다. core/replay는 표준 라이브러리 중심이고 신규 테스트는 pytest, 그림 생성은 matplotlib가 필요하다. seed는 {config.get('seed', '미제공')}; 종목·날짜 정렬로 결정적 실행을 사용한다. 원장·cache·임시경로는 운영 DB와 분리한다.

후보탐색은 train/validation에서만 수행하고 선택을 저장한 뒤 test를 실행한다. 결과를 본 뒤 후보·기간을 바꾼 실행은 별도 실험으로 표시해야 한다. 기존에 본 역사자료인 한 prospective holdout이라고 주장하지 않는다. 백테스트는 수분 이상 걸릴 수 있고 실제 환경 성능에 따라 달라진다.

테스트 하네스의 기본 runtime 중립화는 테스트에만 적용한다. 운영 runtime_control.json을 수정하지 않는다. 위 전체 suite의 원래 남은 실패는 보고서/로그에 명시되어 있다. 사용자 기준선 소스에는 기존 미커밋 수정이 있고 신규 ARCG 커밋은 이를 포함하지 않으므로 신규 커밋만 clean checkout에 적용해도 완전한 재현이 되지 않는다. 연구 패키지의 전체 sanitized source snapshot과 설정이 정확한 재현물이다. 기존 수정이 정식 통합되기 전까지 이 의존성을 유지한다.

자동매매 main/server 실행이나 live config 적용 명령은 재현 절차에 포함하지 않는다. 보고서 생성기에 필수 CSV/JSON이 없으면 오류로 종료하며 임의 결과를 만들지 않는다.
"""
    (out / "REPRODUCE.md").write_text(reproduce, encoding="utf-8")
    render_figures(out, equities, optimization)
    return ["01_strategy_spec.md", "02_code_change_report.md", "07_parameter_recommendation.md", "08_risk_analysis.md", "09_final_research_report.md", "REPRODUCE.md"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "analysis_output")
    args = parser.parse_args()
    files = render(args.output.resolve())
    print("한국어 보고서와 PNG 생성: " + ", ".join(files))


if __name__ == "__main__":
    main()
