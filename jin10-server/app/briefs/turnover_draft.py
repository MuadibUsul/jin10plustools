"""Render an editorial turnover draft with program-bound evidence fragments."""

from collections import Counter
import re
import unicodedata

from .editorial_cn import _day, _turnover


_PREFIX = '【A股温度计】'
_BODY_REQUIRED = ('date', 'low_turnover', 'high_turnover', 'reference_line')
_TITLE_ONLY = frozenset({'low_title', 'high_title', 'reference_title'})
_TOKEN = re.compile(r'\{\{([a-z][a-z0-9_]*)\}\}')
_PUNCTUATION = re.compile(r'[ \t\n　，。、；：？！,.;:?!（）()「」“”｜—]')

# The model can join, reorder and frame observations. Direction, trend, timing
# and other factual language come from evidence, including in headlines.
_CONNECTIVES = (
    'A股换手观察', 'A股交投观察', 'A股温度计', 'A股换手率', 'A股交投',
    '换手分布观察', '换手率观察', '换手结构观察', '交投观察', '市场观察',
    '从换手率均线看', '从换手结构看', '从换手分布看', '从数据看', '从两项均线看',
    '与前一交易日相比', '对照前一交易日', '对照参考线',
    '值得关注的是', '值得留意的是', '这组数据中', '在两项指标中', '从这组数据看',
    '在换手率方面', '就换手率而言', '本期观察重点', '本期关注', '换手率方面',
    '先从', '先看', '再看', '回看', '另看', '重点关注', '关注', '观察',
    '具体来看', '综合来看', '整体来看', '合起来看', '对照来看', '放在一起看',
    '可以看到', '可以看出', '数据显示', '数据表明', '可以留意', '有待观察',
    '与此同时', '另一方面', '相较之下', '作为对照', '与之对照', '相应地',
    '其中', '另外', '此外', '同时', '同期', '同日', '同样', '不过', '但是', '虽然', '尽管',
    '截至', '当日', '本期', '先', '再', '也', '和', '与', '而', '但', '则', '看',
)
_CONNECTIVE = re.compile('|'.join(re.escape(word) for word in sorted(_CONNECTIVES, key=len, reverse=True)))


def build_context(payload, examples, instructions):
    from .sample_story import _turnover_story

    day = _day(payload.get('data_date'))
    facts = payload.get('facts')
    if not isinstance(facts, dict):
        raise ValueError('turnover_draft_missing_facts')
    scenario, _, _, _ = _turnover(payload, day, facts)
    features = facts.get('editorial_features')
    verified_streak = None
    if isinstance(features, dict):
        count = features.get('trend_streak')
        if (features.get('verified_consecutive_sessions') is True
                and isinstance(count, int) and not isinstance(count, bool) and count >= 2
                and features.get('comparison_end') == day.isoformat()
                and scenario in ('warming', 'cooling') and features.get('trend_direction') == scenario):
            verified_streak = features

    clauses, _ = _turnover_story(facts, scenario, verified_streak)
    statements = {name: variants[0].removesuffix('。') for name, variants in clauses}
    low = statements['low_turnover'].replace('股票占比的9日均线', '股票占比9日均线')
    high = statements['high_turnover'].replace('股票占比的9日均线', '股票占比9日均线')
    reference = statements['comparison']
    position, _, gap = reference.partition('，')
    reference_title = f'高换手占比均线与17%参考线的{gap}' if gap.startswith(('差距收窄', '差距扩大')) else position
    activity = {
        'warming': '换手率均线显示交投活跃度回升',
        'cooling': '换手率均线显示交投活跃度回落',
        'mixed': '两项换手率占比均线对交投活跃度的指示有所分化',
        'unchanged': '两项换手率占比均线较前一交易日持平',
    }
    tokens = {
        'date': f'{day.year}年{day.month}月{day.day}日',
        'low_turnover': low,
        'high_turnover': high,
        'reference_line': reference,
        'activity': activity[scenario],
        'low_title': low.split('，', 1)[0].replace('A股换手率低于3%的股票占比9日均线', '低换手股票占比均线'),
        'high_title': high.split('，', 1)[0].replace('A股换手率高于5%的股票占比9日均线', '高换手股票占比均线'),
        'reference_title': reference_title,
    }
    if 'trend' in statements:
        tokens['streak'] = statements['trend']
    semantics = {
        'date': '数据日，仅正文',
        'low_turnover': '低于3%的股票占比9日均线及前一交易日变动',
        'high_turnover': '高于5%的股票占比9日均线及前一交易日变动',
        'reference_line': '高换手占比均线相对17%参考线的位置及差距变化',
        'activity': '两项均线合起来反映的交投变化，可选',
        'low_title': '低换手占比均线的简短事实，仅标题',
        'high_title': '高换手占比均线的简短事实，仅标题',
        'reference_title': '参考线关系的简短事实，仅标题',
        'streak': '当前已核验的连续回暖或降温，可选',
    }
    return {'facts': tokens, 'token_semantics': {key: semantics[key] for key in tokens},
            'required_body_tokens': list(_BODY_REQUIRED), 'examples': examples,
            'instructions': instructions, 'data_date': day.isoformat()}


def _validate_connectives(prose):
    if any(unicodedata.category(char) in {'Nd', 'Nl', 'No'} for char in prose):
        raise ValueError('turnover_draft_raw_number')
    if '{' in prose or '}' in prose:
        raise ValueError('turnover_draft_malformed_token')
    offset = 0
    while offset < len(prose):
        match = _PUNCTUATION.match(prose, offset) or _CONNECTIVE.match(prose, offset)
        if not match:
            raise ValueError('turnover_draft_unbound_prose')
        offset = match.end()


def _render_field(text, facts, forbidden):
    tokens = []
    end = 0
    for match in _TOKEN.finditer(text):
        between = text[end:match.start()]
        _validate_connectives(between)
        if tokens and not between.strip():
            raise ValueError('turnover_draft_adjacent_tokens')
        name = match[1]
        if name not in facts or name in forbidden:
            raise ValueError('turnover_draft_invalid_token')
        tokens.append(name)
        end = match.end()
    _validate_connectives(text[end:])
    counts = Counter(tokens)
    if any(count != 1 for count in counts.values()):
        raise ValueError('turnover_draft_duplicate_token')
    return _TOKEN.sub(lambda match: facts[match[1]], text), counts


def render_draft(context, draft):
    if (not isinstance(draft, dict) or set(draft) != {'title', 'body'}
            or any(not isinstance(field, str) or not 1 <= len(field) <= 1000 for field in draft.values())):
        raise ValueError('turnover_draft_invalid_format')
    title = draft['title'].strip().removeprefix(_PREFIX).strip()
    body = draft['body'].replace('\r\n', '\n').strip()
    paragraphs = re.split(r'\n(?:[ \t]*\n)*', body)
    if ('\r' in title or '\n' in title or not 1 <= len(paragraphs) <= 3
            or any(not paragraph.strip() for paragraph in paragraphs)):
        raise ValueError('turnover_draft_invalid_format')

    facts = context['facts']
    title, title_counts = _render_field(title, facts, {'date'})
    body, body_counts = _render_field(body, facts, _TITLE_ONLY)
    if not title_counts or not set(context['required_body_tokens']) <= body_counts.keys():
        raise ValueError('turnover_draft_missing_evidence')
    if any(long in title_counts and short in title_counts for long, short in (
            ('low_turnover', 'low_title'), ('high_turnover', 'high_title'), ('reference_line', 'reference_title'))):
        raise ValueError('turnover_draft_duplicate_token')
    title = _PREFIX + title
    if len(title) > 100 or len(body) > 420:
        raise ValueError('turnover_draft_invalid_length')
    return title, body
