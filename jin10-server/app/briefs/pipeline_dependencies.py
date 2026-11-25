"""Category-scoped provenance for shared functions and their real helper calls."""
import ast
from pathlib import Path


def function_slice(path,names):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    functions={n.name:n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    selected=set();pending=list(names)
    while pending:
        name=pending.pop()
        if name not in functions or name in selected:continue
        selected.add(name)
        pending.extend(n.id for n in ast.walk(functions[name]) if isinstance(n,ast.Name) and n.id in functions)
    nodes=[]
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
            if n.name in selected:nodes.append(n)
        elif isinstance(n,(ast.Import,ast.ImportFrom,ast.Assign,ast.AnnAssign,ast.ClassDef)):
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ADAPTERS' for t in n.targets):continue
            nodes.append(n)
    return ast.dump(ast.Module(body=nodes,type_ignores=[]),include_attributes=False).encode()


def dependencies(here,category):
    shared=('runner.py','policy.py','trading_calendar.py','series_store.py',
        'editorial_cn.py' if category in ('volume','turnover','valuation','liquidity','oil_etf') else 'editorial_market.py',
        'editorial_suite.py','editorial_authorization.py','sample_story.py','gpu_editorial_ai.py',
        'editorial_features.py','skill_production.py','body_templates.py','delivery.py','pipeline_dependencies.py')
    for name in shared:
        path=here/name
        if path.is_file():yield name,path.read_bytes()
    yield 'sources/functions',function_slice(here/'sources.py',[category])
    from .trading_calendar import calendars,market_for
    import json
    yield 'calendar/market',json.dumps(calendars().get(market_for(category),{'frequency':'daily'}),sort_keys=True).encode()
    renderer={'volume':'volume_chart','valuation':'valuation_chart','us_barometer':'us_barometer_chart','us_structure':'us_structure_chart'}.get(category)
    if renderer:yield 'charts/functions',function_slice(here/'charts.py',[renderer])
    if category in ('token_cost','gpu_rental'):
        yield 'ai_compute/functions',function_slice(here/'ai_compute.py',[category])
        yield 'ai_compute_charts/functions',function_slice(here/'ai_compute_charts.py',['token_chart' if category=='token_cost' else 'gpu_chart'])
        yield 'ai_compute_style.py',(here/'ai_compute_style.py').read_bytes()
    if category=='valuation':yield 'valuation_repair.py',(here/'valuation_repair.py').read_bytes()
    if category=='valuation':
        for path in sorted((here/'seed').glob('*')):
            if path.is_file():yield 'seed/'+path.name,path.read_bytes()
    if category=='liquidity':yield 'central_bank.py',(here/'central_bank.py').read_bytes()
    if category=='turnover':
        for name in ('turnover_draft.py','turnover_draft_ai.py'):
            yield name,(here/name).read_bytes()
    if category=='gpu_rental':yield 'gpu_editorial.py',(here/'gpu_editorial.py').read_bytes()
    directories=[here/'assets',here/'engines'/category]
    if category=='us_structure':directories.append(here/'engines/us_barometer')
    for folder in directories:
        if not folder.is_dir():continue
        for path in sorted(folder.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':yield path.relative_to(here).as_posix(),path.read_bytes()
    from .skill_ai import asset_dir
    for relative in ('SKILL.md','production/SKILL.md','references/history.md','production/style-pack.json'):
        path=asset_dir(category)/relative
        if path.is_file():yield 'skill/'+relative,path.read_bytes()
    if category in ('us_barometer','us_structure'):
        path=here/'editorial_packs'/(category+'.json');yield str(path.name),path.read_bytes()
    for name in ('copy_dates.py',):
        path=here.parent/'services'/name;yield 'services/'+name,path.read_bytes()
