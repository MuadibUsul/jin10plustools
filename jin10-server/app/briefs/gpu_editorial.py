"""Isolated GPU editorial pilot: select modules, never rewrite a fact span.

Shadow mode is offline and additive. The existing Skill product remains the
delivered edition until an editor has reviewed a versioned production pack.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from string import Formatter

from app.config import get_settings

from .skill_ai import asset_dir

CARDS = ('b200', 'h100', 'a100')
SCENARIOS = frozenset({
    'peak_tie', 'peak_above', 'near_divergence_up', 'k_divergence',
    'divergence_up', 'divergence_down', 'older_not_following',
    'near_peak_up', 'near_peak_down', 'near_peak_flat',
    'all_up', 'all_down', 'all_flat', 'mixed', 'missing_week',
})
_ROLES = {'headline': 'headlines', 'lead': 'leads',
          'comparison': 'comparisons', 'closing': 'closings'}
_FIELDS = {'leads': {'data_day', 'b200_fact', 'peak_tail'},
           'comparisons': {'h100_fact', 'a100_fact'}}
_FORBIDDEN = ('新高', '新低', '紧缺', '需求迁移', '需求旺盛', '定价权',
              '烧钱', '投资建议', '点击', '扫码', '注：', 'http://', 'https://')


def pack_path():
    return asset_dir('gpu_rental') / 'production' / 'style-pack.json'


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def load_pack() -> dict:
    return _checked_pack(json.loads(pack_path().read_text(encoding='utf-8')))


def _checked_pack(source: dict) -> dict:
    if not isinstance(source, dict):
        raise ValueError('style_pack_invalid')
    pack = deepcopy(source)
    if (pack.get('schema_version') != 1 or pack.get('category') != 'gpu_rental'
            or not pack.get('version') or not pack.get('pack_id')
            or pack.get('status') not in {'draft_pending_editor_review', 'approved'}):
        raise ValueError('style_pack_invalid')
    modules = pack.get('modules') or {}
    for role in _ROLES.values():
        rows = modules.get(role)
        if not isinstance(rows, list) or not rows:
            raise ValueError('style_pack_invalid')
        ids = set()
        for item in rows:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] in ids:
                raise ValueError('style_pack_invalid')
            ids.add(item['id'])
            if role in _FIELDS:
                template = item.get('template')
                if not isinstance(template, str):
                    raise ValueError('style_pack_invalid')
                parsed = list(Formatter().parse(template))
                fields = [name for _, name, _, _ in parsed if name is not None]
                if (set(fields) != _FIELDS[role] or len(fields) != len(_FIELDS[role])
                        or any(spec or conversion for _, _, spec, conversion in parsed)):
                    raise ValueError('style_pack_invalid')
            elif (not isinstance(item.get('text'), str)
                  or not isinstance(item.get('scenarios'), list)
                  or not item['scenarios'] or not set(item['scenarios']) <= SCENARIOS):
                raise ValueError('style_pack_invalid')
    for scenario in SCENARIOS:
        if any(not any(scenario in row['scenarios'] for row in modules[role])
               for role in ('headlines', 'closings')):
            raise ValueError('style_pack_invalid')
    pack.pop('digest', None)
    pack['digest'] = _digest(pack)
    return pack


def _day(raw, reason='invalid_date') -> date:
    try:
        parsed = date.fromisoformat(raw)
        if parsed.isoformat() != raw:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise ValueError(reason) from None


def _number(raw, reason: str, *, optional=False) -> Decimal | None:
    if optional and raw in (None, ''):
        return None
    if isinstance(raw, bool) or not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', str(raw).strip()):
        raise ValueError(reason)
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        raise ValueError(reason) from None
    if not value.is_finite():
        raise ValueError(reason)
    return value


def _origins(payload: dict, changes: dict, previous: date | None) -> dict:
    facts = payload['facts']
    source = facts.get('weekly_source') or 'calculated_from_seven_day_baseline'
    if source == 'public_visible_history_mixed':
        supplied = facts.get('week_change_sources') or {}
        origins = {key: supplied.get(key) for key in CARDS}
        for item in (payload.get('process_details') or {}).get('sources', []):
            label = item.get('label', '')
            for key in CARDS:
                if label.startswith(f'{key.upper()} · {payload["data_date"]} · '):
                    if label.endswith('网页历史价格表'):
                        origin = 'visible_history'
                    elif label.endswith('公开卡片'):
                        origin = 'card'
                    else:
                        continue
                    if origins[key] is not None and origins[key] != origin:
                        raise ValueError('source_provenance_missing')
                    origins[key] = origin
        if any(value not in {'visible_history', 'card'} for value in origins.values()):
            raise ValueError('source_provenance_missing')
        if 'visible_history' not in origins.values():
            raise ValueError('source_provenance_missing')
        return origins
    if source == 'public_visible_summary':
        return dict.fromkeys(CARDS, 'card')
    if source == 'manual_entry':
        return dict.fromkeys(CARDS, 'manual')
    if source != 'calculated_from_seven_day_baseline':
        raise ValueError('source_provenance_missing')
    if previous is None and any(value is not None for value in changes.values()):
        raise ValueError('invalid_baseline')
    return dict.fromkeys(CARDS, 'calculated')


def _facts(payload: dict) -> dict:
    actual = _day(payload.get('data_date'))
    facts = payload.get('facts')
    if not isinstance(facts, dict):
        raise ValueError('invalid_price')
    prices = {key: _number(facts.get(key), 'invalid_price') for key in CARDS}
    if any(value <= 0 for value in prices.values()):
        raise ValueError('invalid_price')
    changes = {key: _number(facts.get(key + '_change_pct'), 'invalid_change', optional=True)
               for key in CARDS}
    if any(value is not None and value <= -100 for value in changes.values()):
        raise ValueError('invalid_change')
    previous_raw = facts.get('previous_date')
    previous = _day(previous_raw, 'invalid_baseline') if previous_raw else None
    if previous and (actual - previous).days != 7:
        raise ValueError('invalid_baseline')
    relation = facts.get('b200_peak_relation') or ''
    peak_raw = facts.get('b200_peak')
    peak = _number(peak_raw, 'peak_mismatch', optional=True)
    peak_day = _day(facts['b200_peak_date'], 'peak_mismatch') if facts.get('b200_peak_date') else None
    if peak_day and peak_day >= actual:
        raise ValueError('peak_mismatch')
    if relation:
        if peak is None or peak <= 0 or peak_day is None or relation not in {'above', 'tie', 'below'}:
            raise ValueError('peak_mismatch')
        expected = 'above' if prices['b200'] > peak else 'below' if prices['b200'] < peak else 'tie'
        if relation != expected:
            raise ValueError('peak_mismatch')
    elif peak is not None:
        raise ValueError('peak_mismatch')
    gap = _number(facts.get('b200_gap_pct'), 'peak_mismatch', optional=True)
    if gap is not None and (relation != 'below' or not 0 <= gap < 100):
        raise ValueError('peak_mismatch')
    if gap is not None and abs((1 - prices['b200'] / peak) * 100 - gap) > Decimal('0.02'):
        raise ValueError('peak_mismatch')
    origins = _origins(payload, changes, previous)
    return {'actual': actual, 'prices': prices, 'changes': changes, 'previous': previous,
            'relation': relation, 'peak': peak, 'peak_day': peak_day, 'gap': gap,
            'origins': origins, 'raw': facts}


def _scenario(facts: dict) -> str:
    changes, relation = facts['changes'], facts['relation']
    if any(value is None for value in changes.values()):
        return 'missing_week'
    if relation == 'tie':
        return 'peak_tie'
    if relation == 'above':
        return 'peak_above'
    b, h, a = (changes[key] for key in CARDS)
    near = relation == 'below' and facts['gap'] is not None and facts['gap'] <= 5
    if near and b > 0 > a:
        return 'near_divergence_up'
    if b > 0 > a and h <= 0:
        return 'k_divergence'
    if b > 0 > a:
        return 'divergence_up'
    if a > 0 > b:
        return 'divergence_down'
    if b > 0 and h <= 0 and a <= 0:
        return 'older_not_following'
    if near:
        return 'near_peak_up' if b > 0 else 'near_peak_down' if b < 0 else 'near_peak_flat'
    if all(value > 0 for value in changes.values()):
        return 'all_up'
    if all(value < 0 for value in changes.values()):
        return 'all_down'
    if all(value == 0 for value in changes.values()):
        return 'all_flat'
    return 'mixed'


def _month_day(value: date) -> str:
    return f'{value.month}月{value.day}日'


def _move(raw, value: Decimal | None) -> str:
    if value is None:
        return '七日变化基准缺失'
    if value == 0:
        # Preserve the supplied precision, including 0.0 and -0.00.
        return '持平（' + str(raw).strip().lstrip('+-') + '%）'
    direction = '微涨' if 0 < value < 1 else '上涨' if value > 0 else '下跌'
    return direction + str(raw).strip().lstrip('+-') + '%'


def _span(key: str, facts: dict) -> str:
    raw, value = facts['raw'], facts['changes'][key]
    if key == 'b200':
        verb = '升至' if value is not None and value > 0 else '回落至' if value is not None and value < 0 else '报'
        name = '英伟达B200租赁价格' + verb
    else:
        name = key.upper() + '报'
    text = f'{name}{raw[key]}美元/GPU每小时'
    if value is None:
        return text + '，七日变化基准缺失'
    move = _move(raw[key + '_change_pct'], value)
    origin = facts['origins'][key]
    if origin == 'visible_history':
        return text + '，按官网历史价格计算，七日约' + move
    if origin == 'card':
        return text + '，一周' + move + '（官网展示值）'
    if origin == 'manual':
        return text + '，一周' + move + '（人工录入）'
    return text + f'，较{_month_day(facts["previous"])}' + move


def _peak_span(facts: dict) -> str:
    relation = facts['relation']
    if not relation:
        return ''
    peak = str(facts['raw']['b200_peak']) + '美元/GPU每小时'
    located = f'（{_month_day(facts["peak_day"])}）' if facts['peak_day'] else ''
    if relation == 'tie':
        return f'与此前已收录高点{peak}{located}持平'
    if relation == 'above':
        return f'高于此前已收录高点{peak}{located}'
    if facts['gap'] is not None:
        return f'较此前已收录高点{peak}{located}低{facts["raw"]["b200_gap_pct"]}%'
    return f'低于此前已收录高点{peak}{located}'


def _options(pack: dict, scenario: str) -> dict:
    return {role: [item for item in pack['modules'][collection]
                   if 'scenarios' not in item or scenario in item['scenarios']]
            for role, collection in _ROLES.items()}


def _plan(options: dict, selection: dict | None) -> dict:
    plan = selection if selection is not None else {role: rows[0]['id'] for role, rows in options.items()}
    if not isinstance(plan, dict) or set(plan) != set(_ROLES):
        raise ValueError('invalid_plan')
    if any(not isinstance(plan[role], str) or plan[role] not in {row['id'] for row in rows}
           for role, rows in options.items()):
        raise ValueError('invalid_plan')
    return dict(plan)


def build_candidate(payload: dict, *, pack=None, selection=None) -> dict:
    """Pure, replayable candidate; no network, mutation, chart or database access."""
    pack = load_pack() if pack is None else _checked_pack(pack)
    facts = _facts(payload)
    scenario = _scenario(facts)
    options = _options(pack, scenario)
    plan = _plan(options, selection)
    chosen = {role: next(row for row in rows if row['id'] == plan[role])
              for role, rows in options.items()}
    peak = _peak_span(facts)
    slots = {'data_day': _month_day(facts['actual']), 'b200_fact': _span('b200', facts),
             'peak_tail': '，' + peak if peak else '',
             'h100_fact': _span('h100', facts), 'a100_fact': _span('a100', facts)}
    title = chosen['headline']['text']
    body = (chosen['lead']['template'].format_map(slots)
            + chosen['comparison']['template'].format_map(slots) + chosen['closing']['text'])
    if (not title.startswith('【AI景气度指标】') or '\n' in title + body
            or len(title) > 100 or len(body) > 1200
            or any(phrase in title + body for phrase in _FORBIDDEN)):
        raise ValueError('invalid_plan')
    return {
        'status': 'candidate', 'title': title, 'body': body, 'scenario': scenario, 'plan': plan,
        'pack_id': pack['pack_id'], 'pack_version': pack['version'], 'pack_digest': pack['digest'],
        'review_status': pack['status'],
        'module_snapshot': deepcopy(chosen),
        'facts_digest': _digest({'data_date': payload['data_date'], 'facts': payload['facts'],
                                 'origins': facts['origins']}),
        'example_ids': [row['id'] for row in pack.get('examples', [])
                        if row.get('scenario') == scenario][:3],
        'bindings': {key: {'price': str(facts['raw'][key]),
                          'change_pct': str(facts['raw'].get(key + '_change_pct') or ''),
                          'week_source': facts['origins'][key]} for key in CARDS},
        'hard_checks': {'facts': True, 'structure': True},
        'style_review': 'pending_editor_review',
    }


def selection_context(payload: dict, pack: dict) -> dict:
    facts = _facts(payload)
    scenario = _scenario(facts)
    return {'scenario': scenario, 'options': _options(pack, scenario),
            'examples': [row for row in pack.get('examples', [])
                         if row.get('scenario') == scenario][:3]}


def apply_gpu_editorial(payload: dict, team_settings: dict | None, *, mode=None) -> dict:
    """Optional GPU-only handoff. Any failure preserves the complete legacy copy."""
    baseline = {'title': payload.get('title', ''), 'body': payload.get('dynamic_body', '')}
    trace = {'mode': mode, 'baseline': baseline}
    try:
        mode = mode or get_settings().briefs_gpu_editorial_mode
        if mode == 'off':
            return payload
        trace['mode'] = mode
        payload['editorial'] = trace
        saved = ((team_settings or {}).get('skills') or {}).get('gpu_rental') or {}
        reason = ('custom_body_template' if payload.get('body_template_custom') else
                  'skill_disabled' if saved.get('enabled') is False else
                  'custom_instructions' if saved.get('instructions') else None)
        if reason:
            trace.update(status='skipped', reason=reason)
            return payload
        if mode not in {'shadow', 'active'}:
            raise ValueError('invalid_mode')
        pack = load_pack()
        reviewed = (pack['status'] == 'approved'
                    and bool((pack.get('review') or {}).get('approved_by'))
                    and bool((pack.get('review') or {}).get('reviewed_at')))
        if mode == 'active' and not reviewed:
            trace.update(mode='shadow', requested_mode='active', reason='pack_not_reviewed')
        candidate = build_candidate(payload, pack=pack)
        if trace['mode'] == 'active':
            from .gpu_editorial_ai import select_plan
            result = select_plan(team_settings, selection_context(payload, pack))
            trace['selector'] = {key: value for key, value in result.items() if key != 'plan'}
            if result.get('status') == 'ok':
                candidate = build_candidate(payload, pack=pack, selection=result['plan'])
            trace['selection_status'] = ('model_selected' if result.get('status') == 'ok'
                                         else 'deterministic_fallback')
        trace.update(status='shadow' if trace['mode'] == 'shadow' else 'active', candidate=candidate)
        if trace['mode'] == 'active':
            payload['title'], payload['dynamic_body'] = candidate['title'], candidate['body']
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        trace.update(status='fallback', reason=str(exc) if isinstance(exc, ValueError)
                     else 'editorial_unavailable')
    except Exception:
        # A pilot or optional provider failure must never fail a valid brief job.
        trace.update(status='fallback', reason='editorial_unavailable')
    payload['editorial'] = trace
    return payload
