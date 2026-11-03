"""A product's source dependencies, separate from the full archival inventory."""
from datetime import date,timedelta
from .data_reliability import extract,digest

FILES={'volume':('plot-data.json',),'turnover':('source.json',),
    'valuation':('buffett-plot-data.json','erp-plot-data.json'),
    'liquidity':('liquidity-snapshot.json',),'oil_etf':('result.json',),
    'us_barometer':('us-snapshot.json',),'us_structure':('us-structure-snapshot.json',),
    'token_cost':('ai-compute-source.json',),'gpu_rental':('ai-compute-source.json',)}


def publication_inputs(category,folder,payload):
    target=payload['data_date'];points=[]
    for name in FILES[category]:points.extend(extract(category,folder,payload,only_file=name))
    points=[p for p in points if p['date']<=target]
    if category in ('turnover','valuation','us_barometer','us_structure'):
        end=date.fromisoformat(target)
        try:start=end.replace(year=end.year-10)
        except ValueError:start=end.replace(year=end.year-10,day=28)
        if category=='turnover':start-=timedelta(days=45) # MA9 context at the first plotted session.
        start=min(start.isoformat(),((payload.get('facts') or {}).get('editorial_features') or {}).get('comparison_start',start.isoformat()))
        older_gdp=[p for p in points if p['series']=='gdp' and p['date']<start]
        prior=max((p['date'] for p in older_gdp),default=None)
        points=[p for p in points if p['date']>=start or p['series']=='gdp' and p['date']==prior]
    keyed={(p['series'],p['method'],p['date']):p for p in points}
    return {'version':2,'files':list(FILES[category]),'points':[keyed[k] for k in sorted(keyed)]}


def publication_digest(category,folder,payload):
    values=publication_inputs(category,folder,payload)
    return digest({'version':2,'category':category,'data_date':payload['data_date'],'points':values['points']}) if values['points'] else None
