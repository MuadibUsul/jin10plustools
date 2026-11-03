"""Verified historical features for copy; missing sessions break every streak."""
from decimal import Decimal
from .trading_calendar import previous_session,sessions
from .sources import read


def attach(payload,category,folder):
    if category not in ('volume','turnover'):return
    day=payload['data_date']
    if category=='volume':
        path=folder/'plot-data.json'
        if not path.is_file():return
        rows=[r for r in read(path) if r['date']<=day]
        def direction(a,b):
            x,y=Decimal(str(a['turn'])),Decimal(str(b['turn']))
            return 'up' if x>y else 'down' if x<y else 'flat'
    else:
        path=folder/'source.json'
        if not path.is_file():return
        raw=read(path)
        if not isinstance(raw,list):return
        from .engines.turnover.build_turnover import load_and_calculate
        rows=load_and_calculate(path,session_dates=sessions('xshg',raw[0]['date'],day))
        def direction(a,b):
            low=Decimal(str(a['low_ma9']))-Decimal(str(b['low_ma9']))
            high=Decimal(str(a['high_ma9']))-Decimal(str(b['high_ma9']))
            return 'warming' if low<=0<=high and (low or high) else 'cooling' if high<=0<=low and (low or high) else 'mixed'
    if len(rows)<2 or rows[-1]['date']!=day:return
    trend=direction(rows[-1],rows[-2]);count=0;start=day
    for index in range(len(rows)-1,0,-1):
        current,prior=rows[index],rows[index-1]
        if prior['date']!=previous_session('xshg',current['date']).isoformat() or direction(current,prior)!=trend:break
        count+=1;start=prior['date']
    if trend in ('up','down','warming','cooling'):
        payload.setdefault('facts',{})['editorial_features']={'trend_direction':trend,'trend_streak':count,
            'comparison_start':start,'comparison_end':day,'calendar':'xshg','verified_consecutive_sessions':True}
