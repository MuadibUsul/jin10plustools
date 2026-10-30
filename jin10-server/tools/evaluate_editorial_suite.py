"""Offline, category-complete replay and anonymous review for fixed-brief copy.

Only saved payloads and local style assets are read. No provider, source fetch,
database, chart rendering, publication or pack approval is performed here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys

SERVER = Path(__file__).resolve().parents[1]
REPOSITORY = SERVER.parent
DEFAULT_CASES = SERVER / 'tests/fixtures/editorial_suite_cases.json'
CATEGORIES = {'volume', 'turnover', 'valuation', 'liquidity', 'oil_etf',
              'us_barometer', 'us_structure', 'token_cost', 'gpu_rental'}

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))
from evaluate_gpu_editorial import check_expectations, incorporate_reviews


def read_cases(path: Path) -> dict:
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('schema_version') != 1 or not isinstance(data.get('cases'), list):
        raise ValueError('Expected schema_version=1 case collection')
    ids = set()
    for case in data['cases']:
        if (not isinstance(case, dict) or not isinstance(case.get('id'), str)
                or case['id'] in ids or case.get('category') not in CATEGORIES
                or case.get('split') not in {'development', 'holdout'}
                or not isinstance(case.get('payload'), dict)
                or case.get('expect', {}).get('status') not in {'ok', 'rejected'}):
            raise ValueError('Invalid or duplicate editorial case')
        ids.add(case['id'])
    return data


def evaluate(data: dict, seed: str) -> tuple[dict, list[dict], list[dict]]:
    if str(SERVER) not in sys.path:
        sys.path.insert(0, str(SERVER))
    from app.briefs.editorial_suite import build_candidate, load_pack
    from app.briefs.policy import CATEGORIES as LABELS

    present = sorted({case['category'] for case in data['cases']})
    packs = {category: load_pack(category) for category in present}
    results, pairs = [], []
    for case in sorted(data['cases'], key=lambda item: item['id']):
        payload = deepcopy(case['payload'])
        baseline = {'title': str(payload.get('title', '')),
                    'body': str(payload.get('dynamic_body', ''))}
        candidate, error = None, None
        try:
            candidate = build_candidate(case['category'], payload, pack=packs[case['category']])
        except (ValueError, TypeError, ArithmeticError, KeyError) as exc:
            error = str(exc)
        failures = check_expectations(case, candidate, error)
        accepted = candidate is not None and candidate.get('status') == 'candidate'
        results.append({'case_id': case['id'], 'category': case['category'], 'split': case['split'],
                        'source_document': case.get('source_document'),
                        'source_sha256': case.get('source_sha256'),
                        'expected_status': case['expect']['status'],
                        'actual_status': 'candidate' if accepted else 'rejected',
                        'rejection_reason': error, 'assertion_failures': failures,
                        'scenario': candidate.get('scenario') if candidate else None,
                        'plan': candidate.get('plan') if candidate else None,
                        'facts_digest': candidate.get('facts_digest') if candidate else None,
                        'pack_digest': candidate.get('pack_digest') if candidate else None,
                        'hard_checks': candidate.get('hard_checks') if candidate else None})
        if accepted:
            order = ['baseline', 'candidate']
            random.Random(seed + ':' + case['id']).shuffle(order)
            copies = {'baseline': baseline, 'candidate': {'title': candidate['title'], 'body': candidate['body']}}
            pairs.append({'case_id': case['id'], 'category': case['category'], 'split': case['split'],
                          'A': copies[order[0]], 'B': copies[order[1]],
                          'mapping': {'A': order[0], 'B': order[1]}})
    random.Random(seed).shuffle(pairs)
    review, key = [], []
    for index, pair in enumerate(pairs, 1):
        review_id = f'样稿{index:03d}'
        review.append({'review_id': review_id, 'category': pair['category'],
                       'category_label': LABELS[pair['category']], 'A': pair['A'], 'B': pair['B'],
                       'human_review': {'preferred': None, 'A_readiness': None,
                                        'B_readiness': None, 'reason': None}})
        key.append({'review_id': review_id, 'case_id': pair['case_id'], 'category': pair['category'],
                    'split': pair['split'], 'mapping': pair['mapping']})
    per_category = {}
    for category in sorted(CATEGORIES):
        rows = [row for row in results if row['category'] == category]
        per_category[category] = {
            'label': LABELS[category], 'case_count': len(rows),
            'candidate_count': sum(row['actual_status'] == 'candidate' for row in rows),
            'expected_rejection_count': sum(row['expected_status'] == 'rejected' for row in rows),
            'actual_rejection_count': sum(row['actual_status'] == 'rejected' for row in rows),
            'unsafe_acceptance_count': sum(row['expected_status'] == 'rejected' and row['actual_status'] == 'candidate' for row in rows),
            'unexpected_rejection_count': sum(row['expected_status'] == 'ok' and row['actual_status'] == 'rejected' for row in rows),
            'assertion_failure_case_count': sum(bool(row['assertion_failures']) for row in rows),
            'human_direct_acceptance_rate': None,
        }
    pack_digests = {category: pack.get('digest') for category, pack in packs.items()}
    compound_digest = hashlib.sha256(json.dumps(pack_digests, sort_keys=True).encode()).hexdigest()
    summary = {'case_count': len(results), 'category_count': len(present),
               'review_pair_count': len(review),
               'development_count': sum(row['split'] == 'development' for row in results),
               'holdout_count': sum(row['split'] == 'holdout' for row in results),
               'expected_rejection_count': sum(row['expected_status'] == 'rejected' for row in results),
               'actual_rejection_count': sum(row['actual_status'] == 'rejected' for row in results),
               'assertion_failure_case_count': sum(bool(row['assertion_failures']) for row in results),
               'human_direct_acceptance_rate': None, 'style_accepted': None}
    report = {'schema_version': 1, 'provenance': data.get('provenance', 'unspecified'),
              'seed': seed, 'model_calls': 0, 'database_access': False,
              'pack_digest': compound_digest, 'packs': {category: {
                  'version': pack.get('version'), 'digest': pack.get('digest'), 'status': pack.get('status')}
                  for category, pack in packs.items()},
              'summary': summary, 'per_category': per_category, 'cases': results,
              'limitations': ['Synthetic cases are not real market observations.',
                              'Hard checks do not measure editorial acceptance.',
                              'US copy without a user corpus is derived from existing production rules.']}
    return report, review, key


def render_markdown(rows: list[dict], provenance: str) -> str:
    lines = ['# 全部固定快讯匿名对照稿', '', '资料性质：' + provenance, '',
             'A/B 位置已随机换位，不代表优劣。' + (
                 '工程合成场景不得作为真实行情稿发布。' if provenance == 'synthetic_not_market_data'
                 else '存档事实未重新请求来源核验，不代表当前行情。'),
             '请记录可直接采用／仅需微调／需要实质改写和具体修改原因。程序通过不等于风格通过。', '']
    for row in rows:
        lines.extend([f'## {row["review_id"]} · {row["category_label"]}', ''])
        for label in ('A', 'B'):
            lines.extend(['### ' + label, '', row[label]['title'], '', row[label]['body'], ''])
        lines.extend(['选择：____　A 可用程度：____　B 可用程度：____', '',
                      '修改原因：________________________________________________', ''])
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', default='fixed-editorial-20261030')
    parser.add_argument('--reviews', type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    protected = (SERVER / 'app', SERVER / 'tests', REPOSITORY / 'skills', REPOSITORY / 'deploy')
    if out in (SERVER, REPOSITORY) or any(out.is_relative_to(path.resolve()) for path in protected):
        parser.error('Choose an output directory outside source, tests, skills and deploy')
    if out.exists():
        parser.error('Output directory already exists; choose a new directory')
    data = read_cases(args.cases)
    report, review, key = evaluate(data, args.seed)
    if args.reviews:
        incorporate_reviews(args.reviews, report, review, key)
        by_id = {row['review_id']: row for row in review}
        mapping = {row['review_id']: row for row in key}
        by_category = {}
        for feedback in report['human_feedback']:
            review_id = feedback['review_id']
            row = mapping[review_id]
            human = feedback['human_review']
            label = next(label for label in ('A', 'B') if row['mapping'][label] == 'candidate')
            readiness = human.get(label + '_readiness')
            if readiness is not None:
                by_category.setdefault(row['category'], Counter())[readiness] += 1
        for category, counts in by_category.items():
            report['per_category'][category]['human_rated_count'] = sum(counts.values())
            report['per_category'][category]['human_direct_acceptance_rate'] = counts['direct'] / sum(counts.values())
    report['case_file_sha256'] = hashlib.sha256(args.cases.read_bytes()).hexdigest()
    out.mkdir(parents=True, exist_ok=False)
    outputs = {'evaluation.json': report,
               'blind-review.json': {'schema_version': 1, 'provenance': report['provenance'],
                                     'pack_digest': report['pack_digest'], 'cases': review},
               'review-key.json': {'schema_version': 1, 'seed': args.seed, 'cases': key}}
    for name, content in outputs.items():
        with (out / name).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(content, ensure_ascii=False, indent=2) + '\n')
    with (out / 'blind-review.md').open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(render_markdown(review, report['provenance']))
    print(json.dumps({'out': str(out), **report['summary']}, ensure_ascii=False))
    return 1 if report['summary']['assertion_failure_case_count'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
