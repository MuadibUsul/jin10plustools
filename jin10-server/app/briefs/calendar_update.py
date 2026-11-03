"""Validate an official annual calendar evidence bundle; dry-run by default.

This deliberately cannot invent unpublished holidays. The bundle is prepared
from an official notice and includes its original bytes, hash and reviewer.
"""
import argparse,hashlib,json
from datetime import date,datetime
from pathlib import Path
from urllib.parse import urlparse
from .sources import save
from .trading_calendar import calendars

DOMAINS={'xshg':('sse.com.cn','szse.cn'),'cfets':('chinamoney.com.cn','pbc.gov.cn')}


def validate_bundle(bundle,evidence,current):
    market=bundle.get('market');year=bundle.get('year')
    if market not in DOMAINS or not isinstance(year,int) or isinstance(year,bool):raise ValueError('无效市场或年份')
    host=(urlparse(bundle.get('source_url','')).hostname or '').lower()
    if not any(host==h or host.endswith('.'+h) for h in DOMAINS[market]):raise ValueError('必须使用该市场官方公告')
    if hashlib.sha256(evidence).hexdigest()!=bundle.get('source_sha256'):raise ValueError('公告证据哈希不符')
    if str(year) not in evidence.decode('utf-8','ignore'):raise ValueError('公告未包含目标年份')
    if not bundle.get('reviewed_by') or not bundle.get('reviewed_at'):raise ValueError('缺少官方日历核对记录')
    if datetime.fromisoformat(bundle['reviewed_at'].replace('Z','+00:00')).tzinfo is None:raise ValueError('核对时间须带时区')
    if year!=date.fromisoformat(current[market]['end']).year+1:raise ValueError('只能逐年追加，不可覆盖已核验历史或跳过年份')
    def day(value):
        parsed=date.fromisoformat(value)
        if parsed.year!=year:raise ValueError('日期超出目标年份')
        return parsed
    update=json.loads(json.dumps(current[market]))
    if market=='xshg':
        closed=bundle.get('closed')
        if not isinstance(closed,list) or len(closed)<10 or len(closed)!=len(set(closed)):raise ValueError('全年休市日期不完整或重复')
        for value in closed:day(value)
        update['closed']=sorted(set(update['closed'])|set(closed))
    else:
        ranges=bundle.get('closed_ranges');opened=bundle.get('open_weekends')
        if not isinstance(ranges,list) or len(ranges)<6 or not isinstance(opened,list):raise ValueError('全年银行间日历不完整')
        for first,last in ranges:
            if day(first)>day(last):raise ValueError('休市区间倒置')
        for value in opened:
            if day(value).weekday()<5 or any(first<=value<=last for first,last in ranges):raise ValueError('调休开市日期冲突')
        update['closed_ranges']+=ranges;update['open_weekends']=sorted(set(update['open_weekends'])|set(opened))
    update['end']=str(year)+'-12-31'
    update.setdefault('annual_evidence',{})[str(year)]={k:bundle[k] for k in ('source_url','source_sha256','reviewed_by','reviewed_at')}
    return market,update


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--evidence',type=Path,required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    bundle=json.loads(a.bundle.read_text(encoding='utf-8'));market,updated=validate_bundle(bundle,a.evidence.read_bytes(),calendars())
    if a.apply:
        target=Path(__file__).parent/'calendars/sessions.json';all_calendars=dict(calendars());all_calendars[market]=updated
        save(target,all_calendars);calendars.cache_clear()
    print(json.dumps({'applied':a.apply,'market':market,'verified_through':updated['end']},ensure_ascii=False))

if __name__=='__main__':main()
