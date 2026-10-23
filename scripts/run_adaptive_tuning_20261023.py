"""Reproducible research only: frozen market data -> numeric parameter study.

No broker clients, production database or operating configuration writers.
Candidate selection stops at September 18; final evaluation is a separate phase.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from dataclasses import asdict
from datetime import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from kis_msj.config import load_config
from research.adaptive_tuning_20261023.engine import run_quote_backtest
from research.adaptive_tuning_20261023.metrics import summarize_path
from research.adaptive_tuning_20261023.parameters import BASELINE, Parameters, RANGES
from research.adaptive_tuning_20261023.engine import execution_price

OUT = ROOT / 'reports/adaptive_tuning_20261023'
DATA = OUT / 'data'
SEED = 20261023
CAPITAL = 150_000_000  # Explicit hypothetical all-cash account, NOT actual historical capital.
TRAIN_END, VALID_START, VALID_END = '2026-08-21', '2026-08-24', '2026-09-18'
TEST_START, TEST_END = '2026-09-21', '2026-10-16'
_RECORDS, _FEATURES, _CONFIG = None, None, None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def prepare():
    manifest = OUT / 'experiment_plan.json'
    if manifest.exists():
        plan = read_json(manifest)
        if 'focused_search' not in plan:
            plan['focused_search'] = 'After OAT training runs, 12 additional uniform samples vary the four parameters with largest training return-span + 0.5*MDD-span; other knobs baseline. Seed 20261024.'
            write_json(manifest, plan)
        return read_json(OUT / 'candidates.json')
    candidates, seen = [], set()

    def add(params, stage, varied=None):
        values = params.to_dict()
        key = json.dumps(values, sort_keys=True)
        if key in seen:
            return
        seen.add(key)
        candidates.append(dict(id=f'C{len(candidates):03d}', stage=stage, varied=varied, parameters=values))

    add(BASELINE, 'baseline')
    for name, (lo, hi) in RANGES.items():
        for value in sorted({lo, hi, (lo + hi) / 2}):
            if name == 'reserve_lots':
                value = int(value)
            add(BASELINE.changed(**{name: value}), 'sensitivity', name)
    pairs = [('buy_gap_pct', 'sell_profit_pct'), ('buy_gap_pct', 'trend_step'),
             ('volatility_exponent', 'buy_gap_pct'), ('decay_pp', 'sell_profit_pct'),
             ('rebuy_gap_pct', 'cash_buffer_ratio'), ('rebuy_gap_pct', 'reserve_lots')]
    for first, second in pairs:
        for a in RANGES[first]:
            for b in RANGES[second]:
                add(BASELINE.changed(**{first: a, second: b}), 'interaction', f'{first} x {second}')
    rng = random.Random(SEED)
    for _ in range(24):
        values = {name: (rng.randint(lo, hi) if name == 'reserve_lots' else round(rng.uniform(lo, hi), 7))
                  for name, (lo, hi) in RANGES.items()}
        add(Parameters(**values), 'random')
    write_json(OUT / 'candidates.json', candidates)
    plan = dict(created=datetime.now().isoformat(), seed=SEED, capital=CAPITAL,
        initial_state='Hypothetical all-cash; actual historical initial capital/flows/LOT states unavailable.',
        baseline=BASELINE.to_dict(), ranges=RANGES, candidate_count=len(candidates),
        split=dict(train_end=TRAIN_END, validation_start=VALID_START, validation_end=VALID_END,
                   test_start=TEST_START, test_end=TEST_END),
        selection='Carry portfolio across chronological boundaries; choose on train/validation only. Retrospective holdout, not pristine unseen market.',
        role_rules={
            'profit': 'Maximum validation NAV return; tie: lower validation absolute MDD, then ID.',
            'balanced': '0.2*train return + 0.8*validation return - 0.5*abs(validation MDD) + 0.25*minimum available cash / capital.',
            'safety': 'Minimum validation abs(MDD) + 2*max(0,0.1-minimum available cash/capital), tie higher validation return.'},
        pareto='Validation return maximized, abs(MDD) minimized, minimum available cash maximized; no test-based selection.',
        quote_resolution_search='30m bucket last observation, exposed only at bucket end',
        default_execution=dict(timeout_seconds=30, slippage_bps=5, buy_fee_bps=1.5, sell_fee_tax_bps=21,
                               max_signal_age_seconds=120, official_execution_grid=True),
        excluded_codes={'417310': 'Unverified raw-price discontinuity/entitlement; removed from ALL experiments.'},
        limitations='Current selected universe/flags, no actual book depth/halt/price sampling/settlement/entitlements; results conditional, no operational parameter changes.')
    write_json(manifest, plan)
    return candidates


def freeze_research_version(resolution):
    files = [ROOT / 'research/adaptive_tuning_20261023' / name for name in ('engine.py', 'metrics.py', 'parameters.py')]
    files += [Path(__file__).resolve(), DATA / 'config_public_snapshot.json', DATA / 'daily_features.json',
              OUT / 'baseline_source_manifest.json']
    version = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    path = OUT / 'research_version.json'
    if path.exists():
        previous = read_json(path)
        if previous['files'] != version:
            raise ValueError('Research source/data changed after run freeze; archive previous run before rerunning.')
    else:
        write_json(path, dict(created=datetime.now().isoformat(), files=version))
    quote = DATA / f'quotes_{resolution}m.csv.gz'
    return dict(research=hashlib.sha256(json.dumps(version, sort_keys=True).encode()).hexdigest(),
                quotes=hashlib.sha256(quote.read_bytes()).hexdigest())


def focused_candidates(candidates):
    if any(c['stage'] == 'focused' for c in candidates):
        return candidates
    spans = {}
    baseline = read_json(OUT / 'screening/30m/C000.json')['train']
    for name in RANGES:
        metrics = [baseline] + [read_json(OUT / 'screening/30m' / f'{c["id"]}.json')['train']
            for c in candidates if c['stage'] == 'sensitivity' and c['varied'] == name]
        returns = [m['total_return_pct'] for m in metrics]
        risks = [m['max_drawdown_pct'] for m in metrics]
        spans[name] = max(returns)-min(returns) + .5*(max(risks)-min(risks))
    high = sorted(spans, key=lambda k: (-spans[k], k))[:4]
    rng = random.Random(SEED + 1)
    for _ in range(12):
        values = {}
        for name in high:
            lo, hi = RANGES[name]
            values[name] = rng.randint(lo, hi) if name == 'reserve_lots' else round(rng.uniform(lo, hi), 7)
        candidates.append(dict(id=f'C{len(candidates):03d}', stage='focused', varied=','.join(high),
                               parameters=BASELINE.changed(**values).to_dict()))
    write_json(OUT / 'focused_sampling_after_train.json', dict(created=datetime.now().isoformat(),
        training_end=TRAIN_END, sensitivity_scores=spans, selected_parameters=high, seed=SEED+1,
        rule='training return span + 0.5*MDD span; no validation/test metrics used to focus samples.'))
    write_json(OUT / 'candidates.json', candidates)
    return candidates


def init_worker(resolution):
    global _RECORDS, _FEATURES, _CONFIG
    _CONFIG = load_config(DATA / 'config_public_snapshot.json')
    _FEATURES = read_json(DATA / 'daily_features.json')
    with gzip.open(DATA / f'quotes_{resolution}m.csv.gz', 'rt', encoding='utf-8', newline='') as stream:
        _RECORDS = list(csv.DictReader(stream))


def segment(result, start=None, end=None, capital=CAPITAL):
    rows = [r for r in result.equity if (not start or r['date'] >= start) and (not end or r['date'] <= end)]
    if not rows:
        return None
    previous = [r for r in result.equity if r['date'] < rows[0]['date']]
    previous = previous[-1] if previous else None
    trades = [t for t in result.trades if rows[0]['date'] <= t['datetime'][:10] <= rows[-1]['date']]
    diag = {}
    # Daily counters are cumulative where present. Never use whole-path test
    # statistics to label a training or validation segment.
    for field in ('counts', 'buy_blocks'):
        key = 'cumulative_' + field
        current = rows[-1].get(key, {})
        before = previous.get(key, {}) if previous else {}
        if current:
            diag[field] = {k: v - before.get(k, 0) for k, v in current.items()}
    for key, field in [('cumulative_reentry_orders', 'reentry_order_signals'),
                       ('cumulative_reentry_fills', 'reentry_fills')]:
        if key in rows[-1]:
            diag[field] = rows[-1][key] - (previous.get(key, 0) if previous else 0)
    diag['reentry_attempts'] = diag.get('reentry_order_signals', 0)
    daily_mins = [r['minimum_available_cash_intraday_day'] for r in rows if 'minimum_available_cash_intraday_day' in r]
    if daily_mins:
        diag['minimum_available_cash_intraday'] = min(daily_mins)
    diag['max_open_lots_intraday'] = max(r.get('maximum_open_lots_intraday_day', r['open_lots']) for r in rows)
    diag['closed_lot_holding_days'] = [t['holding_days'] for t in trades if t.get('closed_lot', t.get('lot_fully_closed', False))]
    m = summarize_path(rows, initial_equity=previous['equity'] if previous else capital,
        initial_cash=previous['cash'] if previous else capital,
        initial_unrealized=previous['unrealized_pnl'] if previous else 0,
        initial_realized=previous['realized_pnl'] if previous else 0,
        initial_date=previous['date'] if previous else rows[0]['date'], trades=trades, diagnostics=diag)
    m['boundary'] = 'Portfolio, LOT ages, realized/unrealized P&L and restrictions carried from preceding segment.'
    return m


def evaluate(job):
    candidate, resolution, phase, additions = job
    start_time = time.perf_counter()
    options = dict(capital=CAPITAL, end=VALID_END if phase == 'screening' else TEST_END,
        execution_timeout_seconds=30, max_signal_age_seconds=120, slippage_bps=5,
        buy_fee_bps=1.5, sell_fee_tax_bps=21, official_execution_grid=True,
        exclude_codes=['417310'], completed_closes=_FEATURES.get('completed_closes', {}), mark_daily_closes=True)
    options.update(additions)
    volatility = _FEATURES['volatility']
    if options.get('volatility_stress'):
        scale = options['volatility_stress']
        volatility = {day: {code: [v[0]*scale, v[1]*scale] for code, v in codes.items()}
                      for day, codes in volatility.items()}
    records = _RECORDS
    if options.get('synthetic_decline'):
        beginning, ending = datetime(2026, 5, 27), datetime(2026, 10, 17)
        span = (ending-beginning).total_seconds()
        def factor(timestamp):
            fraction = max(0., min(1., (datetime.fromisoformat(timestamp)-beginning).total_seconds()/span))
            return math.exp(math.log(.55)*fraction)
        def decline_records():
            for row in _RECORDS:
                changed = dict(row)
                changed['price'] = execution_price(float(row['price'])*factor(row['timestamp']), 0, False, True)
                if row.get('next_after_exposure_timestamp') and row.get('next_after_exposure_price'):
                    changed['next_after_exposure_price'] = execution_price(float(row['next_after_exposure_price'])*factor(row['next_after_exposure_timestamp']), 0, False, True)
                yield changed
        records = decline_records()
        options['completed_closes'] = {day: {code: execution_price(close*factor(day+'T15:30:00'),0,False,True)
            for code, close in codes.items()} for day, codes in _FEATURES.get('completed_closes', {}).items()}
    result = run_quote_backtest(_CONFIG, records, Parameters(**candidate['parameters']), options,
                                volatility=volatility)
    full = summarize_path(result.equity, initial_equity=options['capital'], initial_cash=options['capital'],
                          trades=result.trades, diagnostics=result.diagnostics)
    # Preserve precise intraday minima; primary metrics include the initial NAV.
    full['minimum_available_cash'] = result.diagnostics['minimum_available_cash_intraday']
    full['maximum_simultaneous_open_lots'] = result.diagnostics['max_open_lots_intraday']
    payload = dict(candidate=candidate, resolution_minutes=resolution, phase=phase,
        summary=full, train=segment(result, end=TRAIN_END, capital=options['capital']),
        validation=segment(result, VALID_START, VALID_END, capital=options['capital']),
        test=segment(result, TEST_START, TEST_END, capital=options['capital']) if phase != 'screening' else None,
        equity=result.equity, trades=result.trades, diagnostics=result.diagnostics,
        elapsed_seconds=time.perf_counter()-start_time, version=freeze_research_version(resolution))
    payload['diagnostics']['assumptions'].pop('completed_closes', None)
    name = candidate['id'] + (f'_{additions["scenario"]}' if 'scenario' in additions else '')
    target = OUT / phase / f'{resolution}m' / f'{name}.json'
    write_json(target, payload)
    return dict(id=name, seconds=payload['elapsed_seconds'], total_return_pct=full['total_return_pct'],
                trades=full['total_trades'], path=str(target.relative_to(ROOT)))


def run_jobs(jobs, workers):
    if not jobs:
        return
    resolution = jobs[0][1]
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker, initargs=(resolution,)) as pool:
        futures = {pool.submit(evaluate, job): job for job in jobs}
        for done, future in enumerate(as_completed(futures), 1):
            print(json.dumps(dict(done=done, total=len(jobs), **future.result())), flush=True)


def select(candidates):
    if (OUT / 'selection_before_test.json').exists():
        return read_json(OUT / 'selection_before_test.json')
    rows = [read_json(OUT / 'screening/30m' / f'{c["id"]}.json') for c in candidates]
    def ret(x): return x['validation']['total_return_pct']
    def dd(x): return abs(x['validation']['max_drawdown_pct'])
    def cash(x): return x['validation']['minimum_available_cash'] / CAPITAL
    profit = max(rows, key=lambda x: (ret(x), -dd(x), x['candidate']['id']))
    balanced = max(rows, key=lambda x: (.2*x['train']['total_return_pct'] + .8*ret(x) -.5*dd(x)+.25*cash(x), x['candidate']['id']))
    safety = min(rows, key=lambda x: (dd(x)+2*max(0, .1-cash(x)), -ret(x), x['candidate']['id']))
    pareto = []
    for row in rows:
        dominated = any(ret(other) >= ret(row) and dd(other) <= dd(row) and cash(other) >= cash(row)
            and (ret(other) > ret(row) or dd(other) < dd(row) or cash(other) > cash(row)) for other in rows)
        if not dominated:
            pareto.append(row['candidate']['id'])
    roles = {'baseline': 'C000', 'profit': profit['candidate']['id'],
             'balanced': balanced['candidate']['id'], 'safety': safety['candidate']['id']}
    selected = list(dict.fromkeys(roles.values()))
    for identifier in pareto:
        if identifier not in selected and len(selected) < 5:
            selected.append(identifier)
    for row in sorted(rows, key=lambda x: (-ret(x), dd(x), x['candidate']['id'])):
        identifier = row['candidate']['id']
        if len(selected) >= 4:
            break
        if identifier not in selected:
            selected.append(identifier)
    selection = dict(created=datetime.now().isoformat(), roles=roles, selected_ids=selected,
        pareto_ids=pareto, selection_period_end=VALID_END, test_not_evaluated=True,
        warning='Exploratory conditional candidates, not validated optimal operating settings.')
    write_json(OUT / 'selection_before_test.json', selection)
    return selection


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['prepare', 'screen', 'select', 'final', 'stress', 'benchmark'])
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--resolution', type=int, default=30)
    args = parser.parse_args()
    candidates = prepare()
    if args.phase == 'prepare':
        print(json.dumps({'candidates': len(candidates), 'plan': str(OUT / 'experiment_plan.json')}))
    elif args.phase in ('screen', 'benchmark'):
        freeze_research_version(30)
        chosen = candidates[:1] if args.phase == 'benchmark' else [c for c in candidates if c['stage'] in ('baseline', 'sensitivity')]
        jobs = [(c, 30, 'screening', {}) for c in chosen
                if not (OUT / 'screening/30m' / f'{c["id"]}.json').exists()]
        run_jobs(jobs, args.workers)
        if args.phase == 'screen':
            candidates = focused_candidates(candidates)
            run_jobs([(c, 30, 'screening', {}) for c in candidates
                if not (OUT / 'screening/30m' / f'{c["id"]}.json').exists()], args.workers)
    elif args.phase == 'select':
        print(json.dumps(select(candidates)))
    else:
        selection = read_json(OUT / 'selection_before_test.json')
        chosen = [c for c in candidates if c['id'] in selection['selected_ids']]
        if args.phase == 'final':
            run_jobs([(c, args.resolution, 'final', {}) for c in chosen
                if not (OUT / 'final' / f'{args.resolution}m' / f'{c["id"]}.json').exists()], args.workers)
        else:
            scenarios = [dict(scenario='fee_2x', buy_fee_bps=3, sell_fee_tax_bps=42),
                dict(scenario='slippage_15bps', slippage_bps=15),
                dict(scenario='cash_30m', capital=30_000_000),
                dict(scenario='strict_quote_age_30s', max_signal_age_seconds=30),
                dict(scenario='volatility_2x', volatility_stress=2),
                dict(scenario='synthetic_decline_45pct_vol2x', synthetic_decline=True, volatility_stress=2)]
            # A volatility stress changes the observable input, not policy.
            run_jobs([(c, args.resolution, 'stress', s) for c in chosen for s in scenarios
                if not (OUT / 'stress' / f'{args.resolution}m' / f'{c["id"]}_{s["scenario"]}.json').exists()], args.workers)


if __name__ == '__main__':
    main()
