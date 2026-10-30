"""Versioned, bounded editorial candidates for all nine fixed-brief categories.

Adapters own factual semantics. This module only composes intact fact spans;
models select allowed module IDs. Shadow mode never changes delivered copy.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from string import Formatter

from app.config import get_settings

from .policy import CATEGORIES
from .skill_ai import asset_dir

SUPPORTED_CATEGORIES = frozenset(CATEGORIES)
_ROLES = {'headline': 'headlines', 'lead': 'leads',
          'comparison': 'comparisons', 'closing': 'closings'}
_FORBIDDEN = ('投资建议', '抄底', '买入机会', '结构性紧缺', '需求迁移',
              '需求旺盛', '点击', '扫码', '注：', 'http://', 'https://')


def pack_path(category: str) -> Path:
    if category not in SUPPORTED_CATEGORIES:
        raise ValueError('unsupported_category')
    if category in ('us_barometer', 'us_structure'):
        return Path(__file__).parent / 'editorial_packs' / (category + '.json')
    return asset_dir(category) / 'production' / 'style-pack.json'


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


def _checked_pack(category: str, source: dict) -> dict:
    if not isinstance(source, dict):
        raise ValueError('style_pack_invalid')
    pack = deepcopy(source)
    if (pack.get('schema_version') != 1 or pack.get('category') != category
            or not pack.get('pack_id') or not pack.get('version')
            or pack.get('status') not in {'draft_pending_editor_review', 'approved'}):
        raise ValueError('style_pack_invalid')
    scenarios = {row['id'] for row in pack.get('scenarios', [])}
    if not scenarios:
        raise ValueError('style_pack_invalid')
    slots = pack.get('slot_roles') or {}
    if set(slots) != {'lead', 'comparison'} or any(
            not isinstance(fields, list) or any(not isinstance(field, str) for field in fields)
            for fields in slots.values()):
        raise ValueError('style_pack_invalid')
    all_slots = slots['lead'] + slots['comparison']
    if len(all_slots) != len(set(all_slots)):
        raise ValueError('style_pack_invalid')
    modules = pack.get('modules') or {}
    for role, collection in _ROLES.items():
        rows = modules.get(collection)
        if not isinstance(rows, list) or not rows:
            raise ValueError('style_pack_invalid')
        ids = set()
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                    or row['id'] in ids):
                raise ValueError('style_pack_invalid')
            ids.add(row['id'])
            if 'scenarios' in row and (not isinstance(row['scenarios'], list)
                                       or not row['scenarios']
                                       or not set(row['scenarios']) <= scenarios):
                raise ValueError('style_pack_invalid')
            if role in slots:
                if not isinstance(row.get('template'), str):
                    raise ValueError('style_pack_invalid')
                parsed = list(Formatter().parse(row['template']))
                fields = [field for _, field, _, _ in parsed if field is not None]
                if (set(fields) != set(slots[role]) or len(fields) != len(slots[role])
                        or any(spec or conversion for _, _, spec, conversion in parsed)):
                    raise ValueError('style_pack_invalid')
            elif (not isinstance(row.get('text'), str)
                  or not isinstance(row.get('scenarios'), list)
                  or not row['scenarios'] or not set(row['scenarios']) <= scenarios):
                raise ValueError('style_pack_invalid')
    for scenario in scenarios:
        if any(not any('scenarios' not in row or scenario in row['scenarios']
                       for row in modules[collection]) for collection in _ROLES.values()):
            raise ValueError('style_pack_invalid')
    pack.pop('digest', None)
    pack['digest'] = _digest(pack)
    return pack


def load_pack(category: str) -> dict:
    if category == 'gpu_rental':
        from .gpu_editorial import load_pack as load_gpu
        return load_gpu()
    return _checked_pack(category, json.loads(pack_path(category).read_text(encoding='utf-8')))


def _context(category: str, payload: dict) -> dict:
    if category in ('volume', 'turnover', 'valuation', 'liquidity', 'oil_etf'):
        from .editorial_cn import build_context
    elif category in ('token_cost', 'us_barometer', 'us_structure'):
        from .editorial_market import build_context
    else:
        raise ValueError('unsupported_category')
    context = build_context(category, payload)
    if (not isinstance(context.get('slots'), dict)
            or any(not isinstance(value, str) for value in context['slots'].values())
            or not isinstance(context.get('bindings'), dict)):
        raise ValueError('invalid_context')
    return context


def _options(pack: dict, scenario: str) -> dict:
    return {role: [row for row in pack['modules'][collection]
                   if 'scenarios' not in row or scenario in row['scenarios']]
            for role, collection in _ROLES.items()}


def _plan(options: dict, selection: dict | None) -> dict:
    plan = selection if selection is not None else {role: rows[0]['id'] for role, rows in options.items()}
    if not isinstance(plan, dict) or set(plan) != set(_ROLES):
        raise ValueError('invalid_plan')
    if any(not isinstance(plan[role], str) or plan[role] not in {row['id'] for row in rows}
           for role, rows in options.items()):
        raise ValueError('invalid_plan')
    return dict(plan)


def build_candidate(category: str, payload: dict, *, pack=None, selection=None) -> dict:
    if category == 'gpu_rental':
        from .gpu_editorial import build_candidate as gpu_candidate
        return {'category': category, **gpu_candidate(payload, pack=pack, selection=selection)}
    if category not in SUPPORTED_CATEGORIES:
        raise ValueError('unsupported_category')
    pack = load_pack(category) if pack is None else _checked_pack(category, pack)
    context = _context(category, payload)
    scenario, slots = context['scenario'], context['slots']
    if scenario not in {row['id'] for row in pack['scenarios']}:
        raise ValueError('unsupported_scenario')
    if set(slots) != set(pack['slot_roles']['lead'] + pack['slot_roles']['comparison']):
        raise ValueError('invalid_context')
    options = _options(pack, scenario)
    plan = _plan(options, selection)
    chosen = {role: next(row for row in rows if row['id'] == plan[role])
              for role, rows in options.items()}
    title = chosen['headline']['text']
    body = (chosen['lead']['template'].format_map(slots)
            + chosen['comparison']['template'].format_map(slots) + chosen['closing']['text'])
    if (not title or not body or '\n' in title or len(title) > 150 or len(body) > 4000
            or any(phrase in title + body for phrase in _FORBIDDEN)):
        raise ValueError('invalid_plan')
    return {
        'status': 'candidate', 'category': category, 'title': title, 'body': body,
        'scenario': scenario, 'plan': plan, 'pack_id': pack['pack_id'],
        'pack_version': pack['version'], 'pack_digest': pack['digest'],
        'review_status': pack['status'], 'module_snapshot': deepcopy(chosen),
        'facts_digest': _digest({'data_date': payload.get('data_date'),
                                 'facts': payload.get('facts'),
                                 'bindings': context['bindings']}),
        'bindings': context['bindings'], 'hard_checks': {'facts': True, 'structure': True},
        'example_ids': [row['id'] for row in pack.get('examples', [])
                        if row.get('scenario') == scenario][:3],
        'style_review': 'pending_editor_review',
    }


def apply_editorial(payload: dict, category: str, team_settings: dict | None, *, mode=None) -> dict:
    if category not in SUPPORTED_CATEGORIES:
        return payload
    baseline = {'title': payload.get('title', ''), 'body': payload.get('dynamic_body', '')}
    trace = {'mode': mode, 'baseline': baseline}
    try:
        settings = get_settings()
        selected_mode = mode or settings.briefs_editorial_mode
        if category == 'gpu_rental':
            from .gpu_editorial import apply_gpu_editorial
            # An explicit per-call mode wins. A configured off at either scope
            # remains a kill switch; otherwise retain the existing GPU override.
            specific = settings.briefs_gpu_editorial_mode
            gpu_mode = (mode if mode is not None else 'off'
                        if 'off' in (selected_mode, specific) else
                        'active' if 'active' in (selected_mode, specific) else 'shadow')
            return apply_gpu_editorial(payload, team_settings, mode=gpu_mode)
        if selected_mode == 'off':
            return payload
        trace['mode'] = selected_mode
        payload['editorial'] = trace
        if selected_mode not in {'shadow', 'active'}:
            raise ValueError('invalid_mode')
        saved = ((team_settings or {}).get('skills') or {}).get(category) or {}
        reason = ('custom_body_template' if payload.get('body_template_custom') else
                  'skill_disabled' if saved.get('enabled') is False else
                  'custom_instructions' if saved.get('instructions') else None)
        if reason:
            trace.update(status='skipped', reason=reason)
            return payload
        pack = load_pack(category)
        reviewed = (pack['status'] == 'approved'
                    and bool((pack.get('review') or {}).get('approved_by'))
                    and bool((pack.get('review') or {}).get('reviewed_at')))
        if selected_mode == 'active' and not reviewed:
            trace.update(mode='shadow', requested_mode='active', reason='pack_not_reviewed')
        candidate = build_candidate(category, payload, pack=pack)
        if trace['mode'] == 'active':
            from .gpu_editorial_ai import select_plan
            context = _context(category, payload)
            result = select_plan(team_settings, {
                'category': category, 'label': CATEGORIES[category], 'scenario': context['scenario'],
                'options': _options(pack, context['scenario']),
                'writing_order': context.get('writing_order', ''),
                'examples': [row for row in pack.get('examples', [])
                             if row.get('scenario') == context['scenario']][:3],
            })
            trace['selector'] = {key: value for key, value in result.items() if key != 'plan'}
            if result.get('status') == 'ok':
                candidate = build_candidate(category, payload, pack=pack, selection=result['plan'])
            trace['selection_status'] = ('model_selected' if result.get('status') == 'ok'
                                         else 'deterministic_fallback')
        trace.update(status='shadow' if trace['mode'] == 'shadow' else 'active', candidate=candidate)
        if trace['mode'] == 'active':
            payload['title'], payload['dynamic_body'] = candidate['title'], candidate['body']
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        trace.update(status='fallback', reason=str(exc) if isinstance(exc, ValueError)
                     else 'editorial_unavailable')
    except Exception:
        trace.update(status='fallback', reason='editorial_unavailable')
    payload['editorial'] = trace
    return payload
